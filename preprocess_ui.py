import io
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from PIL import Image

import pypdfium2 as pdfium

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


# ── Conversion helpers (PIL <-> OpenCV) ──────────────────────────────


def pil_to_cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def cv_to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


# ── Domain Classes ───────────────────────────────────────────────────


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

        blur = self._blur_score(gray)
        brightness = float(np.mean(gray))
        std = float(np.std(gray))
        contrast = std / max(brightness, 1)

        sharpness = self._sharpness_label(blur)
        readability = self._readability_label(brightness, contrast)
        issues = self._detect_issues(mp, blur, brightness, contrast)

        return QualityReport(
            width=w,
            height=h,
            megapixels=round(mp, 2),
            blur_score=round(blur, 1),
            sharpness=sharpness,
            mean_brightness=round(brightness, 1),
            contrast_ratio=round(contrast, 3),
            readability=readability,
            issues=issues,
        )

    def _blur_score(self, gray: np.ndarray) -> float:
        """Variance of Laplacian — higher means sharper."""
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _sharpness_label(self, score: float) -> str:
        if score > 500:
            return "Excellent"
        if score > 200:
            return "Good"
        if score > self.MIN_BLUR_SCORE:
            return "Acceptable"
        return "Blurry"

    def _readability_label(self, brightness: float, contrast: float) -> str:
        if 60 < brightness < 200 and contrast > 0.25:
            return "Good"
        if 40 < brightness < 220:
            return "Fair"
        return "Poor"

    def _detect_issues(
        self, mp: float, blur: float, brightness: float, contrast: float
    ) -> list[str]:
        issues = []
        if blur < self.MIN_BLUR_SCORE:
            issues.append("Image is blurry — sharpen or re-scan")
        if brightness < self.MIN_BRIGHTNESS:
            issues.append("Image too dark — increase brightness")
        if brightness > self.MAX_BRIGHTNESS:
            issues.append("Image overexposed — reduce brightness")
        if mp < self.MIN_MEGAPIXELS:
            issues.append("Resolution too low — need higher quality scan")
        if contrast < self.MIN_CONTRAST:
            issues.append("Low contrast — increase contrast")
        return issues


class ImageEnhancer:
    """OpenCV-powered image enhancement. Stateless, no UI dependency."""

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
            cv_img = self._denoise_nlm(cv_img)
        elif denoise_method == "median":
            cv_img = cv2.medianBlur(cv_img, 3)

        cv_img = self._adjust_contrast_brightness(cv_img, contrast, brightness)

        if adaptive_thresh:
            cv_img = self._adaptive_threshold(cv_img)

        return cv_to_pil(cv_img)

    def _denoise_nlm(self, img: np.ndarray) -> np.ndarray:
        """Non-local means denoising — preserves text edges better than median."""
        return cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

    def _deskew(self, img: np.ndarray) -> np.ndarray:
        """Straighten rotated document scans via minAreaRect on contours."""
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
        """Estimate skew angle in degrees. Positive = clockwise tilt."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (9, 9), 0)
        thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) < 50:
            return 0.0

        rect = cv2.minAreaRect(coords)
        angle = rect[-1]

        # cv2.minAreaRect returns angles in [-90, 0); normalize to [-45, 45]
        if angle < -45:
            angle = 90 + angle
        elif angle > 45:
            angle = angle - 90

        return round(angle, 2)

    def _adjust_contrast_brightness(
        self, img: np.ndarray, contrast: float, brightness: float
    ) -> np.ndarray:
        """Linear contrast/brightness: out = contrast * in + brightness_offset."""
        brightness_offset = (brightness - 1.0) * 127
        return cv2.convertScaleAbs(img, alpha=contrast, beta=brightness_offset)

    def _adaptive_threshold(self, img: np.ndarray) -> np.ndarray:
        """Adaptive Gaussian threshold — handles uneven scan lighting."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
        )
        return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)


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
    """
    Central gate deciding if an image is ready for extraction.
    Checks quality report, analysis metadata, and skew angle.
    """

    MIN_TEXT_DENSITY = 2.0
    MAX_SKEW_ANGLE = 5.0

    def evaluate(self, qc: QualityReport, analysis: dict) -> tuple[bool, list[str]]:
        reasons: list[str] = []

        if not qc.passed:
            reasons.extend(qc.issues)

        if qc.readability == "Poor":
            reasons.append("Readability is Poor — text may not be extractable")

        if analysis.get("text_density_pct", 0) < self.MIN_TEXT_DENSITY:
            reasons.append(
                f"Text density {analysis['text_density_pct']}% is too low — image may be blank"
            )

        skew = abs(analysis.get("skew_angle_deg", 0))
        if skew > self.MAX_SKEW_ANGLE:
            reasons.append(
                f"Skew angle {skew}° exceeds {self.MAX_SKEW_ANGLE}° — enable auto-deskew"
            )

        return len(reasons) == 0, reasons


# ── Streamlit UI ─────────────────────────────────────────────────────


def _render_quality_metrics(qc: QualityReport):
    st.metric("Resolution", f"{qc.width}x{qc.height} ({qc.megapixels} MP)")
    st.metric("Sharpness", f"{qc.sharpness} ({qc.blur_score})")
    st.metric("Brightness", f"{qc.mean_brightness}")
    st.metric("Contrast", f"{qc.contrast_ratio}")
    st.metric("Readability", qc.readability)


def main():
    st.set_page_config(page_title="DocExtract — Preprocessing", page_icon="📄", layout="wide")

    st.markdown(
        "<h1 style='text-align:center;'>DocExtract — Preprocessing</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Upload &rarr; Enhance (OpenCV) &rarr; Quality Gate &rarr; Approve / Edit / Re-upload"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    checker = QualityChecker()
    enhancer = ImageEnhancer()
    analyzer = DocumentAnalyzer()
    loader = PDFLoader()
    gate = QualityGate()

    if "approved" not in st.session_state:
        st.session_state.approved = False
    if "saved_path" not in st.session_state:
        st.session_state.saved_path = None

    uploaded = st.file_uploader(
        "Upload PDF or Image",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
    )

    if not uploaded:
        st.info("Upload a document to begin preprocessing.")
        return

    file_bytes = uploaded.read()
    file_size_kb = len(file_bytes) / 1024
    st.markdown(f"**File:** `{uploaded.name}` &nbsp;|&nbsp; **Size:** {file_size_kb:.1f} KB")

    suffix = Path(uploaded.name).suffix.lower()
    if suffix == ".pdf":
        with st.spinner("Rendering PDF..."):
            images = loader.load(file_bytes)
        st.success(f"PDF: {len(images)} page(s)")
        page_idx = (
            st.selectbox("Page", range(len(images)), format_func=lambda x: f"Page {x + 1}")
            if len(images) > 1
            else 0
        )
        original = images[page_idx]
    else:
        original = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        page_idx = 0

    # ── Enhancement tab ──────────────────────────────────────────
    tab_enhance, tab_quality, tab_analysis, tab_gate = st.tabs(
        ["Enhancement", "Quality Check", "Analysis", "Approve"]
    )

    with tab_enhance:
        st.subheader("Image Enhancement (OpenCV)")
        col_ctrl, col_preview = st.columns([1, 3])

        with col_ctrl:
            contrast = st.slider("Contrast", 0.5, 3.0, 1.5, 0.1)
            brightness = st.slider("Brightness", 0.5, 2.0, 1.1, 0.1)

            denoise_method = st.radio(
                "Denoise method",
                ["nlm", "median", "none"],
                format_func={
                    "nlm": "Non-local means (better quality)",
                    "median": "Median filter (faster)",
                    "none": "No denoising",
                }.get,
                index=0,
            )

            do_deskew = st.checkbox("Auto-deskew (straighten rotated scans)")
            do_thresh = st.checkbox("Adaptive threshold (for uneven lighting)")

        enhanced = enhancer.enhance(
            original,
            contrast=contrast,
            brightness=brightness,
            denoise_method=denoise_method,
            deskew=do_deskew,
            adaptive_thresh=do_thresh,
        )

        with col_preview:
            c1, c2 = st.columns(2)
            with c1:
                st.caption("Original")
                st.image(original, width="stretch")
            with c2:
                st.caption("Enhanced")
                st.image(enhanced, width="stretch")

    # ── Quality Check tab ────────────────────────────────────────
    qc_orig = checker.check(original)
    qc_enh = checker.check(enhanced)

    with tab_quality:
        st.subheader("Quality Check")
        c1, c2 = st.columns(2)
        with c1:
            st.caption("Original")
            _render_quality_metrics(qc_orig)
        with c2:
            st.caption("Enhanced")
            _render_quality_metrics(qc_enh)

        if qc_enh.passed:
            st.success("All quality checks passed.")
        else:
            st.error(f"Issues: {', '.join(qc_enh.issues)}")

    # ── Analysis tab ─────────────────────────────────────────────
    analysis = analyzer.analyze(enhanced)

    with tab_analysis:
        st.subheader("Document Analysis")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Orientation", analysis["orientation"])
        m2.metric("Aspect Ratio", analysis["aspect_ratio"])
        m3.metric("Text Density", f"{analysis['text_density_pct']}%")
        m4.metric("Type", analysis["estimated_type"])
        m5.metric("Skew Angle", f"{analysis['skew_angle_deg']}°")
        st.image(enhanced, caption="Enhanced preview", width="stretch")

    # ── Quality Gate + Approve / Edit / Re-upload ────────────────
    gate_passed, gate_reasons = gate.evaluate(qc_enh, analysis)

    with tab_gate:
        st.subheader("Quality Gate Decision")

        s1, s2, s3 = st.columns(3)
        with s1:
            st.markdown("**File**")
            st.write(f"- `{uploaded.name}`")
            st.write(f"- {file_size_kb:.1f} KB, Page {page_idx + 1}")
        with s2:
            st.markdown("**Quality**")
            st.write(f"- Sharpness: {qc_enh.sharpness}")
            st.write(f"- Readability: {qc_enh.readability}")
            st.write(f"- Contrast: {qc_enh.contrast_ratio}")
        with s3:
            st.markdown("**Analysis**")
            st.write(f"- {analysis['orientation']}, {analysis['text_density_pct']}% text")
            st.write(f"- Skew: {analysis['skew_angle_deg']}°")
            st.write(f"- {analysis['estimated_type']}")

        st.divider()

        if gate_passed:
            st.success("**GATE: PASSED** — Image meets all criteria for extraction.")

            if st.button("Approve & Save", type="primary", width="stretch"):
                save_path = UPLOAD_DIR / f"preprocessed_{Path(uploaded.name).stem}.png"
                enhanced.save(str(save_path), format="PNG")
                st.session_state.approved = True
                st.session_state.saved_path = str(save_path)
                st.success(f"Saved to `{save_path}` — ready for extraction pipeline.")
                st.balloons()
        else:
            st.error("**GATE: FAILED** — Image does not meet extraction criteria.")
            st.markdown("**Issues found:**")
            for reason in gate_reasons:
                st.markdown(f"- {reason}")

            st.divider()
            st.markdown("**What would you like to do?**")

            col_edit, col_reupload = st.columns(2)

            with col_edit:
                if st.button(
                    "Edit Image (adjust enhancement)",
                    type="primary",
                    width="stretch",
                ):
                    st.info(
                        "Go to the **Enhancement** tab and adjust settings to fix: "
                        + ", ".join(gate_reasons)
                    )
                    st.session_state.approved = False

            with col_reupload:
                if st.button(
                    "Re-upload a different file",
                    width="stretch",
                ):
                    st.session_state.approved = False
                    st.session_state.saved_path = None
                    st.rerun()


if __name__ == "__main__":
    main()
