"""The shopper's wish list: things to hunt (phone, shoes, console ...), each
with a target price and one or more shop links. Stored next to the readings."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urlparse

from .models import Product, _digits
from .store import Store

MAX_LINKS = 10
CURRENCIES = ("EUR", "PLN", "USD", "GBP", "CHF")


def parse_links(text: str) -> list[tuple[str, str]]:
    """One link per line: `https://...` or `Shop name | https://...`."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        shop, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        shop, url = shop.strip(), url.strip()
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError(f"not a web link: {url[:60]}")
        out.append((shop[:60] or u.netloc.removeprefix("www."), url[:500]))
    if not out:
        raise ValueError("add at least one shop link")
    if len(out) > MAX_LINKS:
        raise ValueError(f"at most {MAX_LINKS} links per item")
    return out


def parse_money(text: str) -> Optional[Decimal]:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        v = Decimal(text)
    except InvalidOperation:
        raise ValueError("price is not a number")
    if v <= 0 or v > 10_000_000:
        raise ValueError("price out of range")
    return v


def add_wish(store: Store, name: str, links: list[tuple[str, str]], category: str = "", target: Optional[Decimal] = None,
             currency: str = "EUR", ean: str = "", variant: str = "") -> int:
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError("give the item a name (up to 120 characters)")
    if currency not in CURRENCIES:
        raise ValueError("unknown currency")
    with store.db:
        cur = store.db.execute(
            "INSERT INTO wishes (name, category, currency, target, ean, variant) VALUES (?,?,?,?,?,?)",
            (name, category.strip()[:40], currency, str(target) if target is not None else None,
             _digits(ean), variant.strip()[:80]))
        wid = cur.lastrowid
        for shop, url in links:
            store.db.execute("INSERT OR IGNORE INTO links (wish_id, shop, url) VALUES (?,?,?)", (wid, shop, url))
    return wid


def list_wishes(store: Store) -> list[dict]:
    out = []
    for w in store.db.execute("SELECT id, name, category, currency, target, ean, variant FROM wishes ORDER BY id").fetchall():
        links = store.db.execute("SELECT shop, url FROM links WHERE wish_id=? ORDER BY id", (w[0],)).fetchall()
        out.append(dict(id=w[0], name=w[1], category=w[2], currency=w[3],
                        target=Decimal(w[4]) if w[4] is not None else None, ean=w[5], variant=w[6], links=links))
    return out


def sku(wish_id: int) -> str:
    return f"W{wish_id}"


def delete_wish(store: Store, wish_id: int) -> None:
    with store.db:
        store.db.execute("DELETE FROM links WHERE wish_id=?", (wish_id,))
        store.db.execute("DELETE FROM wishes WHERE id=?", (wish_id,))
        store.db.execute("DELETE FROM readings WHERE sku=?", (sku(wish_id),))


def wish_products(store: Store) -> list[Product]:
    """One Product per (item, shop link); my_price 0 marks shopper mode."""
    return [Product(sku(w["id"]), w["name"], Decimal(0), w["currency"], w["variant"], shop, url, w["target"], w["ean"])
            for w in list_wishes(store) for shop, url in w["links"]]
