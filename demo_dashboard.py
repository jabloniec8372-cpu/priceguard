"""Offline demo of the control panel (dashboard): a shopper's wish list on a local fake shop.

    python3 demo_dashboard.py            # writes demo_dashboard.html
    python3 demo_dashboard.py --serve    # then open http://127.0.0.1:8765 and try it yourself
"""
from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from priceguard.fetch import Fetcher
from priceguard.store import Store
from priceguard.web import App, make_server
from priceguard.wishlist import add_wish
import demo

TZ = ZoneInfo("Europe/Berlin")


def build(sent: list[str] | None = None):
    state = {"day": 1}
    shop, base = demo.start_shop(state)
    store = Store()
    send = sent.append if sent is not None else None
    clock = {"t": datetime(2026, 11, 13, 8, 0, tzinfo=TZ)}
    app = App(store, lambda: Fetcher(delay=0), "pl", Decimal(3), send, clock=lambda: clock["t"])
    # Shop A = a German shop (price in EUR, delivered to Germany), Shop B of the phone = a US shop (price in USD)
    add_wish(store, "Smartfon Nova 12 128GB", [("Shop A", base + "/m/phone-a", "DE"), ("US shop", base + "/m/phone-b", "US")],
             "Telefony", Decimal(750), dest="DE", markets=["DE", "PL", "US"], duty=Decimal(0))
    add_wish(store, "Konsola PlayStation 5 Slim", [("Shop A", base + "/m/ps5-a"), ("Shop B", base + "/m/ps5-b")], "Konsole", Decimal(450),
             dest="DE", markets=["DE", "PL", "NL"])
    add_wish(store, "Buty biegowe Aero 3", [("Shop A", base + "/m/shoes-a")], "Buty", Decimal(100), dest="DE", markets=["DE", "PL"])
    add_wish(store, "Monitor 27 cali QHD", [("Shop A", base + "/m/monitor-a")], "Monitory", Decimal(220), dest="DE", markets=["DE", "CZ", "US"])
    add_wish(store, "Telewizor OLED 55", [("Shop A", base + "/m/tv-a")], "RTV", Decimal(900), dest="DE", markets=["DE", "AT"])
    for day, hour in ((1, 8), (2, 12), (3, 16)):
        state["day"], clock["t"] = day, datetime(2026, 11, 13, hour, 0, tzinfo=TZ)
        app._run()
    return app, shop


if __name__ == "__main__":
    sent: list[str] = []
    app, shop = build(sent)
    if "--serve" in sys.argv:
        print("Panel: http://127.0.0.1:8765  (Ctrl+C to stop)")
        try:
            make_server(app, 8765).serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        open("demo_dashboard.html", "w", encoding="utf-8").write(app.render())
        print("\n".join("Telegram> " + m for m in sent))
        print("Wrote demo_dashboard.html")
    shop.shutdown()
