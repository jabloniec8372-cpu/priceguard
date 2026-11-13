"""python -m priceguard run --products products.csv [--telegram-chat ID]"""
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
from .sheet import write_history, write_status
from .store import Store
from .telegram import make_sender


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
    args = p.parse_args(argv)

    products = load_products(args.products)
    store = Store(args.db)
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    run_once(products, Fetcher(delay=args.delay), store, now, Decimal(args.threshold), args.lang)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_status(out / "status.csv", products, store)
    write_history(out / "price_history.csv", store)

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
