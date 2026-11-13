"""Write the client's view: current prices, last good read, and history.
CSV here; with the Google Sheets API the same rows go into a worksheet."""
from __future__ import annotations

import csv
from pathlib import Path

from .models import Product
from .store import Store

HEAD = ["SKU", "Product", "My price", "Competitor", "Competitor price", "Difference",
        "In stock", "Last good read", "Status", "Target", "Lowest seen"]


def status_rows(products: list[Product], store: Store) -> list[list[str]]:
    rows = []
    for p in products:
        ok, last = store.last_ok(p.sku, p.url), store.last_any(p.sku, p.url)
        price = ok.price if ok else None
        prices = store.ok_prices(p.sku, p.url)
        rows.append([
            p.sku, p.name, f"{p.my_price:.2f}", p.competitor,
            f"{price:.2f}" if price is not None else "",
            f"{price - p.my_price:+.2f}" if price is not None else "",
            "" if not ok or ok.available is None else ("yes" if ok.available else "no"),
            ok.ts if ok else "",
            last.status if last else "not read yet",
            f"{p.target:.2f}" if p.target is not None else "",
            f"{min(prices):.2f}" if prices else "",
        ])
    return rows


def write_status(path: Path, products: list[Product], store: Store) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows(status_rows(products, store))


def write_history(path: Path, store: Store) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SKU", "URL", "Time", "Status", "Price", "Currency", "In stock"])
        w.writerows(store.history())
