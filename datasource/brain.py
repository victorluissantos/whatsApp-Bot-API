"""Configuração e execução do Brain (resposta dinâmica via API antes dos triggers)."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

import pymongo
import requests

from datasource import curl_parser
from datasource import triggers as triggers_store
from datasource.app_timezone import now_local
from datasource.phone_utils import phone_digit_variants

logger = logging.getLogger(__name__)

BRAIN_CONFIG_ID = "default"
BRAIN_COLLECTION = "wa_brain_config"
BRAIN_EXECUTIONS_COLLECTION = "wa_brain_executions"
REQUEST_TIMEOUT_SECONDS = 60


def _config_coll(mgd) -> Any:
    return mgd.db[BRAIN_COLLECTION]


def _exec_coll(mgd) -> Any:
    return mgd.db[BRAIN_EXECUTIONS_COLLECTION]


def ensure_indexes(mgd) -> None:
    try:
        _config_coll(mgd).create_index("brain_id", unique=True)
        _exec_coll(mgd).create_index(
            [
                ("brain_id", pymongo.ASCENDING),
                ("contact_key", pymongo.ASCENDING),
                ("scope_key", pymongo.ASCENDING),
            ],
            unique=True,
        )
    except Exception as e:
        logger.debug("Índices do brain (pode ser idempotente): %s", e)


def get_config(mgd) -> Optional[dict]:
    doc = _config_coll(mgd).find_one({"brain_id": BRAIN_CONFIG_ID})
    if not doc:
        return None
    return _doc_to_dict(doc)


def save_config(mgd, data: dict) -> dict:
    curl_text = str(data.get("curl") or "").strip()
    if not curl_text:
        raise ValueError("Comando curl é obrigatório")

    response_field = str(data.get("response_field") or "").strip()
    if not response_field:
        raise ValueError("Campo de resposta é obrigatório")

    try:
        curl_parser.validate_phone_variable(curl_text)
        request = curl_parser.parse_curl(curl_text)
    except curl_parser.CurlParseError as e:
        raise ValueError(str(e)) from e
    now = datetime.utcnow()
    doc = {
        "brain_id": BRAIN_CONFIG_ID,
        "curl": curl_text,
        "request": request,
        "response_field": response_field,
        "enabled": bool(data.get("enabled", False)),
        "schedule": triggers_store.normalize_schedule(data.get("schedule") or {}),
        "unique": triggers_store.normalize_unique(data.get("unique") or {}),
        "updated_at": now,
    }
    _config_coll(mgd).update_one(
        {"brain_id": BRAIN_CONFIG_ID},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    saved = get_config(mgd)
    assert saved is not None
    return saved


def set_enabled(mgd, enabled: bool) -> Optional[dict]:
    result = _config_coll(mgd).update_one(
        {"brain_id": BRAIN_CONFIG_ID},
        {"$set": {"enabled": bool(enabled), "updated_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        return None
    return get_config(mgd)


def test_curl(curl_text: str, test_phone: Optional[str] = None) -> dict:
    try:
        curl_parser.validate_phone_variable(curl_text)
        request = curl_parser.parse_curl(curl_text)
    except curl_parser.CurlParseError as e:
        raise ValueError(str(e)) from e

    if curl_parser.has_phone_variable(curl_text):
        phone = re.sub(r"\D", "", str(test_phone or ""))
        if not phone:
            raise ValueError(
                f"Informe um telefone para teste (o curl usa a variável {curl_parser.PHONE_VARIABLE})"
            )
        request = curl_parser.substitute_phone(request, phone)

    response_data = _execute_request(request)
    paths = list_json_paths(response_data)
    return {
        "success": True,
        "data": response_data,
        "paths": paths,
        "phone_param": request.get("phone_param") or "",
    }


def fetch_message_for_phone(mgd, phone: str, now: Optional[datetime] = None) -> Optional[str]:
    """Executa o curl do Brain com o telefone e retorna o texto do campo configurado."""
    message, _reason = resolve_message_for_phone(mgd, phone, now=now)
    return message


def _ordered_phone_variants(phone: str) -> list[str]:
    """Variantes BR ordenadas: prefere celular com 9º dígito (11 dígitos locais)."""
    digits = re.sub(r"\D", "", str(phone or ""))
    variants = phone_digit_variants(digits) or ({digits} if digits else set())
    if not variants:
        return []

    def _sort_key(v: str) -> tuple:
        local = v[2:] if v.startswith("55") else v
        has_ninth = len(local) == 11 and len(local) > 2 and local[2] == "9"
        return (0 if has_ninth else 1, 0 if not v.startswith("55") else 1, -len(local))

    return sorted(variants, key=_sort_key)


def resolve_message_for_phone(
    mgd,
    phone: str,
    now: Optional[datetime] = None,
    contact_key: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """
    Executa o curl e retorna (mensagem, motivo).
    motivo vazio = sucesso; outros valores explicam skip/falha.
    Tenta variantes do telefone (com/sem 55 e 9º dígito) quando a API falha.
    """
    config = get_config(mgd)
    if not config or not config.get("enabled"):
        return None, "inativo"

    now = now or now_local()
    schedule = config.get("schedule") or {}
    if not triggers_store.is_within_schedule(schedule, now):
        logger.info("Brain fora do horário")
        return None, "fora_do_horario"

    try:
        base_request = curl_parser.parse_curl(str(config.get("curl") or ""))
    except curl_parser.CurlParseError as e:
        logger.warning("Brain com curl inválido: %s", e)
        return None, "curl_invalido"

    field = config.get("response_field") or ""
    variants = _ordered_phone_variants(phone)
    if not variants:
        return None, "telefone_invalido"

    last_reason = "campo_vazio"
    for variant in variants:
        request = curl_parser.substitute_phone(base_request, variant)
        celular = (request.get("data") or {}).get("celular") or (
            request.get("data") or {}
        ).get("telefone")
        logger.info(
            "Brain: consultando API para %s (variante=%s param=%s)",
            phone,
            variant,
            celular,
        )
        try:
            response_data = _execute_request(request)
        except Exception as e:
            logger.warning(
                "Brain: falha na requisição para %s (variante=%s): %s",
                phone,
                variant,
                e,
            )
            last_reason = "api_erro"
            continue

        message = extract_json_path(response_data, field)
        if message:
            if variant != re.sub(r"\D", "", str(phone or "")):
                logger.info(
                    "Brain: API respondeu com variante %s (original %s)",
                    variant,
                    phone,
                )
            return message, ""

        if isinstance(response_data, dict):
            status = response_data.get("status")
            if status in (False, "false", "0", 0):
                logger.info(
                    "Brain: API retornou status=false para %s (variante=%s)",
                    phone,
                    variant,
                )
                last_reason = "api_status_false"
                continue

        logger.info(
            "Brain: campo %r vazio para %s variante=%s (resposta=%s)",
            field,
            phone,
            variant,
            str(response_data)[:200],
        )
        last_reason = "campo_vazio"

    return None, last_reason


def is_enabled(mgd) -> bool:
    config = get_config(mgd)
    return bool(config and config.get("enabled"))


def should_allow_triggers(mgd, contact_key: str, now: Optional[datetime] = None) -> bool:
    """
    Triggers só podem disparar depois que o Brain já executou no escopo unique.
    Se unique desligado, triggers sempre permitidos após tentativa de brain.
    """
    config = get_config(mgd)
    if not config or not config.get("enabled"):
        return True

    unique_cfg = config.get("unique") or {}
    if not unique_cfg.get("enabled"):
        return True

    now = now or now_local()
    return has_execution_claim(mgd, contact_key, unique_cfg, now)


def is_active_for_contact(
    mgd,
    phone: str,
    contact_key: str,
    now: Optional[datetime] = None,
) -> bool:
    config = get_config(mgd)
    if not config or not config.get("enabled"):
        return False
    now = now or now_local()
    if not triggers_store.is_within_schedule(config.get("schedule") or {}, now):
        return False
    unique_cfg = config.get("unique") or {}
    if unique_cfg.get("enabled") and has_execution_claim(
        mgd, contact_key, unique_cfg, now
    ):
        return False
    return True


def has_execution_claim(
    mgd,
    contact_key: str,
    unique: dict,
    now: datetime,
) -> bool:
    if not unique.get("enabled"):
        return False
    scope_key = triggers_store.unique_scope_key(str(unique.get("scope") or "day"), now)
    from datasource.phone_utils import phone_digit_variants

    keys = list(phone_digit_variants(contact_key) or {contact_key})
    return (
        _exec_coll(mgd).find_one(
            {
                "brain_id": BRAIN_CONFIG_ID,
                "contact_key": {"$in": keys},
                "scope_key": scope_key,
            },
            {"_id": 1},
        )
        is not None
    )


def try_claim_execution(
    mgd,
    contact_key: str,
    unique: dict,
    now: datetime,
) -> bool:
    if not unique.get("enabled"):
        return True
    scope_key = triggers_store.unique_scope_key(str(unique.get("scope") or "day"), now)
    doc = {
        "brain_id": BRAIN_CONFIG_ID,
        "contact_key": contact_key,
        "scope_key": scope_key,
        "executed_at": now if now.tzinfo else now_local(),
    }
    try:
        _exec_coll(mgd).insert_one(doc)
        return True
    except pymongo.errors.DuplicateKeyError:
        return False


def release_execution_claim_by_keys(
    mgd,
    contact_key: str,
    scope_key: str,
) -> None:
    if not (contact_key and scope_key):
        return
    _exec_coll(mgd).delete_one(
        {
            "brain_id": BRAIN_CONFIG_ID,
            "contact_key": contact_key,
            "scope_key": scope_key,
        }
    )


def release_execution_claims_for_contact(mgd, phone: str) -> int:
    from datasource.phone_utils import phone_digit_variants

    keys = list(phone_digit_variants(phone))
    if not keys:
        return 0
    result = _exec_coll(mgd).delete_many(
        {"brain_id": BRAIN_CONFIG_ID, "contact_key": {"$in": keys}}
    )
    return int(result.deleted_count or 0)


def extract_json_path(data: Any, path: str) -> Optional[str]:
    path = str(path or "").strip()
    if not path:
        return None
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    if current is None:
        return None
    text = str(current).strip()
    return text or None


def list_json_paths(data: Any, prefix: str = "") -> list[dict]:
    paths: list[dict] = []
    if isinstance(data, dict):
        for key, value in data.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                paths.extend(list_json_paths(value, current))
            else:
                preview = str(value)
                if len(preview) > 120:
                    preview = preview[:117] + "..."
                paths.append({"path": current, "preview": preview, "type": type(value).__name__})
    elif isinstance(data, list):
        for index, value in enumerate(data):
            current = f"{prefix}[{index}]"
            if isinstance(value, (dict, list)):
                paths.extend(list_json_paths(value, current))
            else:
                preview = str(value)
                if len(preview) > 120:
                    preview = preview[:117] + "..."
                paths.append({"path": current, "preview": preview, "type": type(value).__name__})
    return paths


def _execute_request(request: dict[str, Any]) -> Any:
    method = str(request.get("method") or "GET").upper()
    url = str(request.get("url") or "")
    headers = dict(request.get("headers") or {})
    data = dict(request.get("data") or {})
    data_raw = request.get("data_raw")

    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": REQUEST_TIMEOUT_SECONDS,
    }

    if method in ("GET", "DELETE", "HEAD"):
        if data:
            kwargs["params"] = data
        response = requests.request(method, url, **kwargs)
    elif data_raw is not None:
        kwargs["data"] = data_raw
        response = requests.request(method, url, **kwargs)
    else:
        content_type = ""
        for key, value in headers.items():
            if key.lower() == "content-type":
                content_type = value.lower()
                break
        if "application/json" in content_type:
            import json

            kwargs["json"] = data
        else:
            kwargs["data"] = data
        response = requests.request(method, url, **kwargs)

    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        text = (response.text or "").strip()
        if not text:
            return {}
        return {"_raw": text}


def _doc_to_dict(doc: dict) -> dict:
    out = dict(doc)
    out.pop("_id", None)
    out["id"] = out.get("brain_id", BRAIN_CONFIG_ID)
    created = out.get("created_at")
    updated = out.get("updated_at")
    if created is not None:
        out["created_at"] = created.isoformat() + ("Z" if getattr(created, "tzinfo", None) is None else "")
    if updated is not None:
        out["updated_at"] = updated.isoformat() + ("Z" if getattr(updated, "tzinfo", None) is None else "")
    schedule = out.get("schedule") or triggers_store.normalize_schedule({})
    unique = out.get("unique") or triggers_store.normalize_unique({})
    out["schedule"] = schedule
    out["unique"] = unique
    out["schedule_summary"] = triggers_store.format_schedule_summary(schedule)
    out["unique_summary"] = triggers_store.format_unique_summary(unique)
    return out


def config_to_form(config: Optional[dict]) -> dict:
    if not config:
        return _default_form()
    schedule = config.get("schedule") or {}
    unique = config.get("unique") or {}
    return {
        "curl": config.get("curl") or "",
        "response_field": config.get("response_field") or "",
        "enabled": bool(config.get("enabled")),
        "days_of_week": schedule.get("days_of_week") or [0, 1, 2, 3, 4, 5, 6],
        "all_day": bool(schedule.get("all_day", True)),
        "time_start": schedule.get("time_start") or "09:00",
        "time_end": schedule.get("time_end") or "18:00",
        "unique_enabled": bool(unique.get("enabled")),
        "unique_scope": unique.get("scope") or "day",
        "schedule_summary": config.get("schedule_summary") or "",
        "unique_summary": config.get("unique_summary") or "",
        "test_phone": config.get("test_phone") or "",
    }


def _default_form() -> dict:
    return {
        "curl": "",
        "response_field": "",
        "enabled": False,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "all_day": True,
        "time_start": "09:00",
        "time_end": "18:00",
        "unique_enabled": False,
        "unique_scope": "day",
        "schedule_summary": "",
        "unique_summary": "",
        "test_phone": "",
    }


def form_to_payload(
    curl: str,
    response_field: str,
    enabled: Optional[str],
    days_of_week: list[str],
    all_day: Optional[str],
    time_start: str,
    time_end: str,
    unique_enabled: Optional[str],
    unique_scope: str,
) -> dict:
    days = [int(d) for d in days_of_week if str(d).isdigit()]
    return {
        "curl": curl,
        "response_field": response_field,
        "enabled": enabled is not None,
        "schedule": triggers_store.normalize_schedule(
            {
                "days_of_week": days,
                "all_day": all_day is not None,
                "time_start": time_start,
                "time_end": time_end,
            }
        ),
        "unique": triggers_store.normalize_unique(
            {"enabled": unique_enabled is not None, "scope": unique_scope}
        ),
    }
