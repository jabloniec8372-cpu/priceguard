"""python -m priceguard run --products products.csv [--telegram-chat ID]
   python -m priceguard serve      (the control panel / dashboard)"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .fetch import Fetcher
from .models import load_products
from .monitor import deliver, run_once
from .report import write_report
from .sheet import write_history, write_status
from .store import Store
from .telegram import make_sender


def serve(args) -> int:
    import threading
    import time

    from .markets import parse_rates
    from .web import App, make_server

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    send = make_sender(token, args.telegram_chat) if token and args.telegram_chat else None
    app = App(Store(args.db), lambda: Fetcher(delay=args.delay), args.lang, Decimal(args.threshold), send, rates=parse_rates(args.rates))
    if args.interval > 0:
        def loop():
            while True:
                time.sleep(args.interval * 60)
                app.start_check()
        threading.Thread(target=loop, daemon=True).start()
    srv = make_server(app, args.port)
    print(f"PriceGuard panel: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="priceguard")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="read competitor pages, write the sheet, send alerts")
    r.add_argument("--products", required=True)
    r.add_argument("--db", default="priceguard.db")
    r.add_argument("--out", default="out")
    r.add_argument("--lang", choices=["pl", "de", "en"], default="pl")
    r.add_argument("--threshold", default="5", help="alert when price drops by at least this %%")
    r.add_argument("--delay", type=float, default=2.0, help="seconds between requests to one host")
    r.add_argument("--telegram-chat", help="chat id; the bot token is read from $TELEGRAM_BOT_TOKEN")
    w = sub.add_parser("serve", help="open the control panel (dashboard) in your browser")
    w.add_argument("--db", default="priceguard.db")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--lang", choices=["pl", "de", "en"], default="pl")
    w.add_argument("--threshold", default="5")
    w.add_argument("--delay", type=float, default=2.0)
    w.add_argument("--interval", type=int, default=0, help="re-check every N minutes (0 = only when you press the button)")
    w.add_argument("--telegram-chat")
    w.add_argument("--rates", default="", help='rough exchange rates to EUR, e.g. "USD=0.9,PLN=0.23"')
    args = p.parse_args(argv)
    if args.cmd == "serve":
        return serve(args)

    products = load_products(args.products)
    store = Store(args.db)
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    run_once(products, Fetcher(delay=args.delay), store, now, Decimal(args.threshold), args.lang)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_status(out / "status.csv", products, store)
    write_history(out / "price_history.csv", store)
    write_report(out / "report.html", products, store)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token and args.telegram_chat:
        n = deliver(store, make_sender(token, args.telegram_chat))
        print(f"{n} alert(s) sent")
    else:
        pending = store.pending_alerts()
        print(f"{len(pending)} alert(s) queued (set TELEGRAM_BOT_TOKEN and --telegram-chat to send)")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
