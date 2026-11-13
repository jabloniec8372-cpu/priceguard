"""Telegram delivery via the Bot API (sendMessage). HTTP is injectable."""
from __future__ import annotations

import json
import urllib.request
from typing import Callable


def _post(url: str, body: dict) -> None:  # pragma: no cover - needs network
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status != 200:
            raise RuntimeError(f"Telegram HTTP {r.status}")


def make_sender(token: str, chat_id: str, post: Callable[[str, dict], None] = _post) -> Callable[[str], None]:
    def send(text: str) -> None:
        post(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat_id, "text": text})
    return send
