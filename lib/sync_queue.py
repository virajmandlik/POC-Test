"""
Sync Queue — MongoDB-backed queue for offline-to-online processing.

Items are enqueued when field engineers process documents offline.
The connectivity monitor pulls pending items and enriches them
when internet (VPN) becomes available.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from lib.db import get_db

log = logging.getLogger("f4f.sync_queue")

COLLECTION = "sync_queue"


def _col():
    return get_db()[COLLECTION]


def enqueue(job_type: str, file_path: str, offline_result: dict | None = None,
            user: str = "system") -> str:
    doc = {
        "job_type": job_type,
        "file_path": file_path,
        "offline_result": offline_result or {},
        "user": user,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "synced_at": None,
        "combined_result": None,
        "error": None,
    }
    result = _col().insert_one(doc)
    log.info("Enqueued %s item %s for %s", job_type, result.inserted_id, file_path)
    return str(result.inserted_id)


def get_pending(limit: int = 20) -> list[dict]:
    items = list(_col().find({"status": "pending"}).sort("created_at", 1).limit(limit))
    for item in items:
        item["_id"] = str(item["_id"])
    return items


def mark_syncing(item_id: str):
    _col().update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"status": "syncing"}},
    )


def mark_synced(item_id: str, combined_result: dict):
    _col().update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {
            "status": "synced",
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "combined_result": combined_result,
        }},
    )
    log.info("Synced item %s", item_id)


def mark_failed(item_id: str, error: str):
    _col().update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {
            "status": "failed",
            "error": error,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    log.warning("Sync failed for item %s: %s", item_id, error)


def get_counts() -> dict:
    col = _col()
    return {
        "pending": col.count_documents({"status": "pending"}),
        "syncing": col.count_documents({"status": "syncing"}),
        "synced": col.count_documents({"status": "synced"}),
        "failed": col.count_documents({"status": "failed"}),
        "total": col.count_documents({}),
    }


def get_recent_synced(limit: int = 10) -> list[dict]:
    items = list(
        _col()
        .find({"status": "synced"})
        .sort("synced_at", -1)
        .limit(limit)
    )
    for item in items:
        item["_id"] = str(item["_id"])
    return items
