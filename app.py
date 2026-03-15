"""
DocExtract — FastAPI Application
Serves /egress, /status, /verify endpoints for verified document data,
and /verify-training for carbon credit training photo verification.
"""

import io
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel, Field

from cc_verify import TrainingPhotoVerifier
from cc_pipeline import PDFExtractor, PDFIdentifiers, PipelineFactory

load_dotenv()

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
VERIFIED_DIR = OUTPUT_DIR / "verified"
UPLOAD_DIR = BASE_DIR / "uploads"
INDEX_PATH = OUTPUT_DIR / "documents.json"

VERIFIED_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="DocExtract Pipeline API",
    description=(
        "Egress API for verified Maharashtra 7/12 land record extractions "
        "and carbon credit training photo verification"
    ),
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Pydantic Models ──────────────────────────────────────────────


class CorrectionItem(BaseModel):
    field_name: str
    original_value: str
    corrected_value: str


class VerifyRequest(BaseModel):
    verified_by: str
    role: str = "farmer"
    decision: str = Field(..., pattern="^(APPROVED|REJECTED)$")
    corrections: list[CorrectionItem] = []


class VerifyResponse(BaseModel):
    document_id: str
    status: str
    message: str
    corrections_saved: int
    training_data_logged: bool


class StatusStage(BaseModel):
    completed: bool
    timestamp: str | None


class StatusResponse(BaseModel):
    document_id: str
    current_status: str
    pipeline_stages: dict[str, StatusStage]


class EgressMetadata(BaseModel):
    source_file: str = ""
    ingress_timestamp: str = ""
    processing_duration_ms: int = 0
    pages_processed: int = 1
    retry_count: int = 0


class EgressResponse(BaseModel):
    document_id: str
    status: str
    extraction_method: str = ""
    overall_confidence: float = 0.0
    verified_by: str = ""
    verified_at: str = ""
    data: dict[str, Any] = {}
    corrections_applied: list[dict] = []
    metadata: EgressMetadata = EgressMetadata()


class IngressResponse(BaseModel):
    document_id: str
    status: str
    message: str
    status_url: str
    egress_url: str


# ── Training Photo Verification Models ────────────────────────────


class TrainingCheckDetail(BaseModel):
    passed: bool
    model_config = {"extra": "allow"}


class TrainingVerifyResponse(BaseModel):
    decision: str
    checks: dict[str, TrainingCheckDetail]
    rejection_reasons: list[str]
    metadata: dict[str, Any]


# ── Helpers ──────────────────────────────────────────────────────


def _load_index() -> dict:
    if not INDEX_PATH.exists():
        return {}
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_index(index: dict):
    with INDEX_PATH.open("w", encoding="utf-8") as fp:
        json.dump(index, fp, ensure_ascii=False, indent=2)


def _load_verified(doc_id: str) -> dict | None:
    verified_path = VERIFIED_DIR / f"{doc_id}.json"
    if not verified_path.exists():
        return None
    with verified_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ── Endpoints ────────────────────────────────────────────────────


@app.get("/")
def root():
    return {
        "service": "DocExtract Pipeline API",
        "version": "1.0.0",
        "endpoints": {
            "egress": "GET /egress/{document_id}",
            "status": "GET /status/{document_id}",
            "verify": "POST /verify/{document_id}",
            "list": "GET /documents",
            "verify_training": "POST /verify-training",
        },
    }


@app.get("/documents")
def list_documents():
    """List all verified documents available for egress."""
    index = _load_index()
    return {
        "total": len(index),
        "documents": [
            {
                "document_id": doc_id,
                "status": info.get("status", "UNKNOWN"),
                "extraction_method": info.get("extraction_method", ""),
                "verified_at": info.get("verified_at", ""),
                "source_file": Path(info.get("source_file", "")).name,
            }
            for doc_id, info in index.items()
        ],
    }


@app.get("/egress/{document_id}", response_model=EgressResponse)
def egress(document_id: str):
    """
    Return verified, structured extraction data for a document.
    This is the primary consumer endpoint.
    """
    index = _load_index()

    if document_id not in index:
        doc = _load_verified(document_id)
        if not doc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "DOCUMENT_NOT_FOUND",
                    "message": f"No document found with id: {document_id}",
                    "document_id": document_id,
                },
            )
        return doc

    entry = index[document_id]
    status = entry.get("status", "UNKNOWN")

    if status != "COMPLETED":
        return {
            "document_id": document_id,
            "status": status,
            "message": "Document is still being processed.",
            "data": {},
            "metadata": {},
        }

    verified = _load_verified(document_id)
    if not verified:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "VERIFIED_DATA_MISSING",
                "message": "Document is marked COMPLETED but verified data file is missing.",
                "document_id": document_id,
            },
        )

    return verified


@app.get("/status/{document_id}", response_model=StatusResponse)
def status(document_id: str):
    """Return pipeline status for a document."""
    index = _load_index()

    if document_id not in index:
        verified = _load_verified(document_id)
        if verified:
            now = datetime.now(timezone.utc).isoformat()
            return StatusResponse(
                document_id=document_id,
                current_status="COMPLETED",
                pipeline_stages={
                    "UPLOADED": StatusStage(completed=True, timestamp=now),
                    "PREPROCESSING": StatusStage(completed=True, timestamp=now),
                    "EXTRACTING": StatusStage(completed=True, timestamp=now),
                    "FORMATTING": StatusStage(completed=True, timestamp=now),
                    "VERIFYING": StatusStage(completed=True, timestamp=now),
                    "COMPLETED": StatusStage(completed=True, timestamp=now),
                },
            )
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "DOCUMENT_NOT_FOUND",
                "message": f"No document found with id: {document_id}",
            },
        )

    entry = index[document_id]
    current_status = entry.get("status", "UNKNOWN")
    verified_at = entry.get("verified_at", "")

    stage_order = [
        "UPLOADED", "PREPROCESSING", "EXTRACTING",
        "FORMATTING", "VERIFYING", "COMPLETED",
    ]
    reached = current_status in stage_order
    stages = {}
    completed_flag = True
    for s in stage_order:
        if s == current_status:
            stages[s] = StatusStage(completed=True, timestamp=verified_at)
            completed_flag = False
        elif completed_flag:
            stages[s] = StatusStage(completed=True, timestamp=verified_at)
        else:
            stages[s] = StatusStage(completed=False, timestamp=None)

    return StatusResponse(
        document_id=document_id,
        current_status=current_status,
        pipeline_stages=stages,
    )


@app.post("/verify/{document_id}", response_model=VerifyResponse)
def verify(document_id: str, body: VerifyRequest):
    """Accept verification decision (APPROVED/REJECTED) for a document."""
    verified = _load_verified(document_id)
    if not verified:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "DOCUMENT_NOT_FOUND",
                "message": f"No extraction found for document: {document_id}",
            },
        )

    if body.decision == "APPROVED":
        if body.corrections:
            data = verified.get("data", {})
            for correction in body.corrections:
                _apply_correction(data, correction.field_name, correction.corrected_value)
            verified["data"] = data
            verified["corrections_applied"] = [c.model_dump() for c in body.corrections]

        verified["status"] = "COMPLETED"
        verified["verified_by"] = body.verified_by
        verified["verified_at"] = datetime.now(timezone.utc).isoformat()

        egress_path = VERIFIED_DIR / f"{document_id}.json"
        with egress_path.open("w", encoding="utf-8") as fp:
            json.dump(verified, fp, ensure_ascii=False, indent=2)

        index = _load_index()
        if document_id in index:
            index[document_id]["status"] = "COMPLETED"
            index[document_id]["verified_at"] = verified["verified_at"]
            _save_index(index)

        return VerifyResponse(
            document_id=document_id,
            status="COMPLETED",
            message="Verification recorded. Document approved.",
            corrections_saved=len(body.corrections),
            training_data_logged=bool(body.corrections),
        )
    else:
        index = _load_index()
        if document_id in index:
            index[document_id]["status"] = "REJECTED"
            _save_index(index)

        return VerifyResponse(
            document_id=document_id,
            status="REJECTED",
            message="Document rejected. Re-extraction required.",
            corrections_saved=0,
            training_data_logged=False,
        )


@app.post("/verify-training", response_model=TrainingVerifyResponse)
def verify_training(
    photo: UploadFile = File(..., description="JPEG/PNG training session photo"),
    skip_vision: bool = Form(False, description="Skip GPT-4 Vision scene analysis"),
):
    """
    Verify a carbon credit training session photo.
    Returns ACCEPT/REJECT with detailed check results.
    """
    if photo.content_type and not photo.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload must be an image file (JPEG/PNG)")

    try:
        contents = photo.file.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read image: {exc}")

    api_key = os.environ.get("CXAI_API_KEY", "")
    verifier = TrainingPhotoVerifier(api_key=api_key)

    result = verifier.verify(img=img, skip_vision=skip_vision)

    return result.to_dict()


@app.post("/verify-training-pdf")
def verify_training_pdf(
    pdf: UploadFile = File(..., description="CC training PDF (3-page report)"),
    skip_vision: bool = Form(False, description="Skip GPT-4 Vision scene analysis"),
):
    """
    Extract training photo from a CC training PDF and verify it.
    The PDF should follow the standard 3-page format:
    page 1 = meeting minutes, page 2 = attendees, page 3 = training photo.
    """
    if pdf.content_type and pdf.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Upload must be a PDF file")

    import tempfile
    try:
        contents = pdf.file.read()
        tmp = Path(tempfile.mktemp(suffix=".pdf"))
        tmp.write_bytes(contents)

        extractor = PDFExtractor()
        img = extractor.extract_photo(tmp)
        tmp.unlink(missing_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not extract photo from PDF: {exc}")

    ids = PDFIdentifiers.from_filename(pdf.filename or "unknown.pdf")

    api_key = os.environ.get("CXAI_API_KEY", "")
    verifier = TrainingPhotoVerifier(api_key=api_key)
    result = verifier.verify(img=img, skip_vision=skip_vision)

    response = result.to_dict()
    if ids:
        response["metadata"]["lid"] = ids.lid
        response["metadata"]["fid"] = ids.fid

    return response


def _apply_correction(data: dict, field_path: str, new_value: str):
    """Apply a dot-notation field correction to nested dict."""
    keys = field_path.split(".")
    obj = data
    for key in keys[:-1]:
        if key in obj and isinstance(obj[key], dict):
            obj = obj[key]
        else:
            return
    if keys[-1] in obj:
        obj[keys[-1]] = new_value


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
