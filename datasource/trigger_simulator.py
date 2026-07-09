"""Simulação de triggers em memória (sem persistência)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from datasource import triggers as triggers_store
from datasource.app_timezone import now_local


SIMULATOR_CONTACT_KEY = "simulator"


def _claim_key(trigger_id: str, scope_key: str) -> str:
    return f"{trigger_id}|{scope_key}"


def _has_claim(claimed_keys: set[str], trigger_id: str, unique: dict, now: datetime) -> bool:
    if not unique.get("enabled"):
        return False
    scope_key = triggers_store.unique_scope_key(str(unique.get("scope") or "day"), now)
    return _claim_key(trigger_id, scope_key) in claimed_keys


def evaluate_message(
    message: str,
    active_triggers: list[dict],
    claimed_keys: set[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Avalia uma mensagem contra triggers ativos sem gravar no MongoDB.
    Retorna respostas do bot, eventos de debug e chaves de unique simuladas.
    """
    text = (message or "").strip()
    if not text:
        return {"replies": [], "events": [], "claimed_keys": sorted(claimed_keys)}

    now = now or now_local()
    claimed = set(claimed_keys)
    replies: list[dict[str, str]] = []
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

        if not triggers_store.message_matches_trigger(text, trigger):
            events.append(
                {
                    "trigger_id": trigger_id,
                    "trigger_name": trigger_name,
                    "status": "skipped",
                    "reason": "padrão não corresponde",
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
