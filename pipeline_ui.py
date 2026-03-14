"""
Comparative Analysis Dashboard
Runs combined.py on raw PDF vs preprocessed image, then compares via GPT-4o-mini.
"""

import io
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import requests
import streamlit as st
from dotenv import load_dotenv
from PIL import Image

from preprocess_ui import (
    DocumentAnalyzer,
    ImageEnhancer,
    PDFLoader,
    QualityChecker,
    QualityGate,
    QualityReport,
    pil_to_cv,
    cv_to_pil,
)

load_dotenv()

BASE_DIR = Path(__file__).parent
PYTHON_312 = str(BASE_DIR / "venv312" / "Scripts" / "python.exe")
COMBINED_SCRIPT = str(BASE_DIR / "combined.py")
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
API_URL = "https://cxai-playground.cisco.com/chat/completions"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Pipeline runner (loosely coupled, no UI) ─────────────────────────


class PipelineRunner:
    """Runs combined.py as a subprocess and returns results."""

    def __init__(self, python_path: str, script_path: str, base_dir: Path):
        self._python = python_path
        self._script = script_path
        self._base_dir = base_dir

    def run(
        self,
        input_path: str,
        output_path: str,
        lang: str = "mr",
        vision_input: str | None = None,
    ) -> tuple[int, float, str, str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        cmd = [
            self._python,
            self._script,
            "--input", input_path,
            "--output", output_path,
            "--lang", lang,
        ]
        if vision_input:
            cmd.extend(["--vision-input", vision_input])

        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self._base_dir),
            env=env,
        )
        elapsed = time.perf_counter() - t0
        return proc.returncode, elapsed, proc.stdout, proc.stderr

    def load_result(self, json_path: str) -> dict:
        p = Path(json_path)
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)


class ComparativeAnalyzer:
    """Sends two extraction results to GPT-4o-mini for comparison."""

    def __init__(self, api_url: str, api_key: str):
        self._api_url = api_url
        self._api_key = api_key

    def compare(self, raw_extraction: dict, prep_extraction: dict) -> tuple[dict, float]:
        raw_str = json.dumps(raw_extraction, ensure_ascii=False, indent=2)
        prep_str = json.dumps(prep_extraction, ensure_ascii=False, indent=2)

        system_prompt = (
            "You are a document extraction quality analyst.\n"
            "You receive two extractions of the SAME Maharashtra land record (गाव नमुना सात).\n"
            "- Source A: extracted from the RAW (unprocessed) PDF.\n"
            "- Source B: extracted from a PREPROCESSED (enhanced) image of the same PDF.\n\n"
            "Your job:\n"
            "1. Compare field by field. For each field that differs, show both values.\n"
            "2. List fields that improved in Source B (more accurate/complete).\n"
            "3. List fields that degraded in Source B (worse than A).\n"
            "4. List fields only found in one source.\n"
            "5. Give an overall verdict: did preprocessing help, hurt, or make no difference?\n"
            "6. Give a confidence percentage for each source's overall accuracy.\n"
            "7. Respond ONLY with valid JSON. No markdown fences, no explanation.\n\n"
            "Use this structure:\n"
            "{\n"
            '  "field_comparison": {\"field_name\": {\"raw\": \"...\", \"preprocessed\": \"...\", \"verdict\": \"improved|degraded|same\"}},\n'
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
            "=== Source A: RAW extraction ===\n"
            f"{raw_str}\n\n"
            "=== Source B: PREPROCESSED extraction ===\n"
            f"{prep_str}\n\n"
            "Compare these two extractions field by field."
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        payload = {
            "model": "gpt-4o-mini",
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        t0 = time.perf_counter()
        resp = requests.post(self._api_url, headers=headers, json=payload, timeout=180)
        resp.raise_for_status()
        elapsed = time.perf_counter() - t0

        body = resp.json()
        raw_content = ""
        if "choices" in body and body["choices"]:
            raw_content = body["choices"][0].get("message", {}).get("content", "")

        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            result = {"raw_llm_response": raw_content}

        return result, elapsed


# ── Streamlit UI ─────────────────────────────────────────────────────


def _render_quality(qc: QualityReport):
    st.metric("Resolution", f"{qc.width}x{qc.height} ({qc.megapixels} MP)")
    st.metric("Sharpness", f"{qc.sharpness} ({qc.blur_score})")
    st.metric("Brightness", f"{qc.mean_brightness}")
    st.metric("Contrast", f"{qc.contrast_ratio}")
    st.metric("Readability", qc.readability)


def _run_pipeline_thread(
    label: str,
    runner: PipelineRunner,
    input_path: str,
    output_path: str,
    lang: str,
    vision_input: str | None = None,
) -> tuple[str, int, float, str, str]:
    rc, elapsed, stdout, stderr = runner.run(
        input_path, output_path, lang, vision_input=vision_input
    )
    return label, rc, elapsed, stdout, stderr


def main():
    st.set_page_config(
        page_title="DocExtract — Comparative Analysis",
        page_icon="📊",
        layout="wide",
    )

    st.markdown(
        "<h1 style='text-align:center;'>DocExtract — Comparative Analysis</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Raw vs Preprocessed extraction &mdash; side-by-side comparison"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    api_key = os.environ.get("CXAI_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        st.error("Set CXAI_API_KEY in `.env` file first.")
        return

    checker = QualityChecker()
    enhancer = ImageEnhancer()
    analyzer = DocumentAnalyzer()
    loader = PDFLoader()
    gate = QualityGate()
    runner = PipelineRunner(PYTHON_312, COMBINED_SCRIPT, BASE_DIR)
    comparator = ComparativeAnalyzer(API_URL, api_key)

    for key, default in [
        ("step", 1),
        ("approved", False),
        ("prep_path", None),
        ("preprocessing_skipped", False),
        ("force_preprocess", False),
        ("raw_json_path", None),
        ("prep_json_path", None),
        ("raw_result", None),
        ("prep_result", None),
        ("comparison", None),
        ("final_saved", False),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    tab1, tab2, tab3, tab4 = st.tabs([
        "1. Upload & Preprocess",
        "2. Run Pipelines",
        "3. Comparative Analysis",
        "4. Final Output",
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
            page_idx = 0
            original = images[page_idx]
        else:
            original = Image.open(io.BytesIO(file_bytes)).convert("RGB")

        # ── Quality gate on RAW image first ───────────────────────
        raw_qc = checker.check(original)
        raw_analysis = analyzer.analyze(original)
        raw_passed, raw_reasons = gate.evaluate(raw_qc, raw_analysis)

        if raw_passed and not st.session_state.get("force_preprocess"):
            st.success(
                f"**Raw Quality Gate: PASSED** | Sharpness: {raw_qc.sharpness} "
                f"| Readability: {raw_qc.readability} | Skew: {raw_analysis['skew_angle_deg']}°"
            )
            st.info(
                "The raw document already meets quality standards. "
                "Preprocessing is **not needed** — skipping to pipeline."
            )
            st.image(original, caption="Original (no preprocessing needed)", width="stretch")

            col_skip, col_force = st.columns(2)
            with col_skip:
                if st.button("Skip Preprocessing & Run Pipelines", type="primary", width="stretch"):
                    st.session_state.approved = True
                    st.session_state.prep_path = None
                    st.session_state.preprocessing_skipped = True
                    st.session_state.step = 2
                    st.rerun()
            with col_force:
                if st.button("Preprocess Anyway", width="stretch"):
                    st.session_state.force_preprocess = True
                    st.rerun()
            return

        # ── Show enhancement controls (raw failed OR user forced) ─
        if not raw_passed:
            st.warning(
                f"**Raw Quality Gate: FAILED** — preprocessing recommended."
            )
            for r in raw_reasons:
                st.markdown(f"- {r}")
            st.divider()

        col_ctrl, col_preview = st.columns([1, 3])

        with col_ctrl:
            st.markdown("**Enhancement Settings**")
            contrast = st.slider("Contrast", 0.5, 3.0, 1.5, 0.1, key="p_contrast")
            brightness = st.slider("Brightness", 0.5, 2.0, 1.1, 0.1, key="p_brightness")
            denoise = st.radio(
                "Denoise",
                ["nlm", "median", "none"],
                format_func={"nlm": "Non-local means", "median": "Median", "none": "None"}.get,
                key="p_denoise",
            )
            do_deskew = st.checkbox("Auto-deskew", key="p_deskew")
            do_thresh = st.checkbox("Adaptive threshold", key="p_thresh")

        enhanced = enhancer.enhance(
            original,
            contrast=contrast,
            brightness=brightness,
            denoise_method=denoise,
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

        qc = checker.check(enhanced)
        analysis = analyzer.analyze(enhanced)
        passed, reasons = gate.evaluate(qc, analysis)

        st.divider()

        if passed:
            st.success(f"**Quality Gate: PASSED** | Sharpness: {qc.sharpness} | Readability: {qc.readability} | Skew: {analysis['skew_angle_deg']}°")

            if st.button("Approve & Proceed to Pipelines", type="primary", width="stretch"):
                prep_path = UPLOAD_DIR / f"preprocessed_{Path(uploaded.name).stem}.png"
                enhanced.save(str(prep_path), format="PNG")
                st.session_state.approved = True
                st.session_state.prep_path = str(prep_path)
                st.session_state.preprocessing_skipped = False
                st.session_state.step = 2
                st.success(f"Saved preprocessed image. Go to **Run Pipelines** tab.")
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

        if skipped:
            st.info(
                "Preprocessing was **skipped** (raw quality gate passed). "
                "Running single pipeline on the raw document."
            )
            st.markdown(
                f"- **Pipeline:** `{Path(raw_path).name}` → PaddleOCR + Vision → `raw_combined.json`"
            )
        else:
            st.markdown(
                f"- **Path A (Raw):** `{Path(raw_path).name}` → PaddleOCR + Vision on raw PDF → `raw_combined.json`\n"
                f"- **Path B (Preprocessed):** PaddleOCR on raw PDF + Vision on `{Path(prep_path).name}` → `prep_combined.json`"
            )

        btn_label = "Run Pipeline" if skipped else "Run Both Pipelines"
        if st.button(btn_label, type="primary", width="stretch"):
            status_msg = "Running pipeline..." if skipped else "Running both pipelines in parallel..."
            t_wall_start = time.perf_counter()

            results = {}
            with st.status(status_msg, expanded=True) as status_widget:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = {
                        pool.submit(
                            _run_pipeline_thread, "Raw", runner,
                            raw_path, raw_out, "mr",
                        ): "raw",
                    }
                    if not skipped:
                        futures[pool.submit(
                            _run_pipeline_thread, "Preprocessed", runner,
                            raw_path, prep_out, "mr",
                            vision_input=prep_path,
                        )] = "prep"

                    for future in as_completed(futures):
                        label, rc, elapsed, stdout, stderr = future.result()
                        results[label] = {"rc": rc, "elapsed": elapsed, "stderr": stderr}
                        if rc == 0:
                            st.write(f":white_check_mark: **{label}:** OK ({elapsed:.1f}s)")
                        else:
                            st.write(f":x: **{label}:** FAILED ({elapsed:.1f}s)")

                total_wall = time.perf_counter() - t_wall_start
                status_widget.update(
                    label=f"Pipeline{'s' if not skipped else ''} finished in {total_wall:.1f}s",
                    state="complete",
                    expanded=False,
                )

            raw_ok = results.get("Raw", {}).get("rc") == 0
            prep_ok = results.get("Preprocessed", {}).get("rc") == 0 if not skipped else False

            if not raw_ok:
                st.expander("Raw pipeline stderr").code(results["Raw"]["stderr"][-1000:])
            if not skipped and not prep_ok:
                st.expander("Preprocessed pipeline stderr").code(
                    results["Preprocessed"]["stderr"][-1000:]
                )

            if raw_ok or prep_ok:
                st.session_state.raw_json_path = raw_out if raw_ok else None
                st.session_state.prep_json_path = prep_out if prep_ok else None
                st.session_state.raw_result = runner.load_result(raw_out) if raw_ok else {}
                st.session_state.prep_result = runner.load_result(prep_out) if prep_ok else {}
                st.session_state.step = 3

                st.divider()
                if skipped:
                    raw_elapsed = results.get("Raw", {}).get("elapsed", 0)
                    st.metric("Pipeline Time", f"{raw_elapsed:.1f}s")
                    st.success("Pipeline complete. Go to **Comparative Analysis** tab (raw-only mode).")
                else:
                    raw_elapsed = results.get("Raw", {}).get("elapsed", 0)
                    prep_elapsed = results.get("Preprocessed", {}).get("elapsed", 0)
                    c1, c2 = st.columns(2)
                    c1.metric("Raw Pipeline", f"{raw_elapsed:.1f}s")
                    c2.metric("Preprocessed Pipeline", f"{prep_elapsed:.1f}s")
                    st.success("Both pipelines complete. Go to **Comparative Analysis** tab.")
            else:
                st.error("Pipeline failed. Check errors above.")

    # ── Tab 3: Comparative Analysis ──────────────────────────────
    with tab3:
        st.subheader("Comparative Analysis")

        raw_result = st.session_state.raw_result
        prep_result = st.session_state.prep_result
        skipped = st.session_state.preprocessing_skipped

        if not raw_result and not prep_result:
            st.warning("Complete Step 2 first — run the pipeline(s).")
            return

        raw_ext = raw_result.get("merged_extraction", {}) if raw_result else {}
        prep_ext = prep_result.get("merged_extraction", {}) if prep_result else {}

        if skipped:
            st.info(
                "Preprocessing was skipped (raw quality gate passed). "
                "Showing raw extraction only — no comparison needed."
            )
            st.markdown("**Extraction Result**")
            st.json(raw_ext)

            st.session_state.comparison = {
                "mode": "raw_only",
                "note": "Preprocessing skipped — raw quality gate passed",
                "raw_accuracy_pct": "N/A (single path)",
            }
            st.session_state.step = 4
            st.success("Go to **Final Output** tab to save results.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Raw Extraction**")
                st.json(raw_ext)
            with col2:
                st.markdown("**Preprocessed Extraction**")
                st.json(prep_ext)

            st.divider()

            if st.button("Run GPT-4o-mini Comparison", type="primary", width="stretch"):
                with st.spinner("Analyzing differences with GPT-4o-mini..."):
                    try:
                        comparison, comp_elapsed = comparator.compare(raw_ext, prep_ext)
                    except requests.exceptions.HTTPError as exc:
                        st.error(f"API error: {exc.response.status_code}")
                        return
                    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                        st.error("Could not connect to API. Check network/VPN.")
                        return

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
                        st.success(f"**Improved fields ({len(improved)}):** {', '.join(improved)}")
                    if degraded:
                        st.error(f"**Degraded fields ({len(degraded)}):** {', '.join(degraded)}")

                    field_comp = comp.get("field_comparison", {})
                    if field_comp:
                        st.markdown("**Field-by-field comparison:**")
                        st.json(field_comp)

                    st.success("Go to **Final Output** tab to save results.")
                else:
                    st.warning("LLM returned non-JSON. Raw response:")
                    st.code(comp.get("raw_llm_response", ""))

    # ── Tab 4: Final Output ──────────────────────────────────────
    with tab4:
        st.subheader("Final Output")

        if not st.session_state.comparison:
            st.warning("Complete Step 3 first — run the comparative analysis.")
            return

        skipped_final = st.session_state.preprocessing_skipped

        final = {
            "source_file": str(UPLOAD_DIR / f"raw_{uploaded.name}") if uploaded else "",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "preprocessing_skipped": skipped_final,
            "raw_extraction": st.session_state.raw_result.get("merged_extraction", {}) if st.session_state.raw_result else {},
        }

        if not skipped_final:
            final["preprocessed_extraction"] = (
                st.session_state.prep_result.get("merged_extraction", {})
                if st.session_state.prep_result else {}
            )
            final["comparative_analysis"] = st.session_state.comparison
            final["timing_seconds"] = {
                "raw_pipeline": st.session_state.raw_result.get("timing_seconds", {}).get("total", 0) if st.session_state.raw_result else 0,
                "preprocessed_pipeline": st.session_state.prep_result.get("timing_seconds", {}).get("total", 0) if st.session_state.prep_result else 0,
                "comparison": getattr(st.session_state, "comp_elapsed", 0),
            }
        else:
            final["note"] = "Preprocessing skipped — raw quality gate passed. Single-path extraction."
            final["timing_seconds"] = {
                "raw_pipeline": st.session_state.raw_result.get("timing_seconds", {}).get("total", 0) if st.session_state.raw_result else 0,
            }

        st.json(final)

        out_path = OUTPUT_DIR / "comparative_output.json"

        if st.button("Save to JSON", type="primary", width="stretch"):
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(final, fp, ensure_ascii=False, indent=2)
            st.session_state.final_saved = True
            st.success(f"Saved to `{out_path.resolve()}`")
            st.balloons()


if __name__ == "__main__":
    main()
