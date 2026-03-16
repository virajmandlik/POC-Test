"""
Use Case 1 — Land Record OCR & Data Extraction

Self-contained Streamlit app for extracting structured data from
Maharashtra 7/12 land record documents (PDFs or images).

Supports three extraction modes:
  - PaddleOCR-only  (offline, subprocess via venv312)
  - Vision-only     (GPT-4 Vision API, direct HTTP)
  - Combined        (PaddleOCR + Vision in parallel, merged via GPT-4o-mini)

Supports single document or batch processing with queue system,
live results table, and CSV/JSON export.

Run:
    streamlit run pipeline_ui.py

Requires:
    - paddleocr_pdf_to_json_demo.py  (subprocess worker, runs in venv312)
    - CXAI_API_KEY in .env            (for Vision and Combined modes)
"""

import base64
import copy
import csv
import io
import json
import logging
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

log = logging.getLogger("pipeline_ui")

BASE_DIR = Path(__file__).parent
# Cross-platform: use config if available, else detect OS
if _HAS_JOB_SYSTEM:
    PYTHON_312 = cfg.python_paddle
else:
    import platform as _plat
    _venv = BASE_DIR / "venv312"
    PYTHON_312 = str(_venv / "Scripts" / "python.exe") if _plat.system() == "Windows" else str(_venv / "bin" / "python3")
PADDLE_SCRIPT = str(BASE_DIR / "paddleocr_pdf_to_json_demo.py")
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
API_URL = "https://cxai-playground.cisco.com/chat/completions"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# 7/12 extract schema — GPT-4o-mini populates this exact structure
OUTPUT_TEMPLATE = {
    "document_type": "",
    "report_date": "",
    "state": "",
    "taluka": "",
    "district": "",
    "village": "",
    "village_code": "",
    "pu_id": "",
    "survey_number": "",
    "sub_division": "",
    "local_name": "",
    "tenure": {"class": "", "type": ""},
    "owners": [],
    "area": {
        "cultivable": {
            "jirayat_hectare": "",
            "bagayat_hectare": "",
            "total_hectare": "",
        },
        "uncultivable": {
            "class_a_hectare": "",
            "class_b_hectare": "",
            "total_hectare": "",
        },
        "pot_kharab_hectare": "",
        "total_area_hectare": "",
        "unit": "\u0939\u0947.\u0906\u0930.\u091a\u094c.\u092e\u0940.",
    },
    "assessment": {"base_rupees": "", "special_rupees": "", "total_rupees": ""},
    "mutation": {
        "last_number": "",
        "last_date": "",
        "pending": "",
        "all_numbers": [],
    },
    "encumbrances": [],
    "water_resources": {
        "wells": [],
        "irrigation": "",
    },
    "public_resources": "",
    "rights": {"tenant_name": "", "other_rights": ""},
    "heir_info": "",
    "boundary_marks": "",
    "digital_signature": {
        "date": "",
        "verification_url": "",
        "reference_number": "",
    },
    "source_comparison": {
        "fields_differing": {},
        "paddle_only": [],
        "vision_only": [],
    },
}

UC1_CSV_COLUMNS = [
    "filename", "extraction_mode", "status",
    "document_type", "report_date", "state", "district", "taluka", "village",
    "survey_number", "owner_count", "primary_owner_name",
    "total_area_hectare", "jirayat_hectare",
    "assessment_total_rupees",
    "encumbrance_count", "encumbrance_total_rupees",
    "well_count", "last_mutation_number", "mutation_count",
    "fields_filled", "fields_total",
    "processing_time_s", "processed_at_utc",
]


# ═══════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════


def pil_to_cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def cv_to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def pdf_page_to_base64(pdf_path: str, page_index: int = 0, scale: float = 2.0) -> str:
    """Render a single PDF page to a base64-encoded JPEG string."""
    pdf = pdfium.PdfDocument(pdf_path)
    if page_index >= len(pdf):
        raise ValueError(f"Page {page_index} does not exist (PDF has {len(pdf)} pages)")
    page = pdf[page_index]
    bitmap = page.render(scale=scale)
    pil_image = bitmap.to_pil()
    buf = io.BytesIO()
    pil_image.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def image_to_base64(image_path: str) -> str:
    """Read an image file and return its base64-encoded string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _count_fields(obj: dict | list) -> tuple[int, int]:
    """Count filled and empty leaf fields in a nested dict."""
    filled = 0
    empty = 0
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, (dict, list)):
                f, e = _count_fields(v)
                filled += f
                empty += e
            elif isinstance(v, str):
                if v:
                    filled += 1
                else:
                    empty += 1
    elif isinstance(obj, list):
        if obj:
            filled += 1
        else:
            empty += 1
    return filled, empty


# ═══════════════════════════════════════════════════════════════════════
# PREPROCESSING CLASSES (from preprocess_ui.py)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class QualityReport:
    width: int = 0
    height: int = 0
    megapixels: float = 0.0
    blur_score: float = 0.0
    sharpness: str = ""
    mean_brightness: float = 0.0
    contrast_ratio: float = 0.0
    readability: str = ""
    issues: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0


class QualityChecker:
    """Image quality assessment using OpenCV."""

    MIN_MEGAPIXELS = 0.3
    MIN_BRIGHTNESS = 40
    MAX_BRIGHTNESS = 230
    MIN_CONTRAST = 0.15
    MIN_BLUR_SCORE = 80

    def check(self, img: Image.Image) -> QualityReport:
        cv_img = pil_to_cv(img)
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        mp = (w * h) / 1_000_000

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

        if 60 < brightness < 200 and contrast > 0.25:
            readability = "Good"
        elif 40 < brightness < 220:
            readability = "Fair"
        else:
            readability = "Poor"

        issues: list[str] = []
        if blur < self.MIN_BLUR_SCORE:
            issues.append("Image is blurry")
        if brightness < self.MIN_BRIGHTNESS:
            issues.append("Image too dark")
        if brightness > self.MAX_BRIGHTNESS:
            issues.append("Image overexposed")
        if mp < self.MIN_MEGAPIXELS:
            issues.append("Resolution too low")
        if contrast < self.MIN_CONTRAST:
            issues.append("Low contrast")

        return QualityReport(
            width=w, height=h, megapixels=round(mp, 2),
            blur_score=round(blur, 1), sharpness=sharpness,
            mean_brightness=round(brightness, 1),
            contrast_ratio=round(contrast, 3),
            readability=readability, issues=issues,
        )


class ImageEnhancer:
    """OpenCV-powered image enhancement."""

    def enhance(
        self,
        img: Image.Image,
        contrast: float = 1.5,
        brightness: float = 1.1,
        denoise_method: str = "nlm",
        deskew: bool = False,
        adaptive_thresh: bool = False,
    ) -> Image.Image:
        cv_img = pil_to_cv(img)

        if deskew:
            cv_img = self._deskew(cv_img)

        if denoise_method == "nlm":
            cv_img = cv2.fastNlMeansDenoisingColored(cv_img, None, 10, 10, 7, 21)
        elif denoise_method == "median":
            cv_img = cv2.medianBlur(cv_img, 3)

        brightness_offset = (brightness - 1.0) * 127
        cv_img = cv2.convertScaleAbs(cv_img, alpha=contrast, beta=brightness_offset)

        if adaptive_thresh:
            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            thresh = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10,
            )
            cv_img = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

        return cv_to_pil(cv_img)

    def _deskew(self, img: np.ndarray) -> np.ndarray:
        angle = self.detect_skew_angle(img)
        if abs(angle) < 0.5:
            return img
        h, w = img.shape[:2]
        center = (w // 2, h // 2)
        mat = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(
            img, mat, (w, h),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_REPLICATE,
        )

    @staticmethod
    def detect_skew_angle(img: np.ndarray) -> float:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (9, 9), 0)
        thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) < 50:
            return 0.0
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        if angle < -45:
            angle = 90 + angle
        elif angle > 45:
            angle = angle - 90
        return round(angle, 2)


class DocumentAnalyzer:
    """Extracts document metadata including skew angle."""

    def analyze(self, img: Image.Image) -> dict:
        cv_img = pil_to_cv(img)
        h, w = cv_img.shape[:2]
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        dark_ratio = float(np.sum(gray < 128)) / gray.size * 100

        if dark_ratio > 15:
            doc_type = "Text-heavy document"
        elif dark_ratio > 5:
            doc_type = "Mixed text/graphic document"
        else:
            doc_type = "Image/graphic-heavy document"

        skew = ImageEnhancer.detect_skew_angle(cv_img)

        return {
            "orientation": "Portrait" if h > w else "Landscape",
            "aspect_ratio": round(w / h, 2) if h else 0,
            "text_density_pct": round(dark_ratio, 1),
            "estimated_type": doc_type,
            "skew_angle_deg": skew,
        }


class PDFLoader:
    """Converts PDF bytes to PIL images."""

    def load(self, pdf_bytes: bytes, scale: float = 2.0) -> list[Image.Image]:
        pdf = pdfium.PdfDocument(pdf_bytes)
        return [pdf[i].render(scale=scale).to_pil() for i in range(len(pdf))]


class QualityGate:
    """Decides if an image is ready for extraction."""

    MIN_TEXT_DENSITY = 2.0
    MAX_SKEW_ANGLE = 5.0

    def evaluate(self, qc: QualityReport, analysis: dict) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not qc.passed:
            reasons.extend(qc.issues)
        if qc.readability == "Poor":
            reasons.append("Readability is Poor")
        if analysis.get("text_density_pct", 0) < self.MIN_TEXT_DENSITY:
            reasons.append(f"Text density {analysis['text_density_pct']}% is too low")
        skew = abs(analysis.get("skew_angle_deg", 0))
        if skew > self.MAX_SKEW_ANGLE:
            reasons.append(f"Skew angle {skew} exceeds {self.MAX_SKEW_ANGLE}")
        return len(reasons) == 0, reasons


# ═══════════════════════════════════════════════════════════════════════
# EXTRACTION ENGINES
# ═══════════════════════════════════════════════════════════════════════


def _run_subprocess(label: str, cmd: list[str]) -> tuple[str, int, str, str, float]:
    """Run a command, return (label, exit_code, stdout, stderr, elapsed)."""
    t0 = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=str(BASE_DIR), env=env,
    )
    elapsed = time.perf_counter() - t0
    return label, proc.returncode, proc.stdout, proc.stderr, elapsed


def call_vision_api(
    image_base64: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    api_url: str = API_URL,
    model: str = "gpt-4-vision-playground",
    temperature: float = 0.2,
) -> dict:
    """Send a base64-encoded image to the Vision API and return the response."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                ],
            },
        ],
    }
    resp = requests.post(api_url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _call_gpt4o_mini(api_key: str, system_prompt: str, user_prompt: str) -> dict:
    """Call GPT-4o-mini for text-to-text tasks (merge, comparison)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _parse_llm_json(response: dict) -> dict:
    """Extract and parse JSON from an LLM response."""
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
        return {"raw_llm_response": content}


def _fill_template(template: dict, source: dict) -> None:
    """Recursively fill template keys from source, keeping template structure."""
    for key in template:
        if key not in source:
            continue
        if isinstance(template[key], dict) and isinstance(source[key], dict):
            _fill_template(template[key], source[key])
        elif isinstance(template[key], list) and isinstance(source[key], list):
            template[key] = source[key]
        else:
            template[key] = source[key]


class ExtractionEngine:
    """Runs document extraction in one of three modes."""

    VISION_SYSTEM_PROMPT = (
        "You are a document OCR and data extraction expert. "
        "Extract ALL text and structured data from the provided document image. "
        "Return the result as valid JSON with fields and their values."
    )

    VISION_USER_PROMPT = (
        "Extract all text and data from this document image. "
        "Return a JSON object with all fields, labels, values, "
        "numbers, dates, and names found in the document. "
        "Preserve the original language (Marathi/Hindi/English)."
    )

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or os.environ.get("CXAI_API_KEY", "")

    def extract(
        self,
        input_path: Path,
        mode: str = "combined",
        lang: str = "mr",
        vision_input: Path | None = None,
    ) -> dict:
        """Run extraction and return the full result dict.

        Args:
            input_path: Path to the PDF or image file.
            mode: 'paddle', 'vision', or 'combined'.
            lang: PaddleOCR language code.
            vision_input: Optional separate input for Vision API
                          (e.g. preprocessed image while PaddleOCR gets raw PDF).
        """
        t_total = time.perf_counter()
        vision_path = vision_input or input_path

        if mode == "paddle":
            return self._paddle_only(input_path, lang, t_total)
        elif mode == "vision":
            return self._vision_only(vision_path, t_total)
        else:
            return self._combined(input_path, vision_path, lang, t_total)

    def _paddle_only(self, input_path: Path, lang: str, t_start: float) -> dict:
        ocr_out = OUTPUT_DIR / "ocr_output.json"
        cmd = [
            PYTHON_312, PADDLE_SCRIPT,
            "--input", str(input_path),
            "--output", str(ocr_out),
            "--lang", lang,
        ]
        _, rc, stdout, stderr, elapsed = _run_subprocess("PaddleOCR", cmd)

        if rc != 0:
            return {
                "status": "failed",
                "error": f"PaddleOCR failed (exit {rc}): {stderr[-500:]}",
                "merged_extraction": {},
                "timing_seconds": {"total": round(time.perf_counter() - t_start, 2)},
            }

        ocr_data = {}
        if ocr_out.exists():
            with ocr_out.open("r", encoding="utf-8") as f:
                ocr_data = json.load(f)

        return {
            "status": "ok",
            "source_file": str(input_path),
            "extraction_mode": "paddle",
            "merged_extraction": ocr_data,
            "timing_seconds": {
                "paddleocr": round(elapsed, 2),
                "total": round(time.perf_counter() - t_start, 2),
            },
        }

    def _vision_only(self, input_path: Path, t_start: float) -> dict:
        suffix = input_path.suffix.lower()
        t0 = time.perf_counter()

        if suffix == ".pdf":
            img_b64 = pdf_page_to_base64(str(input_path))
        elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
            img_b64 = image_to_base64(str(input_path))
        else:
            return {
                "status": "failed",
                "error": f"Unsupported file type: {suffix}",
                "merged_extraction": {},
                "timing_seconds": {"total": round(time.perf_counter() - t_start, 2)},
            }

        try:
            response = call_vision_api(
                image_base64=img_b64,
                api_key=self._api_key,
                system_prompt=self.VISION_SYSTEM_PROMPT,
                user_prompt=self.VISION_USER_PROMPT,
            )
        except requests.exceptions.RequestException as exc:
            return {
                "status": "failed",
                "error": f"Vision API error: {exc}",
                "merged_extraction": {},
                "timing_seconds": {"total": round(time.perf_counter() - t_start, 2)},
            }

        t_api = time.perf_counter() - t0

        content = ""
        if "choices" in response and response["choices"]:
            content = response["choices"][0].get("message", {}).get("content", "")

        vision_data = {
            "extracted_content": content,
            "usage": response.get("usage", {}),
        }

        return {
            "status": "ok",
            "source_file": str(input_path),
            "extraction_mode": "vision",
            "merged_extraction": vision_data,
            "timing_seconds": {
                "vision_api": round(t_api, 2),
                "total": round(time.perf_counter() - t_start, 2),
            },
        }

    def _combined(self, input_path: Path, vision_path: Path, lang: str, t_start: float) -> dict:
        ocr_out = OUTPUT_DIR / "ocr_output.json"
        vision_out_path = OUTPUT_DIR / "vision_output.json"

        paddle_cmd = [
            PYTHON_312, PADDLE_SCRIPT,
            "--input", str(input_path),
            "--output", str(ocr_out),
            "--lang", lang,
        ]

        suffix = vision_path.suffix.lower()
        if suffix == ".pdf":
            get_vision_b64 = lambda: pdf_page_to_base64(str(vision_path))
        elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
            get_vision_b64 = lambda: image_to_base64(str(vision_path))
        else:
            get_vision_b64 = None

        # Run PaddleOCR subprocess and Vision API call in parallel
        paddle_result = {}
        vision_content = ""
        t_parallel_start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=2) as pool:
            paddle_future = pool.submit(_run_subprocess, "PaddleOCR", paddle_cmd)

            vision_future = None
            if get_vision_b64 is not None:
                def _vision_task():
                    b64 = get_vision_b64()
                    resp = call_vision_api(
                        image_base64=b64,
                        api_key=self._api_key,
                        system_prompt=self.VISION_SYSTEM_PROMPT,
                        user_prompt=self.VISION_USER_PROMPT,
                    )
                    content = ""
                    if "choices" in resp and resp["choices"]:
                        content = resp["choices"][0].get("message", {}).get("content", "")
                    return content, time.perf_counter() - t_parallel_start

                vision_future = pool.submit(_vision_task)

            _, p_rc, _, p_stderr, p_elapsed = paddle_future.result()
            paddle_ok = p_rc == 0

            vision_ok = False
            v_elapsed = 0.0
            if vision_future:
                try:
                    vision_content, v_elapsed = vision_future.result()
                    vision_ok = bool(vision_content)
                except Exception as exc:
                    log.error("Vision extraction failed: %s", exc)

        t_parallel = time.perf_counter() - t_parallel_start

        if not paddle_ok and not vision_ok:
            return {
                "status": "failed",
                "error": "Both PaddleOCR and Vision API failed",
                "merged_extraction": {},
                "timing_seconds": {"total": round(time.perf_counter() - t_start, 2)},
            }

        ocr_data = {}
        if paddle_ok and ocr_out.exists():
            with ocr_out.open("r", encoding="utf-8") as f:
                ocr_data = json.load(f)

        ocr_text = ""
        if ocr_data.get("pages"):
            page = ocr_data["pages"][0]
            ocr_text = json.dumps({
                "combined_text": page.get("combined_text", ""),
                "structured_fields": page.get("structured_fields", {}),
                "stats": page.get("stats", {}),
            }, ensure_ascii=False, indent=2)

        # GPT-4o-mini merge
        template_str = json.dumps(OUTPUT_TEMPLATE, ensure_ascii=False, indent=2)

        merge_system = (
            "You are a Maharashtra land record (\u0917\u093e\u0935 \u0928\u092e\u0941\u0928\u093e "
            "\u0938\u093e\u0924 / 7-12 extract) data reconciliation expert.\n"
            "You receive two OCR extractions of the SAME document \u2014 one from PaddleOCR (offline) "
            "and one from GPT-4 Vision (online).\n\n"
            "RULES:\n"
            "1. Fill the EXACT JSON template below. Do NOT add, remove, or rename any keys.\n"
            "2. For each field, pick the most accurate/complete value from either source.\n"
            "3. If a field is not found in either source, set it to empty string \"\" or empty list [].\n"
            "4. 'report_date': extract the \u0905\u0939\u0935\u093e\u0932 \u0926\u093f\u0928\u093e\u0902\u0915 date.\n"
            "5. 'owners': extract ALL owners/shareholders as a list. Each entry must have: "
            "\"name\", \"account_number\" (\u0916\u093e\u0924\u0947 \u0915\u094d\u0930), \"area_hectare\" (\u0915\u094d\u0937\u0947\u0924\u094d\u0930), "
            "\"assessment_rupees\" (\u0906\u0915\u093e\u0930), \"mutation_ref\" (ferfer number in parentheses).\n"
            "6. 'encumbrances': extract ALL liens/mortgages (\u092c\u094b\u091c\u093e) as a list. Each entry: "
            "\"type\" (bank_mortgage/cooperative/other), \"bank_name\", \"branch\", "
            "\"amount_rupees\", \"borrower_name\", \"date\", \"mutation_ref\".\n"
            "7. 'water_resources.wells': list all wells (\u0935\u093f\u0939\u0940\u0930) with \"owner\" and \"mutation_ref\".\n"
            "8. 'water_resources.irrigation': irrigation info (\u0932\u0918\u0941\u0938\u093f\u0902\u091a\u0928 \u0924\u0932\u093e\u0935 etc.).\n"
            "9. 'public_resources': any note about public property (\u0938\u093e\u0930\u094d\u0935\u091c\u0928\u093f\u0915 \u092e\u093e\u0932\u092e\u0924\u094d\u0924\u093e).\n"
            "10. 'mutation.all_numbers': list ALL ferfer/mutation numbers found in the document.\n"
            "11. 'assessment.total_rupees': total assessment amount.\n"
            "12. In 'source_comparison.fields_differing', list fields where the two sources disagree.\n"
            "13. In 'source_comparison.paddle_only', list data found ONLY in PaddleOCR.\n"
            "14. In 'source_comparison.vision_only', list data found ONLY in Vision.\n"
            "15. Respond ONLY with valid JSON matching the template. No markdown fences.\n\n"
            f"TEMPLATE:\n{template_str}"
        )

        merge_user = (
            f"=== PaddleOCR Extraction ===\n{ocr_text}\n\n"
            f"=== GPT-4 Vision Extraction ===\n{vision_content}\n\n"
            "Fill the template with merged data from both sources."
        )

        t_gpt_start = time.perf_counter()
        try:
            gpt_resp = _call_gpt4o_mini(self._api_key, merge_system, merge_user)
        except requests.exceptions.RequestException as exc:
            return {
                "status": "failed",
                "error": f"GPT-4o-mini merge failed: {exc}",
                "merged_extraction": ocr_data if paddle_ok else {"extracted_content": vision_content},
                "timing_seconds": {"total": round(time.perf_counter() - t_start, 2)},
            }
        t_gpt = time.perf_counter() - t_gpt_start

        merged_json = _parse_llm_json(gpt_resp)

        validated = copy.deepcopy(OUTPUT_TEMPLATE)
        if isinstance(merged_json, dict) and "raw_llm_response" not in merged_json:
            _fill_template(validated, merged_json)
        else:
            validated = merged_json

        return {
            "status": "ok",
            "source_file": str(input_path),
            "extraction_mode": "combined",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "pipeline": {
                "paddleocr": {"status": "ok" if paddle_ok else "failed", "elapsed_seconds": round(p_elapsed, 2)},
                "vision_api": {"status": "ok" if vision_ok else "failed", "elapsed_seconds": round(v_elapsed, 2)},
                "gpt4o_mini_merge": {"elapsed_seconds": round(t_gpt, 2), "usage": gpt_resp.get("usage", {})},
            },
            "merged_extraction": validated,
            "timing_seconds": {
                "parallel_extraction": round(t_parallel, 2),
                "gpt_merge": round(t_gpt, 2),
                "total": round(time.perf_counter() - t_start, 2),
            },
        }


# ═══════════════════════════════════════════════════════════════════════
# COMPARATIVE ANALYZER (GPT-4o-mini comparison of two extractions)
# ═══════════════════════════════════════════════════════════════════════


class ComparativeAnalyzer:
    """Sends two extraction results to GPT-4o-mini for comparison."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def compare(self, raw_extraction: dict, prep_extraction: dict) -> tuple[dict, float]:
        raw_str = json.dumps(raw_extraction, ensure_ascii=False, indent=2)
        prep_str = json.dumps(prep_extraction, ensure_ascii=False, indent=2)

        system_prompt = (
            "You are a document extraction quality analyst.\n"
            "You receive two extractions of the SAME Maharashtra land record.\n"
            "- Source A: extracted from the RAW (unprocessed) PDF.\n"
            "- Source B: extracted from a PREPROCESSED (enhanced) image.\n\n"
            "Your job:\n"
            "1. Compare field by field. For each field that differs, show both values.\n"
            "2. List fields that improved in Source B.\n"
            "3. List fields that degraded in Source B.\n"
            "4. List fields only found in one source.\n"
            "5. Give an overall verdict: did preprocessing help, hurt, or make no difference?\n"
            "6. Give a confidence percentage for each source's accuracy.\n"
            "7. Respond ONLY with valid JSON. No markdown fences.\n\n"
            "Use this structure:\n"
            "{\n"
            '  "field_comparison": {"field_name": {"raw": "...", "preprocessed": "...", "verdict": "improved|degraded|same"}},\n'
            '  "improved_fields": ["..."],\n'
            '  "degraded_fields": ["..."],\n'
            '  "raw_only_fields": ["..."],\n'
            '  "preprocessed_only_fields": ["..."],\n'
            '  "overall_verdict": "...",\n'
            '  "raw_accuracy_pct": 0,\n'
            '  "preprocessed_accuracy_pct": 0\n'
            "}"
        )

        user_prompt = (
            f"=== Source A: RAW extraction ===\n{raw_str}\n\n"
            f"=== Source B: PREPROCESSED extraction ===\n{prep_str}\n\n"
            "Compare these two extractions field by field."
        )

        t0 = time.perf_counter()
        resp = _call_gpt4o_mini(self._api_key, system_prompt, user_prompt)
        elapsed = time.perf_counter() - t0

        return _parse_llm_json(resp), elapsed


# ═══════════════════════════════════════════════════════════════════════
# SEMANTIC ANALYZER (ownership chain & knowledge graph via GPT-4o-mini)
# ═══════════════════════════════════════════════════════════════════════


class SemanticAnalyzer:
    """Infers ownership chain, land semantics, and relationship graph from extracted data."""

    SEMANTIC_SCHEMA = json.dumps({
        "land_summary": {
            "survey_number": "", "village": "", "taluka": "", "district": "",
            "total_area_hectare": "", "cultivable_hectare": "", "uncultivable_hectare": "",
            "tenure_type": "",
        },
        "original_owner": {"name": "", "notes": ""},
        "ownership_chain": [{
            "from_owner": "", "to_owner": "", "mutation_ref": "",
            "transfer_type": "", "area_hectare": "", "year_approx": "",
        }],
        "current_owners": [{
            "name": "", "account_number": "", "area_hectare": "", "assessment_rupees": "",
        }],
        "encumbrances_mapped": [{
            "owner_name": "", "bank_name": "", "amount_rupees": "",
            "type": "", "mutation_ref": "",
        }],
        "wells": [{"owner": "", "mutation_ref": ""}],
        "key_dates": {
            "report_date": "", "last_mutation_date": "", "last_mutation_number": "",
        },
    }, ensure_ascii=False, indent=2)

    def __init__(self, api_key: str):
        self._api_key = api_key

    def analyze(self, merged_extraction: dict) -> tuple[dict, float]:
        ext_str = json.dumps(merged_extraction, ensure_ascii=False, indent=2)

        system_prompt = (
            "You are a Maharashtra land record (7/12 extract) domain expert.\n"
            "You receive the structured extraction of a 7/12 land record document.\n\n"
            "Your job is to produce a SEMANTIC KNOWLEDGE GRAPH.\n\n"
            "RULES:\n"
            "1. Identify the ORIGINAL owner (earliest known, मूळ मालक) based on mutation\n"
            "   references. Lower mutation numbers typically indicate older records.\n"
            "2. Build the OWNERSHIP CHAIN — trace how land transferred from the original\n"
            "   owner through mutations to current owners. Each transfer: from_owner,\n"
            "   to_owner, mutation_ref, transfer_type (inheritance/sale/partition/gift/other),\n"
            "   area if known, approximate year if inferable.\n"
            "3. List all CURRENT OWNERS with account numbers, areas, and assessments.\n"
            "4. Map each ENCUMBRANCE (loan/mortgage/boja) to the specific owner it applies to.\n"
            "5. Provide LAND SUMMARY with total area, cultivable/uncultivable breakdown.\n"
            "6. List wells with their owners.\n"
            "7. Extract key dates (report date, last mutation date/number).\n"
            "8. Respond ONLY with valid JSON. No markdown fences.\n\n"
            f"TEMPLATE:\n{self.SEMANTIC_SCHEMA}"
        )

        user_prompt = (
            f"=== Extracted Land Record Data ===\n{ext_str}\n\n"
            "Analyze this data and produce the semantic knowledge graph."
        )

        t0 = time.perf_counter()
        resp = _call_gpt4o_mini(self._api_key, system_prompt, user_prompt)
        elapsed = time.perf_counter() - t0

        return _parse_llm_json(resp), elapsed


# ═══════════════════════════════════════════════════════════════════════
# BATCH PROCESSING (Queue system for UC1)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DocumentJob:
    """One unit of work in the extraction queue."""
    file_path: Path
    filename: str
    status: str = "pending"
    extraction_mode: str = "combined"
    result: dict | None = None
    error: str = ""
    processing_time_s: float = 0.0


class UC1CSVResultStore:
    """Appends extraction results to a CSV file with retry."""

    def __init__(self, csv_path: Path):
        self._path = csv_path
        self._ensure_headers()

    def _ensure_headers(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists() or self._path.stat().st_size == 0:
            self._write(self._path, "w", None, header=True)

    def save(self, job: DocumentJob) -> None:
        row = self._job_to_row(job)
        self._write(self._path, "a", [row], header=False)

    def _write(self, path: Path, mode: str, rows: list[dict] | None, header: bool):
        for attempt in range(3):
            try:
                with open(path, mode, newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=UC1_CSV_COLUMNS)
                    if header:
                        writer.writeheader()
                    if rows:
                        writer.writerows(rows)
                return
            except PermissionError:
                if attempt < 2:
                    time.sleep(1.0)
                else:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    alt = path.with_stem(f"{path.stem}_{ts}")
                    log.warning("CSV locked — writing to %s instead", alt.name)
                    self._path = alt
                    self._ensure_headers()
                    if rows:
                        with open(alt, "a", newline="", encoding="utf-8") as f:
                            csv.DictWriter(f, fieldnames=UC1_CSV_COLUMNS).writerows(rows)

    @staticmethod
    def _job_to_row(job: DocumentJob) -> dict[str, Any]:
        row: dict[str, Any] = {
            "filename": job.filename,
            "extraction_mode": job.extraction_mode,
            "status": job.status,
            "processing_time_s": round(job.processing_time_s, 2),
            "processed_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        if job.status == "failed" or job.result is None:
            row["status"] = "ERROR"
            return row

        merged = job.result.get("merged_extraction", {})
        row["document_type"] = merged.get("document_type", "")
        row["report_date"] = merged.get("report_date", "")
        row["state"] = merged.get("state", "")
        row["district"] = merged.get("district", "")
        row["taluka"] = merged.get("taluka", "")
        row["village"] = merged.get("village", "")
        row["survey_number"] = merged.get("survey_number", "")

        owners = merged.get("owners", [])
        if isinstance(owners, list):
            row["owner_count"] = len(owners)
            row["primary_owner_name"] = owners[0].get("name", "") if owners else ""
        else:
            row["owner_count"] = 0
            row["primary_owner_name"] = ""

        area = merged.get("area", {})
        if isinstance(area, dict):
            row["total_area_hectare"] = area.get("total_area_hectare", "")
            cultivable = area.get("cultivable", {})
            row["jirayat_hectare"] = cultivable.get("jirayat_hectare", "") if isinstance(cultivable, dict) else ""
        else:
            row["total_area_hectare"] = ""
            row["jirayat_hectare"] = ""

        assessment = merged.get("assessment", {})
        row["assessment_total_rupees"] = assessment.get("total_rupees", "") if isinstance(assessment, dict) else ""

        encumbrances = merged.get("encumbrances", [])
        if isinstance(encumbrances, list):
            row["encumbrance_count"] = len(encumbrances)
            total_enc = 0
            for enc in encumbrances:
                amt = enc.get("amount_rupees", "") if isinstance(enc, dict) else ""
                try:
                    total_enc += int(str(amt).replace(",", "").replace("/-", "").strip())
                except (ValueError, TypeError):
                    pass
            row["encumbrance_total_rupees"] = total_enc if total_enc else ""
        else:
            row["encumbrance_count"] = 0
            row["encumbrance_total_rupees"] = ""

        water = merged.get("water_resources", {})
        wells = water.get("wells", []) if isinstance(water, dict) else []
        row["well_count"] = len(wells) if isinstance(wells, list) else 0

        mutation = merged.get("mutation", {})
        if isinstance(mutation, dict):
            row["last_mutation_number"] = mutation.get("last_number", "")
            all_nums = mutation.get("all_numbers", [])
            row["mutation_count"] = len(all_nums) if isinstance(all_nums, list) else 0
        else:
            row["last_mutation_number"] = ""
            row["mutation_count"] = 0

        filled, empty = _count_fields(merged)
        row["fields_filled"] = filled
        row["fields_total"] = filled + empty

        return row


class UC1BatchProcessor:
    """Processes documents through the extraction pipeline via queue."""

    def __init__(self, engine: ExtractionEngine, store: UC1CSVResultStore, mode: str = "combined"):
        self._engine = engine
        self._store = store
        self._mode = mode
        self._queue: Queue[DocumentJob] = Queue()
        self._completed: list[DocumentJob] = []

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def completed_jobs(self) -> list[DocumentJob]:
        return list(self._completed)

    def enqueue_file(self, file_path: Path) -> DocumentJob:
        job = DocumentJob(file_path=file_path, filename=file_path.name, extraction_mode=self._mode)
        self._queue.put(job)
        return job

    def enqueue_folder(self, folder: Path) -> list[DocumentJob]:
        jobs = []
        for ext in ("*.pdf", "*.jpg", "*.jpeg", "*.png"):
            for p in sorted(folder.glob(ext)):
                jobs.append(self.enqueue_file(p))
        log.info("Enqueued %d files from %s", len(jobs), folder)
        return jobs

    def process_all(self, callback=None) -> list[DocumentJob]:
        total = self._queue.qsize()
        index = 0
        while not self._queue.empty():
            job = self._queue.get()
            index += 1
            job.status = "processing"
            log.info("[%d/%d] Processing %s (%s)", index, total, job.filename, job.extraction_mode)

            t0 = time.perf_counter()
            try:
                job.result = self._engine.extract(
                    input_path=job.file_path,
                    mode=job.extraction_mode,
                )
                job.status = job.result.get("status", "ok")
            except Exception as exc:
                log.error("Failed to process %s: %s", job.filename, exc)
                job.status = "failed"
                job.error = str(exc)
            job.processing_time_s = time.perf_counter() - t0

            self._store.save(job)
            self._completed.append(job)
            if callback:
                callback(job, index, total)

        return self._completed


# ═══════════════════════════════════════════════════════════════════════
# STREAMLIT UI — SINGLE MODE
# ═══════════════════════════════════════════════════════════════════════


def _build_ownership_dot(semantic: dict) -> str:
    """Build a Graphviz DOT string for the ownership knowledge graph."""
    summary = semantic.get("land_summary", {})
    original = semantic.get("original_owner", {})
    chain = semantic.get("ownership_chain", [])
    current = semantic.get("current_owners", [])
    encumbrances = semantic.get("encumbrances_mapped", [])
    wells = semantic.get("wells", [])

    survey = summary.get("survey_number", "?")
    village = summary.get("village", "")
    total_area = summary.get("total_area_hectare", "?")

    def esc(s: str) -> str:
        return s.replace('"', '\\"').replace("\n", " ")

    lines = [
        "digraph ownership {",
        "  rankdir=TB;",
        '  graph [fontname="Arial", label="", labelloc=t, fontsize=14];',
        '  node [shape=box, style="rounded,filled", fontname="Arial", fontsize=11];',
        '  edge [fontname="Arial", fontsize=9];',
        "",
        f'  land [label="{esc(village)}\\nSurvey: {esc(survey)} | {esc(total_area)} ha",'
        '  shape=ellipse, fillcolor="#C8E6C9", penwidth=2];',
        "",
    ]

    node_ids: dict[str, str] = {}
    counter = [0]

    def nid(name: str) -> str:
        if name not in node_ids:
            node_ids[name] = f"n{counter[0]}"
            counter[0] += 1
        return node_ids[name]

    declared: set[str] = set()

    def declare(name: str, extra_label: str = "", color: str = "#BBDEFB", bold: bool = False):
        n = nid(name)
        if n in declared:
            return
        declared.add(n)
        label = esc(name)
        if extra_label:
            label += f"\\n{esc(extra_label)}"
        style = '"rounded,filled,bold"' if bold else '"rounded,filled"'
        lines.append(f'  {n} [label="{label}", fillcolor="{color}", style={style}];')

    if original.get("name"):
        declare(original["name"], "(Original Owner)", "#FFF9C4")

    current_names = {o.get("name", "") for o in current if o.get("name")}

    for transfer in chain:
        fr = transfer.get("from_owner", "")
        to = transfer.get("to_owner", "")
        if not fr or not to:
            continue
        declare(fr, color="#FFF9C4")
        is_current = to in current_names
        declare(to, color="#C8E6C9" if is_current else "#BBDEFB", bold=is_current)

        parts = []
        if transfer.get("transfer_type"):
            parts.append(transfer["transfer_type"])
        if transfer.get("mutation_ref"):
            parts.append(f"Mut#{transfer['mutation_ref']}")
        if transfer.get("area_hectare"):
            parts.append(f"{transfer['area_hectare']} ha")
        edge_label = esc("\\n".join(parts))
        lines.append(f'  {nid(fr)} -> {nid(to)} [label="{edge_label}", color="#1565C0"];')

    for owner in current:
        name = owner.get("name", "")
        if not name:
            continue
        acct = owner.get("account_number", "")
        area = owner.get("area_hectare", "")
        extra = []
        if acct:
            extra.append(f"Acct#{acct}")
        if area:
            extra.append(f"{area} ha")
        declare(name, " | ".join(extra), "#C8E6C9", bold=True)
        lines.append(
            f'  {nid(name)} -> land [style=dashed, color="#388E3C",'
            f' label="owns", arrowhead=none];'
        )

    for i, enc in enumerate(encumbrances):
        bank = enc.get("bank_name", f"Institution {i + 1}")
        amount = enc.get("amount_rupees", "")
        bank_label = esc(bank)
        if amount:
            bank_label += f"\\n₹{esc(str(amount))}"
        bid = f"bank_{i}"
        lines.append(
            f'  {bid} [label="{bank_label}", shape=hexagon,'
            f' fillcolor="#FFCDD2", style=filled];'
        )
        owner_name = enc.get("owner_name", "")
        if owner_name in node_ids:
            mut = enc.get("mutation_ref", "")
            elabel = esc(enc.get("type", "encumbrance"))
            if mut:
                elabel += f"\\nMut#{esc(mut)}"
            lines.append(
                f'  {node_ids[owner_name]} -> {bid}'
                f' [style=dotted, color="#D32F2F", label="{elabel}"];'
            )

    for i, well in enumerate(wells):
        wowner = well.get("owner", "")
        if not wowner:
            continue
        wid = f"well_{i}"
        wlabel = "Well"
        if well.get("mutation_ref"):
            wlabel += f"\\nMut#{esc(well['mutation_ref'])}"
        lines.append(
            f'  {wid} [label="{wlabel}", shape=diamond,'
            f' fillcolor="#B3E5FC", style=filled];'
        )
        if wowner in node_ids:
            lines.append(
                f'  {node_ids[wowner]} -> {wid}'
                f' [style=dashed, color="#0288D1", label="well owner"];'
            )

    lines.append("}")
    return "\n".join(lines)


def _render_semantic_view(semantic: dict):
    """Render the full semantic knowledge graph visualization."""
    if "raw_llm_response" in semantic:
        st.warning("Semantic analysis returned non-JSON.")
        st.code(semantic["raw_llm_response"])
        return

    summary = semantic.get("land_summary", {})
    st.markdown("### Land Summary")
    cols = st.columns(4)
    cols[0].metric("Survey No.", summary.get("survey_number", "—"))
    cols[1].metric("Village", summary.get("village", "—"))
    cols[2].metric("Taluka", summary.get("taluka", "—"))
    cols[3].metric("District", summary.get("district", "—"))

    cols2 = st.columns(4)
    cols2[0].metric("Total Area", f"{summary.get('total_area_hectare', '—')} ha")
    cols2[1].metric("Cultivable", f"{summary.get('cultivable_hectare', '—')} ha")
    cols2[2].metric("Uncultivable", f"{summary.get('uncultivable_hectare', '—')} ha")
    cols2[3].metric("Tenure", summary.get("tenure_type", "—"))

    st.divider()

    orig = semantic.get("original_owner", {})
    if orig.get("name"):
        st.markdown("### Original Owner")
        st.info(f"**{orig['name']}** — {orig.get('notes', '')}")

    chain = semantic.get("ownership_chain", [])
    current = semantic.get("current_owners", [])

    if chain or current:
        st.markdown("### Ownership & Encumbrance Graph")
        dot = _build_ownership_dot(semantic)
        st.graphviz_chart(dot, use_container_width=True)

    if current:
        st.markdown("### Current Owners")
        owner_rows = [
            {
                "Name": o.get("name", ""),
                "Account No.": o.get("account_number", ""),
                "Area (ha)": o.get("area_hectare", ""),
                "Assessment (Rs)": o.get("assessment_rupees", ""),
            }
            for o in current
        ]
        st.dataframe(pd.DataFrame(owner_rows), use_container_width=True, hide_index=True)

    enc = semantic.get("encumbrances_mapped", [])
    if enc:
        st.markdown("### Encumbrances (Loans & Mortgages)")
        enc_rows = [
            {
                "Owner": e.get("owner_name", ""),
                "Bank / Institution": e.get("bank_name", ""),
                "Amount (Rs)": e.get("amount_rupees", ""),
                "Type": e.get("type", ""),
                "Mutation Ref": e.get("mutation_ref", ""),
            }
            for e in enc
        ]
        st.dataframe(pd.DataFrame(enc_rows), use_container_width=True, hide_index=True)

    wells = semantic.get("wells", [])
    if wells:
        st.markdown("### Water Resources")
        for w in wells:
            st.markdown(
                f"- Well owned by **{w.get('owner', '—')}** "
                f"(Mutation: {w.get('mutation_ref', '—')})"
            )

    dates = semantic.get("key_dates", {})
    if any(dates.values()):
        st.markdown("### Key Dates")
        dc = st.columns(3)
        dc[0].metric("Report Date", dates.get("report_date", "—"))
        dc[1].metric("Last Mutation No.", dates.get("last_mutation_number", "—"))
        dc[2].metric("Last Mutation Date", dates.get("last_mutation_date", "—"))

    with st.expander("View Full Semantic JSON"):
        st.json(semantic)


def _render_quality(qc: QualityReport):
    st.metric("Resolution", f"{qc.width}x{qc.height} ({qc.megapixels} MP)")
    st.metric("Sharpness", f"{qc.sharpness} ({qc.blur_score})")
    st.metric("Brightness", f"{qc.mean_brightness}")
    st.metric("Contrast", f"{qc.contrast_ratio}")
    st.metric("Readability", qc.readability)


def _single_mode(api_key: str):
    """Upload one document, preprocess, run extraction pipeline, review."""
    checker = QualityChecker()
    enhancer = ImageEnhancer()
    analyzer = DocumentAnalyzer()
    loader = PDFLoader()
    gate = QualityGate()
    comparator = ComparativeAnalyzer(api_key)

    for key, default in [
        ("step", 1), ("approved", False), ("prep_path", None),
        ("preprocessing_skipped", False), ("force_preprocess", False),
        ("raw_result", None), ("prep_result", None),
        ("comparison", None), ("semantic_result", None), ("final_saved", False),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "1. Upload & Preprocess",
        "2. Run Pipelines",
        "3. Comparative Analysis",
        "4. Semantic & Knowledge Graph",
        "5. Final Output",
    ])

    # ── Tab 1: Upload & Preprocess ───────────────────────────────
    with tab1:
        st.subheader("Upload & Preprocess")
        uploaded = st.file_uploader(
            "Upload PDF or Image",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            key="pipeline_upload",
        )
        if not uploaded:
            st.info("Upload a document to begin.")
            return

        file_bytes = uploaded.read()
        file_size_kb = len(file_bytes) / 1024
        st.markdown(f"**File:** `{uploaded.name}` | **Size:** {file_size_kb:.1f} KB")

        raw_save_path = UPLOAD_DIR / f"raw_{uploaded.name}"
        with raw_save_path.open("wb") as f:
            f.write(file_bytes)

        suffix = Path(uploaded.name).suffix.lower()
        if suffix == ".pdf":
            with st.spinner("Rendering PDF..."):
                images = loader.load(file_bytes)
            original = images[0]
        else:
            original = Image.open(io.BytesIO(file_bytes)).convert("RGB")

        raw_qc = checker.check(original)
        raw_analysis = analyzer.analyze(original)
        raw_passed, raw_reasons = gate.evaluate(raw_qc, raw_analysis)

        if raw_passed and not st.session_state.get("force_preprocess"):
            st.success(
                f"**Raw Quality Gate: PASSED** | Sharpness: {raw_qc.sharpness} "
                f"| Readability: {raw_qc.readability} | Skew: {raw_analysis['skew_angle_deg']}"
            )
            st.image(original, caption="Original (no preprocessing needed)", use_container_width=True)

            col_skip, col_force = st.columns(2)
            with col_skip:
                if st.button("Skip Preprocessing & Run Pipelines", type="primary", use_container_width=True):
                    st.session_state.approved = True
                    st.session_state.prep_path = None
                    st.session_state.preprocessing_skipped = True
                    st.session_state.step = 2
                    st.rerun()
            with col_force:
                if st.button("Preprocess Anyway", use_container_width=True):
                    st.session_state.force_preprocess = True
                    st.rerun()
            return

        if not raw_passed:
            st.warning("**Raw Quality Gate: FAILED** — preprocessing recommended.")
            for r in raw_reasons:
                st.markdown(f"- {r}")
            st.divider()

        col_ctrl, col_preview = st.columns([1, 3])

        with col_ctrl:
            st.markdown("**Enhancement Settings**")
            contrast = st.slider("Contrast", 0.5, 3.0, 1.5, 0.1, key="p_contrast")
            brightness = st.slider("Brightness", 0.5, 2.0, 1.1, 0.1, key="p_brightness")
            denoise = st.radio(
                "Denoise", ["nlm", "median", "none"],
                format_func={"nlm": "Non-local means", "median": "Median", "none": "None"}.get,
                key="p_denoise",
            )
            do_deskew = st.checkbox("Auto-deskew", key="p_deskew")
            do_thresh = st.checkbox("Adaptive threshold", key="p_thresh")

        enhanced = enhancer.enhance(
            original, contrast=contrast, brightness=brightness,
            denoise_method=denoise, deskew=do_deskew, adaptive_thresh=do_thresh,
        )

        with col_preview:
            c1, c2 = st.columns(2)
            with c1:
                st.caption("Original")
                st.image(original, use_container_width=True)
            with c2:
                st.caption("Enhanced")
                st.image(enhanced, use_container_width=True)

        qc = checker.check(enhanced)
        analysis = analyzer.analyze(enhanced)
        passed, reasons = gate.evaluate(qc, analysis)

        st.divider()

        if passed:
            st.success(f"**Quality Gate: PASSED** | Sharpness: {qc.sharpness} | Readability: {qc.readability}")
            if st.button("Approve & Proceed to Pipelines", type="primary", use_container_width=True):
                prep_path = UPLOAD_DIR / f"preprocessed_{Path(uploaded.name).stem}.png"
                enhanced.save(str(prep_path), format="PNG")
                st.session_state.approved = True
                st.session_state.prep_path = str(prep_path)
                st.session_state.preprocessing_skipped = False
                st.session_state.step = 2
                st.success("Saved preprocessed image. Go to **Run Pipelines** tab.")
        else:
            st.error("**Quality Gate: FAILED**")
            for r in reasons:
                st.markdown(f"- {r}")
            st.info("Adjust enhancement settings above or re-upload a different file.")

    # ── Tab 2: Run Pipelines ─────────────────────────────────────
    with tab2:
        st.subheader("Run Extraction Pipelines")
        if not st.session_state.approved:
            st.warning("Complete Step 1 first — approve the preprocessed image.")
            return

        raw_path = str(UPLOAD_DIR / f"raw_{uploaded.name}")
        prep_path = st.session_state.prep_path
        skipped = st.session_state.preprocessing_skipped

        raw_out = str(OUTPUT_DIR / "raw_combined.json")
        prep_out = str(OUTPUT_DIR / "prep_combined.json")

        engine = ExtractionEngine(api_key=api_key)

        if skipped:
            st.info("Preprocessing was **skipped** (raw quality gate passed). Running single pipeline.")
        else:
            st.markdown(
                f"- **Path A (Raw):** `{Path(raw_path).name}` -> Combined pipeline -> `raw_combined.json`\n"
                f"- **Path B (Preprocessed):** PaddleOCR on raw PDF + Vision on `{Path(prep_path).name}` -> `prep_combined.json`"
            )

        btn_label = "Run Pipeline" if skipped else "Run Both Pipelines"
        if st.button(btn_label, type="primary", use_container_width=True):
            t_wall_start = time.perf_counter()

            with st.status("Running extraction...", expanded=True) as status_widget:
                raw_result = engine.extract(Path(raw_path), mode="combined")
                st.session_state.raw_result = raw_result

                with Path(raw_out).open("w", encoding="utf-8") as fp:
                    json.dump(raw_result, fp, ensure_ascii=False, indent=2)

                if raw_result.get("status") == "ok":
                    st.write(f":white_check_mark: **Raw pipeline:** OK ({raw_result['timing_seconds']['total']:.1f}s)")
                else:
                    st.write(f":x: **Raw pipeline:** FAILED")

                if not skipped:
                    prep_result = engine.extract(
                        Path(raw_path), mode="combined",
                        vision_input=Path(prep_path),
                    )
                    st.session_state.prep_result = prep_result

                    with Path(prep_out).open("w", encoding="utf-8") as fp:
                        json.dump(prep_result, fp, ensure_ascii=False, indent=2)

                    if prep_result.get("status") == "ok":
                        st.write(f":white_check_mark: **Preprocessed pipeline:** OK ({prep_result['timing_seconds']['total']:.1f}s)")
                    else:
                        st.write(f":x: **Preprocessed pipeline:** FAILED")

                total_wall = time.perf_counter() - t_wall_start
                status_widget.update(
                    label=f"Pipeline{'s' if not skipped else ''} finished in {total_wall:.1f}s",
                    state="complete", expanded=False,
                )

            st.session_state.step = 3
            st.success("Pipelines complete. Go to **Comparative Analysis** tab.")

    # ── Tab 3: Comparative Analysis ──────────────────────────────
    with tab3:
        st.subheader("Comparative Analysis")
        raw_result = st.session_state.raw_result
        prep_result = st.session_state.prep_result
        skipped_cmp = st.session_state.preprocessing_skipped

        if not raw_result:
            st.warning("Complete Step 2 first — run the pipeline(s).")
            return

        raw_ext = raw_result.get("merged_extraction", {})
        prep_ext = prep_result.get("merged_extraction", {}) if prep_result else {}

        if skipped_cmp:
            st.info("Preprocessing was skipped. Showing raw extraction only.")
            st.json(raw_ext)
            st.session_state.comparison = {
                "mode": "raw_only",
                "note": "Preprocessing skipped — raw quality gate passed",
            }
            st.session_state.step = 4
            st.success("Go to **Semantic & Knowledge Graph** tab.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Raw Extraction**")
                st.json(raw_ext)
            with col2:
                st.markdown("**Preprocessed Extraction**")
                st.json(prep_ext)

            st.divider()

            if st.button("Run GPT-4o-mini Comparison", type="primary", use_container_width=True):
                with st.spinner("Analyzing differences with GPT-4o-mini..."):
                    comparison, comp_elapsed = comparator.compare(raw_ext, prep_ext)
                st.session_state.comparison = comparison
                st.session_state.comp_elapsed = comp_elapsed
                st.session_state.step = 4

            if st.session_state.comparison and st.session_state.comparison.get("mode") != "raw_only":
                comp = st.session_state.comparison
                if "raw_llm_response" not in comp:
                    verdict = comp.get("overall_verdict", "N/A")
                    raw_acc = comp.get("raw_accuracy_pct", "?")
                    prep_acc = comp.get("preprocessed_accuracy_pct", "?")

                    st.divider()
                    v1, v2, v3 = st.columns(3)
                    v1.metric("Raw Accuracy", f"{raw_acc}%")
                    v2.metric("Preprocessed Accuracy", f"{prep_acc}%")
                    v3.metric("Verdict", verdict[:30] if isinstance(verdict, str) else str(verdict))

                    improved = comp.get("improved_fields", [])
                    degraded = comp.get("degraded_fields", [])
                    if improved:
                        st.success(f"**Improved ({len(improved)}):** {', '.join(improved)}")
                    if degraded:
                        st.error(f"**Degraded ({len(degraded)}):** {', '.join(degraded)}")

                    field_comp = comp.get("field_comparison", {})
                    if field_comp:
                        st.markdown("**Field-by-field comparison:**")
                        st.json(field_comp)

                    st.success("Go to **Semantic & Knowledge Graph** tab.")
                else:
                    st.warning("LLM returned non-JSON. Raw response:")
                    st.code(comp.get("raw_llm_response", ""))

    # ── Tab 4: Semantic & Knowledge Graph ─────────────────────────
    with tab4:
        st.subheader("Semantic & Knowledge Graph")
        raw_result = st.session_state.raw_result
        if not raw_result:
            st.warning("Complete Step 2 first — run the pipeline(s).")
            return

        best_extraction = raw_result.get("merged_extraction", {})
        prep_result = st.session_state.prep_result
        if prep_result and prep_result.get("status") == "ok":
            best_extraction = prep_result.get("merged_extraction", best_extraction)

        st.markdown(
            "Analyze the extracted data to build an **ownership chain**, "
            "identify **current vs original owners**, and visualize "
            "**encumbrances and land relationships** as a graph."
        )

        if st.button("Run Semantic Analysis", type="primary", use_container_width=True):
            sem_analyzer = SemanticAnalyzer(api_key)
            with st.spinner("Building semantic knowledge graph with GPT-4o-mini..."):
                sem_result, sem_elapsed = sem_analyzer.analyze(best_extraction)
            st.session_state.semantic_result = sem_result
            st.success(f"Semantic analysis complete ({sem_elapsed:.1f}s). "
                       "Go to **Final Output** tab to save.")
            st.session_state.step = 5

        if st.session_state.semantic_result:
            st.divider()
            _render_semantic_view(st.session_state.semantic_result)

    # ── Tab 5: Final Output ──────────────────────────────────────
    with tab5:
        st.subheader("Final Output")
        if not st.session_state.raw_result:
            st.warning("Complete earlier steps first.")
            return

        skipped_fin = st.session_state.preprocessing_skipped
        final = {
            "source_file": str(UPLOAD_DIR / f"raw_{uploaded.name}") if uploaded else "",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "preprocessing_skipped": skipped_fin,
            "raw_extraction": st.session_state.raw_result.get("merged_extraction", {}),
        }
        if not skipped_fin and st.session_state.prep_result:
            final["preprocessed_extraction"] = st.session_state.prep_result.get("merged_extraction", {})
        if st.session_state.comparison:
            final["comparative_analysis"] = st.session_state.comparison
        if st.session_state.semantic_result:
            final["semantic_knowledge_graph"] = st.session_state.semantic_result

        st.json(final)

        out_path = OUTPUT_DIR / "comparative_output.json"
        if st.button("Save to JSON", type="primary", use_container_width=True):
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(final, fp, ensure_ascii=False, indent=2)
            st.session_state.final_saved = True
            st.success(f"Saved to `{out_path.resolve()}`")
            st.balloons()


# ═══════════════════════════════════════════════════════════════════════
# STREAMLIT UI — BATCH MODE
# ═══════════════════════════════════════════════════════════════════════


def _batch_mode(api_key: str):
    """Upload or scan multiple documents, process via queue, show live table."""
    for key in ("batch_jobs", "batch_results_df", "batch_sources"):
        if key not in st.session_state:
            st.session_state[key] = None

    col_upload, col_folder = st.columns(2)

    with col_upload:
        st.subheader("Upload Documents")
        uploaded_files = st.file_uploader(
            "Upload one or more PDFs/images",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="batch_upload_uc1",
        )

    with col_folder:
        st.subheader("Or scan a local folder")
        folder_path = st.text_input(
            "Folder path containing documents",
            value="uploads",
            help="Relative or absolute path to a folder with PDFs/images",
            key="uc1_folder_path",
        )
        if st.button("Scan Folder", key="uc1_scan_folder"):
            fp = Path(folder_path)
            if fp.is_dir():
                found = []
                for ext in ("*.pdf", "*.jpg", "*.jpeg", "*.png"):
                    for p in sorted(fp.glob(ext)):
                        found.append((p.name, p))
                st.session_state.batch_sources = found
                st.session_state.batch_results_df = None
                st.session_state.batch_jobs = None
            else:
                st.error(f"Folder not found: `{folder_path}`")

    if uploaded_files:
        upload_list = []
        for uf in uploaded_files:
            upload_list.append((uf.name, uf.read()))
        st.session_state.batch_sources = upload_list

    st.divider()

    extraction_mode = st.radio(
        "Extraction Mode",
        ["combined", "paddle", "vision"],
        format_func={"combined": "Combined (PaddleOCR + Vision + GPT-4o-mini)",
                      "paddle": "PaddleOCR Only (offline)",
                      "vision": "Vision API Only"}.get,
        horizontal=True,
        key="uc1_extraction_mode",
    )

    batch_sources = st.session_state.batch_sources
    if batch_sources:
        st.success(f"**{len(batch_sources)} documents** ready for processing")

        if st.button("Process All Documents", type="primary", use_container_width=True):
            _run_batch_uc1(batch_sources, api_key, extraction_mode)

    if st.session_state.batch_results_df is not None:
        st.divider()
        _render_batch_results_uc1()


def _run_batch_uc1(sources: list[tuple[str, "Path | bytes"]], api_key: str, mode: str):
    """Process all documents with live-updating results table."""
    engine = ExtractionEngine(api_key=api_key)
    output_csv = Path("output/uc1_extraction_results.csv")
    store = UC1CSVResultStore(csv_path=output_csv)
    processor = UC1BatchProcessor(engine=engine, store=store, mode=mode)

    tmp_dir = Path(tempfile.mkdtemp(prefix="uc1_batch_"))
    file_paths: list[Path] = []
    for name, source in sources:
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

    m_col1, m_col2, m_col3 = st.columns(3)
    m_total = m_col1.empty()
    m_ok = m_col2.empty()
    m_err = m_col3.empty()

    table_placeholder = st.empty()
    live_rows: list[dict] = []
    completed_jobs: list[DocumentJob] = []

    def on_complete(job: DocumentJob, index: int, total: int):
        progress_bar.progress(
            index / total,
            text=f"Processing {index}/{total} — {job.filename}: {job.status}",
        )

        row = UC1CSVResultStore._job_to_row(job)
        live_rows.append(row)
        completed_jobs.append(job)

        df = pd.DataFrame(live_rows)
        ok_count = len(df[df["status"] == "ok"])
        err_count = len(df[df["status"] != "ok"])

        m_total.metric("Processed", f"{index}/{total}")
        m_ok.metric("Succeeded", ok_count)
        m_err.metric("Errors", err_count)

        display_cols = [
            "filename", "extraction_mode", "status",
            "district", "taluka", "village", "survey_number",
            "owner_count", "primary_owner_name",
            "total_area_hectare", "encumbrance_count", "well_count",
        ]
        available = [c for c in display_cols if c in df.columns]
        table_placeholder.dataframe(
            df[available], use_container_width=True,
            height=min(400, 50 + 35 * len(df)),
        )

    processor.process_all(callback=on_complete)
    progress_bar.progress(1.0, text=f"Done — {total} documents processed")

    df = pd.DataFrame(live_rows)
    st.session_state.batch_results_df = df
    st.session_state.batch_jobs = completed_jobs

    ok_count = len(df[df["status"] == "ok"])
    st.success(f"Batch complete: **{ok_count}/{len(df)}** succeeded.")


def _render_batch_results_uc1():
    """Render batch results with filtering, CSV download, and inspection."""
    st.subheader("Extraction Results")

    df = st.session_state.batch_results_df

    col1, col2, col3 = st.columns(3)
    col1.metric("Total", len(df))
    col2.metric("Succeeded", len(df[df["status"] == "ok"]))
    col3.metric("Errors", len(df[df["status"] != "ok"]))

    st.dataframe(df, use_container_width=True, height=400)

    st.divider()

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    st.download_button(
        label="Download Results CSV",
        data=csv_buffer.getvalue(),
        file_name="uc1_extraction_results.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.divider()
    st.subheader("Inspect Individual Results")

    api_key = os.environ.get("CXAI_API_KEY", "")
    jobs = st.session_state.batch_jobs
    if jobs:
        for idx, job in enumerate(jobs):
            icon = "+" if job.status == "ok" else "-"
            with st.expander(f"[{icon}] {job.filename} -- {job.status} ({job.processing_time_s:.1f}s)"):
                if job.result:
                    merged = job.result.get("merged_extraction", {})

                    view_tab, graph_tab = st.tabs(["Extracted JSON", "Semantic Graph"])
                    with view_tab:
                        st.json(merged)
                        timing = job.result.get("timing_seconds", {})
                        if timing:
                            st.caption(f"Timing: {json.dumps(timing)}")

                    with graph_tab:
                        sem_key = f"batch_semantic_{idx}"
                        if sem_key not in st.session_state:
                            st.session_state[sem_key] = None

                        if st.button(
                            "Run Semantic Analysis",
                            key=f"sem_btn_{idx}",
                            use_container_width=True,
                        ):
                            if api_key:
                                sem = SemanticAnalyzer(api_key)
                                with st.spinner("Building knowledge graph..."):
                                    result, elapsed = sem.analyze(merged)
                                st.session_state[sem_key] = result
                                st.success(f"Done ({elapsed:.1f}s)")
                            else:
                                st.error("CXAI_API_KEY required for semantic analysis.")

                        if st.session_state[sem_key]:
                            _render_semantic_view(st.session_state[sem_key])

                if job.error:
                    st.error(job.error)


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
        st.caption("Submit extraction as a tracked background job")

        job_mode = st.selectbox(
            "Extraction mode",
            ["combined", "paddle", "vision"],
            key="sidebar_job_mode",
        )
        job_file = st.text_input(
            "File path (in uploads/)",
            placeholder="uploads/example.pdf",
            key="sidebar_job_file",
        )
        job_user = st.text_input(
            "Your name (for audit)",
            value=os.environ.get("USER", os.environ.get("USERNAME", "user")),
            key="sidebar_job_user",
        )

        if st.button("Submit as Background Job", key="sidebar_submit_job"):
            if not job_file:
                st.warning("Enter a file path.")
            else:
                try:
                    job_id = job_manager.submit(
                        job_type="uc1.extract",
                        params={"file_path": job_file, "mode": job_mode, "lang": "mr"},
                        user=job_user,
                    )
                    st.success(f"Job submitted: `{job_id}`")
                except Exception as exc:
                    st.error(f"Failed: {exc}")

        # Show recent jobs
        st.divider()
        st.markdown("### Recent Jobs")
        try:
            recent = job_manager.list_jobs(job_type="uc1.extract", limit=5)
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
        page_title="DocExtract — Land Record OCR",
        page_icon="📄",
        layout="wide",
    )

    st.markdown(
        "<h1 style='text-align:center;'>DocExtract — Land Record OCR & Extraction</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Single or batch document extraction &mdash; PaddleOCR / Vision / Combined"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    api_key = os.environ.get("CXAI_API_KEY", "")
    if not api_key:
        st.warning("**CXAI_API_KEY** not found in `.env`. Vision and Combined modes require this key.")

    # Render background job sidebar
    _render_job_sidebar()

    mode = st.radio(
        "Processing Mode",
        ["Single Document", "Batch Processing"],
        horizontal=True,
        key="uc1_mode",
    )

    st.divider()

    if _HAS_JOB_SYSTEM:
        audit_log("uc1.page_loaded", user=os.environ.get("USER", "unknown"))

    if mode == "Single Document":
        _single_mode(api_key)
    else:
        _batch_mode(api_key)


if __name__ == "__main__":
    main()
