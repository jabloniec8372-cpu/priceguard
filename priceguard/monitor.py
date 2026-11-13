"""One monitoring pass over the product list."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from .fetch import Blocked, Fetcher
from .models import Product
from .parse import parse_reading
from .store import Store

TEXT = {
    "pl": {
        "drop": "{name}. Konkurent {comp} obniżył cenę z {old} do {new}. Twoja cena to {mine}.",
        "stock": "{name}. Konkurent {comp}: produkt znów dostępny (cena {new}). Twoja cena to {mine}.",
        "target": "{name}. Konkurent {comp}: cena {new} osiągnęła Twój próg {target}. Twoja cena to {mine}.",
        "lowest": " To najniższa cena z {n} naszych odczytów.",
    },
    "de": {
        "drop": "{name}. Wettbewerber {comp} hat den Preis von {old} auf {new} gesenkt. Dein Preis: {mine}.",
        "stock": "{name}. Wettbewerber {comp}: wieder verfügbar (Preis {new}). Dein Preis: {mine}.",
        "target": "{name}. Wettbewerber {comp}: Preis {new} hat deine Schwelle {target} erreicht. Dein Preis: {mine}.",
        "lowest": " Das ist der niedrigste Preis aus {n} Messungen.",
    },
    "en": {
        "drop": "{name}. Competitor {comp} lowered the price from {old} to {new}. Your price is {mine}.",
        "stock": "{name}. Competitor {comp}: back in stock (price {new}). Your price is {mine}.",
        "target": "{name}. Competitor {comp}: price {new} reached your target {target}. Your price is {mine}.",
        "lowest": " That is the lowest price in {n} readings.",
    },
}


def money(value: Decimal, currency: str, lang: str = "pl") -> str:
    text = f"{value:.2f}"
    if text.endswith(".00"):
        text = text[:-3]
    elif lang in ("pl", "de"):
        text = text.replace(".", ",")
    return f"{text} {'€' if currency == 'EUR' else currency}"


def check_product(
    p: Product, fetcher: Fetcher, store: Store, now: datetime, threshold_pct: Decimal, lang: str = "pl"
) -> str:
    """Fetch one competitor page, record the result, queue alerts.
    Returns the status: 'OK' or 'TO CHECK: <reason>'. A failed read never
    overwrites the last known price; it is only recorded as 'to check'."""
    ts = now.isoformat(timespec="minutes")

    def fail(reason: str) -> str:
        store.record(p.sku, p.url, ts, f"TO CHECK: {reason}")
        return f"TO CHECK: {reason}"

    try:
        html = fetcher.fetch(p.url)
    except Blocked:
        return fail("blocked by robots.txt")
    except Exception:
        return fail("page could not be fetched")

    reading = parse_reading(html)
    if reading is None or reading.price is None:
        return fail("no price found (page layout changed?)")
    if reading.currency and reading.currency.upper() != p.currency:
        return fail(f"currency {reading.currency} instead of {p.currency}")
    if p.variant and p.variant.casefold() not in reading.title.casefold():
        return fail(f'different variant (page says "{reading.title}")')
    if p.ean and reading.gtin and reading.gtin != p.ean:
        return fail(f"different product (EAN {reading.gtin} instead of {p.ean})")

    prev = store.last_ok(p.sku, p.url)
    earlier = store.ok_prices(p.sku, p.url)  # before this reading
    rid = store.record(p.sku, p.url, ts, "OK", reading)

    args = dict(name=p.name, comp=p.competitor, new=money(reading.price, p.currency, lang),
                mine=money(p.my_price, p.currency, lang))
    if p.target is not None and reading.price <= p.target and (prev is None or prev.price > p.target):
        store.add_alert(f"{rid}|target", TEXT[lang]["target"].format(target=money(p.target, p.currency, lang), **args))
    if prev is None:
        return "OK"  # first reading is the baseline, nothing more to compare yet

    if prev.price is not None and reading.price < prev.price and \
            reading.price <= prev.price * (1 - threshold_pct / 100):
        text = TEXT[lang]["drop"].format(old=money(prev.price, p.currency, lang), **args)
        # Say "lowest" only for what we really measured: at least 3 readings, new one strictly below all.
        if len(earlier) >= 2 and reading.price < min(earlier):
            text += TEXT[lang]["lowest"].format(n=len(earlier) + 1)
        store.add_alert(f"{rid}|drop", text)
    if prev.available is False and reading.available is True:
        store.add_alert(f"{rid}|stock", TEXT[lang]["stock"].format(**args))
    return "OK"


def run_once(
    products: list[Product], fetcher: Fetcher, store: Store, now: datetime,
    threshold_pct: Decimal = Decimal("5"), lang: str = "pl",
) -> dict[tuple[str, str], str]:
    return {(p.sku, p.url): check_product(p, fetcher, store, now, threshold_pct, lang) for p in products}


def deliver(store: Store, send: Callable[[str], None]) -> int:
    """Send queued alerts; mark each as sent only after `send` succeeded."""
    sent = 0
    for alert_id, text in store.pending_alerts():
        try:
            send(text)
        except Exception:
            break  # keep it (and everything after it) queued for the next run
        store.mark_alert_sent(alert_id)
        sent += 1
    return sent
