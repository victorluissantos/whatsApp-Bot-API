"""Motor de triggers: diff de unread, match, schedule, unique e fila assíncrona."""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from datasource import async_send_queue
from datasource import Messages
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


def process_unread_changes(mgd, old_chats: list[dict], new_chats: list[dict], nav=None) -> dict:
    """
    Avalia chats que mudaram na lista de não lidas e enfileira respostas dos triggers.
    Retorna estatísticas do ciclo.
    """
    global _TRIGGER_BOOTSTRAPPED
    stats = {"changed": 0, "matched": 0, "queued": 0, "skipped": 0, "errors": 0}

    if not _TRIGGER_BOOTSTRAPPED:
        # Não engole unread existentes no bootstrap: chats com unread>0 ainda
        # entram em diff_changed (prev_msg=None) e podem disparar. Só marca
        # como já visto os que estão no painel sem contador (já lidos/irrelevantes).
        for chat in new_chats:
            if _unread_int(chat) > 0:
                continue
            key = _chat_key(chat)
            msg = (chat.get("lastMessage") or "").strip()
            if key and msg:
                _TRIGGER_SEEN[key] = msg
        _TRIGGER_BOOTSTRAPPED = True
        logger.info(
            "Triggers: baseline inicial (%s chats marcados como já vistos; unread ainda serão avaliados).",
            len(_TRIGGER_SEEN),
        )
        # Continua o fluxo normalmente para avaliar unread existentes.

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
    messages_runner = Messages.Run() if nav is not None else None

    for chat in changed:
        message_text = (chat.get("lastMessage") or "").strip()
        if not message_text or message_text == "Sem mensagem":
            stats["skipped"] += 1
            continue
        if _unread_int(chat) <= 0:
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
        if messages_runner is not None and not _is_latest_message_incoming(
            messages_runner, nav, phone, message_text
        ):
            logger.info(
                "Triggers: chat %s msg=%r ignorado (última mensagem não recebida)",
                phone,
                message_text[:80],
            )
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
                    unRead=True,
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
    """Extrai telefone numérico do chat (phone ou name), com DDI 55 quando possível."""
    for raw in (chat.get("phone"), chat.get("name")):
        if not raw:
            continue
        text = str(raw).strip()
        digits = re.sub(r"\D", "", text)
        if len(digits) < 10:
            continue
        if not digits.startswith("55"):
            digits = "55" + digits
        return digits
    return None


def _unread_int(chat: dict) -> int:
    try:
        return int(str(chat.get("unreadCount") or "0") or 0)
    except ValueError:
        return 0


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _is_latest_message_incoming(messages_runner, nav, phone: str, preview_text: str) -> bool:
    """
    Confirma que a última mensagem relevante do chat é recebida.
    Usa getMessages para evitar disparo em mensagem enviada pelo próprio bot/usuário.
    Se não conseguir abrir o histórico, não bloqueia o trigger (fail-open).
    """
    try:
        result = messages_runner.getMessages(nav, phone)
    except Exception:
        logger.exception(
            "Triggers: falha ao abrir histórico para validar origem (%s); seguindo sem bloqueio",
            phone,
        )
        return True
    if not result or not result.get("success"):
        logger.warning(
            "Triggers: não foi possível validar origem em %s (%s); seguindo sem bloqueio",
            phone,
            (result or {}).get("error") if isinstance(result, dict) else "sem resultado",
        )
        return True
    messages = result.get("messages") or []
    if not messages:
        return True

    preview_norm = _normalize_text(preview_text)
    if not preview_norm:
        return True

    # Varre de trás para frente e tenta casar com o preview do painel.
    for msg in reversed(messages):
        text_norm = _normalize_text(str(msg.get("message") or ""))
        if not text_norm:
            continue
        if preview_norm in text_norm or text_norm in preview_norm:
            return str(msg.get("origem") or "").strip().lower() == "recebida"

    # Fallback: sem match textual claro, exige que a última mensagem do chat seja recebida.
    last_origin = str(messages[-1].get("origem") or "").strip().lower()
    return last_origin == "recebida"


def _recently_processed(key: str) -> bool:
    now = time.time()
    expired = [k for k, ts in _DEDUP.items() if now - ts > _DEDUP_TTL_SECONDS]
    for k in expired:
        _DEDUP.pop(k, None)
    ts = _DEDUP.get(key)
    return ts is not None and now - ts <= _DEDUP_TTL_SECONDS


def _mark_processed(key: str) -> None:
    _DEDUP[key] = time.time()
