"""
Connectivity Monitor — background daemon that auto-syncs offline items.

Periodically checks if the CXAI Vision API is reachable (requires VPN).
When connectivity is detected, pulls pending items from the sync queue
and processes them:
  - UC1: runs Vision API only, merges with stored PaddleOCR result
  - UC2: runs full verification pipeline
"""

import logging
import socket
import threading
import time
from urllib.parse import urlparse

log = logging.getLogger("f4f.connectivity")

CXAI_ENDPOINT = "cxai-playground.cisco.com"
CHECK_INTERVAL = 3
_running = False
_thread: threading.Thread | None = None
_online = False


def is_online() -> bool:
    return _online


def _check_connectivity() -> bool:
    try:
        sock = socket.create_connection((CXAI_ENDPOINT, 443), timeout=5)
        sock.close()
        return True
    except (socket.timeout, socket.error, OSError):
        return False


def _process_pending():
    from lib import sync_queue

    items = sync_queue.get_pending(limit=5)
    if not items:
        return

    log.info("Processing %d pending sync items", len(items))

    for item in items:
        item_id = item["_id"]
        job_type = item.get("job_type", "")
        file_path = item.get("file_path", "")
        offline_result = item.get("offline_result", {})

        sync_queue.mark_syncing(item_id)

        try:
            if job_type == "uc1":
                result = _sync_uc1(file_path, offline_result)
            elif job_type == "uc2":
                result = _sync_uc2(file_path)
            else:
                sync_queue.mark_failed(item_id, f"Unknown job type: {job_type}")
                continue

            sync_queue.mark_synced(item_id, result)
        except Exception as exc:
            log.exception("Sync failed for %s", item_id)
            sync_queue.mark_failed(item_id, str(exc))


def _sync_uc1(file_path: str, paddle_result: dict) -> dict:
    from pathlib import Path
    from usecase1_land_record_ocr import ExtractionEngine
    engine = ExtractionEngine()
    return engine.extract(Path(file_path), mode="combined", lang="mr")


def _sync_uc2(file_path: str) -> dict:
    from PIL import Image
    from usecase2_photo_verification import TrainingPhotoVerifier
    verifier = TrainingPhotoVerifier()
    img = Image.open(file_path)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    result = verifier.verify(img, skip_vision=False)
    return result.to_dict()


def _monitor_loop():
    global _online, _running

    log.info("Connectivity monitor started (checking %s every %ds)", CXAI_ENDPOINT, CHECK_INTERVAL)

    while _running:
        try:
            was_online = _online
            _online = _check_connectivity()

            if _online and not was_online:
                log.info("Internet/VPN detected — triggering auto-sync")

            if _online:
                _process_pending()

        except Exception:
            log.exception("Error in connectivity monitor loop")

        time.sleep(CHECK_INTERVAL)

    log.info("Connectivity monitor stopped")


def start():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_monitor_loop, daemon=True, name="connectivity-monitor")
    _thread.start()
    log.info("Connectivity monitor thread started")


def stop():
    global _running
    _running = False
    log.info("Connectivity monitor stopping...")
