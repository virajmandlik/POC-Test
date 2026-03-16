"""
UC2 router — Carbon Credit Training Photo Verification endpoints.

POST /api/uc2/verify        → submit single-photo verification job
POST /api/uc2/batch         → submit batch PDF verification job
POST /api/uc2/quality-check → synchronous image quality check
"""

import io
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from PIL import Image

from api.schemas import (
    UC2VerifyRequest,
    UC2BatchRequest,
    JobSubmitResponse,
    QualityCheckResponse,
)
from lib.audit import audit_log
from lib.jobs import job_manager

router = APIRouter(prefix="/api/uc2", tags=["uc2"])


# ═══════════════════════════════════════════════════════════════════════
# JOB SUBMISSION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


@router.post("/verify", response_model=JobSubmitResponse)
def submit_verification(req: UC2VerifyRequest):
    """Submit a single-photo verification job.

    Upload the image first via POST /api/upload, then pass the returned
    path here. The job runs async — poll GET /api/jobs/{id}.
    """
    fp = Path(req.image_path)
    if not fp.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {req.image_path}")

    job_id = job_manager.submit(
        job_type="uc2.verify",
        params={"image_path": req.image_path, "skip_vision": req.skip_vision},
        user=req.user,
        tags=req.tags,
    )

    audit_log(
        "api.uc2.verify_submitted",
        user=req.user,
        job_id=job_id,
        detail={"image_path": req.image_path, "skip_vision": req.skip_vision},
    )

    return JobSubmitResponse(
        job_id=job_id,
        job_type="uc2.verify",
        message="Verification job submitted",
    )


@router.post("/batch", response_model=JobSubmitResponse)
def submit_batch_verification(req: UC2BatchRequest):
    """Submit a batch PDF verification job.

    Upload PDFs first, then pass paths here. PDFs must follow
    the naming pattern: {surrogate_key}-{FID}-{LID}.pdf
    """
    for fp_str in req.pdf_paths:
        if not Path(fp_str).exists():
            raise HTTPException(status_code=400, detail=f"File not found: {fp_str}")

    job_id = job_manager.submit(
        job_type="uc2.batch",
        params={"pdf_paths": req.pdf_paths},
        user=req.user,
        tags=req.tags,
    )

    audit_log(
        "api.uc2.batch_submitted",
        user=req.user,
        job_id=job_id,
        detail={"pdf_count": len(req.pdf_paths)},
    )

    return JobSubmitResponse(
        job_id=job_id,
        job_type="uc2.batch",
        message=f"Batch verification submitted ({len(req.pdf_paths)} PDFs)",
    )


# ═══════════════════════════════════════════════════════════════════════
# SYNCHRONOUS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


@router.post("/quality-check", response_model=QualityCheckResponse)
async def quality_check(
    file: UploadFile = File(...),
    user: str = Query("api"),
):
    """Quick image quality check — synchronous, OpenCV only, no API calls."""
    from usecase2_photo_verification import ImageQualityChecker

    content = await file.read()
    suffix = Path(file.filename or "").suffix.lower()

    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {suffix}")

    img = Image.open(io.BytesIO(content)).convert("RGB")

    checker = ImageQualityChecker()
    result = checker.check(img)

    audit_log(
        "api.uc2.quality_check",
        user=user,
        detail={"filename": file.filename, "passed": result.passed},
    )

    return QualityCheckResponse(
        passed=result.passed,
        blur_score=result.details.get("blur_score", 0),
        sharpness=result.details.get("sharpness", ""),
        mean_brightness=result.details.get("mean_brightness", 0),
        contrast_ratio=result.details.get("contrast_ratio", 0),
        issues=result.reason.split("; ") if result.reason else [],
    )
