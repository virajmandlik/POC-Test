"""
Carbon Credit Training Photo Verification — Core Engine

Orchestrates checks to accept/reject a training session photo:
  1. Image quality  (blur, brightness, contrast via OpenCV)
  2. Scene + metadata analysis (people, training context, GPS overlay via GPT-4 Vision)
"""

import base64
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

log = logging.getLogger("cc_verify")

VISION_API_URL = "https://cxai-playground.cisco.com/chat/completions"


# ── Data classes ──────────────────────────────────────────────────────


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
                name: {
                    "passed": c.passed,
                    **c.details,
                }
                for name, c in self.checks.items()
            },
            "rejection_reasons": self.rejection_reasons,
            "metadata": {"processing_time_ms": self.processing_time_ms},
        }


# ── Check 1: Image Quality ───────────────────────────────────────────


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

        passed = len(issues) == 0
        return CheckResult(
            name="image_quality",
            passed=passed,
            details={
                "blur_score": round(blur, 1),
                "sharpness": sharpness,
                "mean_brightness": round(brightness, 1),
                "contrast_ratio": round(contrast, 3),
            },
            reason="; ".join(issues) if issues else "",
        )


# ── Check 2: Scene + Metadata Analysis (GPT-4 Vision) ────────────────


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
                reason="Vision API key not available — cannot perform scene analysis",
            )

        img_b64 = self._pil_to_base64(img)

        try:
            response = self._call_vision(img_b64)
        except requests.exceptions.RequestException as exc:
            log.error("Vision API call failed: %s", exc)
            return CheckResult(
                name="scene_analysis",
                passed=False,
                details={"error": str(exc)},
                reason=f"Vision API unavailable: {exc}",
            )

        parsed = self._parse_response(response)
        if "error" in parsed:
            return CheckResult(
                name="scene_analysis",
                passed=False,
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
                f"Need at least 2 people (farmer + representative); found {parsed.get('people_count', 0)}"
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


# ── Orchestrator ──────────────────────────────────────────────────────


class TrainingPhotoVerifier:
    """Runs image quality + GPT Vision checks and produces accept/reject."""

    def __init__(self, api_key: str | None = None):
        self.quality_checker = ImageQualityChecker()
        self.scene_analyzer = SceneAnalyzer(api_key=api_key)

    def verify(
        self,
        img: Image.Image,
        skip_vision: bool = False,
    ) -> VerificationResult:
        t0 = time.perf_counter()
        checks: dict[str, CheckResult] = {}
        rejection_reasons: list[str] = []

        # Check 1: Image quality (OpenCV)
        qc = self.quality_checker.check(img)
        checks["image_quality"] = qc
        if not qc.passed:
            rejection_reasons.append(f"Quality: {qc.reason}")

        # Check 2: Scene + metadata analysis (GPT-4 Vision)
        if not skip_vision:
            scene = self.scene_analyzer.analyze(img)
            checks["scene_analysis"] = scene
            if not scene.passed:
                rejection_reasons.append(f"Scene: {scene.reason}")

            # Extract GPS/timestamp from Vision overlay detection
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
                if has_gps
                else None
            )
            ts_detail = (
                f"{overlay_date} {overlay_time or ''}".strip()
                if has_ts
                else None
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
