"""Read price, currency, stock and shipping from a product page.

Order of trust: schema.org JSON-LD first (what most shops publish for Google),
then schema.org microdata. If neither yields a price we return None and the
caller marks the product "to check". We never guess a price from page text.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Iterator, Optional

from .models import Reading

_LD = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_SYMBOLS = {"€": "EUR", "zł": "PLN", "£": "GBP", "$": "USD"}


def parse_price(text) -> Optional[Decimal]:
    s = re.sub(r"[^\d,.]", "", str(text))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _walk(node) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _available(value) -> Optional[bool]:
    s = str(value or "")
    if "InStock" in s or "LimitedAvailability" in s:
        return True
    if "OutOfStock" in s or "SoldOut" in s:
        return False
    return None


def _from_jsonld(html: str) -> Optional[Reading]:
    for block in _LD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" not in types:
                continue
            offer = node.get("offers")
            offer = offer[0] if isinstance(offer, list) and offer else offer
            if not isinstance(offer, dict):
                continue
            rate = ((offer.get("shippingDetails") or {}).get("shippingRate") or {}).get("value")
            return Reading(
                price=parse_price(offer.get("price")) if offer.get("price") is not None else None,
                currency=(offer.get("priceCurrency") or None),
                available=_available(offer.get("availability")),
                shipping=parse_price(rate) if rate is not None else None,
                title=str(node.get("name", "")),
            )
    return None


class _Microdata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.props: dict[str, str] = {}
        self._pending: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        prop = a.get("itemprop")
        if not prop:
            return
        value = a.get("content") or a.get("href")
        if value:
            self.props.setdefault(prop, value)
        else:
            self._pending = prop

    def handle_data(self, data):
        if self._pending and data.strip():
            self.props.setdefault(self._pending, data.strip())
            self._pending = None


def _from_microdata(html: str) -> Optional[Reading]:
    p = _Microdata()
    p.feed(html)
    if "price" not in p.props:
        return None
    raw = p.props["price"]
    currency = p.props.get("priceCurrency")
    if not currency:
        currency = next((c for sym, c in _SYMBOLS.items() if sym in raw), None)
    return Reading(
        price=parse_price(raw),
        currency=currency,
        available=_available(p.props.get("availability")),
        shipping=None,
        title=p.props.get("name", ""),
    )


def parse_reading(html: str) -> Optional[Reading]:
    reading = _from_jsonld(html)
    if reading is None or reading.price is None:
        reading = _from_microdata(html) or reading
    return reading
