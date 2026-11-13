"""The shopper's wish list: things to hunt (phone, shoes, console ...), each
with a destination country, the markets to look in, a target price and shop
links. Stored next to the readings."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

from .markets import COUNTRIES, DEFAULT_RATES, DESTINATIONS, SHOPS, country_of_url, make_landed
from .models import Product, _digits
from .store import Store

MAX_LINKS = 10
CURRENCIES = ("EUR", "PLN", "USD", "GBP", "CHF", "CZK", "CNY")


def _shop_and_url(line: str) -> tuple[str, str]:
    shop, _, url = line.rpartition("|") if "|" in line else ("", "", line)
    shop, url = shop.strip(), url.strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"not a web link: {url[:60]}")
    return (shop[:60] or u.netloc.removeprefix("www.")), url[:500]


def parse_links(text: str, allow_empty: bool = False) -> list[tuple[str, str]]:
    """One link per line: `https://...` or `Shop name | https://...`."""
    out = [_shop_and_url(line.strip()) for line in text.splitlines() if line.strip()]
    if not out and not allow_empty:
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


def parse_pct(text: str) -> Optional[Decimal]:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        v = Decimal(text)
    except InvalidOperation:
        raise ValueError("duty is not a number")
    if v < 0 or v > 100:
        raise ValueError("duty must be between 0 and 100 %")
    return v


def clean_markets(codes: Iterable[str]) -> list[str]:
    out = []
    for c in codes:
        c = c.strip().upper()
        if c and c not in COUNTRIES:
            raise ValueError(f"unknown market: {c[:10]}")
        if c and c not in out:
            out.append(c)
    return out


def add_wish(store: Store, name: str, links: list[tuple], category: str = "", target: Optional[Decimal] = None,
             currency: str = "EUR", ean: str = "", variant: str = "", dest: str = "", markets: Iterable[str] = (),
             ship: Optional[Decimal] = None, duty: Optional[Decimal] = None) -> int:
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError("give the item a name (up to 120 characters)")
    if dest:
        if dest not in DESTINATIONS:
            raise ValueError("unknown destination country")
        currency = COUNTRIES[dest]["cur"]
    if currency not in CURRENCIES:
        raise ValueError("unknown currency")
    mk = clean_markets(markets)
    with store.db:
        cur = store.db.execute(
            "INSERT INTO wishes (name, category, currency, target, ean, variant, dest, markets, ship_eur, duty_pct) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, category.strip()[:40], currency, str(target) if target is not None else None,
             _digits(ean), variant.strip()[:80], dest, ",".join(mk),
             str(ship) if ship is not None else None, str(duty) if duty is not None else None))
        wid = cur.lastrowid
        for item in links:
            shop, url, *rest = item
            store.db.execute("INSERT OR IGNORE INTO links (wish_id, shop, url, country) VALUES (?,?,?,?)",
                             (wid, shop, url, rest[0] if rest else country_of_url(url)))
    return wid


def add_link(store: Store, wish_id: int, shop: str, url: str) -> None:
    shop, url = _shop_and_url(f"{shop} | {url}" if shop else url)
    with store.db:
        if not store.db.execute("SELECT 1 FROM wishes WHERE id=?", (wish_id,)).fetchone():
            raise ValueError("no such item")
        if store.db.execute("SELECT COUNT(*) FROM links WHERE wish_id=?", (wish_id,)).fetchone()[0] >= MAX_LINKS:
            raise ValueError(f"at most {MAX_LINKS} links per item")
        store.db.execute("INSERT OR IGNORE INTO links (wish_id, shop, url, country) VALUES (?,?,?,?)",
                         (wish_id, shop, url, country_of_url(url)))


def list_wishes(store: Store) -> list[dict]:
    out = []
    rows = store.db.execute(
        "SELECT id, name, category, currency, target, ean, variant, dest, markets, ship_eur, duty_pct FROM wishes ORDER BY id").fetchall()
    for w in rows:
        links = store.db.execute("SELECT shop, url, country FROM links WHERE wish_id=? ORDER BY id", (w[0],)).fetchall()
        out.append(dict(id=w[0], name=w[1], category=w[2], currency=w[3],
                        target=Decimal(w[4]) if w[4] is not None else None, ean=w[5], variant=w[6],
                        dest=w[7], markets=[m for m in w[8].split(",") if m],
                        ship=Decimal(w[9]) if w[9] is not None else None,
                        duty=Decimal(w[10]) if w[10] is not None else None, links=links))
    return out


def sku(wish_id: int) -> str:
    return f"W{wish_id}"


def delete_wish(store: Store, wish_id: int) -> None:
    with store.db:
        store.db.execute("DELETE FROM links WHERE wish_id=?", (wish_id,))
        store.db.execute("DELETE FROM wishes WHERE id=?", (wish_id,))
        store.db.execute("DELETE FROM readings WHERE sku=?", (sku(wish_id),))


def landed_fn(w: dict, country: str, rates: dict) -> Optional[Callable[[Decimal], Decimal]]:
    """Cost estimator for one shop link, or None when we do not know the shop's country or the destination."""
    if country in COUNTRIES and w.get("dest") in COUNTRIES:
        return make_landed(country, w["dest"], rates, w.get("ship"), w.get("duty"))
    return None


def link_currency(w: dict, country: str) -> str:
    return COUNTRIES[country]["cur"] if country in COUNTRIES else w["currency"]


def wish_products(store: Store, rates: Optional[dict] = None) -> list[Product]:
    """One Product per (item, shop link); my_price 0 marks shopper mode."""
    rates = rates or DEFAULT_RATES
    return [Product(sku(w["id"]), w["name"], Decimal(0), link_currency(w, c), w["variant"], shop, url, w["target"], w["ean"],
                    landed_fn(w, c, rates), w["currency"])
            for w in list_wishes(store) for shop, url, c in w["links"]]


def shop_name(country: str, domain: str) -> str:
    return next((n for n, d, _ in SHOPS.get(country, []) if d == domain), domain)
