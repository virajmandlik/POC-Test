"""
Carbon Credit Training — Batch PDF Verification Pipeline

End-to-end pipeline that processes CC training PDFs:
  1. Extract the training photo (page 3) from each PDF
  2. Run image quality + GPT Vision checks
  3. Store results in CSV

Architecture (SOLID):
  - PDFExtractor:      Single Responsibility — extracts images from PDF pages
  - TrainingPhotoVerifier: from cc_verify — handles verification logic
  - VerificationJob:   Value object representing one unit of work
  - ResultStore:       Handles CSV persistence (append-safe, atomic writes)
  - BatchProcessor:    Orchestrates the queue, processes jobs sequentially
  - PipelineFactory:   Creates configured pipeline instances

Usage:
    from cc_pipeline import PipelineFactory

    pipeline = PipelineFactory.create(api_key="...", output_csv="results.csv")
    pipeline.process_folder("cc_data_final/")
"""

import csv
import io
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any

import pypdfium2 as pdfium
from dotenv import load_dotenv
from PIL import Image

from cc_verify import (
    CheckResult,
    ImageQualityChecker,
    SceneAnalyzer,
    TrainingPhotoVerifier,
    VerificationResult,
)

load_dotenv()

log = logging.getLogger("cc_pipeline")

# PDF filename pattern: {surrogate_key}-{FID}-{LID}.pdf
_FILENAME_RE = re.compile(
    r"^(?P<surrogate>\d+)-(?P<fid>[^-]+)-(?P<lid>[^.]+)\.pdf$",
    re.IGNORECASE,
)

# Training photo is always the last page (page 3, index 2)
_TRAINING_PHOTO_PAGE = 2

# CSV column order
CSV_COLUMNS = [
    "lid",
    "fid",
    "surrogate_key",
    "filename",
    "decision",
    "image_quality_passed",
    "blur_score",
    "sharpness",
    "brightness",
    "contrast",
    "scene_analysis_passed",
    "people_count",
    "has_multiple_people",
    "has_representative",
    "is_training_scene",
    "is_outdoor_rural",
    "overlay_latitude",
    "overlay_longitude",
    "overlay_date",
    "overlay_time",
    "scene_description",
    "confidence",
    "metadata_passed",
    "metadata_gps_lat",
    "metadata_gps_lon",
    "metadata_timestamp",
    "rejection_reasons",
    "processing_time_ms",
    "processed_at_utc",
]


# ── Value Objects ─────────────────────────────────────────────────────


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
    status: str = "pending"  # pending | processing | completed | failed
    result: VerificationResult | None = None
    error: str = ""
    image: Image.Image | None = None


# ── PDF Extractor (Single Responsibility) ─────────────────────────────


class PDFExtractor:
    """Extracts the training photo from a CC training PDF.

    Each PDF has 3 pages: meeting minutes, attendee list, and the
    GPS-stamped training photo. We render the photo page to a PIL Image.
    """

    def __init__(self, photo_page_index: int = _TRAINING_PHOTO_PAGE, render_scale: float = 2.0):
        self._page_index = photo_page_index
        self._scale = render_scale

    def extract_photo(self, pdf_path: Path) -> Image.Image:
        """Render the training photo page from a PDF as a PIL Image."""
        doc = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(doc)

        if total_pages == 0:
            raise ValueError(f"PDF has no pages: {pdf_path.name}")

        # Use the last page if the PDF has fewer than expected pages
        page_idx = min(self._page_index, total_pages - 1)
        page = doc[page_idx]
        bitmap = page.render(scale=self._scale)
        pil_image = bitmap.to_pil().convert("RGB")

        log.info(
            "Extracted page %d from %s (%dx%d)",
            page_idx, pdf_path.name, pil_image.width, pil_image.height,
        )
        return pil_image


# ── Result Store (Single Responsibility) ──────────────────────────────


class ResultStore(ABC):
    """Abstract interface for persisting verification results."""

    @abstractmethod
    def save(self, job: VerificationJob) -> None:
        ...

    @abstractmethod
    def save_batch(self, jobs: list[VerificationJob]) -> None:
        ...


class CSVResultStore(ResultStore):
    """Appends verification results to a CSV file.

    Creates the file with headers on first write. Thread-safe for
    sequential access (designed for single-threaded queue processing).
    """

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
        """Write to CSV with retry on PermissionError (file locked on Windows)."""
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
                    log.warning(
                        "CSV locked (%s), retrying in %.0fs... (%d/%d)",
                        path.name, delay, attempt + 1, retries,
                    )
                    time.sleep(delay)
                else:
                    # Fall back to timestamped alternative path
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
        """Flatten a VerificationJob into a CSV row dict."""
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

        # Image quality
        qc = r.checks.get("image_quality")
        if qc:
            row["image_quality_passed"] = qc.passed
            row["blur_score"] = qc.details.get("blur_score")
            row["sharpness"] = qc.details.get("sharpness")
            row["brightness"] = qc.details.get("mean_brightness")
            row["contrast"] = qc.details.get("contrast_ratio")

        # Scene analysis
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

        # Metadata (extracted from vision overlay)
        meta = r.checks.get("metadata")
        if meta:
            row["metadata_passed"] = meta.passed
            gps = meta.details.get("gps")
            if gps:
                row["metadata_gps_lat"] = gps.get("lat")
                row["metadata_gps_lon"] = gps.get("lon")
            row["metadata_timestamp"] = meta.details.get("timestamp")

        return row


# ── Batch Processor (Orchestrator) ────────────────────────────────────


class BatchProcessor:
    """Processes CC training PDFs through the verification pipeline.

    Accepts single files or entire folders. Jobs are enqueued and
    processed sequentially to respect API rate limits.
    """

    def __init__(
        self,
        extractor: PDFExtractor,
        verifier: TrainingPhotoVerifier,
        store: ResultStore,
    ):
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
        """Parse filename, validate, and add to the processing queue."""
        ids = PDFIdentifiers.from_filename(pdf_path.name)
        if ids is None:
            log.warning("Skipping %s — filename does not match expected pattern", pdf_path.name)
            return None

        job = VerificationJob(pdf_path=pdf_path, identifiers=ids)
        self._queue.put(job)
        return job

    def enqueue_folder(self, folder: Path) -> list[VerificationJob]:
        """Enqueue all matching PDFs from a folder."""
        jobs = []
        pdf_files = sorted(folder.glob("*.pdf"))
        for pdf_path in pdf_files:
            job = self.enqueue_file(pdf_path)
            if job:
                jobs.append(job)
        log.info("Enqueued %d PDFs from %s", len(jobs), folder)
        return jobs

    def process_all(self, callback=None) -> list[VerificationJob]:
        """Process all queued jobs sequentially.

        Args:
            callback: Optional callable(job, index, total) invoked after
                      each job completes — useful for progress reporting.
        """
        total = self._queue.qsize()
        index = 0

        while not self._queue.empty():
            job = self._queue.get()
            index += 1
            job.status = "processing"
            log.info("[%d/%d] Processing %s", index, total, job.pdf_path.name)

            try:
                job.image = self._extractor.extract_photo(job.pdf_path)

                job.result = self._verifier.verify(
                    img=job.image,
                    skip_vision=False,
                )
                job.status = "completed"

            except Exception as exc:
                log.error("Failed to process %s: %s", job.pdf_path.name, exc)
                job.status = "failed"
                job.error = str(exc)

            # Persist immediately after each job (crash-safe)
            self._store.save(job)
            self._completed.append(job)

            if callback:
                callback(job, index, total)

        return self._completed

    def process_single(self, pdf_path: Path, callback=None) -> VerificationJob:
        """Convenience method to process a single PDF."""
        job = self.enqueue_file(pdf_path)
        if job is None:
            raise ValueError(f"Invalid filename: {pdf_path.name}")
        self.process_all(callback=callback)
        return self._completed[-1]


# ── Factory (creates pre-configured pipeline) ─────────────────────────


class PipelineFactory:
    """Factory for creating configured pipeline instances.

    Usage:
        pipeline = PipelineFactory.create(output_csv="results.csv")
        pipeline.process_folder(Path("cc_data_final/"))
    """

    @staticmethod
    def create(
        api_key: str | None = None,
        output_csv: str = "output/cc_verification_results.csv",
        photo_page_index: int = _TRAINING_PHOTO_PAGE,
        render_scale: float = 2.0,
    ) -> BatchProcessor:
        key = api_key or os.environ.get("CXAI_API_KEY", "")

        extractor = PDFExtractor(
            photo_page_index=photo_page_index,
            render_scale=render_scale,
        )
        verifier = TrainingPhotoVerifier(api_key=key)
        store = CSVResultStore(csv_path=Path(output_csv))

        return BatchProcessor(
            extractor=extractor,
            verifier=verifier,
            store=store,
        )


# ── CLI entry point ───────────────────────────────────────────────────


def _cli_callback(job: VerificationJob, index: int, total: int):
    status = job.result.decision if job.result else "ERROR"
    print(f"  [{index}/{total}] {job.identifiers.lid} -> {status}")


def main():
    import sys
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    import argparse

    parser = argparse.ArgumentParser(description="CC Training Photo Batch Verification")
    parser.add_argument(
        "input",
        help="Path to a single PDF or a folder of PDFs",
    )
    parser.add_argument(
        "--output",
        default="output/cc_verification_results.csv",
        help="Output CSV path (default: output/cc_verification_results.csv)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    input_path = Path(args.input)
    pipeline = PipelineFactory.create(output_csv=args.output)

    print("=" * 56)
    print("  CC Training Photo — Batch Verification Pipeline")
    print("=" * 56)

    t0 = time.perf_counter()

    if input_path.is_dir():
        jobs = pipeline.enqueue_folder(input_path)
        print(f"  Enqueued {len(jobs)} PDFs from {input_path}")
    elif input_path.is_file():
        pipeline.enqueue_file(input_path)
        print(f"  Enqueued 1 PDF: {input_path.name}")
    else:
        print(f"  ERROR: Path not found: {input_path}")
        return 1

    print(f"\n  Processing queue ({pipeline.queue_size} jobs)...\n")
    results = pipeline.process_all(callback=_cli_callback)

    elapsed = time.perf_counter() - t0
    accepted = sum(1 for j in results if j.result and j.result.decision == "ACCEPT")
    rejected = sum(1 for j in results if j.result and j.result.decision == "REJECT")
    errors = sum(1 for j in results if j.status == "failed")

    print(f"\n{'=' * 56}")
    print(f"  RESULTS: {accepted} accepted, {rejected} rejected, {errors} errors")
    print(f"  Total time: {elapsed:.1f}s ({elapsed/max(len(results),1):.1f}s per PDF)")
    print(f"  CSV saved to: {args.output}")
    print(f"{'=' * 56}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
