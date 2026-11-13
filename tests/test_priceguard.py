import sys
import unittest
import unittest.mock
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demo
from priceguard.fetch import Blocked, FetchError, Fetcher
from priceguard.models import Product, load_products
from priceguard.report import render, sparkline
from priceguard.monitor import check_product, deliver, money
from priceguard.parse import parse_price, parse_reading
from priceguard.store import Store

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 11, 13, 8, 0, tzinfo=TZ)
T1 = datetime(2026, 11, 13, 12, 0, tzinfo=TZ)
URL = "http://shop.test/p1"
P = Product("W1", "Wkrętarka X", Decimal("209"), "EUR", "2 akumulator", "Shop A", URL)


def page(price="219.00", title="Wkrętarka X, zestaw z 2 akumulatorami", cur="EUR", avail="InStock", gtin=""):
    g = f'"gtin13":"{gtin}",' if gtin else ""
    return (f'<script type="application/ld+json">{{"@type":"Product","name":"{title}",{g}"offers":'
            f'{{"price":"{price}","priceCurrency":"{cur}","availability":"https://schema.org/{avail}"}}}}</script>')


class Web:
    """Fake HTTP: url -> (status, text). Records calls."""
    def __init__(self, pages, robots=(404, "")):
        self.pages, self.robots, self.calls = pages, robots, []

    def __call__(self, url):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots
        item = self.pages[url]
        return item.pop(0) if isinstance(item, list) else item


def fetcher(pages, **kw):
    return Fetcher(get=Web(pages, kw.pop("robots", (404, ""))), delay=0, sleep=lambda s: None, **kw)


class ParseTests(unittest.TestCase):
    def test_price_formats(self):
        for raw, want in [("199", "199"), ("199,00 €", "199.00"), ("1.299,00 €", "1299.00"),
                          ("1,299.00", "1299.00"), ("1 299,50", "1299.50")]:
            self.assertEqual(parse_price(raw), Decimal(want), raw)
        self.assertIsNone(parse_price("ask us"))

    def test_jsonld_with_shipping_and_stock(self):
        html = ('<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"X","offers":'
                '[{"price":199,"priceCurrency":"EUR","availability":"https://schema.org/OutOfStock",'
                '"shippingDetails":{"shippingRate":{"value":"4.90"}}}]}]}</script>')
        r = parse_reading(html)
        self.assertEqual((r.price, r.currency, r.available, r.shipping), (Decimal("199"), "EUR", False, Decimal("4.90")))

    def test_microdata_fallback_reads_symbol_currency(self):
        html = ('<h1 itemprop="name">Y</h1><span itemprop="price">199,00 €</span>'
                '<link itemprop="availability" href="https://schema.org/InStock">')
        r = parse_reading(html)
        self.assertEqual((r.price, r.currency, r.available, r.title), (Decimal("199.00"), "EUR", True, "Y"))

    def test_no_price_is_none_not_a_guess(self):
        self.assertIsNone(parse_reading("<p>Cena w koszyku: 199 zł</p>"))

    def test_broken_jsonld_is_ignored(self):
        self.assertIsNone(parse_reading('<script type="application/ld+json">{oops</script>'))


class FetchTests(unittest.TestCase):
    def test_robots_disallow(self):
        f = fetcher({URL: (200, "x")}, robots=(200, "User-agent: *\nDisallow: /p1\n"))
        with self.assertRaises(Blocked):
            f.fetch(URL)

    def test_missing_robots_allows(self):
        self.assertEqual(fetcher({URL: (200, "ok")}).fetch(URL), "ok")

    def test_unreadable_robots_is_conservative(self):
        with self.assertRaises(Blocked):
            fetcher({URL: (200, "ok")}, robots=(500, "")).fetch(URL)

    def test_retries_on_429_then_succeeds(self):
        self.assertEqual(fetcher({URL: [(429, ""), (503, ""), (200, "ok")]}).fetch(URL), "ok")

    def test_gives_up_after_retries(self):
        with self.assertRaises(FetchError):
            fetcher({URL: [(500, "")] * 5}).fetch(URL)

    def test_no_retry_on_404(self):
        web = Web({URL: (404, "")})
        with self.assertRaises(FetchError):
            Fetcher(get=web, delay=0, sleep=lambda s: None).fetch(URL)
        self.assertEqual(web.calls.count(URL), 1)

    def test_pauses_between_requests_to_same_host(self):
        slept, now = [], [100.0]
        f = Fetcher(get=Web({URL: (200, "a")}), delay=2.0, sleep=slept.append, clock=lambda: now[0])
        f.fetch(URL)
        now[0] += 0.5
        f.fetch(URL)
        self.assertEqual(slept, [1.5])


class MonitorTests(unittest.TestCase):
    def run_pass(self, store, html, now, product=P, pct="5"):
        status = check_product(product, fetcher({URL: (200, html)}), store, now, Decimal(pct), "pl")
        return status

    def test_first_reading_is_baseline_without_alert(self):
        s = Store()
        self.assertEqual(self.run_pass(s, page(), T0), "OK")
        self.assertEqual(s.pending_alerts(), [])

    def test_drop_alert_text_matches_the_client_example(self):
        s = Store()
        self.run_pass(s, page("219.00"), T0)
        self.run_pass(s, page("199.00"), T1)
        self.assertEqual([t for _, t in s.pending_alerts()], [
            "Wkrętarka X. Konkurent Shop A obniżył cenę z 219 € do 199 €. Twoja cena to 209 €."])

    def test_small_drop_and_price_increase_do_not_alert(self):
        s = Store()
        self.run_pass(s, page("219.00"), T0)
        self.run_pass(s, page("212.00"), T1)  # -3.2%, below 5%
        self.run_pass(s, page("250.00"), T1)
        self.assertEqual(s.pending_alerts(), [])

    def test_back_in_stock(self):
        s = Store()
        self.run_pass(s, page(avail="OutOfStock"), T0)
        self.run_pass(s, page(avail="InStock"), T1)
        self.assertEqual(len(s.pending_alerts()), 1)
        self.assertIn("znów dostępny", s.pending_alerts()[0][1])

    def test_failed_read_keeps_last_known_price(self):
        s = Store()
        self.run_pass(s, page("219.00"), T0)
        status = self.run_pass(s, "<p>redesign</p>", T1)
        self.assertTrue(status.startswith("TO CHECK"))
        self.assertEqual(s.last_ok(P.sku, URL).price, Decimal("219.00"))
        self.assertTrue(s.last_any(P.sku, URL).status.startswith("TO CHECK"))
        self.assertEqual(s.pending_alerts(), [])

    def test_currency_mismatch_is_not_compared(self):
        s = Store()
        self.assertIn("currency PLN", self.run_pass(s, page("199", cur="PLN"), T0))
        self.assertIsNone(s.last_ok(P.sku, URL))

    def test_variant_mismatch_is_not_compared(self):
        s = Store()
        self.assertIn("different variant", self.run_pass(s, page(title="Wkrętarka X, 1 akumulator"), T0))

    def test_unreachable_and_blocked(self):
        s = Store()
        f = fetcher({URL: (500, "")}, retries=0)
        self.assertIn("could not be fetched", check_product(P, f, s, T0, Decimal(5)))
        f = fetcher({URL: (200, "x")}, robots=(200, "User-agent: *\nDisallow: /\n"))
        self.assertIn("robots.txt", check_product(P, f, s, T0, Decimal(5)))

    def test_money_format(self):
        self.assertEqual(money(Decimal("199.00"), "EUR"), "199 €")
        self.assertEqual(money(Decimal("199.50"), "EUR", "de"), "199,50 €")
        self.assertEqual(money(Decimal("199.50"), "PLN", "en"), "199.50 PLN")


class FeatureTests(unittest.TestCase):
    def run_pass(self, store, html, now, product):
        return check_product(product, fetcher({URL: (200, html)}), store, now, Decimal("5"), "pl")

    def test_gtin_is_read_from_jsonld_and_microdata(self):
        self.assertEqual(parse_reading(page(gtin="5900000000011")).gtin, "5900000000011")
        md = '<h1 itemprop="name">Y</h1><span itemprop="price">9</span><meta itemprop="gtin13" content="590 000 000 0028">'
        self.assertEqual(parse_reading(md).gtin, "5900000000028")
        self.assertEqual(parse_reading(page()).gtin, "")

    def test_ean_mismatch_is_not_compared_but_missing_gtin_is_not_a_claim(self):
        p = Product("W1", "Wkrętarka X", Decimal("209"), "EUR", "", "Shop A", URL, ean="5900000000011")
        s = Store()
        self.assertIn("different product", self.run_pass(s, page(gtin="5900000000099"), T0, p))
        self.assertIsNone(s.last_ok(p.sku, URL))
        self.assertEqual(self.run_pass(s, page(gtin="5900000000011"), T0, p), "OK")
        self.assertEqual(self.run_pass(s, page(), T1, p), "OK")  # page has no GTIN: nothing to compare

    def test_target_alert_fires_on_crossing_not_on_every_read(self):
        p = Product("W1", "Wkrętarka X", Decimal("209"), "EUR", "", "Shop A", URL, target=Decimal("195"))
        s = Store()
        self.run_pass(s, page("200"), T0, p)
        self.assertEqual(s.pending_alerts(), [])
        self.run_pass(s, page("190"), T1, p)
        self.run_pass(s, page("190"), T1, p)  # still below: no second alert
        self.assertEqual(sum("próg 195 €" in t for _, t in s.pending_alerts()), 1)
        self.run_pass(s, page("210"), T1, p)  # back above
        self.run_pass(s, page("190"), T1, p)  # crosses again
        self.assertEqual(sum("próg 195 €" in t for _, t in s.pending_alerts()), 2)

    def test_target_hit_on_first_reading_still_alerts(self):
        p = Product("W1", "Wkrętarka X", Decimal("209"), "EUR", "", "Shop A", URL, target=Decimal("195"))
        s = Store()
        self.run_pass(s, page("190"), T0, p)
        self.assertEqual(len(s.pending_alerts()), 1)

    def test_lowest_note_only_with_enough_history(self):
        s = Store()
        self.run_pass(s, page("230"), T0, P)
        self.run_pass(s, page("210"), T0, P)  # drop, only 2 readings: no claim
        self.assertNotIn("najniższa", s.pending_alerts()[-1][1])
        self.run_pass(s, page("190"), T1, P)  # lowest of 3
        self.assertIn("To najniższa cena z 3 naszych odczytów.", s.pending_alerts()[-1][1])

    def test_lowest_note_not_added_when_an_earlier_price_was_lower(self):
        s = Store()
        for price in ("150", "230", "210", "180"):
            self.run_pass(s, page(price), T0, P)
        self.assertNotIn("najniższa", s.pending_alerts()[-1][1])

    def test_csv_optional_columns_and_backward_compatibility(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        old = os.path.join(d, "old.csv")
        open(old, "w", encoding="utf-8").write("sku,name,my_price,currency,variant,competitor,url\nA,X,10,EUR,,S,http://x/1\n")
        a = load_products(old)[0]
        self.assertEqual((a.target, a.ean), (None, ""))
        new = os.path.join(d, "new.csv")
        open(new, "w", encoding="utf-8").write("sku,name,my_price,currency,variant,competitor,url,target_price,ean\nA,X,10,EUR,,S,http://x/1,\"8,50\",590 000\n")
        b = load_products(new)[0]
        self.assertEqual((b.target, b.ean), (Decimal("8.50"), "590000"))

    def test_report_marks_rows_to_check_and_escapes_html(self):
        evil = Product("E1", "<b>Evil</b>", Decimal("10"), "EUR", "", "S", URL)
        s = Store()
        check_product(evil, fetcher({URL: (200, "<p>nothing</p>")}), s, T0, Decimal(5))
        out = render([evil], s)
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt;", out)
        self.assertNotIn("<b>Evil</b>", out)
        self.assertIn('class="check"', out)
        self.assertIn("TO CHECK", out)

    def test_sparkline_needs_two_points(self):
        self.assertIn("not enough data", sparkline([Decimal(1)]))
        self.assertIn("<polyline", sparkline([Decimal(3), Decimal(2), Decimal(2)]))  # flat/equal values do not crash


class DashboardTests(unittest.TestCase):
    def test_wishlist_validation(self):
        from priceguard.wishlist import parse_links, parse_money
        self.assertEqual(parse_links("https://a.example/x\nShop B | http://b.example/y"),
                         [("a.example", "https://a.example/x"), ("Shop B", "http://b.example/y")])
        for bad in ("", "javascript:alert(1)", "ftp://x/y", "just text"):
            with self.assertRaises(ValueError):
                parse_links(bad)
        with self.assertRaises(ValueError):
            parse_links("\n".join(f"https://s{i}.example/p" for i in range(11)))
        self.assertEqual(parse_money("429,90"), Decimal("429.90"))
        self.assertIsNone(parse_money(""))
        for bad in ("abc", "-5", "0"):
            with self.assertRaises(ValueError):
                parse_money(bad)

    def test_dashboard_story_shopper_alerts_and_escaping(self):
        import demo_dashboard
        sent = []
        app, shop = demo_dashboard.build(sent)
        try:
            page_html = app.render()
        finally:
            shop.shutdown()
        self.assertIn("Konsola PlayStation 5 Slim: Shop A ma teraz 449 €, czyli w Twoim progu 450 €.", sent)
        self.assertEqual(sum("progu" in m for m in sent), 3)  # phone, PS5, shoes
        self.assertTrue(all("Twoja cena" not in m for m in sent))  # shopper mode: no "your price"
        self.assertEqual(page_html.count('class="card hit"'), 3)
        self.assertIn("TO CHECK: no price found", page_html)

    def test_http_flow_with_token_and_host_checks(self):
        import threading, urllib.request, urllib.parse, urllib.error
        from priceguard.web import App, make_server
        state = {"day": 1}
        shop, base = demo.start_shop(state)
        app = App(Store(), lambda: Fetcher(delay=0), "en", Decimal(3), None, clock=lambda: T0)
        srv = make_server(app, 0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        root = f"http://127.0.0.1:{port}"

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)

        def post(path, **data):
            req = urllib.request.Request(root + path, urllib.parse.urlencode(data).encode(), method="POST")
            try:
                return opener.open(req).status
            except urllib.error.HTTPError as ex:
                return ex.code
        try:
            self.assertEqual(post("/add", name="PS5", links=base + "/m/ps5-a", target="450", currency="EUR", token="wrong"), 403)
            self.assertEqual(post("/add", name="PS5", links="not a link", currency="EUR", token=app.token), 400)
            self.assertEqual(post("/add", name="<script>x</script>", links=base + "/m/ps5-a", target="450",
                                  currency="EUR", token=app.token), 303)
            self.assertEqual(post("/check", token=app.token), 303)
            app.join_check()
            body = urllib.request.urlopen(root + "/").read().decode()
            self.assertIn("&lt;script&gt;x&lt;/script&gt;", body)
            self.assertNotIn("<script>x</script>", body)
            self.assertIn("479.00", body)
            req = urllib.request.Request(root + "/", headers={"Host": "evil.example"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 403)
            self.assertEqual(post("/delete", id="1", token=app.token), 303)
            self.assertNotIn("PS5", urllib.request.urlopen(root + "/").read().decode())
        finally:
            srv.shutdown()
            shop.shutdown()


class MarketTests(unittest.TestCase):
    R = {"EUR": Decimal(1), "USD": Decimal(1), "GBP": Decimal(1), "PLN": Decimal("0.25"), "CZK": Decimal("0.04"),
         "CNY": Decimal("0.12"), "CHF": Decimal(1)}

    def test_landed_cost_rules(self):
        from priceguard.markets import make_landed
        L = lambda src, dest, price, **kw: make_landed(src, dest, self.R, **kw)(Decimal(price))
        self.assertEqual(L("DE", "DE", 100), Decimal("105.00"))            # same country: price + 5 shipping
        self.assertEqual(L("NL", "DE", 121), Decimal("128.00"))            # EU: 121 incl. 21% -> 100 net -> +19% + 9 shipping
        self.assertEqual(L("US", "DE", 100), Decimal("148.75"))            # (100+25 shipping) * 1.19
        self.assertEqual(L("US", "DE", 100, duty_pct=Decimal(10)), Decimal("163.63"))  # + 10% duty before VAT
        self.assertEqual(L("GB", "DE", 120), Decimal("148.75"))            # UK VAT removed on export
        self.assertEqual(L("US", "PL", 100), Decimal("615.00"))            # 125 * 1.23 = 153.75 EUR / 0.25 = PLN
        self.assertEqual(L("US", "DE", 100, shipping_eur=Decimal(0)), Decimal("119.00"))

    def test_country_of_url_and_rates(self):
        from priceguard.markets import country_of_url, parse_rates
        self.assertEqual(country_of_url("https://www.cyberport.de/x"), "DE")
        self.assertEqual(country_of_url("https://www.bhphotovideo.com/c/product/1"), "US")
        self.assertEqual(country_of_url("https://shop.example.cz/p"), "CZ")
        self.assertEqual(country_of_url("https://unknown.example.com/p"), "")
        self.assertEqual(parse_rates("usd=0.9, PLN=0.2")["USD"], Decimal("0.9"))
        with self.assertRaises(ValueError):
            parse_rates("USD=abc")

    def test_target_is_compared_with_the_total_cost(self):
        p = Product("W1", "X", Decimal(0), "USD", "", "US shop", URL, Decimal("150"), "", landed=lambda x: x * 2, target_currency="EUR")
        s = Store()
        self.assertEqual(check_product(p, fetcher({URL: (200, page("80", cur="USD"))}), s, T0, Decimal(5), "pl"), "OK")
        self.assertEqual(s.pending_alerts(), [])                           # 80 USD -> 160 total, above the target 150
        check_product(p, fetcher({URL: (200, page("70", cur="USD"))}), s, T1, Decimal(50), "pl")
        text = s.pending_alerts()[0][1]
        self.assertIn("w Twoim progu 150 €", text)
        self.assertIn("Szacowany koszt końcowy: 140 €", text)

    def test_wish_with_markets_links_and_migration(self):
        import sqlite3, tempfile, os
        from priceguard.wishlist import add_link, add_wish, list_wishes, wish_products
        s = Store()
        wid = add_wish(s, "PS5", [("Cyberport", "https://www.cyberport.de/p/1")], "Konsole", Decimal(450), dest="PL",
                       markets=["DE", "US", "de"], ship=Decimal(7), duty=Decimal(0))
        add_link(s, wid, "", "https://www.bhphotovideo.com/c/product/9")
        w = list_wishes(s)[0]
        self.assertEqual((w["dest"], w["currency"], w["markets"], w["ship"]), ("PL", "PLN", ["DE", "US"], Decimal(7)))
        self.assertEqual([c for _, _, c in w["links"]], ["DE", "US"])
        prods = wish_products(s)
        self.assertEqual([p.currency for p in prods], ["EUR", "USD"])      # each link in its shop's currency
        self.assertTrue(all(p.landed for p in prods) and prods[0].target_currency == "PLN")
        for bad in (dict(dest="XX"), dict(markets=["ZZ"])):
            with self.assertRaises(ValueError):
                add_wish(s, "Y", [], **bad)
        with self.assertRaises(ValueError):
            add_link(s, 999, "", "https://a.example/p")
        # an older database (before destinations existed) is upgraded in place
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        con = sqlite3.connect(path)
        con.executescript("CREATE TABLE wishes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', "
                          "currency TEXT NOT NULL DEFAULT 'EUR', target TEXT, ean TEXT NOT NULL DEFAULT '', variant TEXT NOT NULL DEFAULT '');"
                          "CREATE TABLE links (id INTEGER PRIMARY KEY AUTOINCREMENT, wish_id INTEGER NOT NULL, shop TEXT NOT NULL, url TEXT NOT NULL, UNIQUE (wish_id, url));"
                          "INSERT INTO wishes (name) VALUES ('old item');")
        con.commit(); con.close()
        old = Store(path)
        self.assertEqual(list_wishes(old)[0]["dest"], "")

    def test_quick_compare_does_not_save_and_sorts_by_total_cost(self):
        from priceguard import markets
        from priceguard.web import App
        shop, base = demo.start_shop({"day": 3})
        alt = base.replace("127.0.0.1", "localhost")
        with unittest.mock.patch.dict(markets.EXTRA_HOSTS, {"127.0.0.1": "DE", "localhost": "US"}):
            app = App(Store(), lambda: Fetcher(delay=0), "pl", Decimal(3), None, clock=lambda: T0)
            try:
                out = app.quick({"name": ["Smartfon"], "dest": ["DE"], "markets": ["DE", "US"],
                                 "links": [f"Shop A | {base}/m/phone-a\nUS shop | {alt}/m/phone-b\nBroken | {base}/m/tv-a"]})
            finally:
                shop.shutdown()
        self.assertLess(out.index("≈ 744.00 €"), out.index("≈ 919.08 €"))  # cheapest total first
        self.assertIn("TO CHECK: no price found", out)
        self.assertIn("Zapisz na wishliście", out)
        self.assertEqual(list_wishes_count(app.store), 0)                  # nothing saved


def list_wishes_count(store):
    from priceguard.wishlist import list_wishes
    return len(list_wishes(store))


class OutboxTests(unittest.TestCase):
    def test_marks_sent_only_after_success_and_never_resends(self):
        s = Store()
        s.add_alert("a", "one")
        s.add_alert("b", "two")
        got = []
        self.assertEqual(deliver(s, got.append), 2)
        self.assertEqual(deliver(s, got.append), 0)
        self.assertEqual(got, ["one", "two"])

    def test_outage_keeps_alerts_queued(self):
        s = Store()
        s.add_alert("a", "one")
        s.add_alert("b", "two")

        def down(text):
            raise OSError("telegram down")

        self.assertEqual(deliver(s, down), 0)
        self.assertEqual([t for _, t in s.pending_alerts()], ["one", "two"])
        got = []
        self.assertEqual(deliver(s, got.append), 2)  # next run delivers them

    def test_same_key_is_not_queued_twice(self):
        s = Store()
        self.assertTrue(s.add_alert("k", "x"))
        self.assertFalse(s.add_alert("k", "x"))


class DemoTests(unittest.TestCase):
    def test_demo_story(self):
        out = demo.main("pl")
        self.assertIn("obniżył cenę z 219 € do 199 €", out)
        self.assertIn("znów dostępny", out)
        self.assertEqual(out.count("Telegram>"), 5)
        self.assertIn("osiągnęła Twój próg 195 €", out)
        self.assertIn("To najniższa cena z 3 naszych odczytów.", out)
        self.assertIn("different product (EAN", out)
        self.assertIn("blocked by robot", out)
        self.assertIn("different varian", out)
        self.assertIn("no price found", out)


if __name__ == "__main__":
    unittest.main()
