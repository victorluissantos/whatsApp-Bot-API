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


def chats_have_unread(chats: list[dict]) -> bool:
    return any(
        str(c.get("unreadCount") or "0").strip() not in ("", "0")
        for c in (chats or [])
    )


def _chat_merge_key(chat: dict) -> str:
    phone = str(chat.get("phone") or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits:
        if not digits.startswith("55") and len(digits) >= 10:
            digits = "55" + digits
        return f"phone:{digits}"
    name = str(chat.get("name") or "").strip()
    if name:
        return f"name:{name}"
    return ""


def merge_chats_for_processing(raw: list[dict], cached: list[dict]) -> list[dict]:
    """Mescla leitura atual do DOM com cache (preserva unread se o DOM falhar no contador)."""
    by_key: dict[str, dict] = {}
    for chat in cached or []:
        key = _chat_merge_key(chat)
        if key:
            by_key[key] = dict(chat)
    for chat in raw or []:
        key = _chat_merge_key(chat)
        if not key:
            continue
        merged = dict(by_key.get(key) or {})
        prev_unread = str(merged.get("unreadCount") or "0").strip()
        merged.update(chat)
        raw_unread = str(chat.get("unreadCount") or "0").strip()
        if raw_unread in ("", "0") and prev_unread not in ("", "0"):
            merged["unreadCount"] = prev_unread
        by_key[key] = merged
    return list(by_key.values())
