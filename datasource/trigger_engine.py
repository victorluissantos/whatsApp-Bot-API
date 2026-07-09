"""Motor de triggers: diff de unread, match, schedule, unique e fila assíncrona."""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from datasource import async_send_queue
from datasource import Messages
from datasource import AutoBoot
from datasource import triggers as triggers_store
from datasource.app_timezone import now_local
from datasource.phone_utils import phone_digit_variants

logger = logging.getLogger(__name__)

_DEDUP: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 60.0
# Última lastMessage vista por chat (independente do cache unread)
_TRIGGER_SEEN: dict[str, str] = {}
_TRIGGER_BOOTSTRAPPED = False
# Soft-delete (ou liberação de unique) pede reavaliação no próximo poll
# mesmo se a lista de unread não mudou no DOM.
_FORCE_RECALC = False


def reset_baseline() -> None:
    """Chamar ao deslogar/reiniciar sessão WhatsApp."""
    global _TRIGGER_BOOTSTRAPPED, _FORCE_RECALC
    _TRIGGER_SEEN.clear()
    _TRIGGER_BOOTSTRAPPED = False
    _DEDUP.clear()
    _FORCE_RECALC = False
    logger.info("Triggers: baseline reiniciado")


def request_force_recalc() -> None:
    """Próximo ciclo do poller deve reavaliar triggers mesmo sem mudança no pane."""
    global _FORCE_RECALC
    _FORCE_RECALC = True
    logger.info("Triggers: force recalc solicitado")


def consume_force_recalc() -> bool:
    """Retorna e limpa o flag de reavaliação forçada."""
    global _FORCE_RECALC
    if not _FORCE_RECALC:
        return False
    _FORCE_RECALC = False
    return True


def forget_chat(phone: Optional[str] = None, name: Optional[str] = None) -> None:
    """
    Remove chat do baseline/dedup para que a próxima última mensagem volte a ser avaliada.
    Usado após soft-delete na fila (reenvio / re-disparo de trigger).
    """
    variants = phone_digit_variants(str(phone or ""))
    for d in variants:
        _TRIGGER_SEEN.pop(f"phone:{d}", None)
    name_s = (name or "").strip()
    if name_s:
        _TRIGGER_SEEN.pop(f"name:{name_s}", None)
    for dedup_key in list(_DEDUP.keys()):
        phone_part = dedup_key.split("|", 1)[0]
        if phone_part in variants:
            _DEDUP.pop(dedup_key, None)
    request_force_recalc()
    logger.info("Triggers: baseline esquecido para phone=%s name=%s", phone, name)


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

    # Avalia unread atuais + chats com lastMessage nova. Unread parado após
    # soft-delete também precisa ser reavaliado (não só "mensagem mudou").
    to_process = chats_to_evaluate(new_chats)
    stats["changed"] = len(to_process)
    if not to_process:
        _sync_seen_from_chats(new_chats)
        return stats

    active_triggers = triggers_store.list_triggers(mgd, enabled_only=True)
    if not active_triggers:
        logger.debug("Triggers: %s chat(s) a avaliar, mas nenhum trigger ativo", len(to_process))
        _sync_seen_from_chats(new_chats)
        return stats

    now = now_local()
    messages_runner = Messages.Run() if nav is not None else None
    # Chats que devem continuar elegíveis no próximo ciclo (unique bloqueado /
    # falha). Removidos DEPOIS de _sync_seen_from_chats.
    keep_unseen: set[str] = set()

    for chat in to_process:
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

        chat_key = _chat_key(chat)
        dedup_key = f"{phone}|{message_text}"
        if _recently_processed(dedup_key):
            stats["skipped"] += 1
            continue

        contact_key = triggers_store.contact_key(phone, chat.get("name"))
        # Descobre matches (schedule+pattern) e se unique já bloqueia — ANTES de abrir o chat.
        candidates: list[dict] = []
        unique_blocked_only = False
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
            unique_cfg = trigger.get("unique") or {}
            if unique_cfg.get("enabled") and triggers_store.has_execution_claim(
                mgd,
                trigger["id"],
                contact_key,
                unique_cfg,
                now,
            ):
                # Unique só libera no soft-delete explícito do job — NÃO reabrir
                # por existência de job deleted (senão reenvia o dia inteiro).
                logger.info(
                    "Trigger %r já executado (unique) para %s",
                    trigger.get("name"),
                    phone,
                )
                unique_blocked_only = True
                stats["skipped"] += 1
                continue
            candidates.append(trigger)

        if not candidates:
            if unique_blocked_only and chat_key:
                # Mantém elegível: soft-delete pode liberar o unique e o mesmo unread reaparece.
                keep_unseen.add(chat_key)
            else:
                logger.info(
                    "Triggers: chat %s msg=%r — nenhum trigger disparou (pattern/horário/unique)",
                    phone,
                    message_text[:80],
                )
            continue

        if messages_runner is None or nav is None:
            # Sem browser: só enfileira no Rabbit (API/async legado).
            fired_for_chat = _enqueue_candidates(
                mgd,
                phone,
                contact_key,
                candidates,
                message_text,
                now,
                stats,
                dedup_key,
                chat_key,
                keep_unseen,
            )
            if not fired_for_chat:
                logger.info(
                    "Triggers: chat %s msg=%r — nenhum trigger disparou (pattern/horário/unique)",
                    phone,
                    message_text[:80],
                )
                if chat_key:
                    keep_unseen.add(chat_key)
            continue

        # Uma abertura: validar + enviar no mesmo chat (sem RabbitMQ reabrir).
        fired_for_chat = _validate_and_send_inline(
            messages_runner,
            nav,
            mgd,
            phone,
            message_text,
            contact_key,
            candidates,
            now,
            stats,
            dedup_key,
            chat_key,
            keep_unseen,
        )
        if not fired_for_chat:
            if chat_key:
                keep_unseen.add(chat_key)

    _sync_seen_from_chats(new_chats)
    for key in keep_unseen:
        _TRIGGER_SEEN.pop(key, None)
    return stats


def _enqueue_candidates(
    mgd,
    phone: str,
    contact_key: str,
    candidates: list[dict],
    message_text: str,
    now,
    stats: dict,
    dedup_key: str,
    chat_key: str,
    keep_unseen: set[str],
) -> bool:
    fired_for_chat = False
    for trigger in candidates:
        stats["matched"] += 1
        trigger_id = trigger["id"]
        unique_cfg = trigger.get("unique") or {}

        if not triggers_store.try_claim_execution(
            mgd,
            trigger_id,
            contact_key,
            unique_cfg,
            now,
        ):
            logger.info(
                "Trigger %r já executado (unique) para %s",
                trigger.get("name"),
                phone,
            )
            stats["skipped"] += 1
            if chat_key:
                keep_unseen.add(chat_key)
            continue

        enqueue_kwargs: dict = {}
        if unique_cfg.get("enabled"):
            enqueue_kwargs["trigger_id"] = trigger_id
            enqueue_kwargs["contact_key"] = contact_key
            enqueue_kwargs["scope_key"] = triggers_store.unique_scope_key(
                str(unique_cfg.get("scope") or "day"), now
            )

        try:
            reply_messages = triggers_store.get_reply_messages(trigger)
            for reply_text in reply_messages:
                job_id = async_send_queue.enqueue_job(
                    mgd,
                    phone,
                    reply_text,
                    unic_sent=False,
                    unRead=True,
                    **enqueue_kwargs,
                )
                logger.info(
                    "Trigger %r disparado para %s (job %s) resposta=%r",
                    trigger.get("name"),
                    phone,
                    job_id,
                    reply_text[:80],
                )
                stats["queued"] += 1
            fired_for_chat = True
            _mark_processed(dedup_key)
        except Exception:
            stats["errors"] += 1
            triggers_store.release_execution_claim(
                mgd, trigger_id, contact_key, unique_cfg, now
            )
            logger.exception(
                "Triggers: falha ao enfileirar resposta do trigger %s", trigger_id
            )
    return fired_for_chat


def _validate_and_send_inline(
    messages_runner,
    nav,
    mgd,
    phone: str,
    message_text: str,
    contact_key: str,
    candidates: list[dict],
    now,
    stats: dict,
    dedup_key: str,
    chat_key: str,
    keep_unseen: set[str],
) -> bool:
    """
    Abre o chat uma vez, valida (recebida + sem humano) e envia as respostas
    no mesmo chat aberto. Registra na fila como job inline (sem RabbitMQ).
    """
    chat_opened = False
    sent_any = False
    try:
        result = messages_runner.getMessages(nav, phone, limit=50, leave_open=True)
        chat_opened = True
    except Exception:
        logger.exception(
            "Triggers: falha ao abrir histórico para validar/enviar (%s); sem envio",
            phone,
        )
        stats["errors"] += 1
        return False

    if not result or not result.get("success"):
        logger.warning(
            "Triggers: não validou/enviou em %s (%s)",
            phone,
            (result or {}).get("error") if isinstance(result, dict) else "sem resultado",
        )
        stats["skipped"] += 1
        if chat_opened:
            messages_runner._leave_conversation(nav, restore_unread=True)
        return False

    messages = result.get("messages") or []
    if messages and not _preview_is_incoming(messages, message_text):
        logger.info(
            "Triggers: chat %s msg=%r ignorado (última mensagem não recebida)",
            phone,
            message_text[:80],
        )
        stats["skipped"] += 1
        messages_runner._leave_conversation(nav, restore_unread=True)
        return False

    if messages and _chat_has_human_outbound(mgd, phone, messages):
        logger.info(
            "Triggers: chat %s msg=%r ignorado (conversa com atendimento humano)",
            phone,
            message_text[:80],
        )
        stats["skipped"] += 1
        messages_runner._leave_conversation(nav, restore_unread=True)
        return False

    bot = AutoBoot.WhatsAppBot(nav, mgd)
    remaining = list(candidates)
    while remaining:
        trigger = remaining.pop(0)
        stats["matched"] += 1
        trigger_id = trigger["id"]
        unique_cfg = trigger.get("unique") or {}

        if not triggers_store.try_claim_execution(
            mgd,
            trigger_id,
            contact_key,
            unique_cfg,
            now,
        ):
            logger.info(
                "Trigger %r já executado (unique) para %s",
                trigger.get("name"),
                phone,
            )
            stats["skipped"] += 1
            if chat_key:
                keep_unseen.add(chat_key)
            continue

        enqueue_kwargs: dict = {}
        if unique_cfg.get("enabled"):
            enqueue_kwargs["trigger_id"] = trigger_id
            enqueue_kwargs["contact_key"] = contact_key
            enqueue_kwargs["scope_key"] = triggers_store.unique_scope_key(
                str(unique_cfg.get("scope") or "day"), now
            )

        job_id = None
        try:
            reply_messages = triggers_store.get_reply_messages(trigger)
            for msg_index, reply_text in enumerate(reply_messages):
                job_id = async_send_queue.create_inline_job(
                    mgd,
                    phone,
                    reply_text,
                    unRead=True,
                    **enqueue_kwargs,
                )
                is_last_trigger = len(remaining) == 0
                is_last_message = msg_index == len(reply_messages) - 1
                is_last = is_last_trigger and is_last_message
                send_result = bot.syncSendText(
                    phone,
                    reply_text,
                    unic_sent=False,
                    unRead=True,
                    skip_open=True,
                    return_home=is_last,
                )
                ok = send_result == "Enviado"
                async_send_queue.finalize_job(mgd, job_id, ok, send_result)
                if ok:
                    logger.info(
                        "Trigger %r enviado inline para %s (job %s) resposta=%r",
                        trigger.get("name"),
                        phone,
                        job_id,
                        reply_text[:80],
                    )
                    stats["queued"] += 1
                    sent_any = True
                    chat_opened = not is_last
                else:
                    triggers_store.release_execution_claim(
                        mgd, trigger_id, contact_key, unique_cfg, now
                    )
                    stats["errors"] += 1
                    logger.warning(
                        "Trigger %r falhou no envio inline (%s): %s",
                        trigger.get("name"),
                        phone,
                        send_result,
                    )
                    break
            else:
                _mark_processed(dedup_key)
                continue
            break
        except Exception:
            stats["errors"] += 1
            triggers_store.release_execution_claim(
                mgd, trigger_id, contact_key, unique_cfg, now
            )
            if job_id:
                async_send_queue.finalize_job(mgd, job_id, False, "erro no envio inline")
            logger.exception(
                "Triggers: falha no envio inline do trigger %s", trigger_id
            )
            break

    if chat_opened:
        # Validou mas não enviou nada (todos unique), ou quebrou no meio.
        messages_runner._leave_conversation(nav, restore_unread=not sent_any)
    return sent_any


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


def chats_to_evaluate(new_chats: list[dict]) -> list[dict]:
    """
    Unread atuais + chats com mensagem nova.
    Assim, após soft-delete liberar unique, o mesmo "Bom dia" unread re-dispara
    sem precisar de outra mensagem no pane.
    """
    by_key: dict[str, dict] = {}
    for chat in diff_changed_chats(new_chats):
        key = _chat_key(chat)
        if key:
            by_key[key] = chat
    for chat in new_chats:
        if _unread_int(chat) <= 0:
            continue
        last_msg = (chat.get("lastMessage") or "").strip()
        if not last_msg or last_msg == "Sem mensagem":
            continue
        key = _chat_key(chat)
        if key:
            by_key[key] = chat
    return list(by_key.values())


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


def _preview_is_incoming(messages: list, preview_text: str) -> bool:
    preview_norm = _normalize_text(preview_text)
    if not preview_norm:
        return True

    for msg in reversed(messages):
        text_norm = _normalize_text(str(msg.get("message") or ""))
        if not text_norm:
            continue
        if preview_norm in text_norm or text_norm in preview_norm:
            return str(msg.get("origem") or "").strip().lower() == "recebida"

    last_origin = str(messages[-1].get("origem") or "").strip().lower()
    return last_origin == "recebida"


def _chat_has_human_outbound(mgd, phone: str, messages: list) -> bool:
    """
    True se existe mensagem 'enviada' no WhatsApp que não está na fila/histórico
    do sistema — indica que um humano respondeu no celular ou Web.
    """
    for msg in messages:
        if str(msg.get("origem") or "").strip().lower() != "enviada":
            continue
        text = str(msg.get("message") or "").strip()
        if not text or text in ("Mensagem não legível", "[Áudio]", "[Mídia]"):
            # Sem texto confiável: trata como possível humano e bloqueia.
            return True
        if not async_send_queue.is_system_outbound_message(mgd, phone, text):
            return True
    return False


def _recently_processed(key: str) -> bool:
    now = time.time()
    expired = [k for k, ts in _DEDUP.items() if now - ts > _DEDUP_TTL_SECONDS]
    for k in expired:
        _DEDUP.pop(k, None)
    ts = _DEDUP.get(key)
    return ts is not None and now - ts <= _DEDUP_TTL_SECONDS


def _mark_processed(key: str) -> None:
    _DEDUP[key] = time.time()
