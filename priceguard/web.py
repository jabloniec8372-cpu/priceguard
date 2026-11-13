"""The control panel: a local web dashboard.

Add the things you want to hunt (phone, shoes, console ...), paste the shop
links, set a target price. The dashboard shows every item with the best price
now, the lowest price seen and a price chart, and the alerts go to Telegram.
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
from .monitor import check_product, deliver
from .report import CSS as REPORT_CSS, _eur, sparkline
from .store import Store
from .wishlist import CURRENCIES, add_wish, delete_wish, list_wishes, parse_links, parse_money, sku, wish_products

UI = {
    "pl": dict(title="PriceGuard – panel łowcy cen", add="Dodaj do obserwowanych", name="Co chcesz kupić?",
               cat="Kategoria", target="Próg ceny (alert, gdy cena spadnie do tej kwoty)", links="Linki do sklepów (jeden w linii, opcjonalnie „Sklep | link”)",
               ean="EAN (opcjonalnie, chroni przed pomyłką modelu)", variant="Fragment tytułu, który musi się zgadzać (opcjonalnie)",
               save="Dodaj", check="Sprawdź teraz", checking="Sprawdzanie trwa…", last="Ostatnie sprawdzenie", never="jeszcze nie sprawdzano",
               items="Obserwowane rzeczy", hit="próg osiągnięty", tocheck="do sprawdzenia", best="Najniżej teraz", lowest="Najniżej widziane",
               tgt="Twój próg", shop="Sklep", price="Cena", stock="Dostępność", hist="Historia", status="Status", remove="Usuń",
               empty="Nic jeszcze nie obserwujesz. Dodaj pierwszą rzecz powyżej.", yes="dostępny", no="brak", cur="Waluta",
               sub="Wiersze na żółto nie mogły zostać porównane; zostaje ostatnia dobra cena."),
    "en": dict(title="PriceGuard – deal hunter dashboard", add="Add to watch list", name="What do you want to buy?",
               cat="Category", target="Target price (alert when the price drops to this)", links="Shop links (one per line, optionally “Shop | link”)",
               ean="EAN (optional, guards against the wrong model)", variant="Part of the title that must match (optional)",
               save="Add", check="Check now", checking="Checking…", last="Last check", never="not checked yet",
               items="Watched items", hit="target reached", tocheck="to check", best="Best now", lowest="Lowest seen",
               tgt="Your target", shop="Shop", price="Price", stock="Stock", hist="History", status="Status", remove="Remove",
               empty="Nothing watched yet. Add the first item above.", yes="in stock", no="out of stock", cur="Currency",
               sub="Amber rows could not be compared; the last good price is kept."),
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
.card table{box-shadow:none}.pill{font-size:12px;font-weight:700;padding:3px 10px;border-radius:12px;background:#e0f4e6;color:#1d6b35}
"""


def _e(x) -> str:
    return html.escape(str(x))


class App:
    def __init__(self, store: Store, make_fetcher: Callable[[], Fetcher] = lambda: Fetcher(delay=2.0), lang: str = "pl",
                 threshold: Decimal = Decimal(5), send: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(ZoneInfo("Europe/Berlin"))):
        self.store, self.make_fetcher, self.lang, self.threshold, self.send, self.clock = store, make_fetcher, lang, threshold, send, clock
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
            products = wish_products(self.store)
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
    def render(self, error: str = "") -> str:
        t = self.t
        with self.lock:
            wishes = list_wishes(self.store)
            cards, n_hit, n_chk = [], 0, 0
            for w in wishes:
                sk, rows, best = sku(w["id"]), [], None
                for shop, url in w["links"]:
                    ok, last = self.store.last_ok(sk, url), self.store.last_any(sk, url)
                    prices = self.store.ok_prices(sk, url)
                    status = last.status if last else "—"
                    bad = bool(last) and status != "OK"
                    n_chk += bad
                    if ok and ok.price is not None and (best is None or ok.price < best[0]):
                        best = (ok.price, shop)
                    stock = "" if not ok or ok.available is None else (t["yes"] if ok.available else t["no"])
                    st = f'<span class="b amber">{_e(status)}</span>' if bad else ('<span class="b green">OK</span>' if last else "—")
                    rows.append(f'<tr class="{"check" if bad else ""}"><td><a href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(shop)}</a></td>'
                                f'<td class="n">{_e(_eur(ok.price, w["currency"])) if ok and ok.price is not None else ""}</td>'
                                f'<td>{_e(stock)}</td><td>{sparkline(prices)}</td><td>{st}</td></tr>')
                allp = [p for _, u in w["links"] for p in self.store.ok_prices(sk, u)]
                hit = best is not None and w["target"] is not None and best[0] <= w["target"]
                n_hit += hit
                meta = []
                if w["target"] is not None:
                    meta.append(f'{t["tgt"]}: <b>{_e(_eur(w["target"], w["currency"]))}</b>')
                if best:
                    meta.append(f'{t["best"]}: <b>{_e(_eur(best[0], w["currency"]))}</b> ({_e(best[1])})')
                if allp:
                    meta.append(f'{t["lowest"]}: <b>{_e(_eur(min(allp), w["currency"]))}</b>')
                cat = f'<span class="chip">{_e(w["category"])}</span>' if w["category"] else ""
                pill = f' <span class="pill">✓ {_e(t["hit"])}</span>' if hit else ""
                cards.append(
                    f'<section class="card {"hit" if hit else ""}"><h2>{_e(w["name"])}{cat}{pill}</h2><div class="meta">{" · ".join(meta)}</div>'
                    f'<table><tr><th>{t["shop"]}</th><th>{t["price"]}</th><th>{t["stock"]}</th><th>{t["hist"]}</th><th>{t["status"]}</th></tr>{"".join(rows)}</table>'
                    f'<form method="post" action="/delete" style="margin-top:8px"><input type="hidden" name="token" value="{self.token}">'
                    f'<input type="hidden" name="id" value="{w["id"]}"><button class="x">{t["remove"]}</button></form></section>')
            last = (self.store.last_checked() or "").replace("T", " ")[:16]
        busy = self.checking()
        opts = "".join(f"<option>{c}</option>" for c in CURRENCIES)
        refresh = '<meta http-equiv="refresh" content="3">' if busy else ""
        return (
            f'<!doctype html><html lang="{self.lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{refresh}'
            f'<title>{_e(t["title"])}</title><style>{CSS}</style></head><body><div class="wrap"><h1>{_e(t["title"])}</h1>'
            f'<div class="bar"><form method="post" action="/check"><input type="hidden" name="token" value="{self.token}">'
            f'<button {"disabled" if busy else ""}>{t["checking"] if busy else t["check"]}</button></form>'
            f'<span class="sub">{t["last"]}: {_e(last or t["never"])} · <b>{n_hit}</b> {t["hit"]} · <b>{n_chk}</b> {t["tocheck"]}</span></div>'
            + (f'<div class="err">{_e(error)}</div>' if error else "")
            + f'<form class="add" method="post" action="/add"><input type="hidden" name="token" value="{self.token}">'
              f'<label>{t["name"]}<input name="name" required maxlength="120" placeholder="PlayStation 5"></label>'
              f'<label>{t["cat"]}<input name="category" maxlength="40" placeholder="Konsole"></label>'
              f'<label>{t["target"]}<input name="target" inputmode="decimal" placeholder="429"></label>'
              f'<label>{t["cur"]}<select name="currency">{opts}</select></label>'
              f'<label class="wide">{t["links"]}<textarea name="links" rows="3" required placeholder="Shop A | https://..."></textarea></label>'
              f'<label>{t["ean"]}<input name="ean" maxlength="20"></label>'
              f'<label class="wide">{t["variant"]}<input name="variant" maxlength="80"></label>'
              f'<div><button>{t["save"]}</button></div></form>'
            f'<h2 style="margin:20px 0 8px;font-size:16px">{t["items"]} ({len(wishes)})</h2><div class="sub">{t["sub"]}</div>'
            + ("".join(cards) or f'<p class="sub">{t["empty"]}</p>')
            + "</div></body></html>"
        )

    # ---- actions
    def add(self, form: dict) -> None:
        f = lambda k: (form.get(k) or [""])[0]
        with self.lock:
            add_wish(self.store, f("name"), parse_links(f("links")), f("category"), parse_money(f("target")),
                     f("currency") or "EUR", f("ean"), f("variant"))

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
