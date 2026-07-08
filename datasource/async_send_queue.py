"""Fila de envio assíncrono via RabbitMQ e status/webhook no MongoDB."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, time as dt_time
from typing import Any, Optional

import pika
import pymongo
import requests

QUEUE_COLLECTION = "wa_message_queue"
WEBHOOK_CONFIG_COLLECTION = "wa_delivery_webhook"
WEBHOOK_DOC_ID = "config"

logger = logging.getLogger(__name__)
_RABBIT_CONNECTION: Optional[pika.BlockingConnection] = None
_RABBIT_CHANNEL: Optional[pika.adapters.blocking_connection.BlockingChannel] = None


def _queue(mgd) -> Any:
    return mgd.db[QUEUE_COLLECTION]


def _webhook_coll(mgd) -> Any:
    return mgd.db[WEBHOOK_CONFIG_COLLECTION]


def _rabbit_config() -> dict:
    return {
        "host": os.environ.get("RABBITMQ_HOST", "rabbitmq"),
        "port": int(os.environ.get("RABBITMQ_PORT", "5672")),
        "user": os.environ.get("RABBITMQ_USER", "admin"),
        "password": os.environ.get("RABBITMQ_PASS", "admin123"),
        "vhost": os.environ.get("RABBITMQ_VHOST", "/"),
        "queue": os.environ.get("RABBITMQ_SEND_QUEUE", "wa_send_message_async"),
    }


def _ensure_rabbit_channel():
    global _RABBIT_CONNECTION, _RABBIT_CHANNEL
    cfg = _rabbit_config()
    if _RABBIT_CONNECTION and _RABBIT_CONNECTION.is_open and _RABBIT_CHANNEL and _RABBIT_CHANNEL.is_open:
        return _RABBIT_CHANNEL, cfg["queue"]

    credentials = pika.PlainCredentials(cfg["user"], cfg["password"])
    params = pika.ConnectionParameters(
        host=cfg["host"],
        port=cfg["port"],
        virtual_host=cfg["vhost"],
        credentials=credentials,
        heartbeat=30,
        blocked_connection_timeout=30,
    )
    _RABBIT_CONNECTION = pika.BlockingConnection(params)
    _RABBIT_CHANNEL = _RABBIT_CONNECTION.channel()
    _RABBIT_CHANNEL.queue_declare(queue=cfg["queue"], durable=True)
    return _RABBIT_CHANNEL, cfg["queue"]


def _publish_to_rabbit(payload: dict) -> None:
    channel, queue_name = _ensure_rabbit_channel()
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2),
    )


ACTIVE_QUEUE_STATUSES = ("sent",)


def _normalize_legacy_phone(phone: str) -> str:
    digits = "".join(filter(str.isdigit, str(phone or "")))
    if not digits.startswith("55"):
        digits = "55" + digits
    return "+" + digits


def enqueue_job(
    mgd,
    phone: str,
    message: str,
    unic_sent: bool,
    unRead: bool = False,
    trigger_id: Optional[str] = None,
    contact_key: Optional[str] = None,
    scope_key: Optional[str] = None,
) -> str:
    job_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "job_id": job_id,
        "phone": phone,
        "message": message,
        "unic_sent": bool(unic_sent),
        "unRead": bool(unRead),
        "status": "pending",
        "created_at": datetime.utcnow(),
    }
    if trigger_id:
        doc["trigger_id"] = str(trigger_id)
    if contact_key:
        doc["contact_key"] = str(contact_key)
    if scope_key:
        doc["scope_key"] = str(scope_key)
    _queue(mgd).insert_one(doc)
    try:
        _publish_to_rabbit(
            {
                "job_id": job_id,
                "phone": phone,
                "message": message,
                "unic_sent": bool(unic_sent),
                "unRead": bool(unRead),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
        )
    except Exception as e:
        _queue(mgd).update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "result": f"Falha ao publicar no RabbitMQ: {e}", "processed_at": datetime.utcnow()}},
        )
        raise
    return job_id


def create_inline_job(
    mgd,
    phone: str,
    message: str,
    unRead: bool = True,
    trigger_id: Optional[str] = None,
    contact_key: Optional[str] = None,
    scope_key: Optional[str] = None,
) -> str:
    """
    Registra job na fila já em processing (sem RabbitMQ).
    Usado pelo trigger unificado: valida + envia na mesma abertura do chat.
    """
    job_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "job_id": job_id,
        "phone": phone,
        "message": message,
        "unic_sent": False,
        "unRead": bool(unRead),
        "status": "processing",
        "created_at": datetime.utcnow(),
        "started_at": datetime.utcnow(),
    }
    if trigger_id:
        doc["trigger_id"] = str(trigger_id)
    if contact_key:
        doc["contact_key"] = str(contact_key)
    if scope_key:
        doc["scope_key"] = str(scope_key)
    _queue(mgd).insert_one(doc)
    return job_id


def get_next_rabbit_job() -> tuple[Optional[dict], Optional[int]]:
    channel, _queue_name = _ensure_rabbit_channel()
    method, _properties, body = channel.basic_get(queue=_queue_name, auto_ack=False)
    if method is None:
        return None, None
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        # Mensagem inválida não deve ficar presa na fila.
        channel.basic_ack(delivery_tag=method.delivery_tag)
        logger.warning("Mensagem inválida descartada da fila RabbitMQ")
        return None, None
    return data, method.delivery_tag


def ack_rabbit_job(delivery_tag: int) -> None:
    channel, _queue_name = _ensure_rabbit_channel()
    channel.basic_ack(delivery_tag=delivery_tag)


def nack_rabbit_job(delivery_tag: int, requeue: bool = True) -> None:
    channel, _queue_name = _ensure_rabbit_channel()
    channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)


def get_job_status(mgd, job_id: str) -> Optional[str]:
    doc = _queue(mgd).find_one({"job_id": job_id}, {"status": 1})
    if not doc:
        return None
    return str(doc.get("status", ""))


def mark_job_processing(mgd, job_id: str) -> bool:
    """Só passa de pending → processing; retorna False se já cancelado ou finalizado."""
    result = _queue(mgd).update_one(
        {"job_id": job_id, "status": "pending"},
        {"$set": {"status": "processing", "started_at": datetime.utcnow()}},
    )
    return result.modified_count > 0


def cancel_job(mgd, job_id: str) -> tuple[bool, str]:
    """Cancela job ainda pendente (não será enviado quando o worker consumir a fila)."""
    doc = _queue(mgd).find_one_and_update(
        {"job_id": job_id, "status": "pending"},
        {
            "$set": {
                "status": "cancelled",
                "result": "Cancelado pelo usuário",
                "processed_at": datetime.utcnow(),
            }
        },
    )
    if doc:
        return True, "Job cancelado com sucesso"
    existing = _queue(mgd).find_one({"job_id": job_id})
    if not existing:
        return False, "Job não encontrado"
    current = existing.get("status", "")
    if current == "cancelled":
        return True, "Job já estava cancelado"
    return False, f"Não é possível cancelar job com status '{current}'"


def _soft_delete_legacy_history(mgd, phone: str, message: str) -> None:
    """Marca histórico legado correspondente como deleted (unic_sent deixa de ver como Enviado)."""
    legacy_phone = _normalize_legacy_phone(phone)
    now = datetime.utcnow()
    try:
        mgd.collection.update_many(
            {"telefone": legacy_phone, "mensagem": message, "status": "Enviado"},
            {"$set": {"status": "deleted", "deleted_at": now}},
        )
    except Exception as e:
        logger.warning("Falha ao soft-delete no histórico legado: %s", e)


def _release_trigger_claim_from_job(mgd, doc: dict) -> None:
    trigger_id = (doc.get("trigger_id") or "").strip()
    contact_key = (doc.get("contact_key") or "").strip()
    scope_key = (doc.get("scope_key") or "").strip()
    phone = str(doc.get("phone") or "")
    try:
        from datasource import triggers as triggers_store
        from datasource import trigger_engine

        if trigger_id:
            # Só este trigger — não zera unique dos outros (Ajuda, Encerramento, etc.).
            if contact_key and scope_key:
                triggers_store.release_execution_claim_by_keys(
                    mgd, trigger_id, contact_key, scope_key
                )
            released = triggers_store.release_execution_claims_for_trigger_contact(
                mgd, trigger_id, phone
            )
            if released:
                logger.info(
                    "Unique liberado do trigger %s após delete (%s claim(s), phone=%s)",
                    trigger_id,
                    released,
                    phone,
                )
        else:
            # Job antigo sem metadados: fallback amplo por telefone.
            released = triggers_store.release_execution_claims_for_phone(mgd, phone)
            if released:
                logger.info(
                    "Unique liberado por telefone após delete (%s claim(s), phone=%s)",
                    released,
                    phone,
                )
        # Esquece baseline + force recalc no próximo poll (mesmo se unread não mudar).
        trigger_engine.forget_chat(phone)
    except Exception as e:
        logger.warning("Falha ao liberar unique do trigger após delete: %s", e)


def delete_job(mgd, job_id: str) -> tuple[bool, str]:
    """Soft-delete: status deleted. Registro permanece no histórico; dedup/worker/triggers ignoram."""
    existing = _queue(mgd).find_one({"job_id": job_id})
    if not existing:
        return False, "Job não encontrado"
    current = str(existing.get("status", ""))
    if current == "deleted":
        # Idempotente: re-libera unique/baseline (útil se delete anterior foi parcial).
        _soft_delete_legacy_history(
            mgd, str(existing.get("phone") or ""), str(existing.get("message") or "")
        )
        _release_trigger_claim_from_job(mgd, existing)
        return True, "Job já estava excluído (unique/baseline reavaliados)"
    doc = _queue(mgd).find_one_and_update(
        {"job_id": job_id, "status": {"$ne": "deleted"}},
        {
            "$set": {
                "status": "deleted",
                "result": "Excluído pelo usuário",
                "deleted_at": datetime.utcnow(),
                "processed_at": datetime.utcnow(),
            }
        },
    )
    if not doc:
        return False, "Não foi possível excluir o job"
    _soft_delete_legacy_history(mgd, str(doc.get("phone") or ""), str(doc.get("message") or ""))
    _release_trigger_claim_from_job(mgd, doc)
    return True, "Job excluído com sucesso"


def has_active_queue_message(mgd, phone: str, message: str) -> bool:
    """True se existe job sent (não deleted) com mesmo phone+message."""
    digits = "".join(filter(str.isdigit, str(phone or "")))
    if not digits:
        return False
    return (
        _queue(mgd).find_one(
            {
                "phone": {"$regex": re.escape(digits)},
                "message": message,
                "status": {"$in": list(ACTIVE_QUEUE_STATUSES)},
            },
            {"_id": 1},
        )
        is not None
    )


# Status de jobs que contam como “enviados pelo sistema” (inclui soft-delete:
# a bolha ainda está no WhatsApp, mas foi o bot quem enviou).
SYSTEM_OUTBOUND_QUEUE_STATUSES = ("sent", "deleted", "processing")


def _normalize_message_for_match(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip()).lower()
    # Remove formatação WhatsApp comum (*, _, ~) para casar preview vs fila.
    s = re.sub(r"[*_~]", "", s)
    return s


def is_system_outbound_message(mgd, phone: str, message: str) -> bool:
    """
    True se a mensagem enviada consta como saída do sistema (fila ou histórico legado).
    Usado para detectar atendimento humano na conversa.
    """
    digits = "".join(filter(str.isdigit, str(phone or "")))
    if not digits or not (message or "").strip():
        return False
    target = _normalize_message_for_match(message)

    for doc in _queue(mgd).find(
        {
            "phone": {"$regex": re.escape(digits)},
            "status": {"$in": list(SYSTEM_OUTBOUND_QUEUE_STATUSES)},
        },
        {"message": 1},
    ):
        if _normalize_message_for_match(str(doc.get("message") or "")) == target:
            return True

    try:
        legacy_phone = _normalize_legacy_phone(phone)
        for doc in mgd.collection.find(
            {
                "telefone": legacy_phone,
                "status": {"$in": ["Enviado", "deleted"]},
            },
            {"mensagem": 1},
        ):
            if _normalize_message_for_match(str(doc.get("mensagem") or "")) == target:
                return True
    except Exception as e:
        logger.debug("Checagem legado em is_system_outbound_message: %s", e)

    return False


def finalize_job(mgd, job_id: str, success: bool, result: str) -> None:
    """Finaliza job; não sobrescreve se já estiver deleted."""
    _queue(mgd).update_one(
        {"job_id": job_id, "status": {"$ne": "deleted"}},
        {
            "$set": {
                "status": "sent" if success else "failed",
                "result": result,
                "processed_at": datetime.utcnow(),
            }
        },
    )


def get_delivery_webhook_url(mgd) -> Optional[str]:
    doc = _webhook_coll(mgd).find_one({"_id": WEBHOOK_DOC_ID})
    if not doc:
        return None
    url = doc.get("url")
    if not url or not str(url).strip():
        return None
    return str(url).strip()


def set_delivery_webhook_url(mgd, url: str) -> None:
    _webhook_coll(mgd).update_one(
        {"_id": WEBHOOK_DOC_ID},
        {"$set": {"url": url.strip(), "updated_at": datetime.utcnow()}},
        upsert=True,
    )


def clear_delivery_webhook(mgd) -> None:
    _webhook_coll(mgd).delete_one({"_id": WEBHOOK_DOC_ID})


def notify_delivery_webhook(url: str, payload: dict) -> None:
    """POST JSON na URL única configurada (fila assíncrona, lista não lidas, etc.)."""
    try:
        r = requests.post(url, json=payload, timeout=15, headers={"Content-Type": "application/json"})
        r.raise_for_status()
    except Exception as e:
        logger.warning("Webhook falhou: %s", e)


def ensure_queue_indexes(mgd) -> None:
    try:
        _queue(mgd).create_index([("status", pymongo.ASCENDING), ("created_at", pymongo.ASCENDING)])
        _queue(mgd).create_index([("created_at", pymongo.DESCENDING)])
        _queue(mgd).create_index("job_id", unique=True)
        _queue(mgd).create_index("phone")
    except Exception as e:
        logger.debug("Índices da fila (pode ser idempotente): %s", e)


def ensure_rabbit_topology() -> None:
    """Garante conexão e declaração da fila no startup."""
    try:
        _ensure_rabbit_channel()
    except Exception as e:
        logger.warning("RabbitMQ indisponível no startup: %s", e)


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.isoformat()
    return dt.isoformat() + "Z"


def _parse_filter_datetime(value: str, end_of_day: bool = False) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            year, month, day = (int(part) for part in raw.split("-"))
            if end_of_day:
                return datetime.combine(year, month, day, dt_time(23, 59, 59, 999999))
            return datetime.combine(year, month, day, dt_time.min)
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def build_queue_filter(
    phone: Optional[str] = None,
    message: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    query: dict[str, Any] = {}
    phone_q = (phone or "").strip()
    if phone_q:
        query["phone"] = {"$regex": re.escape(phone_q)}
    message_q = (message or "").strip()
    if message_q:
        query["message"] = {"$regex": re.escape(message_q), "$options": "i"}
    status_q = (status or "").strip()
    if status_q:
        query["status"] = status_q
    created_range: dict[str, datetime] = {}
    dt_from = _parse_filter_datetime(date_from or "", end_of_day=False)
    dt_to = _parse_filter_datetime(date_to or "", end_of_day=True)
    if dt_from:
        created_range["$gte"] = dt_from
    if dt_to:
        created_range["$lte"] = dt_to
    if created_range:
        query["created_at"] = created_range
    return query


def list_queue_jobs_desc(
    mgd,
    page: int,
    page_size: int,
    phone: Optional[str] = None,
    message: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> tuple[list[dict], int]:
    """Lista jobs da fila, mais recentes primeiro (created_at DESC), com paginação e filtros."""
    coll = _queue(mgd)
    query = build_queue_filter(
        phone=phone,
        message=message,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    total = coll.count_documents(query)
    page = max(1, page)
    page_size = max(1, page_size)
    skip = (page - 1) * page_size
    cursor = (
        coll.find(query)
        .sort("created_at", pymongo.DESCENDING)
        .skip(skip)
        .limit(page_size)
    )
    items: list[dict] = []
    for doc in cursor:
        doc.pop("_id", None)
        doc["created_at"] = _iso_utc(doc.get("created_at"))
        doc["started_at"] = _iso_utc(doc.get("started_at"))
        doc["processed_at"] = _iso_utc(doc.get("processed_at"))
        doc["deleted_at"] = _iso_utc(doc.get("deleted_at"))
        items.append(doc)
    return items, total
