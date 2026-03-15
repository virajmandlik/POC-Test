"""
Carbon Credit Training Photo Verification — Streamlit Demo UI

Upload a training session photo and see GPT Vision verify it
step by step with a final accept/reject verdict.

Run:
    streamlit run cc_verify_ui.py

FastAPI endpoint:
    python app.py
    curl -X POST http://localhost:8000/verify-training -F "photo=@training_photo.jpg"

POC DEMO SCENARIOS:

  1. ACCEPT case:
     - Photo of 2+ people outdoors taken with GPS Map Camera app.
     - GPS overlay and training scene visible.

  2. REJECT — blurry:
     - Use any blurry photo (out of focus, motion blur).
     - Quality check will fail (blur score below 80).

  3. REJECT — no GPS overlay:
     - Upload a random photo without GPS Map Camera watermark.
     - Metadata check will fail (no GPS/timestamp extracted).

  4. REJECT — single person / selfie:
     - Upload a selfie with only one person.
     - Scene analysis will flag insufficient people count.
"""

import io
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from PIL import Image

from cc_verify import (
    TrainingPhotoVerifier,
    VerificationResult,
)

load_dotenv()


def _render_check_card(label: str, passed: bool, details: dict, reason: str):
    """Render a single check result as a styled card."""
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


def main():
    st.set_page_config(
        page_title="CC Training Photo Verification",
        page_icon="🌳",
        layout="wide",
    )

    st.markdown(
        "<h1 style='text-align:center;'>Carbon Credit Training — Photo Verification</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Upload training session photo &rarr; Automated checks &rarr; Accept / Reject"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    api_key = os.environ.get("CXAI_API_KEY", "")

    for key in ("result", "img"):
        if key not in st.session_state:
            st.session_state[key] = None

    tab_upload, tab_checks, tab_scene, tab_verdict = st.tabs([
        "1. Upload Photo",
        "2. Quality Check",
        "3. Scene & Metadata (GPT Vision)",
        "4. Final Verdict",
    ])

    # ── Tab 1: Upload ───────────────────────────────────────────
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
                st.warning("CXAI_API_KEY not set in `.env`. Vision analysis will fail.")

        if st.session_state.img is not None:
            if st.button("Run All Verification Checks", type="primary", use_container_width=True):
                verifier = TrainingPhotoVerifier(api_key=api_key)

                with st.spinner("Running verification pipeline..."):
                    result = verifier.verify(
                        img=st.session_state.img,
                        skip_vision=skip_vision,
                    )

                st.session_state.result = result
                st.success(f"Verification complete in {result.processing_time_ms} ms. Check other tabs for details.")
        else:
            st.info("Upload a photo to begin verification.")

    result: VerificationResult | None = st.session_state.result

    # ── Tab 2: Quality Check ──────────────────────────────────────
    with tab_checks:
        st.subheader("Image Quality")

        if not result:
            st.warning("Run verification from Tab 1 first.")
            return

        qc = result.checks.get("image_quality")
        if qc:
            _render_check_card("Image Quality", qc.passed, qc.details, qc.reason)

    # ── Tab 3: Scene & Metadata (GPT Vision) ──────────────────────
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

        # Metadata extracted from overlay
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

    # ── Tab 4: Final Verdict ──────────────────────────────────────
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


if __name__ == "__main__":
    main()
