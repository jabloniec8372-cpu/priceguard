"""Offline demo: a tiny fake shop on localhost, watched over two days.

    python3 demo.py

Nothing leaves your machine. Two "competitor shops" (A and B) are served by a
local HTTP server whose prices the demo changes between day 1 and day 2.
"""
from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from priceguard.fetch import Fetcher
from priceguard.models import load_products
from priceguard.monitor import deliver, run_once
from priceguard.sheet import status_rows, HEAD
from priceguard.store import Store

HERE = Path(__file__).parent
TZ = ZoneInfo("Europe/Berlin")


def jsonld(title, price, avail="InStock", shipping=None):
    ship = ""
    if shipping is not None:
        ship = f',"shippingDetails":{{"shippingRate":{{"value":"{shipping}"}}}}'
    return (f'<html><body><h1>{title}</h1><script type="application/ld+json">'
            f'{{"@context":"https://schema.org","@type":"Product","name":"{title}",'
            f'"offers":{{"@type":"Offer","price":"{price}","priceCurrency":"EUR",'
            f'"availability":"https://schema.org/{avail}"{ship}}}}}</script></body></html>')


def microdata(title, price, avail="InStock"):
    return (f'<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">{title}</h1>'
            f'<div itemprop="offers" itemscope><span itemprop="price">{price} €</span>'
            f'<link itemprop="availability" href="https://schema.org/{avail}"></div></div>')


def pages(day: int) -> dict[str, str]:
    d2 = day == 2
    return {
        "/a/wkretarka-x": jsonld("Wkrętarka X, zestaw z 2 akumulatorami", "199.00" if d2 else "219.00", shipping="4.90"),
        "/b/wkretarka-x": microdata("Wkrętarka X, zestaw z 2 akumulatorami", "205,00",
                                    "InStock" if d2 else "OutOfStock"),
        # day 2: the shop redesigned the page, the price is no longer machine-readable
        "/a/pila-p77": '<html><h1>Piła tarczowa P77</h1><p>Cena w koszyku</p></html>' if d2
                       else jsonld("Piła tarczowa P77", "134.00"),
        # a different variant (1 battery): comparing it would be wrong
        "/b/szlifierka-s12": jsonld("Szlifierka S12, zestaw z 1 akumulatorem", "69.00"),
        "/a/private/mlot-m5": jsonld("Młot udarowy M5", "149.00"),
    }


ROBOTS = "User-agent: *\nDisallow: /a/private/\n"


def start_shop(state: dict) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/robots.txt":
                body, code = ROBOTS, 200
            else:
                body = pages(state["day"]).get(self.path)
                code = 200 if body else 404
            data = (body or "not found").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def table(rows) -> str:
    cols = ["Product", "Competitor", "Comp. price", "Diff", "Status"]
    idx = [HEAD.index(c) for c in ("Product", "Competitor", "Competitor price", "Difference", "Status")]
    out = [cols] + [[r[i] for i in idx] for r in rows]
    out = [[(r[0][:26] + "…") if len(r[0]) > 27 else r[0]] + r[1:] for r in out]
    w = [max(len(r[i]) for r in out) for i in range(len(cols))]
    return "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in out)


def main(lang: str = "pl") -> str:
    state = {"day": 1}
    srv, base = start_shop(state)
    try:
        products = load_products(str(HERE / "products_demo.csv"), base)
        store = Store()
        fetcher = Fetcher(delay=0)
        sent: list[str] = []
        out = []
        for day, when in ((1, datetime(2026, 11, 13, 8, 0, tzinfo=TZ)), (2, datetime(2026, 11, 13, 12, 0, tzinfo=TZ))):
            state["day"] = day
            run_once(products, fetcher, store, when, Decimal("5"), lang)
            n = deliver(store, sent.append)
            out.append(f"=== Read {day} ({when:%H:%M}) - {n} Telegram alert(s) ===")
            for msg in sent[-n:] if n else []:
                out.append("Telegram> " + msg)
            out.append("")
            out.append(table(status_rows(products, store)))
            out.append("")
        return "\n".join(out)
    finally:
        srv.shutdown()


if __name__ == "__main__":
    print(main())
