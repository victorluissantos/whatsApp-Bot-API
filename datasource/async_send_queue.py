"""Fila de envio assíncrono e URL única de webhook no MongoDB (todos os POSTs de integração)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

import pymongo
import requests

QUEUE_COLLECTION = "wa_message_queue"
WEBHOOK_CONFIG_COLLECTION = "wa_delivery_webhook"
WEBHOOK_DOC_ID = "config"

logger = logging.getLogger(__name__)


def _queue(mgd) -> Any:
    return mgd.db[QUEUE_COLLECTION]


def _webhook_coll(mgd) -> Any:
    return mgd.db[WEBHOOK_CONFIG_COLLECTION]


def enqueue_job(mgd, phone: str, message: str, unic_sent: bool) -> str:
    job_id = str(uuid.uuid4())
    _queue(mgd).insert_one(
        {
            "job_id": job_id,
            "phone": phone,
            "message": message,
            "unic_sent": bool(unic_sent),
            "status": "pending",
            "created_at": datetime.utcnow(),
        }
    )
    return job_id


def claim_next_pending_job(mgd) -> Optional[dict]:
    doc = _queue(mgd).find_one_and_update(
        {"status": "pending"},
        {
            "$set": {
                "status": "processing",
                "started_at": datetime.utcnow(),
            }
        },
        sort=[("created_at", pymongo.ASCENDING)],
        return_document=pymongo.ReturnDocument.AFTER,
    )
    return doc


def finalize_job(mgd, job_id: str, success: bool, result: str) -> None:
    _queue(mgd).update_one(
        {"job_id": job_id},
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
    except Exception as e:
        logger.debug("Índices da fila (pode ser idempotente): %s", e)


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.isoformat()
    return dt.isoformat() + "Z"


def list_queue_jobs_desc(mgd, page: int, page_size: int) -> tuple[list[dict], int]:
    """Lista jobs da fila, mais recentes primeiro (created_at DESC), com paginação."""
    coll = _queue(mgd)
    total = coll.count_documents({})
    page = max(1, page)
    page_size = max(1, page_size)
    skip = (page - 1) * page_size
    cursor = (
        coll.find()
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
        items.append(doc)
    return items, total
