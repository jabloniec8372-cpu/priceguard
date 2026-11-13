"""The control panel: a local web dashboard.

Two modes: "search now" (compare pasted product links, nothing saved) and the
wish list (items with a destination country, markets, target price and shop
links, re-checked on a button or every N minutes). Prices are shown with an
estimated total cost at the destination. Alerts go to Telegram.
Listens on 127.0.0.1 only; every form carries a random token.
"""
from __future__ import annotations

import html
import secrets
import threading
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from .fetch import Fetcher
from .markets import COUNTRIES, DEFAULT_RATES, DESTINATIONS, SHOPS, country_of_url, search_url
from .models import Product
from .monitor import check_product, deliver
from .report import CSS as REPORT_CSS, _eur, sparkline
from .store import Store
from .wishlist import (add_link, add_wish, clean_markets, delete_wish, landed_fn, link_currency, list_wishes,
                       parse_links, parse_money, parse_pct, shop_name, sku, wish_products)

UI = {
    "pl": dict(title="PriceGuard – panel łowcy cen", add="Dodaj do obserwowanych", name="Co chcesz kupić?",
               cat="Kategoria", target="Próg ceny (alert, gdy cena spadnie do tej kwoty)", links="Linki do sklepów (jeden w linii, opcjonalnie „Sklep | link”)",
               ean="EAN (opcjonalnie, chroni przed pomyłką modelu)", variant="Fragment tytułu, który musi się zgadzać (opcjonalnie)",
               save="Dodaj", check="Sprawdź teraz", checking="Sprawdzanie trwa…", last="Ostatnie sprawdzenie", never="jeszcze nie sprawdzano",
               items="Obserwowane rzeczy", hit="próg osiągnięty", tocheck="do sprawdzenia", best="Najniżej teraz", lowest="Najniżej widziane",
               tgt="Twój próg", shop="Sklep", price="Cena", stock="Dostępność", hist="Historia", status="Status", remove="Usuń",
               empty="Nic jeszcze nie obserwujesz. Dodaj pierwszą rzecz powyżej.", yes="dostępny", no="brak", cur="Waluta",
               sub="Wiersze na żółto nie mogły zostać porównane; zostaje ostatnia dobra cena.",
               dest="Dokąd ma przyjść produkt?", markets="Gdzie szukać (rynki)", ship="Wysyłka, EUR (opcjonalnie, szacunek)",
               duty="Cło % (opcjonalnie)", total="Koszt końcowy ≈", search="Szukaj ↗", addlink="Wklej link do produktu", other="inny sklep",
               quick="Szukaj teraz (bez zapisywania)", quickgo="Porównaj teraz", savewish="Zapisz na wishliście", back="← wróć do panelu",
               cost_note="Koszt końcowy to szacunek: cena + wysyłka + VAT/cło wg założeń, nie oferta sklepu. Kursy i listy sklepów są orientacyjne.",
               rates="Kursy (EUR)", wishlist="Wishlista", shops="Sklepy", paste="Wyszukaj produkt w sklepie, skopiuj link do strony produktu i wklej go tutaj.",
               target_hint="Próg dotyczy kosztu końcowego w walucie kraju docelowego", results="Wyniki", nolinks="Wklej linki do produktów, żeby porównać ceny."),
    "en": dict(title="PriceGuard – deal hunter dashboard", add="Add to watch list", name="What do you want to buy?",
               cat="Category", target="Target price (alert when the price drops to this)", links="Shop links (one per line, optionally “Shop | link”)",
               ean="EAN (optional, guards against the wrong model)", variant="Part of the title that must match (optional)",
               save="Add", check="Check now", checking="Checking…", last="Last check", never="not checked yet",
               items="Watched items", hit="target reached", tocheck="to check", best="Best now", lowest="Lowest seen",
               tgt="Your target", shop="Shop", price="Price", stock="Stock", hist="History", status="Status", remove="Remove",
               empty="Nothing watched yet. Add the first item above.", yes="in stock", no="out of stock", cur="Currency",
               sub="Amber rows could not be compared; the last good price is kept.",
               dest="Where should the product be delivered?", markets="Where to look (markets)", ship="Shipping, EUR (optional, estimate)",
               duty="Customs duty % (optional)", total="Total cost ≈", search="Search ↗", addlink="Paste a product link", other="other shop",
               quick="Search now (without saving)", quickgo="Compare now", savewish="Save to wish list", back="← back to the dashboard",
               cost_note="Total cost is an estimate: price + shipping + VAT/duty on stated assumptions, not a shop offer. Rates and shop lists are approximate.",
               rates="Rates (EUR)", wishlist="Wish list", shops="Shops", paste="Find the product in the shop, copy the product page link and paste it here.",
               target_hint="The target applies to the total cost in the destination country's currency", results="Results", nolinks="Paste product links to compare prices."),
}
CSS = REPORT_CSS + """
.wrap{max-width:1100px;margin:0 auto}.bar{display:flex;gap:12px;align-items:center;margin:10px 0 18px;flex-wrap:wrap}
.card{background:#fff;border-radius:10px;box-shadow:0 1px 3px #0001;padding:14px 16px;margin:0 0 14px}
.card.hit{box-shadow:0 0 0 2px #2e9e57}.card h2{font-size:17px;margin:0 0 4px}.meta{color:#566;font-size:13px;margin-bottom:8px}
.meta b{color:#1d2330}.chip{font-size:11px;background:#eceef2;color:#566;border-radius:9px;padding:1px 8px;margin-left:6px;font-weight:600}
form.add{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:8px;background:#fff;padding:14px 16px;border-radius:10px;box-shadow:0 1px 3px #0001}
form.add label{font-size:12px;color:#566;display:flex;flex-direction:column;gap:3px}.wide{grid-column:1/-1}
input,textarea,select{font:inherit;padding:6px 8px;border:1px solid #cfd4dd;border-radius:6px}
button{font:inherit;padding:7px 14px;border:0;border-radius:6px;background:#3b6fd6;color:#fff;cursor:pointer}
button.x{background:#eceef2;color:#a12a2a;padding:3px 10px;font-size:12px}.err{background:#fde4e4;color:#a12a2a;padding:8px 12px;border-radius:6px;margin-bottom:10px}
details{margin:6px 0}summary{cursor:pointer;font-size:13px;font-weight:600}.shops{font-size:13px;margin:4px 0 0 14px;color:#566}
.cc{font-size:10px;background:#e8eefc;color:#2f58b8;border-radius:6px;padding:1px 5px;margin-left:5px;font-weight:700}.tot{color:#2f58b8;font-weight:600}
.checks{display:flex;gap:10px;flex-wrap:wrap;font-size:13px}form.add .checks label{flex-direction:row;align-items:center;gap:4px;color:#1d2330;font-size:13px}
.inl{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}.inl input{flex:1;min-width:200px}.sec{margin:22px 0 8px;font-size:16px}
.card table{box-shadow:none}.pill{font-size:12px;font-weight:700;padding:3px 10px;border-radius:12px;background:#e0f4e6;color:#1d6b35}
"""


def _e(x) -> str:
    return html.escape(str(x))


class App:
    def __init__(self, store: Store, make_fetcher: Callable[[], Fetcher] = lambda: Fetcher(delay=2.0), lang: str = "pl",
                 threshold: Decimal = Decimal(5), send: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(ZoneInfo("Europe/Berlin")),
                 rates: Optional[dict] = None):
        self.store, self.make_fetcher, self.lang, self.threshold, self.send, self.clock = store, make_fetcher, lang, threshold, send, clock
        self.rates = rates or DEFAULT_RATES
        self.t = UI.get(lang, UI["en"])
        self.token = secrets.token_urlsafe(16)
        self.lock = threading.RLock()  # guards the SQLite connection
        self._wl = threading.Lock()
        self.worker: Optional[threading.Thread] = None

    # ---- checking (background, never blocks the page)
    def start_check(self) -> bool:
        with self._wl:
            if self.worker and self.worker.is_alive():
                return False
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
            return True

    def checking(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def join_check(self, timeout: float = 30) -> None:
        w = self.worker
        if w:
            w.join(timeout)

    def _run(self) -> None:
        fetcher = self.make_fetcher()
        now = self.clock()
        with self.lock:
            products = wish_products(self.store, self.rates)
        for p in products:
            try:
                with self.lock:
                    check_product(p, fetcher, self.store, now, self.threshold, self.lang)
            except Exception:
                pass  # one broken page must not stop the rest
        if self.send:
            with self.lock:
                deliver(self.store, self.send)

    # ---- page
    def _checks(self, selected=("PL", "DE")) -> str:
        lang = "pl" if self.lang == "pl" else "en"
        return "".join(f'<label><input type="checkbox" name="markets" value="{c}" {"checked" if c in selected else ""}> '
                       f'{_e(v[lang])}</label>' for c, v in COUNTRIES.items())

    def _dest_select(self, selected="DE") -> str:
        lang = "pl" if self.lang == "pl" else "en"
        return "".join(f'<option value="{c}" {"selected" if c == selected else ""}>{_e(COUNTRIES[c][lang])}</option>' for c in DESTINATIONS)

    def _shops_html(self, markets, name: str) -> str:
        lang = "pl" if self.lang == "pl" else "en"
        out = []
        for c in markets:
            items = "".join(
                f'<li>{_e(n)}{(" – " + _e(note)) if note else ""} · <a href="{_e(search_url(d, name))}" target="_blank" rel="noopener noreferrer">{self.t["search"]}</a></li>'
                for n, d, note in SHOPS.get(c, []))
            out.append(f'<details><summary>{_e(COUNTRIES[c][lang])} ({c})</summary><ul class="shops">{items}</ul></details>')
        return "".join(out)

    def _quick_form(self) -> str:
        t = self.t
        return (f'<form class="add" method="post" action="/quick"><input type="hidden" name="token" value="{self.token}">'
                f'<label>{t["name"]}<input name="name" required maxlength="120" placeholder="PlayStation 5"></label>'
                f'<label>{t["dest"]}<select name="dest">{self._dest_select()}</select></label>'
                f'<div class="wide"><div class="sub">{t["markets"]}</div><div class="checks">{self._checks()}</div></div>'
                f'<label class="wide">{t["links"]}<textarea name="links" rows="2" placeholder="Shop A | https://..."></textarea></label>'
                f'<div><button>{t["quickgo"]}</button></div></form>')

    def render(self, error: str = "") -> str:
        t = self.t
        with self.lock:
            wishes = list_wishes(self.store)
            cards, n_hit, n_chk = [], 0, 0
            for w in wishes:
                sk, rows, best, allv = sku(w["id"]), [], None, []
                for shop, url, country in w["links"]:
                    ok, last = self.store.last_ok(sk, url), self.store.last_any(sk, url)
                    prices = self.store.ok_prices(sk, url)
                    land = landed_fn(w, country, self.rates)
                    val = land or (lambda x: x)
                    cur = link_currency(w, country)
                    status = last.status if last else "—"
                    bad = bool(last) and status != "OK"
                    n_chk += bad
                    allv += [val(p) for p in prices]
                    if ok and ok.price is not None and (best is None or val(ok.price) < best[0]):
                        best = (val(ok.price), shop)
                    stock = "" if not ok or ok.available is None else (t["yes"] if ok.available else t["no"])
                    st = f'<span class="b amber">{_e(status)}</span>' if bad else ('<span class="b green">OK</span>' if last else "—")
                    price_cell = _e(_eur(ok.price, cur)) if ok and ok.price is not None else ""
                    tot = f'<span class="tot">≈ {_e(_eur(land(ok.price), w["currency"]))}</span>' if land and ok and ok.price is not None else ""
                    cc = f'<span class="cc">{country}</span>' if country else ""
                    rows.append(f'<tr class="{"check" if bad else ""}"><td><a href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(shop)}</a>{cc}</td>'
                                f'<td class="n">{price_cell}</td><td class="n">{tot}</td>'
                                f'<td>{_e(stock)}</td><td>{sparkline(prices)}</td><td>{st}</td></tr>')
                hit = best is not None and w["target"] is not None and best[0] <= w["target"]
                n_hit += hit
                meta = []
                if w["dest"]:
                    meta.append(f'{t["dest"].rstrip("?")}: <b>{_e(COUNTRIES[w["dest"]]["pl" if self.lang == "pl" else "en"])}</b>')
                if w["target"] is not None:
                    meta.append(f'{t["tgt"]}: <b>{_e(_eur(w["target"], w["currency"]))}</b>')
                if best:
                    meta.append(f'{t["best"]}: <b>{_e(_eur(best[0], w["currency"]))}</b> ({_e(best[1])})')
                if allv:
                    meta.append(f'{t["lowest"]}: <b>{_e(_eur(min(allv), w["currency"]))}</b>')
                cat = f'<span class="chip">{_e(w["category"])}</span>' if w["category"] else ""
                pill = f' <span class="pill">✓ {_e(t["hit"])}</span>' if hit else ""
                table = (f'<table><tr><th>{t["shop"]}</th><th>{t["price"]}</th><th>{t["total"]}</th><th>{t["stock"]}</th><th>{t["hist"]}</th>'
                         f'<th>{t["status"]}</th></tr>{"".join(rows)}</table>') if rows else f'<div class="sub">{t["nolinks"]}</div>'
                options = "".join(
                    f'<optgroup label="{_e(COUNTRIES[m]["pl" if self.lang == "pl" else "en"])}">'
                    + "".join(f'<option value="{_e(d)}">{_e(n)}</option>' for n, d, _ in SHOPS.get(m, [])) + "</optgroup>"
                    for m in w["markets"])
                addform = (f'<form class="inl" method="post" action="/addlink"><input type="hidden" name="token" value="{self.token}">'
                           f'<input type="hidden" name="id" value="{w["id"]}"><select name="shop"><option value="">{t["other"]}</option>{options}</select>'
                           f'<input name="url" required placeholder="{_e(t["addlink"])} https://..."><button>{t["save"]}</button></form>')
                shops = (f'<details><summary>{t["shops"]} ({len(w["markets"])})</summary><div class="sub">{t["paste"]}</div>'
                         f'{self._shops_html(w["markets"], w["name"])}</details>') if w["markets"] else ""
                cards.append(
                    f'<section class="card {"hit" if hit else ""}"><h2>{_e(w["name"])}{cat}{pill}</h2><div class="meta">{" · ".join(meta)}</div>'
                    f'{table}{shops}{addform}'
                    f'<form method="post" action="/delete" style="margin-top:8px"><input type="hidden" name="token" value="{self.token}">'
                    f'<input type="hidden" name="id" value="{w["id"]}"><button class="x">{t["remove"]}</button></form></section>')
            last = (self.store.last_checked() or "").replace("T", " ")[:16]
        busy = self.checking()
        refresh = '<meta http-equiv="refresh" content="3">' if busy else ""
        rate_note = " · ".join(f"1 {c} ≈ {self.rates[c]} EUR" for c in ("USD", "PLN", "GBP", "CHF", "CZK", "CNY") if c in self.rates)
        return (
            f'<!doctype html><html lang="{self.lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{refresh}'
            f'<title>{_e(t["title"])}</title><style>{CSS}</style></head><body><div class="wrap"><h1>{_e(t["title"])}</h1>'
            f'<div class="bar"><form method="post" action="/check"><input type="hidden" name="token" value="{self.token}">'
            f'<button {"disabled" if busy else ""}>{t["checking"] if busy else t["check"]}</button></form>'
            f'<span class="sub">{t["last"]}: {_e(last or t["never"])} · <b>{n_hit}</b> {t["hit"]} · <b>{n_chk}</b> {t["tocheck"]}</span></div>'
            + (f'<div class="err">{_e(error)}</div>' if error else "")
            + f'<h2 class="sec">{t["quick"]}</h2>{self._quick_form()}'
            + f'<h2 class="sec">{t["add"]}</h2><form class="add" method="post" action="/add"><input type="hidden" name="token" value="{self.token}">'
              f'<label>{t["name"]}<input name="name" required maxlength="120" placeholder="PlayStation 5"></label>'
              f'<label>{t["cat"]}<input name="category" maxlength="40" placeholder="Konsole"></label>'
              f'<label>{t["dest"]}<select name="dest">{self._dest_select()}</select></label>'
              f'<label>{t["target"]}<input name="target" inputmode="decimal" placeholder="429" title="{_e(t["target_hint"])}"></label>'
              f'<div class="wide"><div class="sub">{t["markets"]}</div><div class="checks">{self._checks()}</div></div>'
              f'<label class="wide">{t["links"]}<textarea name="links" rows="2" placeholder="Shop A | https://..."></textarea></label>'
              f'<label>{t["ship"]}<input name="ship" inputmode="decimal"></label>'
              f'<label>{t["duty"]}<input name="duty" inputmode="decimal"></label>'
              f'<label>{t["ean"]}<input name="ean" maxlength="20"></label>'
              f'<label>{t["variant"]}<input name="variant" maxlength="80"></label>'
              f'<div><button>{t["save"]}</button></div></form>'
            f'<h2 class="sec">{t["wishlist"]} ({len(wishes)})</h2><div class="sub">{t["sub"]}</div>'
            + ("".join(cards) or f'<p class="sub">{t["empty"]}</p>')
            + f'<p class="sub" style="margin-top:18px">{t["cost_note"]}<br>{t["rates"]}: {_e(rate_note)}</p></div></body></html>'
        )

    def quick(self, form: dict) -> str:
        """Compare right now, without saving: fetch the pasted links, estimate the total cost, offer to save."""
        t, f = self.t, (lambda k: (form.get(k) or [""])[0])
        name, dest = f("name").strip(), f("dest") or "DE"
        if not name or len(name) > 120:
            raise ValueError("give the item a name (up to 120 characters)")
        if dest not in DESTINATIONS:
            raise ValueError("unknown destination country")
        markets = clean_markets(form.get("markets") or [])
        links = parse_links(f("links"), allow_empty=True)
        tmp = Store()
        results, fetcher, now = [], self.make_fetcher(), self.clock()
        w = dict(dest=dest, currency=COUNTRIES[dest]["cur"], ship=None, duty=None)
        for i, (shop, url) in enumerate(links):
            country = country_of_url(url)
            land = landed_fn(w, country, self.rates)
            p = Product(f"Q{i}", name, Decimal(0), link_currency(w, country), "", shop, url, None, "", land, w["currency"])
            status = check_product(p, fetcher, tmp, now, self.threshold, self.lang)
            ok = tmp.last_ok(p.sku, url)
            price = ok.price if ok else None
            total = land(price) if land and price is not None else price
            results.append((total if total is not None else Decimal("1e12"), shop, url, country, p.currency, price, land, ok, status))
        results.sort(key=lambda r: r[0])
        tcur, rows_html = w["currency"], []
        for _, sh, u, c, cur, pr, ln, ok, st in results:
            stock = "" if not ok or ok.available is None else (t["yes"] if ok.available else t["no"])
            cc = f'<span class="cc">{c}</span>' if c else ""
            price_cell = _e(_eur(pr, cur)) if pr is not None else ""
            tot = f'<span class="tot">≈ {_e(_eur(ln(pr), tcur))}</span>' if ln and pr is not None else ""
            badge = '<span class="b green">OK</span>' if st == "OK" else f'<span class="b amber">{_e(st)}</span>'
            rows_html.append(f'<tr class="{"" if st == "OK" else "check"}"><td><a href="{_e(u)}" target="_blank" rel="noopener noreferrer">{_e(sh)}</a>{cc}</td>'
                             f'<td class="n">{price_cell}</td><td class="n">{tot}</td><td>{_e(stock)}</td><td>{badge}</td></tr>')
        rows = "".join(rows_html)
        table = (f'<table><tr><th>{t["shop"]}</th><th>{t["price"]}</th><th>{t["total"]}</th><th>{t["stock"]}</th><th>{t["status"]}</th></tr>{rows}</table>'
                 if rows else f'<div class="sub">{t["nolinks"]}</div>')
        hidden = "".join(f'<input type="hidden" name="{k}" value="{_e(v)}">' for k, v in
                         (("token", self.token), ("name", name), ("dest", dest), ("links", f("links"))))
        hidden += "".join(f'<input type="hidden" name="markets" value="{m}">' for m in markets)
        lang = "pl" if self.lang == "pl" else "en"
        return (
            f'<!doctype html><html lang="{self.lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(name)}</title><style>{CSS}</style></head><body><div class="wrap"><p><a href="/">{t["back"]}</a></p>'
            f'<h1>{_e(name)}</h1><div class="sub">{t["dest"].rstrip("?")}: <b>{_e(COUNTRIES[dest][lang])}</b></div>'
            f'<section class="card"><h2>{t["results"]}</h2>{table}</section>'
            + (f'<section class="card"><h2>{t["shops"]}</h2><div class="sub">{t["paste"]}</div>{self._shops_html(markets, name)}</section>' if markets else "")
            + f'<form method="post" action="/add">{hidden}<button>{t["savewish"]}</button></form>'
              f'<p class="sub" style="margin-top:18px">{t["cost_note"]}</p></div></body></html>'
        )

    # ---- actions
    def add(self, form: dict) -> None:
        f = lambda k: (form.get(k) or [""])[0]
        with self.lock:
            add_wish(self.store, f("name"), parse_links(f("links"), allow_empty=True), f("category"), parse_money(f("target")),
                     "EUR", f("ean"), f("variant"), f("dest"), clean_markets(form.get("markets") or []),
                     parse_money(f("ship")), parse_pct(f("duty")))

    def addlink(self, form: dict) -> None:
        f = lambda k: (form.get(k) or [""])[0]
        try:
            wid = int(f("id"))
        except ValueError:
            raise ValueError("no such item")
        domain = f("shop")
        with self.lock:
            name = next((shop_name(c, domain) for c in SHOPS if any(d == domain for _, d, _ in SHOPS[c])), "") if domain else ""
            add_link(self.store, wid, name, f("url"))

    def remove(self, form: dict) -> None:
        try:
            wid = int((form.get("id") or [""])[0])
        except ValueError:
            return
        with self.lock:
            delete_wish(self.store, wid)


def make_server(app: App, port: int = 8765) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def _host_ok(self) -> bool:  # blocks DNS-rebinding style access
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: str = "", loc: str = "") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            if loc:
                self.send_header("Location", loc)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "forbidden")
            if self.path.split("?")[0] != "/":
                return self._send(404, "not found")
            self._send(200, app.render())

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, "forbidden")
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > 20000:
                return self._send(413, "too large")
            form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
            if not secrets.compare_digest((form.get("token") or [""])[0], app.token):
                return self._send(403, "bad token")
            try:
                if self.path == "/add":
                    app.add(form)
                elif self.path == "/addlink":
                    app.addlink(form)
                elif self.path == "/quick":
                    return self._send(200, app.quick(form))
                elif self.path == "/delete":
                    app.remove(form)
                elif self.path == "/check":
                    app.start_check()
                else:
                    return self._send(404, "not found")
            except ValueError as ex:
                return self._send(400, app.render(str(ex)))
            self._send(303, "", "/")

        def log_message(self, *a):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), H)
