"""Data model and the product list (stand-in for the client's Google Sheet)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    my_price: Decimal
    currency: str
    variant: str  # text that must appear in the competitor's product title
    competitor: str
    url: str
    target: Optional[Decimal] = None  # alert when the competitor price is at or below this
    ean: str = ""  # if set and the page publishes a GTIN, they must match


@dataclass
class Reading:
    price: Optional[Decimal]
    currency: Optional[str]
    available: Optional[bool]  # None = the page does not say
    shipping: Optional[Decimal]
    title: str
    gtin: str = ""  # digits only; "" = the page does not publish one


def _digits(v: str) -> str:
    return "".join(c for c in v if c.isdigit())


def load_products(path: str, base: str = "") -> list[Product]:
    """CSV columns: sku,name,my_price,currency,variant,competitor,url
    and optionally target_price,ean.
    '{base}' in a url is replaced by `base` (used by the local demo shop)."""
    with open(path, encoding="utf-8", newline="") as f:
        return [
            Product(
                sku=r["sku"].strip(),
                name=r["name"].strip(),
                my_price=Decimal(r["my_price"].strip().replace(",", ".")),
                currency=r["currency"].strip().upper(),
                variant=r["variant"].strip(),
                competitor=r["competitor"].strip(),
                url=r["url"].strip().replace("{base}", base),
                target=Decimal(r["target_price"].strip().replace(",", ".")) if (r.get("target_price") or "").strip() else None,
                ean=_digits(r.get("ean") or ""),
            )
            for r in csv.DictReader(f)
            if r.get("sku", "").strip()
        ]
