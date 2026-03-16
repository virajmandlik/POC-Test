"""
Use Case 2 — Carbon Credit Training Photo Verification

Self-contained Streamlit app for verifying CC training session photos.
Supports single photo upload and batch PDF processing with live results,
queue system, CSV export, and individual result inspection.

Core pipeline:
  1. Image quality check (blur, brightness, contrast via OpenCV)
  2. Scene + metadata analysis (people, training context, GPS overlay via GPT-4 Vision)
  3. Accept / Reject decision

Run:
    streamlit run verify_ui.py
"""

import base64
import csv
import io
import json
import logging
import os
import re
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any

import cv2
import numpy as np
import pandas as pd
import pypdfium2 as pdfium
import requests
import streamlit as st
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# ── Job system + audit integration ────────────────────────────────
try:
    from lib.config import cfg
    from lib.audit import audit_log
    from lib.job_types import register_all_job_types
    from lib.jobs import job_manager
    register_all_job_types()
    _HAS_JOB_SYSTEM = True
except Exception:
    _HAS_JOB_SYSTEM = False

log = logging.getLogger("cc_verify")

VISION_API_URL = "https://cxai-playground.cisco.com/chat/completions"

# PDF filename pattern: {surrogate_key}-{FID}-{LID}.pdf
_FILENAME_RE = re.compile(
    r"^(?P<surrogate>\d+)-(?P<fid>[^-]+)-(?P<lid>[^.]+)\.pdf$",
    re.IGNORECASE,
)

_TRAINING_PHOTO_PAGE = 2

CSV_COLUMNS = [
    "lid", "fid", "surrogate_key", "filename", "decision",
    "image_quality_passed", "blur_score", "sharpness", "brightness", "contrast",
    "scene_analysis_passed", "people_count", "has_multiple_people",
    "has_representative", "is_training_scene", "is_outdoor_rural",
    "overlay_latitude", "overlay_longitude", "overlay_date", "overlay_time",
    "scene_description", "confidence",
    "metadata_passed", "metadata_gps_lat", "metadata_gps_lon", "metadata_timestamp",
    "rejection_reasons", "processing_time_ms", "processed_at_utc",
]


# ═══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class VerificationResult:
    decision: str  # ACCEPT | REJECT
    checks: dict[str, CheckResult] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    processing_time_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "checks": {
                name: {"passed": c.passed, **c.details}
                for name, c in self.checks.items()
            },
            "rejection_reasons": self.rejection_reasons,
            "metadata": {"processing_time_ms": self.processing_time_ms},
        }


@dataclass(frozen=True)
class PDFIdentifiers:
    """Parsed identifiers from a CC training PDF filename."""
    surrogate_key: str
    fid: str
    lid: str
    filename: str

    @classmethod
    def from_filename(cls, filename: str) -> "PDFIdentifiers | None":
        match = _FILENAME_RE.match(filename)
        if not match:
            return None
        return cls(
            surrogate_key=match.group("surrogate"),
            fid=match.group("fid"),
            lid=match.group("lid"),
            filename=filename,
        )


@dataclass
class VerificationJob:
    """One unit of work in the processing queue."""
    pdf_path: Path
    identifiers: PDFIdentifiers
    status: str = "pending"
    result: VerificationResult | None = None
    error: str = ""
    image: Image.Image | None = None


# ═══════════════════════════════════════════════════════════════════════
# IMAGE QUALITY CHECKER (OpenCV)
# ═══════════════════════════════════════════════════════════════════════


class ImageQualityChecker:
    """Checks blur, brightness, and contrast using OpenCV."""

    MIN_BLUR_SCORE = 80.0
    MIN_BRIGHTNESS = 40.0
    MAX_BRIGHTNESS = 230.0
    MIN_CONTRAST = 0.15

    def check(self, img: Image.Image) -> CheckResult:
        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(np.mean(gray))
        std = float(np.std(gray))
        contrast = std / max(brightness, 1)

        if blur > 500:
            sharpness = "Excellent"
        elif blur > 200:
            sharpness = "Good"
        elif blur > self.MIN_BLUR_SCORE:
            sharpness = "Acceptable"
        else:
            sharpness = "Blurry"

        issues: list[str] = []
        if blur < self.MIN_BLUR_SCORE:
            issues.append("Image is blurry — retake with steadier hand")
        if brightness < self.MIN_BRIGHTNESS:
            issues.append("Image too dark — ensure adequate lighting")
        if brightness > self.MAX_BRIGHTNESS:
            issues.append("Image overexposed — reduce brightness")
        if contrast < self.MIN_CONTRAST:
            issues.append("Low contrast — poor visibility")

        return CheckResult(
            name="image_quality",
            passed=len(issues) == 0,
            details={
                "blur_score": round(blur, 1),
                "sharpness": sharpness,
                "mean_brightness": round(brightness, 1),
                "contrast_ratio": round(contrast, 3),
            },
            reason="; ".join(issues) if issues else "",
        )


# ═══════════════════════════════════════════════════════════════════════
# SCENE ANALYZER (GPT-4 Vision)
# ═══════════════════════════════════════════════════════════════════════


class SceneAnalyzer:
    """Uses GPT-4 Vision to analyze whether the photo depicts a valid
    carbon credit training session and extract GPS/timestamp overlays."""

    SYSTEM_PROMPT = (
        "You are a field verification analyst for a carbon credit agroforestry programme. "
        "You must analyze photos submitted as evidence of farmer training sessions.\n\n"
        "IMPORTANT: Many field photos use apps like 'GPS Map Camera Lite' which burn "
        "GPS coordinates, timestamps, and other metadata as a visible text overlay / watermark "
        "at the bottom of the image. You MUST read and extract this overlay text if present.\n\n"
        "Analyze the image and respond with ONLY valid JSON (no markdown fences). "
        "Use this exact structure:\n"
        "{\n"
        '  "people_count": <int>,\n'
        '  "has_multiple_people": <bool>,\n'
        '  "has_representative": <bool>,\n'
        '  "is_training_scene": <bool>,\n'
        '  "is_outdoor_rural": <bool>,\n'
        '  "has_visible_timestamp": <bool>,\n'
        '  "overlay_latitude": <float or null>,\n'
        '  "overlay_longitude": <float or null>,\n'
        '  "overlay_date": "<DD.MM.YYYY or YYYY-MM-DD string or null>",\n'
        '  "overlay_time": "<HH:MM:SS string or null>",\n'
        '  "scene_description": "<one sentence>",\n'
        '  "confidence": <float 0-1>\n'
        "}"
    )

    USER_PROMPT = (
        "Analyze this photo for carbon credit training session verification:\n"
        "1. How many people are visible in the photo?\n"
        "2. Are there at least two people — one who could be a farmer and one who "
        "could be a field representative (wearing uniform, ID badge, carrying clipboard/tablet)?\n"
        "3. Does this look like an outdoor/rural training or meeting session "
        "(not a selfie, not indoors in a city)?\n"
        "4. Is there any visible text overlay, watermark, or GPS stamp on the image?\n"
        "5. If there IS a GPS/location overlay (like from 'GPS Map Camera' app), "
        "extract the exact latitude and longitude numbers shown.\n"
        "6. If there IS a date/time overlay, extract the exact date and time shown.\n"
        "7. Give a brief one-sentence description of the scene.\n"
        "8. How confident are you in this assessment (0.0 to 1.0)?"
    )

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("CXAI_API_KEY", "")

    def analyze(self, img: Image.Image) -> CheckResult:
        if not self._api_key:
            return CheckResult(
                name="scene_analysis",
                passed=False,
                details={"error": "CXAI_API_KEY not configured"},
                reason="Vision API key not available",
            )

        img_b64 = self._pil_to_base64(img)

        try:
            response = self._call_vision(img_b64)
        except requests.exceptions.RequestException as exc:
            log.error("Vision API call failed: %s", exc)
            return CheckResult(
                name="scene_analysis", passed=False,
                details={"error": str(exc)},
                reason=f"Vision API unavailable: {exc}",
            )

        parsed = self._parse_response(response)
        if "error" in parsed:
            return CheckResult(
                name="scene_analysis", passed=False,
                details=parsed,
                reason=f"Could not parse Vision API response: {parsed['error']}",
            )

        has_multiple = parsed.get("has_multiple_people", False)
        has_rep = parsed.get("has_representative", False)
        is_training = parsed.get("is_training_scene", False)
        passed = has_multiple and is_training

        issues: list[str] = []
        if not has_multiple:
            issues.append(
                f"Need at least 2 people; found {parsed.get('people_count', 0)}"
            )
        if not has_rep:
            issues.append("No identifiable F4F representative in the frame")
        if not is_training:
            issues.append("Scene does not appear to be a training session")

        return CheckResult(
            name="scene_analysis",
            passed=passed,
            details={
                "people_count": parsed.get("people_count", 0),
                "has_multiple_people": has_multiple,
                "has_representative": has_rep,
                "is_training_scene": is_training,
                "is_outdoor_rural": parsed.get("is_outdoor_rural", False),
                "has_visible_timestamp": parsed.get("has_visible_timestamp", False),
                "overlay_latitude": parsed.get("overlay_latitude"),
                "overlay_longitude": parsed.get("overlay_longitude"),
                "overlay_date": parsed.get("overlay_date"),
                "overlay_time": parsed.get("overlay_time"),
                "scene_description": parsed.get("scene_description", ""),
                "confidence": parsed.get("confidence", 0),
            },
            reason="; ".join(issues) if issues else "",
        )

    @staticmethod
    def _pil_to_base64(img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _call_vision(self, image_b64: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        payload = {
            "model": "gpt-4-vision-playground",
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.USER_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                            },
                        },
                    ],
                },
            ],
        }
        resp = requests.post(VISION_API_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _parse_response(response: dict) -> dict:
        content = ""
        if "choices" in response and response["choices"]:
            content = response["choices"][0].get("message", {}).get("content", "")

        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON from Vision API", "raw": content[:500]}


# ═══════════════════════════════════════════════════════════════════════
# TRAINING PHOTO VERIFIER (Orchestrator)
# ═══════════════════════════════════════════════════════════════════════


class TrainingPhotoVerifier:
    """Runs image quality + GPT Vision checks and produces accept/reject."""

    def __init__(self, api_key: str | None = None):
        self.quality_checker = ImageQualityChecker()
        self.scene_analyzer = SceneAnalyzer(api_key=api_key)

    def verify(self, img: Image.Image, skip_vision: bool = False) -> VerificationResult:
        t0 = time.perf_counter()
        checks: dict[str, CheckResult] = {}
        rejection_reasons: list[str] = []

        qc = self.quality_checker.check(img)
        checks["image_quality"] = qc
        if not qc.passed:
            rejection_reasons.append(f"Quality: {qc.reason}")

        if not skip_vision:
            scene = self.scene_analyzer.analyze(img)
            checks["scene_analysis"] = scene
            if not scene.passed:
                rejection_reasons.append(f"Scene: {scene.reason}")

            overlay_lat = scene.details.get("overlay_latitude")
            overlay_lon = scene.details.get("overlay_longitude")
            overlay_date = scene.details.get("overlay_date")
            overlay_time = scene.details.get("overlay_time")

            has_gps = (
                overlay_lat is not None
                and overlay_lon is not None
                and isinstance(overlay_lat, (int, float))
                and isinstance(overlay_lon, (int, float))
            )
            has_ts = bool(overlay_date)

            gps_detail = (
                {"lat": float(overlay_lat), "lon": float(overlay_lon)}
                if has_gps else None
            )
            ts_detail = (
                f"{overlay_date} {overlay_time or ''}".strip()
                if has_ts else None
            )

            metadata_passed = has_gps and has_ts
            meta_issues: list[str] = []
            if not has_gps:
                meta_issues.append("No GPS coordinates found in photo overlay")
            if not has_ts:
                meta_issues.append("No date/time found in photo overlay")

            checks["metadata"] = CheckResult(
                name="metadata",
                passed=metadata_passed,
                details={
                    "gps": gps_detail,
                    "timestamp": ts_detail,
                    "source": "GPT Vision overlay extraction",
                },
                reason="; ".join(meta_issues) if meta_issues else "",
            )
            if not metadata_passed:
                rejection_reasons.append(f"Metadata: {'; '.join(meta_issues)}")

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        decision = "ACCEPT" if len(rejection_reasons) == 0 else "REJECT"

        return VerificationResult(
            decision=decision,
            checks=checks,
            rejection_reasons=rejection_reasons,
            processing_time_ms=elapsed_ms,
        )


# ═══════════════════════════════════════════════════════════════════════
# PDF EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════


class PDFExtractor:
    """Extracts the training photo (last page) from a CC training PDF."""

    def __init__(self, photo_page_index: int = _TRAINING_PHOTO_PAGE, render_scale: float = 2.0):
        self._page_index = photo_page_index
        self._scale = render_scale

    def extract_photo(self, pdf_path: Path) -> Image.Image:
        doc = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(doc)
        if total_pages == 0:
            raise ValueError(f"PDF has no pages: {pdf_path.name}")

        page_idx = min(self._page_index, total_pages - 1)
        page = doc[page_idx]
        bitmap = page.render(scale=self._scale)
        pil_image = bitmap.to_pil().convert("RGB")
        log.info("Extracted page %d from %s (%dx%d)", page_idx, pdf_path.name, pil_image.width, pil_image.height)
        return pil_image


# ═══════════════════════════════════════════════════════════════════════
# RESULT STORE (CSV persistence with Windows file-lock retry)
# ═══════════════════════════════════════════════════════════════════════


class ResultStore(ABC):
    @abstractmethod
    def save(self, job: VerificationJob) -> None: ...

    @abstractmethod
    def save_batch(self, jobs: list[VerificationJob]) -> None: ...


class CSVResultStore(ResultStore):
    """Appends verification results to a CSV file with retry on PermissionError."""

    def __init__(self, csv_path: Path):
        self._path = csv_path
        self._ensure_headers()

    def _ensure_headers(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists() or self._path.stat().st_size == 0:
            self._write_with_retry(self._path, mode="w", rows=None, header=True)

    def save(self, job: VerificationJob) -> None:
        row = self._job_to_row(job)
        self._write_with_retry(self._path, mode="a", rows=[row], header=False)

    def save_batch(self, jobs: list[VerificationJob]) -> None:
        rows = [self._job_to_row(j) for j in jobs]
        self._write_with_retry(self._path, mode="a", rows=rows, header=False)

    def _write_with_retry(
        self, path: Path, mode: str, rows: list[dict] | None, header: bool,
        retries: int = 3, delay: float = 1.0,
    ):
        for attempt in range(retries):
            try:
                with open(path, mode, newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                    if header:
                        writer.writeheader()
                    if rows:
                        writer.writerows(rows)
                return
            except PermissionError:
                if attempt < retries - 1:
                    log.warning("CSV locked (%s), retrying in %.0fs... (%d/%d)", path.name, delay, attempt + 1, retries)
                    time.sleep(delay)
                else:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    alt = path.with_stem(f"{path.stem}_{ts}")
                    log.warning("CSV still locked — writing to %s instead", alt.name)
                    self._path = alt
                    self._ensure_headers()
                    if rows:
                        with open(alt, "a", newline="", encoding="utf-8") as f:
                            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerows(rows)

    @staticmethod
    def _job_to_row(job: VerificationJob) -> dict[str, Any]:
        ids = job.identifiers
        row: dict[str, Any] = {
            "lid": ids.lid,
            "fid": ids.fid,
            "surrogate_key": ids.surrogate_key,
            "filename": ids.filename,
            "processed_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        if job.status == "failed" or job.result is None:
            row["decision"] = "ERROR"
            row["rejection_reasons"] = job.error
            row["processing_time_ms"] = 0
            return row

        r = job.result
        row["decision"] = r.decision
        row["processing_time_ms"] = r.processing_time_ms
        row["rejection_reasons"] = "; ".join(r.rejection_reasons)

        qc = r.checks.get("image_quality")
        if qc:
            row["image_quality_passed"] = qc.passed
            row["blur_score"] = qc.details.get("blur_score")
            row["sharpness"] = qc.details.get("sharpness")
            row["brightness"] = qc.details.get("mean_brightness")
            row["contrast"] = qc.details.get("contrast_ratio")

        sc = r.checks.get("scene_analysis")
        if sc:
            row["scene_analysis_passed"] = sc.passed
            row["people_count"] = sc.details.get("people_count")
            row["has_multiple_people"] = sc.details.get("has_multiple_people")
            row["has_representative"] = sc.details.get("has_representative")
            row["is_training_scene"] = sc.details.get("is_training_scene")
            row["is_outdoor_rural"] = sc.details.get("is_outdoor_rural")
            row["overlay_latitude"] = sc.details.get("overlay_latitude")
            row["overlay_longitude"] = sc.details.get("overlay_longitude")
            row["overlay_date"] = sc.details.get("overlay_date")
            row["overlay_time"] = sc.details.get("overlay_time")
            row["scene_description"] = sc.details.get("scene_description")
            row["confidence"] = sc.details.get("confidence")

        meta = r.checks.get("metadata")
        if meta:
            row["metadata_passed"] = meta.passed
            gps = meta.details.get("gps")
            if gps:
                row["metadata_gps_lat"] = gps.get("lat")
                row["metadata_gps_lon"] = gps.get("lon")
            row["metadata_timestamp"] = meta.details.get("timestamp")

        return row


# ═══════════════════════════════════════════════════════════════════════
# BATCH PROCESSOR (Queue-based orchestrator)
# ═══════════════════════════════════════════════════════════════════════


class BatchProcessor:
    """Processes CC training PDFs through the verification pipeline sequentially."""

    def __init__(self, extractor: PDFExtractor, verifier: TrainingPhotoVerifier, store: ResultStore):
        self._extractor = extractor
        self._verifier = verifier
        self._store = store
        self._queue: Queue[VerificationJob] = Queue()
        self._completed: list[VerificationJob] = []

    @property
    def completed_jobs(self) -> list[VerificationJob]:
        return list(self._completed)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def enqueue_file(self, pdf_path: Path) -> VerificationJob | None:
        ids = PDFIdentifiers.from_filename(pdf_path.name)
        if ids is None:
            log.warning("Skipping %s — filename does not match expected pattern", pdf_path.name)
            return None
        job = VerificationJob(pdf_path=pdf_path, identifiers=ids)
        self._queue.put(job)
        return job

    def enqueue_folder(self, folder: Path) -> list[VerificationJob]:
        jobs = []
        for pdf_path in sorted(folder.glob("*.pdf")):
            job = self.enqueue_file(pdf_path)
            if job:
                jobs.append(job)
        log.info("Enqueued %d PDFs from %s", len(jobs), folder)
        return jobs

    def process_all(self, callback=None) -> list[VerificationJob]:
        total = self._queue.qsize()
        index = 0
        while not self._queue.empty():
            job = self._queue.get()
            index += 1
            job.status = "processing"
            log.info("[%d/%d] Processing %s", index, total, job.pdf_path.name)
            try:
                job.image = self._extractor.extract_photo(job.pdf_path)
                job.result = self._verifier.verify(img=job.image, skip_vision=False)
                job.status = "completed"
            except Exception as exc:
                log.error("Failed to process %s: %s", job.pdf_path.name, exc)
                job.status = "failed"
                job.error = str(exc)

            self._store.save(job)
            self._completed.append(job)
            if callback:
                callback(job, index, total)
        return self._completed


class PipelineFactory:
    """Factory for creating configured pipeline instances."""

    @staticmethod
    def create(
        api_key: str | None = None,
        output_csv: str = "output/cc_verification_results.csv",
        photo_page_index: int = _TRAINING_PHOTO_PAGE,
        render_scale: float = 2.0,
    ) -> BatchProcessor:
        key = api_key or os.environ.get("CXAI_API_KEY", "")
        extractor = PDFExtractor(photo_page_index=photo_page_index, render_scale=render_scale)
        verifier = TrainingPhotoVerifier(api_key=key)
        store = CSVResultStore(csv_path=Path(output_csv))
        return BatchProcessor(extractor=extractor, verifier=verifier, store=store)


# ═══════════════════════════════════════════════════════════════════════
# STREAMLIT UI — HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _render_check_card(label: str, passed: bool, details: dict, reason: str):
    if passed:
        st.success(f"**{label}: PASS**")
    else:
        st.error(f"**{label}: FAIL**")
        if reason:
            st.markdown(f"> {reason}")

    if details:
        cols = st.columns(min(len(details), 4))
        for i, (key, val) in enumerate(details.items()):
            display_key = key.replace("_", " ").title()
            if isinstance(val, dict):
                cols[i % len(cols)].json(val)
            elif isinstance(val, float):
                cols[i % len(cols)].metric(display_key, f"{val:.2f}")
            elif isinstance(val, bool):
                cols[i % len(cols)].metric(display_key, "Yes" if val else "No")
            else:
                cols[i % len(cols)].metric(display_key, str(val) if val is not None else "N/A")


# ═══════════════════════════════════════════════════════════════════════
# STREAMLIT UI — SINGLE PHOTO MODE
# ═══════════════════════════════════════════════════════════════════════


def _single_photo_mode(api_key: str):
    """Upload one photo, run verification step-by-step."""
    for key in ("result", "img"):
        if key not in st.session_state:
            st.session_state[key] = None

    tab_upload, tab_checks, tab_scene, tab_verdict = st.tabs([
        "1. Upload Photo",
        "2. Quality Check",
        "3. Scene & Metadata (GPT Vision)",
        "4. Final Verdict",
    ])

    with tab_upload:
        st.subheader("Upload Training Photo")
        col_upload, col_opts = st.columns([3, 1])

        with col_upload:
            uploaded = st.file_uploader(
                "Upload JPEG/PNG photo from field",
                type=["jpg", "jpeg", "png", "webp"],
                key="cc_upload",
            )
            if uploaded:
                img = Image.open(io.BytesIO(uploaded.read())).convert("RGB")
                st.session_state.img = img
                st.image(img, caption=f"{uploaded.name} ({img.width}x{img.height})", use_container_width=True)
            elif st.session_state.img:
                st.image(st.session_state.img, caption="Previously uploaded", use_container_width=True)

        with col_opts:
            skip_vision = st.checkbox(
                "Skip GPT Vision analysis (offline mode)",
                value=not bool(api_key),
                help="Check this if you don't have API access or want faster results",
            )
            if not api_key and not skip_vision:
                st.warning("CXAI_API_KEY not set. Vision analysis will fail.")

        if st.session_state.img is not None:
            if st.button("Run All Verification Checks", type="primary", use_container_width=True):
                verifier = TrainingPhotoVerifier(api_key=api_key)
                with st.spinner("Running verification pipeline..."):
                    result = verifier.verify(img=st.session_state.img, skip_vision=skip_vision)
                st.session_state.result = result
                st.success(f"Verification complete in {result.processing_time_ms} ms. Check other tabs for details.")
        else:
            st.info("Upload a photo to begin verification.")

    result: VerificationResult | None = st.session_state.result

    with tab_checks:
        st.subheader("Image Quality")
        if not result:
            st.warning("Run verification from Tab 1 first.")
            return
        qc = result.checks.get("image_quality")
        if qc:
            _render_check_card("Image Quality", qc.passed, qc.details, qc.reason)

    with tab_scene:
        st.subheader("GPT-4 Vision — Scene & Metadata Analysis")
        if not result:
            st.warning("Run verification from Tab 1 first.")
            return

        scene = result.checks.get("scene_analysis")
        if not scene:
            st.info("Scene analysis was skipped (offline mode).")
        elif "error" in scene.details:
            st.error(f"Vision API error: {scene.details.get('error')}")
        else:
            _render_check_card("Scene Analysis", scene.passed, scene.details, scene.reason)
            desc = scene.details.get("scene_description", "")
            if desc:
                st.markdown(f"**Scene Description:** {desc}")

        st.divider()

        meta = result.checks.get("metadata")
        if not meta:
            st.info("Metadata extraction was skipped (offline mode).")
        else:
            _render_check_card("GPS & Timestamp (from overlay)", meta.passed, meta.details, meta.reason)
            gps = meta.details.get("gps")
            if gps and gps.get("lat") is not None:
                st.markdown("**Photo GPS Location**")
                map_df = pd.DataFrame({"lat": [gps["lat"]], "lon": [gps["lon"]]})
                st.map(map_df, zoom=12)

        if st.session_state.img:
            st.divider()
            st.image(st.session_state.img, caption="Analyzed photo", use_container_width=True)

    with tab_verdict:
        st.subheader("Final Verification Verdict")
        if not result:
            st.warning("Run verification from Tab 1 first.")
            return

        if result.decision == "ACCEPT":
            st.success("## ACCEPTED")
            st.markdown("This training session photo **meets all verification criteria**.")
            st.balloons()
        else:
            st.error("## REJECTED")
            st.markdown("This training session photo **failed verification**.")

        st.divider()
        st.markdown("### Check Summary")
        for name, check in result.checks.items():
            label = name.replace("_", " ").title()
            if check.passed:
                st.markdown(f"- **{label}**: PASS")
            else:
                st.markdown(f"- **{label}**: FAIL — {check.reason}")

        if result.rejection_reasons:
            st.divider()
            st.markdown("### Rejection Reasons")
            for reason in result.rejection_reasons:
                st.markdown(f"- {reason}")

        st.divider()
        st.metric("Processing Time", f"{result.processing_time_ms} ms")
        st.divider()
        st.markdown("### Raw API Response")
        st.json(result.to_dict())


# ═══════════════════════════════════════════════════════════════════════
# STREAMLIT UI — BATCH PDF MODE
# ═══════════════════════════════════════════════════════════════════════


def _run_batch(pdf_sources: list[tuple[str, "Path | bytes"]], api_key: str):
    """Process all PDFs with a live-updating results table."""
    extractor = PDFExtractor()
    verifier = TrainingPhotoVerifier(api_key=api_key)
    output_path = Path("output/cc_verification_results.csv")
    store = CSVResultStore(csv_path=output_path)
    processor = BatchProcessor(extractor=extractor, verifier=verifier, store=store)

    tmp_dir = Path(tempfile.mkdtemp(prefix="cc_batch_"))
    file_paths: list[Path] = []
    for name, source in pdf_sources:
        if isinstance(source, Path):
            file_paths.append(source)
        else:
            dest = tmp_dir / name
            dest.write_bytes(source)
            file_paths.append(dest)

    for fp in file_paths:
        processor.enqueue_file(fp)

    total = processor.queue_size
    progress_bar = st.progress(0, text=f"Processing 0/{total}...")

    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_total = m_col1.empty()
    m_accept = m_col2.empty()
    m_reject = m_col3.empty()
    m_error = m_col4.empty()

    table_placeholder = st.empty()
    live_rows: list[dict] = []
    completed_jobs: list[VerificationJob] = []

    def on_complete(job: VerificationJob, index: int, total: int):
        decision = job.result.decision if job.result else "ERROR"
        progress_bar.progress(
            index / total,
            text=f"Processing {index}/{total} — {job.identifiers.lid}: {decision}",
        )
        row = CSVResultStore._job_to_row(job)
        live_rows.append(row)
        completed_jobs.append(job)

        df = pd.DataFrame(live_rows)
        accepted = len(df[df["decision"] == "ACCEPT"])
        rejected = len(df[df["decision"] == "REJECT"])
        errors = len(df[df["decision"] == "ERROR"])

        m_total.metric("Processed", f"{index}/{total}")
        m_accept.metric("Accepted", accepted)
        m_reject.metric("Rejected", rejected)
        m_error.metric("Errors", errors)

        display_cols = [
            "lid", "decision", "people_count", "has_multiple_people",
            "is_training_scene", "overlay_latitude", "overlay_longitude",
            "scene_description",
        ]
        available = [c for c in display_cols if c in df.columns]
        table_placeholder.dataframe(
            df[available], use_container_width=True,
            height=min(400, 50 + 35 * len(df)),
        )

    processor.process_all(callback=on_complete)
    progress_bar.progress(1.0, text=f"Done — {total} PDFs processed")

    df = pd.DataFrame(live_rows)
    st.session_state.results_df = df
    st.session_state.jobs = completed_jobs

    st.success(
        f"Batch complete: **{len(df[df['decision'] == 'ACCEPT'])}** accepted, "
        f"**{len(df[df['decision'] == 'REJECT'])}** rejected. "
        f"Scroll down for full results and CSV download."
    )


def _render_results():
    """Render the final results with filtering, CSV download, and inspection."""
    st.subheader("Verification Results")

    df = st.session_state.results_df

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total", len(df))
    col2.metric("Accepted", len(df[df["decision"] == "ACCEPT"]))
    col3.metric("Rejected", len(df[df["decision"] == "REJECT"]))
    col4.metric("Errors", len(df[df["decision"] == "ERROR"]) if "decision" in df.columns else 0)

    st.divider()

    filter_decision = st.multiselect(
        "Filter by decision",
        options=["ACCEPT", "REJECT", "ERROR"],
        default=["ACCEPT", "REJECT", "ERROR"],
    )
    filtered = df[df["decision"].isin(filter_decision)]
    st.dataframe(filtered, use_container_width=True, height=400)

    st.divider()

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    st.download_button(
        label="Download Full Results CSV",
        data=csv_buffer.getvalue(),
        file_name="cc_verification_results.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.divider()
    st.subheader("Inspect Individual Results")

    jobs = st.session_state.jobs
    if jobs:
        for job in jobs:
            decision = job.result.decision if job.result else "ERROR"
            icon = "+" if decision == "ACCEPT" else "-"
            with st.expander(f"[{icon}] {job.identifiers.lid} -- {decision}"):
                if job.image:
                    st.image(job.image, caption=job.identifiers.filename, width=400)
                if job.result:
                    st.json(job.result.to_dict())
                if job.error:
                    st.error(job.error)


def _batch_pdf_mode(api_key: str):
    """Upload or scan PDFs for batch verification."""
    for key in ("jobs", "results_df", "pdf_sources"):
        if key not in st.session_state:
            st.session_state[key] = None

    col_upload, col_folder = st.columns(2)

    with col_upload:
        st.subheader("Upload PDFs")
        uploaded_files = st.file_uploader(
            "Upload one or more CC training PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            key="batch_upload",
        )

    with col_folder:
        st.subheader("Or scan a local folder")
        folder_path = st.text_input(
            "Folder path containing PDFs",
            value="cc_data_final",
            help="Relative or absolute path to a folder with CC training PDFs",
        )
        if st.button("Scan Folder"):
            fp = Path(folder_path)
            if fp.is_dir():
                found = []
                for p in sorted(fp.glob("*.pdf")):
                    if PDFIdentifiers.from_filename(p.name):
                        found.append((p.name, p))
                st.session_state.pdf_sources = found
                st.session_state.results_df = None
                st.session_state.jobs = None
            else:
                st.error(f"Folder not found: `{folder_path}`")

    if uploaded_files:
        upload_list = []
        for uf in uploaded_files:
            upload_list.append((uf.name, uf.read()))
        st.session_state.pdf_sources = upload_list

    st.divider()

    pdf_sources = st.session_state.pdf_sources
    if pdf_sources:
        st.success(f"**{len(pdf_sources)} PDFs** ready for processing")
        if st.button("Process All PDFs", type="primary", use_container_width=True):
            _run_batch(pdf_sources, api_key)

    if st.session_state.results_df is not None:
        st.divider()
        _render_results()


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# BACKGROUND JOB SUBMISSION (optional — only when job system is available)
# ═══════════════════════════════════════════════════════════════════════


def _render_job_sidebar():
    """Show background job submission in sidebar when job system is available."""
    if not _HAS_JOB_SYSTEM:
        return

    with st.sidebar:
        st.markdown("### Background Jobs")
        st.caption("Submit verification as a tracked background job")

        job_user = st.text_input(
            "Your name (for audit)",
            value=os.environ.get("USER", os.environ.get("USERNAME", "user")),
            key="sidebar_job_user_uc2",
        )
        job_file = st.text_input(
            "Image path (in uploads/)",
            placeholder="uploads/photo.jpg",
            key="sidebar_job_file_uc2",
        )

        if st.button("Submit as Background Job", key="sidebar_submit_job_uc2"):
            if not job_file:
                st.warning("Enter a file path.")
            else:
                try:
                    job_id = job_manager.submit(
                        job_type="uc2.verify",
                        params={"image_path": job_file, "skip_vision": False},
                        user=job_user,
                    )
                    st.success(f"Job submitted: `{job_id}`")
                except Exception as exc:
                    st.error(f"Failed: {exc}")

        # Show recent jobs
        st.divider()
        st.markdown("### Recent Jobs")
        try:
            recent = job_manager.list_jobs(job_type="uc2.verify", limit=5)
            for j in recent:
                status_icon = {
                    "completed": "+", "failed": "-", "running": "~",
                    "pending": ".", "cancelled": "x",
                }.get(j["status"], "?")
                st.markdown(
                    f"`[{status_icon}]` **{j['status']}** {j['progress']}% "
                    f"— {j.get('progress_message', '')[:40]}"
                )
        except Exception:
            st.caption("Could not load recent jobs.")


def main():
    st.set_page_config(
        page_title="CC Training Photo Verification",
        page_icon="🌳",
        layout="wide",
    )

    st.markdown(
        "<h1 style='text-align:center;'>Carbon Credit Training — Photo Verification</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Single photo or batch PDF verification &rarr; Accept / Reject"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    api_key = os.environ.get("CXAI_API_KEY", "")
    if not api_key:
        st.error("**CXAI_API_KEY** not found in `.env`. GPT Vision analysis requires this key.")
        return

    # Render background job sidebar
    _render_job_sidebar()

    mode = st.radio(
        "Verification Mode",
        ["Single Photo", "Batch PDFs"],
        horizontal=True,
        key="uc2_mode",
    )

    st.divider()

    if _HAS_JOB_SYSTEM:
        audit_log("uc2.page_loaded", user=os.environ.get("USER", "unknown"))

    if mode == "Single Photo":
        _single_photo_mode(api_key)
    else:
        _batch_pdf_mode(api_key)


if __name__ == "__main__":
    main()
