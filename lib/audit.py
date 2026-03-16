"""
Audit logging — records every significant action to MongoDB.

Usage:
    from lib.audit import audit_log
    audit_log("extraction.started", user="matt", detail={"file": "test.pdf", "mode": "combined"})
"""

import logging
import traceback
from datetime import datetime, timezone
from typing import Any

from lib.db import get_db

log = logging.getLogger("f4f.audit")

_COLLECTION = "audit_logs"


def audit_log(
    action: str,
    *,
    user: str = "system",
    detail: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    job_id: str | None = None,
    level: str = "info",
) -> str:
    """Write an audit entry and return its inserted _id as a string.

    Args:
        action:  Dotted action name, e.g. "job.created", "extraction.completed".
        user:    Who triggered it (username or "system").
        detail:  Request / input data (what was asked).
        result:  Outcome / output data (what happened).
        job_id:  Optional link to a job document.
        level:   "info" | "warn" | "error".
    """
    doc = {
        "action": action,
        "user": user,
        "timestamp": datetime.now(timezone.utc),
        "level": level,
        "job_id": job_id,
        "detail": detail or {},
        "result": result or {},
    }

    try:
        col = get_db()[_COLLECTION]
        inserted = col.insert_one(doc)
        log.debug("audit: %s [%s] %s", action, user, inserted.inserted_id)
        return str(inserted.inserted_id)
    except Exception:
        # Audit must never crash the caller — log and continue
        log.error("Failed to write audit log: %s\n%s", action, traceback.format_exc())
        return ""


def get_audit_logs(
    *,
    limit: int = 100,
    skip: int = 0,
    action: str | None = None,
    user: str | None = None,
    level: str | None = None,
    job_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """Query audit logs with optional filters, newest first."""
    query: dict[str, Any] = {}
    if action:
        query["action"] = {"$regex": action, "$options": "i"}
    if user:
        query["user"] = user
    if level:
        query["level"] = level
    if job_id:
        query["job_id"] = job_id
    if since or until:
        ts_filter: dict[str, Any] = {}
        if since:
            ts_filter["$gte"] = since
        if until:
            ts_filter["$lte"] = until
        query["timestamp"] = ts_filter

    col = get_db()[_COLLECTION]
    cursor = col.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


def count_audit_logs(**kwargs) -> int:
    """Count audit logs matching the same filters as get_audit_logs."""
    # Reuse filter building
    query: dict[str, Any] = {}
    if kwargs.get("action"):
        query["action"] = {"$regex": kwargs["action"], "$options": "i"}
    if kwargs.get("user"):
        query["user"] = kwargs["user"]
    if kwargs.get("level"):
        query["level"] = kwargs["level"]
    if kwargs.get("job_id"):
        query["job_id"] = kwargs["job_id"]
    return get_db()[_COLLECTION].count_documents(query)
