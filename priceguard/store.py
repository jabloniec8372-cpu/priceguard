"""SQLite: every reading (the history), plus an alert outbox.

Alerts are written to the outbox first and marked sent only after delivery
succeeded. A crash or a Telegram outage therefore re-sends instead of losing
an alert (at-least-once; in the rare crash between send and mark you may get
one duplicate, which is the right trade for price alerts).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .models import Reading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL, url TEXT NOT NULL, ts TEXT NOT NULL,
    status TEXT NOT NULL,           -- 'OK' or a reason it needs checking
    price TEXT, currency TEXT, available INTEGER, shipping TEXT, title TEXT
);
CREATE INDEX IF NOT EXISTS readings_by_product ON readings (sku, url, id);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class Row:
    id: int
    ts: str
    status: str
    price: Optional[Decimal]
    currency: Optional[str]
    available: Optional[bool]
    shipping: Optional[Decimal]
    title: str


def _row(r) -> Row:
    return Row(
        r[0], r[1], r[2],
        Decimal(r[3]) if r[3] is not None else None,
        r[4],
        None if r[5] is None else bool(r[5]),
        Decimal(r[6]) if r[6] is not None else None,
        r[7] or "",
    )


_COLS = "id, ts, status, price, currency, available, shipping, title"


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)

    def record(self, sku: str, url: str, ts: str, status: str, reading: Optional[Reading] = None) -> int:
        r = reading
        with self.db:
            cur = self.db.execute(
                "INSERT INTO readings (sku,url,ts,status,price,currency,available,shipping,title) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    sku, url, ts, status,
                    str(r.price) if r and r.price is not None else None,
                    r.currency if r else None,
                    None if not r or r.available is None else int(r.available),
                    str(r.shipping) if r and r.shipping is not None else None,
                    r.title if r else None,
                ),
            )
        return cur.lastrowid

    def last_ok(self, sku: str, url: str) -> Optional[Row]:
        r = self.db.execute(
            f"SELECT {_COLS} FROM readings WHERE sku=? AND url=? AND status='OK' ORDER BY id DESC LIMIT 1",
            (sku, url),
        ).fetchone()
        return _row(r) if r else None

    def last_any(self, sku: str, url: str) -> Optional[Row]:
        r = self.db.execute(
            f"SELECT {_COLS} FROM readings WHERE sku=? AND url=? ORDER BY id DESC LIMIT 1", (sku, url)
        ).fetchone()
        return _row(r) if r else None

    def history(self) -> list[tuple]:
        return self.db.execute("SELECT sku, url, ts, status, price, currency, available FROM readings ORDER BY id").fetchall()

    # ---- alert outbox
    def add_alert(self, key: str, text: str) -> bool:
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO alerts (key, text) VALUES (?, ?)", (key, text))
        return cur.rowcount == 1

    def pending_alerts(self) -> list[tuple[int, str]]:
        return self.db.execute("SELECT id, text FROM alerts WHERE sent=0 ORDER BY id").fetchall()

    def mark_alert_sent(self, alert_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE alerts SET sent=1 WHERE id=?", (alert_id,))

    def close(self) -> None:
        self.db.close()
