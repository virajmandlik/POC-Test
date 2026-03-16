"""
Job management router — CRUD + lifecycle operations on jobs.

All the heavy lifting is done by lib.jobs.JobManager. This router
is a thin REST wrapper.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from api.schemas import (
    JobResponse,
    JobSubmitResponse,
    JobListResponse,
    JobActionResponse,
    PurgeRequest,
    PurgeResponse,
)
from lib.jobs import job_manager, ALL_STATUSES

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _doc_to_response(doc: dict) -> JobResponse:
    """Convert a MongoDB job document to a JobResponse."""
    return JobResponse(
        id=doc["_id"],
        job_type=doc.get("job_type", ""),
        status=doc.get("status", ""),
        params=doc.get("params", {}),
        tags=doc.get("tags", []),
        user=doc.get("user", ""),
        progress=doc.get("progress", 0),
        progress_message=doc.get("progress_message", ""),
        created_at=doc.get("created_at"),
        started_at=doc.get("started_at"),
        completed_at=doc.get("completed_at"),
        updated_at=doc.get("updated_at"),
        result=doc.get("result"),
        error=doc.get("error"),
    )


@router.get("", response_model=JobListResponse)
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status (comma-separated for multiple)"),
    job_type: Optional[str] = Query(None),
    user: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
):
    """List jobs with optional filters, newest first."""
    status_filter = None
    if status:
        status_filter = [s.strip() for s in status.split(",")]

    jobs = job_manager.list_jobs(
        status=status_filter,
        job_type=job_type,
        user=user,
        limit=limit,
        skip=skip,
    )
    counts = job_manager.count_jobs()

    return JobListResponse(
        jobs=[_doc_to_response(j) for j in jobs],
        total=counts.get("total", 0),
        counts=counts,
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    """Get a single job by ID, including its result if complete."""
    doc = job_manager.get(job_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _doc_to_response(doc)


@router.post("/{job_id}/cancel", response_model=JobActionResponse)
def cancel_job(job_id: str, user: str = "api"):
    """Cancel a pending or running job."""
    success = job_manager.cancel(job_id, user=user)
    if not success:
        raise HTTPException(
            status_code=409,
            detail="Cannot cancel — job not found or already in terminal state",
        )
    return JobActionResponse(success=True, message="Job cancelled", job_id=job_id)


@router.post("/{job_id}/retry", response_model=JobSubmitResponse)
def retry_job(job_id: str, user: str = "api"):
    """Retry a failed or cancelled job. Creates a new job with the same params."""
    new_id = job_manager.retry(job_id, user=user)
    if not new_id:
        raise HTTPException(
            status_code=409,
            detail="Cannot retry — job not found or not in failed/cancelled state",
        )
    new_job = job_manager.get(new_id)
    return JobSubmitResponse(
        job_id=new_id,
        job_type=new_job["job_type"] if new_job else "",
        status="pending",
        message=f"Retried from {job_id}",
    )


@router.delete("/{job_id}", response_model=JobActionResponse)
def remove_job(job_id: str, user: str = "api"):
    """Remove a job entirely."""
    success = job_manager.remove(job_id, user=user)
    if not success:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobActionResponse(success=True, message="Job removed", job_id=job_id)


@router.post("/purge", response_model=PurgeResponse)
def purge_jobs(req: PurgeRequest, user: str = "api"):
    """Delete completed/failed/cancelled jobs older than N hours."""
    deleted = job_manager.purge_completed(
        older_than_hours=req.older_than_hours,
        user=user,
    )
    return PurgeResponse(
        deleted=deleted,
        message=f"Purged {deleted} jobs older than {req.older_than_hours}h",
    )
