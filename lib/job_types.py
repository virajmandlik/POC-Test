"""
Job type implementations — thin wrappers around existing UC1/UC2 logic.

These functions are registered with the job engine so they can be
dispatched as background jobs. Each function receives a job_id plus
the params dict, runs the pipeline, and returns a result dict.

Registered types:
    uc1.extract       — Single-document land record extraction
    uc1.batch         — Batch land record extraction
    uc2.verify        — Single-photo CC training verification
    uc2.batch         — Batch CC PDF verification
"""

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import cfg
from lib.jobs import job_manager, register_job_type

log = logging.getLogger("f4f.job_types")


# ═══════════════════════════════════════════════════════════════════════
# UC1 — LAND RECORD EXTRACTION
# ═══════════════════════════════════════════════════════════════════════


def _uc1_extract(job_id: str, **params: Any) -> dict:
    """Run a single-document extraction via the UC1 ExtractionEngine.

    Expected params:
        file_path: str          — path to PDF or image
        mode: str               — "combined" | "paddle" | "vision"
        lang: str               — OCR language code (default "mr")
    """
    # Import here to avoid circular / heavy imports at module load
    from usecase1_land_record_ocr import ExtractionEngine

    file_path = Path(params["file_path"])
    mode = params.get("mode", "combined")
    lang = params.get("lang", "mr")

    job_manager.update_progress(job_id, 10, f"Starting {mode} extraction...")

    engine = ExtractionEngine(api_key=cfg.CXAI_API_KEY)
    result = engine.extract(input_path=file_path, mode=mode, lang=lang)

    job_manager.update_progress(job_id, 90, "Saving output...")

    # Persist to output directory
    out_path = cfg.OUTPUT_DIR / f"job_{job_id}.json"
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2, default=str)

    result["output_path"] = str(out_path)
    return result


def _uc1_batch(job_id: str, **params: Any) -> dict:
    """Run batch extraction on a list of file paths.

    Expected params:
        file_paths: list[str]   — list of paths to PDFs/images
        mode: str               — extraction mode
        lang: str               — OCR language code
    """
    from usecase1_land_record_ocr import ExtractionEngine

    file_paths = [Path(p) for p in params["file_paths"]]
    mode = params.get("mode", "combined")
    lang = params.get("lang", "mr")
    total = len(file_paths)

    engine = ExtractionEngine(api_key=cfg.CXAI_API_KEY)
    results = []

    for i, fp in enumerate(file_paths):
        pct = int((i / total) * 90) + 5
        job_manager.update_progress(job_id, pct, f"Processing {i + 1}/{total}: {fp.name}")

        try:
            result = engine.extract(input_path=fp, mode=mode, lang=lang)
            results.append({"file": str(fp), "status": result.get("status", "ok"), "data": result})
        except Exception as exc:
            results.append({"file": str(fp), "status": "failed", "error": str(exc)})

    # Summary
    ok = sum(1 for r in results if r["status"] == "ok")
    failed = total - ok

    out_path = cfg.OUTPUT_DIR / f"batch_job_{job_id}.json"
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(results, fp, ensure_ascii=False, indent=2, default=str)

    return {
        "total": total,
        "succeeded": ok,
        "failed": failed,
        "output_path": str(out_path),
    }


# ═══════════════════════════════════════════════════════════════════════
# UC2 — PHOTO VERIFICATION
# ═══════════════════════════════════════════════════════════════════════


def _uc2_verify(job_id: str, **params: Any) -> dict:
    """Run single-photo verification.

    Expected params:
        image_path: str         — path to image file
        skip_vision: bool       — skip GPT Vision analysis (default False)
    """
    from usecase2_photo_verification import TrainingPhotoVerifier
    from PIL import Image

    image_path = Path(params["image_path"])
    skip_vision = params.get("skip_vision", False)

    job_manager.update_progress(job_id, 10, "Loading image...")

    img = Image.open(str(image_path)).convert("RGB")

    job_manager.update_progress(job_id, 30, "Running verification...")

    verifier = TrainingPhotoVerifier(api_key=cfg.CXAI_API_KEY)
    result = verifier.verify(img=img, skip_vision=skip_vision)

    return result.to_dict()


def _uc2_batch(job_id: str, **params: Any) -> dict:
    """Run batch PDF verification.

    Expected params:
        pdf_paths: list[str]    — list of paths to CC training PDFs
    """
    from usecase2_photo_verification import (
        PDFExtractor, TrainingPhotoVerifier, PDFIdentifiers,
    )

    pdf_paths = [Path(p) for p in params["pdf_paths"]]
    total = len(pdf_paths)

    extractor = PDFExtractor()
    verifier = TrainingPhotoVerifier(api_key=cfg.CXAI_API_KEY)

    results = []
    for i, pdf_path in enumerate(pdf_paths):
        pct = int((i / total) * 90) + 5
        job_manager.update_progress(job_id, pct, f"Verifying {i + 1}/{total}: {pdf_path.name}")

        ids = PDFIdentifiers.from_filename(pdf_path.name)
        try:
            img = extractor.extract_photo(pdf_path)
            vresult = verifier.verify(img=img, skip_vision=False)
            results.append({
                "file": str(pdf_path),
                "lid": ids.lid if ids else "",
                "decision": vresult.decision,
                "data": vresult.to_dict(),
            })
        except Exception as exc:
            results.append({
                "file": str(pdf_path),
                "lid": ids.lid if ids else "",
                "decision": "ERROR",
                "error": str(exc),
            })

    accepted = sum(1 for r in results if r["decision"] == "ACCEPT")
    rejected = sum(1 for r in results if r["decision"] == "REJECT")
    errors = sum(1 for r in results if r["decision"] == "ERROR")

    return {
        "total": total,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "results": results,
    }


# ═══════════════════════════════════════════════════════════════════════
# UC1 — SEMANTIC ANALYSIS (standalone job on existing extraction data)
# ═══════════════════════════════════════════════════════════════════════


def _uc1_semantic(job_id: str, **params: Any) -> dict:
    """Run semantic analysis on an existing extraction result.

    Expected params:
        extraction_data: dict   — the merged_extraction dict from a completed UC1 job
    """
    from usecase1_land_record_ocr import SemanticAnalyzer

    extraction_data = params["extraction_data"]

    job_manager.update_progress(job_id, 10, "Starting semantic analysis...")

    analyzer = SemanticAnalyzer(api_key=cfg.CXAI_API_KEY)
    semantic_result, elapsed = analyzer.analyze(extraction_data)

    job_manager.update_progress(job_id, 90, "Analysis complete")

    return {
        "semantic_knowledge_graph": semantic_result,
        "elapsed_seconds": round(elapsed, 2),
    }


# ═══════════════════════════════════════════════════════════════════════
# REGISTRATION (called at app startup)
# ═══════════════════════════════════════════════════════════════════════


def register_all_job_types() -> None:
    """Register all job types with the job engine."""
    register_job_type("uc1.extract", _uc1_extract)
    register_job_type("uc1.batch", _uc1_batch)
    register_job_type("uc1.semantic", _uc1_semantic)
    register_job_type("uc2.verify", _uc2_verify)
    register_job_type("uc2.batch", _uc2_batch)
