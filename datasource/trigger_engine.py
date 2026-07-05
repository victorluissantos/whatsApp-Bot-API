"""Motor de triggers: diff de unread, match, schedule, unique e fila assíncrona."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Optional

from datasource import async_send_queue
from datasource import triggers as triggers_store
from datasource.app_timezone import now_local

logger = logging.getLogger(__name__)

_DEDUP: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 60.0
# Última lastMessage vista por chat (independente do cache unread)
_TRIGGER_SEEN: dict[str, str] = {}
_TRIGGER_BOOTSTRAPPED = False


def reset_baseline() -> None:
    """Chamar ao deslogar/reiniciar sessão WhatsApp."""
    global _TRIGGER_BOOTSTRAPPED
    _TRIGGER_SEEN.clear()
    _TRIGGER_BOOTSTRAPPED = False
    _DEDUP.clear()
    logger.info("Triggers: baseline reiniciado")


def process_unread_changes(mgd, old_chats: list[dict], new_chats: list[dict]) -> dict:
    """
    Avalia chats que mudaram na lista de não lidas e enfileira respostas dos triggers.
    Retorna estatísticas do ciclo.
    """
    global _TRIGGER_BOOTSTRAPPED
    stats = {"changed": 0, "matched": 0, "queued": 0, "skipped": 0, "errors": 0}

    if not _TRIGGER_BOOTSTRAPPED:
        for chat in new_chats:
            key = _chat_key(chat)
            msg = (chat.get("lastMessage") or "").strip()
            if key and msg:
                _TRIGGER_SEEN[key] = msg
        _TRIGGER_BOOTSTRAPPED = True
        logger.info(
            "Triggers: baseline inicial (%s chats no painel). "
            "Mensagens já presentes não disparam; a próxima mensagem nova sim.",
            len(_TRIGGER_SEEN),
        )
        return stats

    changed = diff_changed_chats(new_chats)
    stats["changed"] = len(changed)
    if not changed:
        _sync_seen_from_chats(new_chats)
        return stats

    active_triggers = triggers_store.list_triggers(mgd, enabled_only=True)
    if not active_triggers:
        logger.debug("Triggers: %s chat(s) mudaram, mas nenhum trigger ativo", len(changed))
        _sync_seen_from_chats(new_chats)
        return stats

    now = now_local()

    for chat in changed:
        message_text = (chat.get("lastMessage") or "").strip()
        if not message_text or message_text == "Sem mensagem":
            stats["skipped"] += 1
            continue

        phone = resolve_phone(chat)
        if not phone:
            logger.warning(
                "Triggers: sem telefone para chat %r; mensagem ignorada",
                chat.get("name"),
            )
            stats["skipped"] += 1
            continue

        dedup_key = f"{phone}|{message_text}"
        if _recently_processed(dedup_key):
            stats["skipped"] += 1
            continue

        fired_for_chat = False
        for trigger in active_triggers:
            if not triggers_store.is_within_schedule(trigger.get("schedule") or {}, now):
                logger.debug("Trigger %r fora do horário", trigger.get("name"))
                continue
            if not triggers_store.message_matches_trigger(message_text, trigger):
                logger.debug(
                    "Trigger %r não bate: pattern=%r msg=%r",
                    trigger.get("name"),
                    trigger.get("pattern"),
                    message_text[:80],
                )
                continue

            stats["matched"] += 1
            trigger_id = trigger["id"]
            contact_key = triggers_store.contact_key(phone, chat.get("name"))

            if not triggers_store.try_claim_execution(
                mgd,
                trigger_id,
                contact_key,
                trigger.get("unique") or {},
                now,
            ):
                logger.info(
                    "Trigger %r já executado (unique) para %s",
                    trigger.get("name"),
                    phone,
                )
                stats["skipped"] += 1
                continue

            try:
                job_id = async_send_queue.enqueue_job(
                    mgd,
                    phone,
                    trigger["reply_message"],
                    unic_sent=False,
                )
                logger.info(
                    "Trigger %r disparado para %s (job %s) msg=%r",
                    trigger.get("name"),
                    phone,
                    job_id,
                    message_text[:80],
                )
                stats["queued"] += 1
                fired_for_chat = True
                _mark_processed(dedup_key)
            except Exception:
                stats["errors"] += 1
                triggers_store.release_execution_claim(
                    mgd, trigger_id, contact_key, trigger.get("unique") or {}, now
                )
                logger.exception(
                    "Triggers: falha ao enfileirar resposta do trigger %s", trigger_id
                )

        if not fired_for_chat and changed:
            logger.info(
                "Triggers: chat %s msg=%r — nenhum trigger disparou (pattern/horário/unique)",
                phone,
                message_text[:80],
            )

    _sync_seen_from_chats(new_chats)
    return stats


def diff_changed_chats(new_chats: list[dict]) -> list[dict]:
    """Chats com lastMessage nova em relação ao baseline de triggers."""
    changed: list[dict] = []

    for chat in new_chats:
        key = _chat_key(chat)
        if not key:
            continue
        last_msg = (chat.get("lastMessage") or "").strip()
        if not last_msg or last_msg == "Sem mensagem":
            continue

        prev_msg = _TRIGGER_SEEN.get(key)
        if prev_msg is None or last_msg != prev_msg:
            changed.append(chat)

    return changed


def _sync_seen_from_chats(chats: list[dict]) -> None:
    for chat in chats:
        key = _chat_key(chat)
        msg = (chat.get("lastMessage") or "").strip()
        if key and msg and msg != "Sem mensagem":
            _TRIGGER_SEEN[key] = msg


def _chat_key(chat: dict) -> str:
    phone = resolve_phone(chat)
    if phone:
        return f"phone:{phone}"
    name = (chat.get("name") or "").strip()
    if name:
        return f"name:{name}"
    return ""


def resolve_phone(chat: dict) -> Optional[str]:
    """Extrai telefone numérico do chat (phone ou name)."""
    for raw in (chat.get("phone"), chat.get("name")):
        if not raw:
            continue
        text = str(raw).strip()
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 10:
            return digits
    return None


def _recently_processed(key: str) -> bool:
    now = time.time()
    expired = [k for k, ts in _DEDUP.items() if now - ts > _DEDUP_TTL_SECONDS]
    for k in expired:
        _DEDUP.pop(k, None)
    ts = _DEDUP.get(key)
    return ts is not None and now - ts <= _DEDUP_TTL_SECONDS


def _mark_processed(key: str) -> None:
    _DEDUP[key] = time.time()
