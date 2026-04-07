"""
Sync API router — exposes sync queue status and enqueue endpoints.
"""

from fastapi import APIRouter

from lib import sync_queue
from lib.connectivity_monitor import is_online

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/status")
def sync_status():
    """Returns sync queue counts."""
    return sync_queue.get_counts()


@router.get("/online")
def check_online():
    """Returns whether CXAI API is reachable (VPN connected)."""
    return {"online": is_online()}


@router.get("/recent")
def recent_synced(limit: int = 10):
    """Returns recently synced items with their combined results."""
    items = sync_queue.get_recent_synced(limit=limit)
    return {"items": items}


@router.post("/enqueue")
def enqueue_item(payload: dict):
    """Enqueue an item for offline-to-online sync."""
    item_id = sync_queue.enqueue(
        job_type=payload.get("job_type", "uc1"),
        file_path=payload.get("file_path", ""),
        offline_result=payload.get("offline_result"),
        user=payload.get("user", "system"),
    )
    return {"item_id": item_id, "status": "pending"}
