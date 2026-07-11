"""Simulação de triggers e Brain em memória (unique simulado, sem gravar execuções)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from datasource import brain as brain_store
from datasource import triggers as triggers_store
from datasource.app_timezone import now_local

SIMULATOR_CONTACT_KEY = "simulator"
BRAIN_SIMULATOR_ID = "brain"
BRAIN_SIMULATOR_NAME = "Brain"


def _claim_key(trigger_id: str, scope_key: str) -> str:
    return f"{trigger_id}|{scope_key}"


def _brain_claim_key(scope_key: str) -> str:
    return f"brain|{scope_key}"


def _has_claim(claimed_keys: set[str], trigger_id: str, unique: dict, now: datetime) -> bool:
    if not unique.get("enabled"):
        return False
    scope_key = triggers_store.unique_scope_key(str(unique.get("scope") or "day"), now)
    return _claim_key(trigger_id, scope_key) in claimed_keys


def _has_brain_claim(claimed_keys: set[str], unique: dict, now: datetime) -> bool:
    if not unique.get("enabled"):
        return False
    scope_key = triggers_store.unique_scope_key(str(unique.get("scope") or "day"), now)
    return _brain_claim_key(scope_key) in claimed_keys


def _evaluate_trigger_candidates(
    messages: list[dict],
    active_triggers: list[dict],
    claimed: set[str],
    now: datetime,
) -> tuple[list[dict], list[dict[str, str]]]:
    candidates: list[dict] = []
    events: list[dict[str, str]] = []

    for trigger in active_triggers:
        trigger_id = str(trigger.get("id") or "")
        trigger_name = str(trigger.get("name") or trigger_id or "Trigger")
        schedule = trigger.get("schedule") or {}

        if not triggers_store.is_within_schedule(schedule, now):
            events.append(
                {
                    "trigger_id": trigger_id,
                    "trigger_name": trigger_name,
                    "status": "skipped",
                    "reason": "fora do horário",
                }
            )
            continue

        if not triggers_store.history_matches_trigger(messages, trigger):
            events.append(
                {
                    "trigger_id": trigger_id,
                    "trigger_name": trigger_name,
                    "status": "skipped",
                    "reason": "padrão não corresponde ao histórico",
                }
            )
            continue

        unique_cfg = trigger.get("unique") or {}
        if _has_claim(claimed, trigger_id, unique_cfg, now):
            events.append(
                {
                    "trigger_id": trigger_id,
                    "trigger_name": trigger_name,
                    "status": "skipped",
                    "reason": "unique já executado nesta simulação",
                }
            )
            continue

        reply_messages = triggers_store.get_reply_messages(trigger)
        if not reply_messages:
            events.append(
                {
                    "trigger_id": trigger_id,
                    "trigger_name": trigger_name,
                    "status": "skipped",
                    "reason": "sem mensagens de resposta",
                }
            )
            continue

        candidates.append(trigger)

    return candidates, events


def _try_brain_simulation(
    mgd,
    phone: str,
    claimed: set[str],
    now: datetime,
) -> tuple[Optional[str], list[dict[str, str]], bool]:
    """
    Tenta obter resposta do Brain (chama API real com o telefone informado).
    Retorna (mensagem ou None, eventos de debug, deferir_para_triggers).
    """
    config = brain_store.get_config(mgd)
    events: list[dict[str, str]] = []

    if not config:
        return None, events, False

    if not config.get("enabled"):
        events.append(
            {
                "trigger_id": BRAIN_SIMULATOR_ID,
                "trigger_name": BRAIN_SIMULATOR_NAME,
                "status": "skipped",
                "reason": "brain inativo",
            }
        )
        return None, events, False

    schedule = config.get("schedule") or {}
    if not triggers_store.is_within_schedule(schedule, now):
        events.append(
            {
                "trigger_id": BRAIN_SIMULATOR_ID,
                "trigger_name": BRAIN_SIMULATOR_NAME,
                "status": "skipped",
                "reason": "fora do horário",
            }
        )
        return None, events, False

    unique_cfg = config.get("unique") or {}
    if _has_brain_claim(claimed, unique_cfg, now):
        events.append(
            {
                "trigger_id": BRAIN_SIMULATOR_ID,
                "trigger_name": BRAIN_SIMULATOR_NAME,
                "status": "skipped",
                "reason": "unique já executado nesta simulação",
            }
        )
        return None, events, False

    phone_digits = "".join(filter(str.isdigit, str(phone or "")))
    if not phone_digits:
        events.append(
            {
                "trigger_id": BRAIN_SIMULATOR_ID,
                "trigger_name": BRAIN_SIMULATOR_NAME,
                "status": "skipped",
                "reason": "telefone inválido",
            }
        )
        return None, events, False

    message, reason = brain_store.resolve_message_for_phone(mgd, phone_digits, now)
    if not message:
        field = config.get("response_field") or ""
        defer_triggers = brain_store.defers_to_triggers(reason)
        events.append(
            {
                "trigger_id": BRAIN_SIMULATOR_ID,
                "trigger_name": BRAIN_SIMULATOR_NAME,
                "status": "skipped",
                "reason": (
                    f"campo {field!r} vazio — seguindo triggers"
                    if defer_triggers
                    else f"campo {field!r} vazio ou API sem resposta"
                ),
            }
        )
        if defer_triggers and unique_cfg.get("enabled"):
            scope_key = triggers_store.unique_scope_key(
                str(unique_cfg.get("scope") or "day"), now
            )
            claimed.add(_brain_claim_key(scope_key))
        return None, events, defer_triggers

    if unique_cfg.get("enabled"):
        scope_key = triggers_store.unique_scope_key(
            str(unique_cfg.get("scope") or "day"), now
        )
        claimed.add(_brain_claim_key(scope_key))

    events.append(
        {
            "trigger_id": BRAIN_SIMULATOR_ID,
            "trigger_name": BRAIN_SIMULATOR_NAME,
            "status": "fired",
            "reason": f"resposta via API ({config.get('response_field')})",
        }
    )
    return message, events, False


def _normalize_history(messages: list[dict] | None, latest_text: str) -> list[dict]:
    history: list[dict] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("message") or "").strip()
        if not text:
            continue
        origin = str(item.get("origem") or "").strip().lower()
        if origin not in ("enviada", "recebida"):
            origin = "recebida"
        history.append(
            {
                "message": text,
                "origem": origin,
                "data": str(item.get("data") or ""),
            }
        )
    if not history and latest_text:
        history.append(
            {
                "message": latest_text,
                "origem": "recebida",
                "data": "",
            }
        )
    return history


def evaluate_message(
    message: str,
    active_triggers: list[dict],
    claimed_keys: set[str],
    now: datetime | None = None,
    mgd=None,
    phone: str | None = None,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Avalia uma mensagem contra triggers ativos (e Brain, se telefone informado).
    Unique fica só em memória — não grava execuções no MongoDB.
    """
    text = (message or "").strip()
    conversation = _normalize_history(history, text)
    if not conversation:
        return {"replies": [], "events": [], "claimed_keys": sorted(claimed_keys)}

    now = now or now_local()
    claimed = set(claimed_keys)
    replies: list[dict[str, str]] = []
    events: list[dict[str, str]] = []

    phone_value = str(phone or "").strip()
    brain_config = brain_store.get_config(mgd) if mgd is not None else None
    brain_active = bool(brain_config and brain_config.get("enabled"))
    allow_triggers = True
    if brain_active and brain_config:
        unique_cfg = brain_config.get("unique") or {}
        if unique_cfg.get("enabled"):
            allow_triggers = _has_brain_claim(claimed, unique_cfg, now)

    if phone_value and mgd is not None and brain_active and not allow_triggers:
        brain_message, brain_events, defer_triggers = _try_brain_simulation(
            mgd, phone_value, claimed, now
        )
        events.extend(brain_events)
        if brain_message:
            replies.append(
                {
                    "text": brain_message,
                    "trigger_id": BRAIN_SIMULATOR_ID,
                    "trigger_name": BRAIN_SIMULATOR_NAME,
                }
            )
            return {
                "replies": replies,
                "events": events,
                "claimed_keys": sorted(claimed),
            }
        if not defer_triggers:
            events.append(
                {
                    "trigger_id": BRAIN_SIMULATOR_ID,
                    "trigger_name": BRAIN_SIMULATOR_NAME,
                    "status": "skipped",
                    "reason": "1ª resposta do dia — triggers suprimidos (brain sem resposta)",
                }
            )
            return {
                "replies": replies,
                "events": events,
                "claimed_keys": sorted(claimed),
            }

    candidates, candidate_events = _evaluate_trigger_candidates(
        conversation, active_triggers, claimed, now
    )
    events.extend(candidate_events)

    if not candidates:
        return {
            "replies": replies,
            "events": events,
            "claimed_keys": sorted(claimed),
        }

    for trigger in candidates:
        trigger_id = str(trigger.get("id") or "")
        trigger_name = str(trigger.get("name") or trigger_id or "Trigger")
        unique_cfg = trigger.get("unique") or {}
        reply_messages = triggers_store.get_reply_messages(trigger)

        if unique_cfg.get("enabled"):
            scope_key = triggers_store.unique_scope_key(
                str(unique_cfg.get("scope") or "day"), now
            )
            claimed.add(_claim_key(trigger_id, scope_key))

        for reply_text in reply_messages:
            replies.append(
                {
                    "text": reply_text,
                    "trigger_id": trigger_id,
                    "trigger_name": trigger_name,
                }
            )

        events.append(
            {
                "trigger_id": trigger_id,
                "trigger_name": trigger_name,
                "status": "fired",
                "reason": f"{len(reply_messages)} mensagem(ns) enfileirada(s)",
            }
        )

    return {
        "replies": replies,
        "events": events,
        "claimed_keys": sorted(claimed),
    }
