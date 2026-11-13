import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demo
from priceguard.fetch import Blocked, FetchError, Fetcher
from priceguard.models import Product
from priceguard.monitor import check_product, deliver, money
from priceguard.parse import parse_price, parse_reading
from priceguard.store import Store

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 11, 13, 8, 0, tzinfo=TZ)
T1 = datetime(2026, 11, 13, 12, 0, tzinfo=TZ)
URL = "http://shop.test/p1"
P = Product("W1", "Wkrętarka X", Decimal("209"), "EUR", "2 akumulator", "Shop A", URL)


def page(price="219.00", title="Wkrętarka X, zestaw z 2 akumulatorami", cur="EUR", avail="InStock"):
    return (f'<script type="application/ld+json">{{"@type":"Product","name":"{title}","offers":'
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
        self.assertEqual(out.count("Telegram>"), 2)
        self.assertIn("blocked by robot", out)
        self.assertIn("different varian", out)
        self.assertIn("no price found", out)


if __name__ == "__main__":
    unittest.main()
