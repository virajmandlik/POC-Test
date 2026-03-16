"""
Audit log router — query and inspect the audit trail.

GET /api/audit → list audit logs with filters
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query

from api.schemas import AuditLogEntry, AuditListResponse
from lib.audit import get_audit_logs, count_audit_logs

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=AuditListResponse)
def list_audit_logs(
    action: Optional[str] = Query(None, description="Filter by action (regex)"),
    user: Optional[str] = Query(None),
    level: Optional[str] = Query(None, description="info | warn | error"),
    job_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None, description="ISO date string"),
    until: Optional[str] = Query(None, description="ISO date string"),
    limit: int = Query(100, ge=1, le=1000),
    skip: int = Query(0, ge=0),
):
    """Query audit logs with optional filters, newest first."""
    kwargs: dict = {"limit": limit, "skip": skip}

    if action:
        kwargs["action"] = action
    if user:
        kwargs["user"] = user
    if level:
        kwargs["level"] = level
    if job_id:
        kwargs["job_id"] = job_id
    if since:
        kwargs["since"] = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    if until:
        kwargs["until"] = datetime.fromisoformat(until).replace(tzinfo=timezone.utc)

    logs = get_audit_logs(**kwargs)
    total = count_audit_logs(**{k: v for k, v in kwargs.items() if k not in ("limit", "skip")})

    entries = []
    for log_entry in logs:
        entries.append(AuditLogEntry(
            _id=str(log_entry.get("_id", "")),
            action=log_entry.get("action", ""),
            user=log_entry.get("user", ""),
            timestamp=log_entry.get("timestamp"),
            level=log_entry.get("level", "info"),
            job_id=log_entry.get("job_id"),
            detail=log_entry.get("detail", {}),
            result=log_entry.get("result", {}),
        ))

    return AuditListResponse(logs=entries, total=total)
