"""
UC1 router — Land Record OCR & Extraction endpoints.

POST /api/uc1/extract       → submit single extraction job
POST /api/uc1/batch         → submit batch extraction job
POST /api/uc1/semantic      → submit semantic analysis job
POST /api/uc1/quality-check → synchronous quality check (fast, no job)
"""

import io
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from PIL import Image

from api.schemas import (
    UC1ExtractRequest,
    UC1BatchRequest,
    UC1SemanticRequest,
    JobSubmitResponse,
    QualityCheckResponse,
)
from lib.audit import audit_log
from lib.config import cfg
from lib.jobs import job_manager

router = APIRouter(prefix="/api/uc1", tags=["uc1"])


# ═══════════════════════════════════════════════════════════════════════
# JOB SUBMISSION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════


@router.post("/extract", response_model=JobSubmitResponse)
def submit_extraction(req: UC1ExtractRequest):
    """Submit a single-document extraction job.

    Upload the file first via POST /api/upload, then pass the returned
    path here. The job runs asynchronously — poll GET /api/jobs/{id}
    for status and results.
    """
    # Validate file exists
    fp = Path(req.file_path)
    if not fp.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {req.file_path}")

    job_id = job_manager.submit(
        job_type="uc1.extract",
        params={"file_path": req.file_path, "mode": req.mode, "lang": req.lang},
        user=req.user,
        tags=req.tags,
    )

    audit_log(
        "api.uc1.extract_submitted",
        user=req.user,
        job_id=job_id,
        detail={"file_path": req.file_path, "mode": req.mode},
    )

    return JobSubmitResponse(
        job_id=job_id,
        job_type="uc1.extract",
        message=f"Extraction job submitted ({req.mode} mode)",
    )


@router.post("/batch", response_model=JobSubmitResponse)
def submit_batch_extraction(req: UC1BatchRequest):
    """Submit a batch extraction job for multiple documents.

    Upload files first, then pass the returned paths here.
    """
    # Validate at least the first file exists
    for fp_str in req.file_paths:
        if not Path(fp_str).exists():
            raise HTTPException(status_code=400, detail=f"File not found: {fp_str}")

    job_id = job_manager.submit(
        job_type="uc1.batch",
        params={
            "file_paths": req.file_paths,
            "mode": req.mode,
            "lang": req.lang,
        },
        user=req.user,
        tags=req.tags,
    )

    audit_log(
        "api.uc1.batch_submitted",
        user=req.user,
        job_id=job_id,
        detail={"file_count": len(req.file_paths), "mode": req.mode},
    )

    return JobSubmitResponse(
        job_id=job_id,
        job_type="uc1.batch",
        message=f"Batch extraction submitted ({len(req.file_paths)} files)",
    )


@router.post("/semantic", response_model=JobSubmitResponse)
def submit_semantic_analysis(req: UC1SemanticRequest):
    """Submit a semantic analysis job on existing extraction data.

    Pass the `merged_extraction` dict from a completed UC1 extraction job.
    This produces the ownership chain and knowledge graph.
    """
    job_id = job_manager.submit(
        job_type="uc1.semantic",
        params={"extraction_data": req.extraction_data},
        user=req.user,
        tags=req.tags,
    )

    audit_log(
        "api.uc1.semantic_submitted",
        user=req.user,
        job_id=job_id,
    )

    return JobSubmitResponse(
        job_id=job_id,
        job_type="uc1.semantic",
        message="Semantic analysis job submitted",
    )


# ═══════════════════════════════════════════════════════════════════════
# SYNCHRONOUS ENDPOINTS (fast operations, no job needed)
# ═══════════════════════════════════════════════════════════════════════


@router.post("/quality-check", response_model=QualityCheckResponse)
async def quality_check(
    file: UploadFile = File(...),
    user: str = Query("api"),
):
    """Run a quick quality check on an uploaded image or PDF.

    This is synchronous (returns immediately) since it's a fast
    OpenCV-only operation. No API calls, no job needed.
    """
    from usecase1_land_record_ocr import (
        QualityChecker, DocumentAnalyzer, QualityGate, PDFLoader,
    )

    content = await file.read()
    suffix = Path(file.filename or "").suffix.lower()

    # Convert to PIL Image
    if suffix == ".pdf":
        loader = PDFLoader()
        images = loader.load(content)
        if not images:
            raise HTTPException(status_code=400, detail="PDF has no pages")
        img = images[0]
    elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
        img = Image.open(io.BytesIO(content)).convert("RGB")
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    # Quality check
    checker = QualityChecker()
    qc = checker.check(img)

    # Document analysis + gate
    analyzer = DocumentAnalyzer()
    analysis = analyzer.analyze(img)
    gate = QualityGate()
    gate_passed, gate_reasons = gate.evaluate(qc, analysis)

    audit_log(
        "api.uc1.quality_check",
        user=user,
        detail={"filename": file.filename, "passed": qc.passed, "gate_passed": gate_passed},
    )

    return QualityCheckResponse(
        passed=qc.passed,
        width=qc.width,
        height=qc.height,
        megapixels=qc.megapixels,
        blur_score=qc.blur_score,
        sharpness=qc.sharpness,
        mean_brightness=qc.mean_brightness,
        contrast_ratio=qc.contrast_ratio,
        readability=qc.readability,
        issues=qc.issues,
        orientation=analysis.get("orientation"),
        text_density_pct=analysis.get("text_density_pct"),
        skew_angle_deg=analysis.get("skew_angle_deg"),
        estimated_type=analysis.get("estimated_type"),
        gate_passed=gate_passed,
        gate_reasons=gate_reasons,
    )
