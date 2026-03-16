"""
File upload router — accepts files and stores them in the uploads directory.

Returns a path that can be passed to job submission endpoints.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, UploadFile, File

from api.schemas import FileUploadResponse
from lib.audit import audit_log
from lib.config import cfg

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    user: str = "api",
):
    """Upload a document (PDF, image) for processing.

    Returns a file path to use in subsequent job submissions.
    """
    content = await file.read()
    size = len(content)

    # Unique filename to avoid collisions: {timestamp}_{uuid_short}_{original}
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    safe_name = Path(file.filename or "upload").name  # sanitise filename
    dest_name = f"{ts}_{short_id}_{safe_name}"
    dest_path = cfg.UPLOAD_DIR / dest_name

    dest_path.write_bytes(content)

    file_id = hashlib.sha256(content).hexdigest()[:16]

    audit_log(
        "file.uploaded",
        user=user,
        detail={
            "filename": safe_name,
            "dest": str(dest_path),
            "size_bytes": size,
            "file_id": file_id,
        },
    )

    return FileUploadResponse(
        file_id=file_id,
        filename=safe_name,
        path=str(dest_path),
        size_bytes=size,
    )


@router.post("/upload/batch", response_model=list[FileUploadResponse])
async def upload_files(
    files: list[UploadFile] = File(...),
    user: str = "api",
):
    """Upload multiple files at once."""
    results = []
    for f in files:
        content = await f.read()
        size = len(content)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        safe_name = Path(f.filename or "upload").name
        dest_name = f"{ts}_{short_id}_{safe_name}"
        dest_path = cfg.UPLOAD_DIR / dest_name

        dest_path.write_bytes(content)
        file_id = hashlib.sha256(content).hexdigest()[:16]

        results.append(FileUploadResponse(
            file_id=file_id,
            filename=safe_name,
            path=str(dest_path),
            size_bytes=size,
        ))

    audit_log(
        "file.batch_uploaded",
        user=user,
        detail={"count": len(results), "filenames": [r.filename for r in results]},
    )

    return results
