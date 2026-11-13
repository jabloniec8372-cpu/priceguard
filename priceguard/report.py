"""A readable HTML report: one row per watched product, a small price chart,
and the rows that need a human look highlighted. Standard library only."""
from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path

from .models import Product
from .store import Store

CSS = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:24px;background:#f6f7f9;color:#1d2330}
h1{font-size:20px;margin:0 0 4px} .sub{color:#6b7385;font-size:13px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px #0001}
th{background:#222a36;color:#fff;text-align:left;font-size:12px;padding:9px 10px;font-weight:600}
td{padding:9px 10px;border-top:1px solid #eceef2;font-size:13px;vertical-align:middle}
tr.check td{background:#fff6e5}
.b{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
.red{background:#fde4e4;color:#a12a2a}.green{background:#e0f4e6;color:#1d6b35}.grey{background:#eceef2;color:#566}
.amber{background:#ffe3b0;color:#8a5200}.n{white-space:nowrap}
"""


def sparkline(prices: list[Decimal], w: int = 110, h: int = 28) -> str:
    if len(prices) < 2:
        return '<span class="b grey">not enough data</span>'
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or Decimal(1)
    pts = []
    for i, p in enumerate(prices):
        x = 2 + i * (w - 4) / (len(prices) - 1)
        y = h - 3 - float((p - lo) / span) * (h - 6)
        pts.append(f"{x:.1f},{y:.1f}")
    last = pts[-1].split(",")
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline fill="none" stroke="#3b6fd6" '
            f'stroke-width="2" points="{" ".join(pts)}"/><circle cx="{last[0]}" cy="{last[1]}" r="3" fill="#3b6fd6"/></svg>')


def _eur(v: Decimal, cur: str) -> str:
    return f"{v:,.2f}".replace(",", " ") + (" €" if cur == "EUR" else f" {cur}")


def render(products: list[Product], store: Store, title: str = "PriceGuard report") -> str:
    rows = []
    for p in products:
        ok, last = store.last_ok(p.sku, p.url), store.last_any(p.sku, p.url)
        prices = store.ok_prices(p.sku, p.url)
        status = last.status if last else "not read yet"
        needs_check = status != "OK"
        if ok and ok.price is not None:
            diff = ok.price - p.my_price
            badge = (f'<span class="b red">{html.escape(_eur(abs(diff), p.currency))} cheaper than you</span>' if diff < 0
                     else f'<span class="b green">you are {html.escape(_eur(diff, p.currency))} cheaper</span>' if diff > 0
                     else '<span class="b grey">same price</span>')
            theirs = html.escape(_eur(ok.price, p.currency))
            stock = "" if ok.available is None else ("in stock" if ok.available else "out of stock")
        else:
            badge, theirs, stock = "", "", ""
        target = html.escape(_eur(p.target, p.currency)) if p.target is not None else ""
        lowest = html.escape(_eur(min(prices), p.currency)) if prices else ""
        st = f'<span class="b amber">{html.escape(status)}</span>' if needs_check else '<span class="b green">OK</span>'
        rows.append(
            f'<tr class="{"check" if needs_check else ""}"><td>{html.escape(p.name)}<br><small>{html.escape(p.sku)}</small></td>'
            f'<td>{html.escape(p.competitor)}</td><td class="n">{html.escape(_eur(p.my_price, p.currency))}</td>'
            f'<td class="n">{theirs}</td><td>{badge}</td><td>{html.escape(stock)}</td>'
            f'<td class="n">{target}</td><td class="n">{lowest}</td><td>{sparkline(prices)}</td><td>{st}</td></tr>'
        )
    head = ["Product", "Competitor", "Your price", "Their price", "Difference", "Stock",
            "Your target", "Lowest seen", "Price history", "Status"]
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>"
            f'<div class="sub">Rows in amber could not be compared; the last good price is kept.</div>'
            f"<table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{''.join(rows)}</table></body></html>")


def write_report(path: Path, products: list[Product], store: Store) -> None:
    Path(path).write_text(render(products, store), encoding="utf-8")
