"""
Pydantic models for the F4F API.

Defines request/response schemas for all endpoints.
Kept in one file — split when it grows beyond ~300 lines.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════
# COMMON
# ═══════════════════════════════════════════════════════════════════════


class HealthResponse(BaseModel):
    status: str = "ok"
    mongo: str = "connected"
    platform: str = ""
    version: str = "0.1.0"
    registered_job_types: list[str] = []


class FileUploadResponse(BaseModel):
    file_id: str
    filename: str
    path: str
    size_bytes: int


# ═══════════════════════════════════════════════════════════════════════
# JOBS
# ═══════════════════════════════════════════════════════════════════════


class JobResponse(BaseModel):
    """Returned when a job is created or queried."""
    id: str
    job_type: str
    status: str
    params: dict[str, Any] = {}
    tags: list[str] = []
    user: str = ""
    progress: int = 0
    progress_message: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    result: Any = None
    error: str | None = None


class JobSubmitResponse(BaseModel):
    """Returned when a job is successfully submitted."""
    job_id: str
    job_type: str
    status: str = "pending"
    message: str = "Job submitted"


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    counts: dict[str, int] = {}


class JobActionResponse(BaseModel):
    success: bool
    message: str
    job_id: str | None = None


class PurgeRequest(BaseModel):
    older_than_hours: int = Field(default=24, ge=1)


class PurgeResponse(BaseModel):
    deleted: int
    message: str


# ═══════════════════════════════════════════════════════════════════════
# UC1 — LAND RECORD EXTRACTION
# ═══════════════════════════════════════════════════════════════════════


class UC1ExtractRequest(BaseModel):
    """Submit a single-document extraction job."""
    file_path: str = Field(..., description="Path to uploaded file (from /api/upload)")
    mode: str = Field(default="combined", description="combined | paddle | vision")
    lang: str = Field(default="mr", description="OCR language code")
    user: str = Field(default="api", description="Username for audit")
    tags: list[str] = []


class UC1BatchRequest(BaseModel):
    """Submit a batch extraction job."""
    file_paths: list[str] = Field(..., description="List of uploaded file paths")
    mode: str = Field(default="combined")
    lang: str = Field(default="mr")
    user: str = Field(default="api")
    tags: list[str] = []


class UC1SemanticRequest(BaseModel):
    """Submit a semantic analysis job on an existing extraction result."""
    extraction_data: dict[str, Any] = Field(
        ..., description="The merged_extraction dict from a completed UC1 job"
    )
    user: str = Field(default="api")
    tags: list[str] = []


class QualityCheckResponse(BaseModel):
    """Synchronous quality check result (no job needed — fast operation)."""
    passed: bool
    width: int = 0
    height: int = 0
    megapixels: float = 0.0
    blur_score: float = 0.0
    sharpness: str = ""
    mean_brightness: float = 0.0
    contrast_ratio: float = 0.0
    readability: str = ""
    issues: list[str] = []

    # Document analysis (UC1 only)
    orientation: str | None = None
    text_density_pct: float | None = None
    skew_angle_deg: float | None = None
    estimated_type: str | None = None
    gate_passed: bool | None = None
    gate_reasons: list[str] | None = None


# ═══════════════════════════════════════════════════════════════════════
# UC2 — PHOTO VERIFICATION
# ═══════════════════════════════════════════════════════════════════════


class UC2VerifyRequest(BaseModel):
    """Submit a single-photo verification job."""
    image_path: str = Field(..., description="Path to uploaded image")
    skip_vision: bool = Field(default=False, description="Skip GPT Vision analysis")
    user: str = Field(default="api")
    tags: list[str] = []


class UC2BatchRequest(BaseModel):
    """Submit a batch PDF verification job."""
    pdf_paths: list[str] = Field(..., description="List of uploaded PDF paths")
    user: str = Field(default="api")
    tags: list[str] = []


# ═══════════════════════════════════════════════════════════════════════
# AUDIT
# ═══════════════════════════════════════════════════════════════════════


class AuditLogEntry(BaseModel):
    id: str = Field(alias="_id", default="")
    action: str = ""
    user: str = ""
    timestamp: datetime | None = None
    level: str = "info"
    job_id: str | None = None
    detail: dict[str, Any] = {}
    result: dict[str, Any] = {}

    model_config = {"populate_by_name": True}


class AuditListResponse(BaseModel):
    logs: list[AuditLogEntry]
    total: int
