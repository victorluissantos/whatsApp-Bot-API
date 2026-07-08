"""CRUD de gatilhos (triggers) no MongoDB."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

import pymongo

from datasource import trigger_matcher
from datasource.app_timezone import now_local

logger = logging.getLogger(__name__)

TRIGGERS_COLLECTION = "wa_triggers"
EXECUTIONS_COLLECTION = "wa_trigger_executions"
EXPORT_SCHEMA_VERSION = 1

UNIQUE_SCOPES = ("minute", "hour", "day", "week", "month", "year", "forever")

RESERVED_TRIGGER_IDS = frozenset({"new", "all", "create", "export", "import"})


def _coll(mgd) -> Any:
    return mgd.db[TRIGGERS_COLLECTION]


def _exec_coll(mgd) -> Any:
    return mgd.db[EXECUTIONS_COLLECTION]


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.isoformat()
    return dt.isoformat() + "Z"


def _doc_to_dict(doc: dict) -> dict:
    out = dict(doc)
    out.pop("_id", None)
    out["id"] = out.pop("trigger_id")
    out["created_at"] = _iso_utc(out.get("created_at"))
    out["updated_at"] = _iso_utc(out.get("updated_at"))
    return out


def ensure_indexes(mgd) -> None:
    try:
        coll = _coll(mgd)
        coll.create_index("trigger_id", unique=True)
        coll.create_index([("enabled", pymongo.ASCENDING), ("name", pymongo.ASCENDING)])
        coll.create_index("name")
        exec_coll = _exec_coll(mgd)
        exec_coll.create_index(
            [("trigger_id", pymongo.ASCENDING), ("contact_key", pymongo.ASCENDING), ("scope_key", pymongo.ASCENDING)],
            unique=True,
        )
    except Exception as e:
        logger.debug("Índices de triggers (pode ser idempotente): %s", e)


def list_triggers(mgd, enabled_only: bool = False) -> list[dict]:
    query = {"enabled": True} if enabled_only else {}
    cursor = _coll(mgd).find(query).sort([("name", pymongo.ASCENDING)])
    return [_doc_to_dict(doc) for doc in cursor]


def get_trigger(mgd, trigger_id: str) -> Optional[dict]:
    doc = _coll(mgd).find_one({"trigger_id": trigger_id})
    if not doc:
        return None
    return _doc_to_dict(doc)


def create_trigger(mgd, data: dict) -> dict:
    pattern = data["pattern"].strip()
    validate_trigger_pattern(pattern)
    trigger_id = str(uuid.uuid4())
    now = datetime.utcnow()
    doc = {
        "trigger_id": trigger_id,
        "name": data["name"].strip(),
        "pattern": pattern,
        "case_sensitive": bool(data.get("case_sensitive", False)),
        "reply_message": data["reply_message"].strip(),
        "enabled": bool(data.get("enabled", True)),
        "schedule": data.get("schedule") or _default_schedule(),
        "unique": data.get("unique") or _default_unique(),
        "created_at": now,
        "updated_at": now,
    }
    _coll(mgd).insert_one(doc)
    return _doc_to_dict(doc)


def update_trigger(mgd, trigger_id: str, data: dict) -> Optional[dict]:
    existing = _coll(mgd).find_one({"trigger_id": trigger_id})
    if not existing:
        return None
    pattern = data["pattern"].strip()
    validate_trigger_pattern(pattern)
    update = {
        "name": data["name"].strip(),
        "pattern": pattern,
        "case_sensitive": bool(data.get("case_sensitive", False)),
        "reply_message": data["reply_message"].strip(),
        "enabled": bool(data.get("enabled", True)),
        "schedule": data.get("schedule") or _default_schedule(),
        "unique": data.get("unique") or _default_unique(),
        "updated_at": datetime.utcnow(),
    }
    _coll(mgd).update_one({"trigger_id": trigger_id}, {"$set": update})
    return get_trigger(mgd, trigger_id)


def delete_trigger(mgd, trigger_id: str) -> bool:
    result = _coll(mgd).delete_one({"trigger_id": trigger_id})
    return result.deleted_count > 0


def set_trigger_enabled(mgd, trigger_id: str, enabled: bool) -> Optional[dict]:
    result = _coll(mgd).update_one(
        {"trigger_id": trigger_id},
        {"$set": {"enabled": bool(enabled), "updated_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        return None
    return get_trigger(mgd, trigger_id)


def set_triggers_enabled_bulk(mgd, trigger_ids: list[str], enabled: bool) -> dict:
    cleaned_ids = [str(trigger_id).strip() for trigger_id in trigger_ids if str(trigger_id).strip()]
    unique_ids = list(dict.fromkeys(cleaned_ids))
    if not unique_ids:
        return {"matched": 0, "modified": 0}

    result = _coll(mgd).update_many(
        {"trigger_id": {"$in": unique_ids}},
        {"$set": {"enabled": bool(enabled), "updated_at": datetime.utcnow()}},
    )
    return {"matched": int(result.matched_count), "modified": int(result.modified_count)}


def _default_schedule() -> dict:
    return {
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "all_day": True,
        "time_start": "09:00",
        "time_end": "18:00",
    }


def _default_unique() -> dict:
    return {"enabled": False, "scope": "day"}


def normalize_schedule(raw: dict) -> dict:
    days = raw.get("days_of_week") or [0, 1, 2, 3, 4, 5, 6]
    days = sorted({int(d) for d in days if 0 <= int(d) <= 6})
    if not days:
        days = [0, 1, 2, 3, 4, 5, 6]
    all_day = bool(raw.get("all_day", True))
    return {
        "days_of_week": days,
        "all_day": all_day,
        "time_start": str(raw.get("time_start") or "09:00")[:5],
        "time_end": str(raw.get("time_end") or "18:00")[:5],
    }


def normalize_unique(raw: dict) -> dict:
    scope = str(raw.get("scope") or "day").lower()
    if scope not in UNIQUE_SCOPES:
        scope = "day"
    return {"enabled": bool(raw.get("enabled", False)), "scope": scope}


def format_schedule_summary(schedule: dict) -> str:
    day_names = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    days = schedule.get("days_of_week") or []
    if len(days) == 7:
        days_label = "Todos os dias"
    else:
        days_label = ", ".join(day_names[d] for d in sorted(days) if 0 <= d <= 6)
    if schedule.get("all_day"):
        return f"{days_label} · Dia todo"
    start = schedule.get("time_start", "09:00")
    end = schedule.get("time_end", "18:00")
    return f"{days_label} · {start}–{end}"


def format_unique_summary(unique: dict) -> str:
    if not unique.get("enabled"):
        return "Repetível"
    scope_labels = {
        "minute": "Única por minuto",
        "hour": "Única por hora",
        "day": "Única por dia",
        "week": "Única por semana",
        "month": "Única por mês",
        "year": "Única por ano",
        "forever": "Única (eterna)",
    }
    return scope_labels.get(unique.get("scope", "day"), "Única")


def message_matches_trigger(message: str, trigger: dict) -> bool:
    """Avalia se a mensagem satisfaz o padrão do trigger."""
    return trigger_matcher.matches_pattern(
        message,
        trigger.get("pattern", ""),
        bool(trigger.get("case_sensitive")),
    )


def contact_key(phone: str, name: Optional[str] = None) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits:
        return digits
    return f"name:{(name or '').strip()}"


def is_within_schedule(schedule: dict, now: datetime) -> bool:
    weekday = now.weekday()
    days = schedule.get("days_of_week") or list(range(7))
    if weekday not in days:
        return False
    if schedule.get("all_day", True):
        return True
    start = str(schedule.get("time_start") or "09:00")[:5]
    end = str(schedule.get("time_end") or "18:00")[:5]
    current = now.strftime("%H:%M")
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def unique_scope_key(scope: str, now: datetime) -> str:
    scope = (scope or "day").lower()
    if scope == "minute":
        return now.strftime("%Y-%m-%dT%H:%M")
    if scope == "hour":
        return now.strftime("%Y-%m-%dT%H")
    if scope == "day":
        return now.strftime("%Y-%m-%d")
    if scope == "week":
        return now.strftime("%Y-W%W")
    if scope == "month":
        return now.strftime("%Y-%m")
    if scope == "year":
        return now.strftime("%Y")
    if scope == "forever":
        return "forever"
    return now.strftime("%Y-%m-%d")


def try_claim_execution(
    mgd,
    trigger_id: str,
    contact_key: str,
    unique: dict,
    now: datetime,
) -> bool:
    """Reserva execução (unique). Retorna True se pode disparar."""
    if not unique.get("enabled"):
        return True
    scope_key = unique_scope_key(str(unique.get("scope") or "day"), now)
    doc = {
        "trigger_id": trigger_id,
        "contact_key": contact_key,
        "scope_key": scope_key,
        "executed_at": now if now.tzinfo else now_local(),
    }
    try:
        _exec_coll(mgd).insert_one(doc)
        return True
    except pymongo.errors.DuplicateKeyError:
        return False


def release_execution_claim(
    mgd,
    trigger_id: str,
    contact_key: str,
    unique: dict,
    now: datetime,
) -> None:
    """Remove claim se enfileiramento falhou após insert."""
    if not unique.get("enabled"):
        return
    scope_key = unique_scope_key(str(unique.get("scope") or "day"), now)
    _exec_coll(mgd).delete_one(
        {"trigger_id": trigger_id, "contact_key": contact_key, "scope_key": scope_key}
    )


def validate_trigger_pattern(pattern: str) -> None:
    """Valida sintaxe do campo pattern."""
    trigger_matcher.validate_pattern(pattern)


def _trigger_export_item(doc: dict) -> dict:
    return {
        "name": doc["name"],
        "pattern": doc["pattern"],
        "case_sensitive": doc.get("case_sensitive", False),
        "reply_message": doc["reply_message"],
        "enabled": doc.get("enabled", True),
        "schedule": doc.get("schedule") or _default_schedule(),
        "unique": doc.get("unique") or _default_unique(),
    }


def export_triggers(mgd) -> dict:
    items = list_triggers(mgd)
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": _iso_utc(datetime.utcnow()),
        "triggers": [_trigger_export_item(t) for t in items],
    }


def _validate_import_item(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"Item {index}: deve ser um objeto")
    name = str(raw.get("name") or "").strip()
    pattern = str(raw.get("pattern") or "").strip()
    reply = str(raw.get("reply_message") or "").strip()
    if not name:
        raise ValueError(f"Item {index}: campo 'name' é obrigatório")
    if not pattern:
        raise ValueError(f"Item {index}: campo 'pattern' é obrigatório")
    if not reply:
        raise ValueError(f"Item {index}: campo 'reply_message' é obrigatório")
    try:
        validate_trigger_pattern(pattern)
    except trigger_matcher.PatternSyntaxError as e:
        raise ValueError(f"Item {index}: padrão inválido — {e}") from e
    return {
        "name": name,
        "pattern": pattern,
        "case_sensitive": bool(raw.get("case_sensitive", False)),
        "reply_message": reply,
        "enabled": bool(raw.get("enabled", True)),
        "schedule": normalize_schedule(raw.get("schedule") or {}),
        "unique": normalize_unique(raw.get("unique") or {}),
    }


def import_triggers(mgd, payload: dict, mode: str = "merge") -> dict:
    if not isinstance(payload, dict):
        raise ValueError("JSON inválido: raiz deve ser um objeto")
    schema_version = payload.get("schema_version")
    if schema_version is not None and int(schema_version) != EXPORT_SCHEMA_VERSION:
        raise ValueError(f"schema_version incompatível: {schema_version}")

    raw_items = payload.get("triggers")
    if raw_items is None and isinstance(payload.get("items"), list):
        raw_items = payload["items"]
    if not isinstance(raw_items, list):
        raise ValueError("Campo 'triggers' deve ser uma lista")

    mode = (mode or "merge").lower()
    if mode not in ("merge", "replace"):
        raise ValueError("mode deve ser 'merge' ou 'replace'")

    if mode == "replace":
        _coll(mgd).delete_many({})

    imported = 0
    skipped = 0
    errors: list[str] = []
    existing_names = {d["name"] for d in _coll(mgd).find({}, {"name": 1})}

    for i, raw in enumerate(raw_items):
        try:
            data = _validate_import_item(raw, i + 1)
        except ValueError as e:
            errors.append(str(e))
            continue

        if mode == "merge" and data["name"] in existing_names:
            skipped += 1
            continue

        create_trigger(mgd, data)
        existing_names.add(data["name"])
        imported += 1

    return {"imported": imported, "skipped": skipped, "errors": errors, "total": len(raw_items)}
