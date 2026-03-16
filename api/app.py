"""
F4F POC — FastAPI Application

Central API server that exposes UC1 (Land Record OCR) and UC2 (Photo
Verification) as modular endpoints. Everything goes through here —
the Streamlit UI is just one possible frontend.

Run:
    uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

Architecture:
    Client → FastAPI → Job System (MongoDB) → Pipeline Workers (threads)
           ↘ Audit Log (MongoDB)
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import HealthResponse
from lib.config import cfg
from lib.db import get_db
from lib.jobs import job_manager, get_registered_types
from lib.job_types import register_all_job_types

log = logging.getLogger("f4f.api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


# ═══════════════════════════════════════════════════════════════════════
# LIFESPAN (startup / shutdown)
# ═══════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: register job types, connect DB.  Shutdown: drain workers."""
    log.info("Starting F4F API server...")
    register_all_job_types()
    get_db()  # force connection + index creation
    log.info("Registered job types: %s", get_registered_types())
    log.info("F4F API ready — http://0.0.0.0:%s", os.environ.get("API_PORT", "8000"))

    yield

    log.info("Shutting down — draining job workers...")
    job_manager.shutdown()
    log.info("Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════════


app = FastAPI(
    title="F4F POC API",
    description=(
        "Farmers for Forests — Document Automation API.\n\n"
        "**UC1:** Land Record OCR & Extraction (Maharashtra 7/12 documents)\n\n"
        "**UC2:** Carbon Credit Training Photo Verification\n\n"
        "Submit jobs, poll for results, query audit logs."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# CORS — allow Streamlit and any local dev frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════
# ROUTERS
# ═══════════════════════════════════════════════════════════════════════


from api.routers import upload, jobs, uc1, uc2, audit  # noqa: E402

app.include_router(upload.router)
app.include_router(jobs.router)
app.include_router(uc1.router)
app.include_router(uc2.router)
app.include_router(audit.router)


# ═══════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════


@app.get("/api/health", response_model=HealthResponse, tags=["system"])
def health_check():
    """System health check — verifies MongoDB connectivity."""
    mongo_status = "connected"
    try:
        get_db().command("ping")
    except Exception as exc:
        mongo_status = f"error: {exc}"

    return HealthResponse(
        status="ok" if mongo_status == "connected" else "degraded",
        mongo=mongo_status,
        platform=cfg.PLATFORM,
        registered_job_types=get_registered_types(),
    )
