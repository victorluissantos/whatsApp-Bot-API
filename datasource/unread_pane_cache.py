"""Cache thread-safe da lista de chats não lidas (DOM #pane-side) e fingerprint para diff."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

_lock = threading.Lock()
_cached_chats: list[dict[str, Any]] = []
_fingerprint: str = ""
_updated_at: float = 0.0


def fingerprint_for_chats(chats: list[dict]) -> str:
    norm: list[dict[str, Any]] = []
    for c in sorted(chats, key=lambda x: (x.get("name") or "", x.get("lastMessage") or "")):
        norm.append(
            {
                "name": c.get("name"),
                "phone": c.get("phone"),
                "lastMessage": (c.get("lastMessage") or "")[:800],
                "dateTime": c.get("dateTime"),
                "unreadCount": str(c.get("unreadCount") or "0"),
                "photo": c.get("photo"),
            }
        )
    return json.dumps(norm, sort_keys=True, ensure_ascii=False)


def get_snapshot() -> tuple[list[dict[str, Any]], float]:
    with _lock:
        return list(_cached_chats), _updated_at


def update_if_changed(chats: list[dict], new_fp: str) -> bool:
    """Atualiza cache se o fingerprint mudou. Retorna True se houve mudança."""
    global _fingerprint, _cached_chats, _updated_at
    with _lock:
        if new_fp == _fingerprint:
            return False
        _fingerprint = new_fp
        _cached_chats = [dict(c) for c in chats]
        _updated_at = time.time()
        return True


def clear_cache() -> None:
    global _fingerprint, _cached_chats, _updated_at
    with _lock:
        _fingerprint = ""
        _cached_chats = []
        _updated_at = 0.0
