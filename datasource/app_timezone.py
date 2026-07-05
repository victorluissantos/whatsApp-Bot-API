"""Fuso horário da aplicação (triggers, logs, comparações locais)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Brasília — padrão quando TZ não está definido no ambiente
DEFAULT_TZ = "America/Sao_Paulo"


def get_timezone_name() -> str:
    name = (os.environ.get("TZ") or DEFAULT_TZ).strip()
    return name or DEFAULT_TZ


def get_timezone() -> ZoneInfo:
    name = get_timezone_name()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def now_local() -> datetime:
    """Hora local usada pelos triggers (faixa de horário, unique por dia, etc.)."""
    return datetime.now(get_timezone())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_local_iso(dt: datetime | None = None) -> str:
    value = dt or now_local()
    return value.isoformat()
