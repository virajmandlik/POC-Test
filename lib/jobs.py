"""
Job System — persistent, audited background job execution.

Architecture:
    - Jobs are MongoDB documents in the `jobs` collection
    - A ThreadPoolExecutor runs job functions in background threads
    - Each job goes through: pending → running → completed | failed | cancelled
    - Every state transition is audit-logged
    - Jobs reference a "job_type" that maps to a callable via the registry

Usage:
    from lib.jobs import job_manager, register_job_type

    # Register a callable for a job type
    register_job_type("uc1.extract", my_extraction_function)

    # Submit a new job
    job_id = job_manager.submit(
        job_type="uc1.extract",
        params={"file_path": "/path/to/doc.pdf", "mode": "combined"},
        user="matt",
    )

    # Check status
    job = job_manager.get(job_id)

    # Cancel / remove
    job_manager.cancel(job_id, user="matt")
    job_manager.remove(job_id, user="matt")
"""

import logging
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from bson import ObjectId

from lib.audit import audit_log
from lib.config import cfg
from lib.db import get_db

log = logging.getLogger("f4f.jobs")

# ═══════════════════════════════════════════════════════════════════════
# JOB TYPE REGISTRY
# ═══════════════════════════════════════════════════════════════════════

_JOB_TYPES: dict[str, Callable[..., dict]] = {}


def register_job_type(name: str, fn: Callable[..., dict]) -> None:
    """Register a callable that implements a job type.

    The callable receives (job_id: str, **params) and must return a dict
    with the result data. Raise an exception to mark the job as failed.
    """
    _JOB_TYPES[name] = fn
    log.info("Registered job type: %s", name)


def get_registered_types() -> list[str]:
    return list(_JOB_TYPES.keys())


# ═══════════════════════════════════════════════════════════════════════
# JOB STATUS CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ALL_STATUSES = [STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED]


# ═══════════════════════════════════════════════════════════════════════
# JOB MANAGER
# ═══════════════════════════════════════════════════════════════════════

class JobManager:
    """Manages job lifecycle: create, execute, track, cancel, remove."""

    _COLLECTION = "jobs"

    def __init__(self, max_workers: int | None = None):
        self._max_workers = max_workers or cfg.JOB_WORKER_THREADS
        self._pool: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        # Track futures so we can attempt cancellation
        self._futures: dict[str, Any] = {}

    @property
    def _col(self):
        return get_db()[self._COLLECTION]

    def _ensure_pool(self):
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="f4f-job",
            )

    # ── Submit ───────────────────────────────────────────────────

    def submit(
        self,
        job_type: str,
        params: dict[str, Any] | None = None,
        user: str = "system",
        tags: list[str] | None = None,
    ) -> str:
        """Create a job document and dispatch it for execution.

        Returns the job_id (str).
        """
        if job_type not in _JOB_TYPES:
            raise ValueError(
                f"Unknown job type '{job_type}'. "
                f"Registered types: {list(_JOB_TYPES.keys())}"
            )

        now = datetime.now(timezone.utc)
        doc = {
            "job_type": job_type,
            "status": STATUS_PENDING,
            "params": params or {},
            "tags": tags or [],
            "user": user,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
            "progress": 0,
            "progress_message": "",
        }

        inserted = self._col.insert_one(doc)
        job_id = str(inserted.inserted_id)

        audit_log(
            "job.created",
            user=user,
            job_id=job_id,
            detail={"job_type": job_type, "params": params, "tags": tags},
        )

        # Dispatch to thread pool
        self._ensure_pool()
        future = self._pool.submit(self._execute, job_id, job_type, params or {})
        with self._lock:
            self._futures[job_id] = future

        log.info("Job submitted: %s [%s] by %s", job_id, job_type, user)
        return job_id

    # ── Execute (runs in worker thread) ──────────────────────────

    def _execute(self, job_id: str, job_type: str, params: dict) -> None:
        """Worker function — runs the registered callable and updates the job doc."""
        now = datetime.now(timezone.utc)

        # Mark running
        self._col.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": STATUS_RUNNING,
                "started_at": now,
                "updated_at": now,
            }},
        )
        audit_log("job.started", job_id=job_id, detail={"job_type": job_type})

        try:
            fn = _JOB_TYPES[job_type]
            result = fn(job_id=job_id, **params)

            # Completed
            self._col.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": STATUS_COMPLETED,
                    "result": result,
                    "completed_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                    "progress": 100,
                    "progress_message": "Done",
                }},
            )
            audit_log(
                "job.completed",
                job_id=job_id,
                result={"summary": _safe_summary(result)},
            )
            log.info("Job completed: %s", job_id)

        except Exception as exc:
            tb = traceback.format_exc()
            self._col.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": STATUS_FAILED,
                    "error": str(exc),
                    "completed_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                    "progress_message": f"Failed: {exc}",
                }},
            )
            audit_log(
                "job.failed",
                job_id=job_id,
                level="error",
                result={"error": str(exc), "traceback": tb[-1000:]},
            )
            log.error("Job failed: %s — %s", job_id, exc)

        finally:
            with self._lock:
                self._futures.pop(job_id, None)

    # ── Progress updates (called from within job functions) ──────

    def update_progress(
        self, job_id: str, progress: int, message: str = ""
    ) -> None:
        """Update job progress (0-100) and optional status message.

        Call this from within a job function to report progress.
        """
        self._col.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "progress": min(max(progress, 0), 100),
                "progress_message": message,
                "updated_at": datetime.now(timezone.utc),
            }},
        )

    # ── Query ────────────────────────────────────────────────────

    def get(self, job_id: str) -> dict | None:
        """Get a single job by ID."""
        try:
            doc = self._col.find_one({"_id": ObjectId(job_id)})
        except Exception:
            return None
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    def list_jobs(
        self,
        *,
        status: str | list[str] | None = None,
        job_type: str | None = None,
        user: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict]:
        """List jobs with optional filters, newest first."""
        query: dict[str, Any] = {}
        if status:
            if isinstance(status, list):
                query["status"] = {"$in": status}
            else:
                query["status"] = status
        if job_type:
            query["job_type"] = job_type
        if user:
            query["user"] = user

        cursor = (
            self._col.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        results = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            results.append(doc)
        return results

    def count_jobs(self, status: str | None = None) -> dict[str, int]:
        """Return counts per status. If status given, just that count."""
        if status:
            return {status: self._col.count_documents({"status": status})}
        pipeline = [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        counts = {s: 0 for s in ALL_STATUSES}
        for doc in self._col.aggregate(pipeline):
            counts[doc["_id"]] = doc["count"]
        counts["total"] = sum(counts.values())
        return counts

    # ── Cancel ───────────────────────────────────────────────────

    def cancel(self, job_id: str, user: str = "system") -> bool:
        """Cancel a pending or running job.

        Returns True if the job was cancelled, False if it couldn't be.
        """
        job = self.get(job_id)
        if not job:
            return False

        if job["status"] not in (STATUS_PENDING, STATUS_RUNNING):
            return False

        # Attempt to cancel the future if still pending
        with self._lock:
            future = self._futures.get(job_id)
            if future:
                future.cancel()

        self._col.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": STATUS_CANCELLED,
                "updated_at": datetime.now(timezone.utc),
                "completed_at": datetime.now(timezone.utc),
                "progress_message": f"Cancelled by {user}",
            }},
        )
        audit_log("job.cancelled", user=user, job_id=job_id)
        log.info("Job cancelled: %s by %s", job_id, user)
        return True

    # ── Remove ───────────────────────────────────────────────────

    def remove(self, job_id: str, user: str = "system") -> bool:
        """Delete a job document entirely. Only terminal jobs can be removed."""
        job = self.get(job_id)
        if not job:
            return False

        if job["status"] in (STATUS_PENDING, STATUS_RUNNING):
            # Cancel first
            self.cancel(job_id, user=user)

        result = self._col.delete_one({"_id": ObjectId(job_id)})
        if result.deleted_count:
            audit_log("job.removed", user=user, job_id=job_id)
            log.info("Job removed: %s by %s", job_id, user)
            return True
        return False

    # ── Retry ────────────────────────────────────────────────────

    def retry(self, job_id: str, user: str = "system") -> str | None:
        """Re-submit a failed or cancelled job with the same params.

        Returns the new job_id, or None if the original job wasn't found
        or isn't in a retriable state.
        """
        job = self.get(job_id)
        if not job or job["status"] not in (STATUS_FAILED, STATUS_CANCELLED):
            return None

        new_id = self.submit(
            job_type=job["job_type"],
            params=job["params"],
            user=user,
            tags=job.get("tags", []) + ["retry"],
        )
        audit_log(
            "job.retried",
            user=user,
            job_id=new_id,
            detail={"original_job_id": job_id},
        )
        return new_id

    # ── Cleanup ──────────────────────────────────────────────────

    def purge_completed(self, older_than_hours: int = 24, user: str = "system") -> int:
        """Remove completed jobs older than N hours."""
        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff = cutoff - timedelta(hours=older_than_hours)
        result = self._col.delete_many({
            "status": {"$in": [STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED]},
            "completed_at": {"$lt": cutoff},
        })
        if result.deleted_count:
            audit_log(
                "jobs.purged",
                user=user,
                detail={"older_than_hours": older_than_hours, "deleted": result.deleted_count},
            )
        return result.deleted_count

    def shutdown(self):
        """Graceful shutdown of the thread pool."""
        if self._pool:
            self._pool.shutdown(wait=False)
            self._pool = None


def _safe_summary(result: Any) -> str:
    """Produce a short string summary of a result dict for audit logs."""
    if isinstance(result, dict):
        status = result.get("status", "")
        keys = list(result.keys())[:5]
        return f"status={status}, keys={keys}"
    return str(result)[:200]


# ── Singleton ────────────────────────────────────────────────────

job_manager = JobManager()
