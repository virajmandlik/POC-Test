"""
UC2 — Photo Verification (stepper wizard).

Steps:  Upload → Quality → Scene & GPS → Verdict

Rich verification view includes:
  - Detailed check cards with PASS/FAIL and metric breakdowns
  - Scene description and people count
  - GPS map with coordinates from photo overlay
  - Batch results with filtering, CSV download, and individual inspection
"""

import io
import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider, stepper, step_nav

_STEPS = ["Upload", "Quality", "Scene & GPS", "Verdict"]
_SS = "uc2_step"
_DONE = "uc2_done"


def render():
    page_header(
        "Photo Verification",
        "Verify CC training photos — quality, scene analysis, GPS & timestamp",
    )

    mode = st.radio(
        "Mode", ["Single Photo", "Batch PDFs"], horizontal=True, key="uc2_mode",
    )

    if mode == "Batch PDFs":
        _batch_pdfs()
        return

    if _SS not in st.session_state:
        st.session_state[_SS] = 0
    if _DONE not in st.session_state:
        st.session_state[_DONE] = set()

    cur = st.session_state[_SS]
    done = st.session_state[_DONE]

    stepper(_STEPS, cur, done)

    [_step_upload, _step_quality, _step_scene, _step_verdict][cur]()

    section_divider()
    new = step_nav(
        cur, len(_STEPS), "uc2",
        next_disabled=_next_disabled(cur),
        next_label=_next_label(cur),
    )
    if new is not None:
        st.session_state[_SS] = new
        st.rerun()


def _next_disabled(cur: int) -> bool:
    if cur == 0:
        return "uc2_result" not in st.session_state
    return False


def _next_label(cur: int) -> str:
    return {0: "Quality →", 1: "Scene & GPS →", 2: "Verdict →"}.get(cur, "Next →")


def _mark_done(step: int):
    st.session_state.setdefault(_DONE, set()).add(step)


# ═══════════════════════════════════════════════════════════════════════
# CHECK CARD — rich PASS/FAIL display with metrics
# ═══════════════════════════════════════════════════════════════════════

def _check_card(label: str, passed: bool, details: dict, reason: str = ""):
    """Render a check result card with PASS/FAIL status and metric details."""
    if passed:
        st.success(f"**{label}: PASS**")
    else:
        st.error(f"**{label}: FAIL**")
        if reason:
            st.markdown(f"> {reason}")

    if not details:
        return

    display_items = {
        k: v for k, v in details.items()
        if k not in ("error", "source") and v is not None
    }

    if not display_items:
        return

    cols = st.columns(min(len(display_items), 4))
    for i, (key, val) in enumerate(display_items.items()):
        col = cols[i % len(cols)]
        display_key = key.replace("_", " ").title()

        if isinstance(val, dict):
            col.json(val)
        elif isinstance(val, float):
            col.metric(display_key, f"{val:.2f}")
        elif isinstance(val, bool):
            col.metric(display_key, "Yes" if val else "No")
        elif isinstance(val, int):
            col.metric(display_key, str(val))
        else:
            col.metric(display_key, str(val)[:40] if val is not None else "N/A")


# ═══════════════════════════════════════════════════════════════════════
# STEP 0 — Upload & Verify
# ═══════════════════════════════════════════════════════════════════════

def _step_upload():
    st.markdown("### Upload & Verify Photo")
    col_up, col_opts = st.columns([3, 1])

    with col_up:
        uploaded = st.file_uploader(
            "Field / training photo",
            type=["jpg", "jpeg", "png", "webp"],
            key="uc2_upload",
        )
    with col_opts:
        skip_vision = st.checkbox(
            "Skip GPT Vision",
            key="uc2_skip_vision",
            help="Check this for offline mode (quality check only)",
        )

    if uploaded:
        st.image(uploaded, caption=f"{uploaded.name} ({uploaded.size / 1024:.1f} KB)", width=480)

        if st.button("Run Full Verification", type="primary", key="uc2_run_verify", use_container_width=True):
            with st.spinner("Uploading…"):
                uploaded.seek(0)
                up_res = api.upload_file(uploaded, st.session_state.get("username", "ui"))
            if not up_res:
                st.error("Upload failed")
                return

            with st.status("Verifying…", expanded=True) as status:
                resp = api.submit_job("/api/uc2/verify", {
                    "image_path": up_res["path"],
                    "skip_vision": skip_vision,
                    "user": st.session_state.get("username", "ui"),
                    "tags": ["ui", "single"],
                })
                if not resp:
                    st.error("Submission failed")
                    return

                job = _poll(resp["job_id"], status)

            if job and job.get("status") == "completed":
                st.session_state["uc2_result"] = job.get("result", {})
                st.session_state["uc2_img_bytes"] = uploaded.getvalue()
                _mark_done(0)
                st.success("Verification complete — use **Quality →** to review results")
            elif job:
                st.error(f"Job {job.get('status')}: {job.get('error', '')}")

    if "uc2_result" in st.session_state:
        st.caption("Verification data available")


# ═══════════════════════════════════════════════════════════════════════
# STEP 1 — Quality
# ═══════════════════════════════════════════════════════════════════════

def _step_quality():
    st.markdown("### Image Quality")
    result = st.session_state.get("uc2_result", {})
    if not result:
        st.warning("Run verification first (step 1).")
        return

    _mark_done(1)

    checks = result.get("checks", {})

    qc = checks.get("image_quality", {})
    if qc:
        passed = qc.get("passed", False)
        details = {
            "blur_score": qc.get("blur_score"),
            "sharpness": qc.get("sharpness"),
            "mean_brightness": qc.get("mean_brightness"),
            "contrast_ratio": qc.get("contrast_ratio"),
        }
        details = {k: v for k, v in details.items() if v is not None}
        reason = qc.get("reason", "")

        _check_card("Image Quality", passed, details, reason)
    else:
        quality_data = checks.get("quality", checks)
        if isinstance(quality_data, dict):
            _check_card_from_raw("Image Quality", quality_data)


# ═══════════════════════════════════════════════════════════════════════
# STEP 2 — Scene & GPS
# ═══════════════════════════════════════════════════════════════════════

def _step_scene():
    st.markdown("### Scene Analysis & Metadata")
    result = st.session_state.get("uc2_result", {})
    if not result:
        st.warning("Run verification first.")
        return

    _mark_done(2)
    checks = result.get("checks", {})

    scene = checks.get("scene_analysis", {})
    if scene:
        if "error" in scene and not scene.get("people_count"):
            st.error(f"Vision API error: {scene.get('error')}")
        else:
            scene_passed = scene.get("passed", False)
            scene_details = {}
            for k in ("people_count", "has_multiple_people", "has_representative",
                       "is_training_scene", "is_outdoor_rural", "confidence"):
                if k in scene:
                    scene_details[k] = scene[k]

            scene_reason = scene.get("reason", "")
            _check_card("Scene Analysis", scene_passed, scene_details, scene_reason)

            desc = scene.get("scene_description", "")
            if desc:
                st.markdown(f"**Scene Description:** {desc}")

            if scene.get("has_visible_timestamp"):
                st.markdown("**Overlay Info Detected:**")
                overlay_cols = st.columns(4)
                overlay_cols[0].metric("Overlay Date", scene.get("overlay_date") or "—")
                overlay_cols[1].metric("Overlay Time", scene.get("overlay_time") or "—")
                overlay_cols[2].metric("Overlay Lat", f"{scene.get('overlay_latitude', '—')}")
                overlay_cols[3].metric("Overlay Lon", f"{scene.get('overlay_longitude', '—')}")
    else:
        scene_raw = checks.get("scene", {})
        if scene_raw:
            _check_card_from_raw("Scene", scene_raw)
        else:
            st.info("Scene analysis was skipped (offline mode).")

    section_divider()

    meta = checks.get("metadata", {})
    if meta:
        meta_passed = meta.get("passed", False)
        meta_details = {}
        gps = meta.get("gps")
        if gps and isinstance(gps, dict):
            meta_details["GPS Lat"] = gps.get("lat")
            meta_details["GPS Lon"] = gps.get("lon")
        ts = meta.get("timestamp")
        if ts:
            meta_details["Timestamp"] = ts
        meta_details["Source"] = meta.get("source", "")

        meta_reason = meta.get("reason", "")
        _check_card("GPS & Timestamp (from overlay)", meta_passed, meta_details, meta_reason)

        if gps and gps.get("lat") is not None and gps.get("lon") is not None:
            try:
                lat = float(gps["lat"])
                lon = float(gps["lon"])
                st.markdown("**Photo GPS Location**")
                map_df = pd.DataFrame({"lat": [lat], "lon": [lon]})
                st.map(map_df, zoom=12)
            except (ValueError, TypeError):
                st.caption(f"GPS: {gps.get('lat')}, {gps.get('lon')}")
    else:
        meta_raw = checks.get("metadata_raw", {})
        if meta_raw:
            _check_card_from_raw("GPS & Timestamp", meta_raw)
        else:
            st.info("Metadata extraction was skipped (offline mode).")

    img = st.session_state.get("uc2_img_bytes")
    if img:
        section_divider()
        st.image(img, caption="Analysed photo", width=480)


# ═══════════════════════════════════════════════════════════════════════
# STEP 3 — Verdict
# ═══════════════════════════════════════════════════════════════════════

def _step_verdict():
    st.markdown("### Final Verification Verdict")
    result = st.session_state.get("uc2_result", {})
    if not result:
        st.warning("Run verification first.")
        return

    _mark_done(3)

    decision = result.get("decision", result.get("verdict", "UNKNOWN")).upper()
    if decision == "ACCEPT":
        st.success("## ACCEPTED")
        st.markdown("This training session photo **meets all verification criteria**.")
        st.balloons()
    elif decision == "REJECT":
        st.error("## REJECTED")
        st.markdown("This training session photo **failed verification**.")
    else:
        st.warning(f"## {decision}")

    section_divider()

    st.markdown("### Check Summary")
    checks = result.get("checks", {})
    for name, check_data in checks.items():
        label = name.replace("_", " ").title()
        passed = check_data.get("passed", None)
        reason = check_data.get("reason", "")
        if passed is True:
            st.markdown(f"- **{label}**: PASS")
        elif passed is False:
            st.markdown(f"- **{label}**: FAIL — {reason}")
        else:
            st.markdown(f"- **{label}**: {check_data}")

    reasons = result.get("rejection_reasons", result.get("reasons", []))
    if reasons:
        section_divider()
        st.markdown("### Rejection Reasons")
        for r in reasons:
            st.markdown(f"- {r}")

    section_divider()

    ms = result.get("metadata", {}).get("processing_time_ms",
         result.get("processing_time_ms", result.get("elapsed_ms")))
    if ms:
        st.metric("Processing Time", f"{ms} ms")

    section_divider()
    with st.expander("Raw API Response"):
        st.json(result)


# ═══════════════════════════════════════════════════════════════════════
# BATCH PDFs
# ═══════════════════════════════════════════════════════════════════════

def _batch_pdfs():
    section_divider()

    import streamlit.components.v1 as components
    _BATCH_HTML = """
    <div style="background:#F1F8E9;border:2px solid #C8E6C9;border-radius:16px;padding:24px;margin-bottom:16px;">
      <div style="text-align:center;margin-bottom:16px;">
        <span style="font-size:2rem;">🌾</span>
        <div style="font-weight:700;font-size:1.1rem;color:#1B3A1B;margin-top:4px;">Digilekha Batch Upload</div>
        <div style="color:#5D7A5D;font-size:0.8rem;">Upload training PDFs from any source</div>
      </div>
      <div style="display:flex;justify-content:center;gap:24px;flex-wrap:wrap;">
        <div style="text-align:center;opacity:0.6;">
          <img src="https://www.google.com/favicon.ico" width="28" height="28" style="border-radius:6px;"/>
          <div style="font-size:0.7rem;color:#5D7A5D;margin-top:4px;">Google Drive</div>
          <div style="font-size:0.6rem;color:#999;">Coming Soon</div>
        </div>
        <div style="text-align:center;opacity:0.6;">
          <img src="https://www.dropbox.com/static/30168/images/favicon.ico" width="28" height="28" style="border-radius:6px;"/>
          <div style="font-size:0.7rem;color:#5D7A5D;margin-top:4px;">Dropbox</div>
          <div style="font-size:0.6rem;color:#999;">Coming Soon</div>
        </div>
        <div style="text-align:center;opacity:0.6;">
          <img src="https://www.microsoft.com/favicon.ico" width="28" height="28" style="border-radius:6px;"/>
          <div style="font-size:0.7rem;color:#5D7A5D;margin-top:4px;">OneDrive</div>
          <div style="font-size:0.6rem;color:#999;">Coming Soon</div>
        </div>
        <div style="text-align:center;">
          <div style="width:28px;height:28px;background:#2E7D32;border-radius:6px;display:inline-flex;align-items:center;justify-content:center;color:white;font-size:16px;">📁</div>
          <div style="font-size:0.7rem;color:#2E7D32;margin-top:4px;font-weight:600;">Local Upload</div>
          <div style="font-size:0.6rem;color:#2E7D32;">Active ✓</div>
        </div>
      </div>
    </div>
    """
    components.html(_BATCH_HTML, height=200)

    batch_files = st.file_uploader(
        "Upload CC training PDFs", type=["pdf"],
        accept_multiple_files=True, key="uc2_batch_up",
    )

    file_paths: list[str] = []
    if batch_files:
        with st.spinner("Uploading…"):
            file_paths = [r["path"] for r in api.upload_files(batch_files, st.session_state.get("username", "ui"))]
            st.success(f"{len(file_paths)} PDFs uploaded")

    if not file_paths:
        st.info("Upload PDFs to begin batch verification.")
        return

    st.markdown(f"**{len(file_paths)}** PDFs ready for processing")
    if st.button("Process All PDFs", type="primary", key="uc2_batch_go", use_container_width=True):
        resp = api.submit_job("/api/uc2/batch", {
            "pdf_paths": file_paths,
            "user": st.session_state.get("username", "ui"),
            "tags": ["ui", "batch"],
        })
        if not resp:
            st.error("Failed")
            return

        prog = st.progress(0, text="Processing…")
        m1, m2, m3, m4 = st.columns(4)
        m_total = m1.empty()
        m_accept = m2.empty()
        m_reject = m3.empty()
        m_error = m4.empty()

        job = None
        start = time.time()
        while time.time() - start < 600:
            job = api.get_job(resp["job_id"])
            if not job:
                break
            pct = min(job.get("progress", 0), 100)
            msg = job.get("progress_message", "…")
            prog.progress(pct / 100 if pct <= 100 else 1.0, text=msg)

            partial = job.get("result", {})
            if isinstance(partial, dict):
                m_total.metric("Processed", partial.get("total", 0))
                m_accept.metric("Accepted", partial.get("accepted", 0))
                m_reject.metric("Rejected", partial.get("rejected", 0))
                m_error.metric("Errors", partial.get("errors", 0))
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(1.5)
        prog.progress(1.0, text="Done")

        if job and job["status"] == "completed":
            st.session_state["uc2_batch_result"] = job.get("result", {})
            st.success("Batch complete!")
        elif job:
            st.error(f"Batch {job['status']}: {job.get('error', '')}")

    batch_result = st.session_state.get("uc2_batch_result")
    if batch_result:
        section_divider()
        _render_batch_results(batch_result)


def _get_photo_bytes(photo_path: str, pdf_file: str) -> bytes | None:
    """Get photo bytes from saved file or extract from PDF on-the-fly."""
    if photo_path and Path(photo_path).exists():
        return Path(photo_path).read_bytes()

    if pdf_file and Path(pdf_file).exists() and pdf_file.lower().endswith(".pdf"):
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(pdf_file)
            page_idx = min(2, len(doc) - 1)
            bitmap = doc[page_idx].render(scale=2.0)
            img = bitmap.to_pil().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return buf.getvalue()
        except Exception:
            return None

    return None


def _render_batch_results(result: dict):
    """Render batch results with summary, filtering, table, CSV download,
    and individual result inspection with images."""
    if not result:
        return

    st.subheader("Verification Results")

    c1, c2, c3, c4 = st.columns(4)
    total = result.get("total", 0)
    accepted = result.get("accepted", 0)
    rejected = result.get("rejected", 0)
    errors = result.get("errors", 0)
    c1.metric("Total", total)
    c2.metric("Accepted", accepted)
    c3.metric("Rejected", rejected)
    c4.metric("Errors", errors)

    section_divider()

    results_list = result.get("results", result.get("rows", result.get("details", [])))
    if not results_list or not isinstance(results_list, list):
        with st.expander("View Raw Result"):
            st.json(result)
        return

    rows_for_table = []
    for r in results_list:
        row = {
            "LID": r.get("lid", ""),
            "Decision": r.get("decision", ""),
            "File": Path(r.get("file", "")).name if r.get("file") else "",
        }
        data = r.get("data", {})
        if isinstance(data, dict):
            checks = data.get("checks", {})
            scene = checks.get("scene_analysis", {})
            if scene:
                row["People"] = scene.get("people_count", "")
                row["Multiple People"] = scene.get("has_multiple_people", "")
                row["Training Scene"] = scene.get("is_training_scene", "")
                row["Lat"] = scene.get("overlay_latitude", "")
                row["Lon"] = scene.get("overlay_longitude", "")
                row["Description"] = str(scene.get("scene_description", ""))[:60]
        rows_for_table.append(row)

    df = pd.DataFrame(rows_for_table)

    filter_decision = st.multiselect(
        "Filter by decision",
        options=["ACCEPT", "REJECT", "ERROR"],
        default=["ACCEPT", "REJECT", "ERROR"],
        key="uc2_batch_filter",
    )
    if "Decision" in df.columns:
        filtered = df[df["Decision"].isin(filter_decision)]
    else:
        filtered = df

    st.dataframe(filtered, use_container_width=True, height=min(400, 50 + 35 * len(filtered)), hide_index=True)

    section_divider()

    csv_buf = io.StringIO()
    df.to_csv(csv_buf, index=False)
    st.download_button(
        "Download Full Results CSV",
        data=csv_buf.getvalue(),
        file_name="uc2_verification_results.csv",
        mime="text/csv",
        use_container_width=True,
    )

    section_divider()
    st.subheader("Inspect Individual Results")

    for idx, item in enumerate(results_list):
        decision = item.get("decision", "UNKNOWN")
        lid = item.get("lid", "")
        fname = Path(item.get("file", f"doc_{idx}")).name
        icon = "+" if decision == "ACCEPT" else "-"
        label = f"[{icon}] {lid or fname} — {decision}"

        with st.expander(label):
            photo_path = item.get("photo_path", "")
            data = item.get("data", {})
            pdf_file = item.get("file", "")

            # Try saved photo, else extract from PDF on-the-fly
            photo_bytes = _get_photo_bytes(photo_path, pdf_file)

            if photo_bytes:
                col_img, col_checks = st.columns([1, 2])
                with col_img:
                    st.image(photo_bytes, caption=f"Extracted from {fname}", use_container_width=True)
            else:
                col_checks = st.container()

            with col_checks:
                if isinstance(data, dict) and data:
                    checks = data.get("checks", {})

                    qc = checks.get("image_quality", {})
                    if qc:
                        qc_details = {k: v for k, v in qc.items()
                                      if k not in ("passed", "reason") and v is not None}
                        _check_card("Image Quality", qc.get("passed", False), qc_details, qc.get("reason", ""))

                    scene = checks.get("scene_analysis", {})
                    if scene:
                        scene_details = {}
                        for k in ("people_count", "has_multiple_people", "has_representative",
                                   "is_training_scene", "is_outdoor_rural", "confidence"):
                            if k in scene:
                                scene_details[k] = scene[k]
                        _check_card("Scene Analysis", scene.get("passed", False), scene_details, scene.get("reason", ""))

                        desc = scene.get("scene_description", "")
                        if desc:
                            st.markdown(f"**Description:** {desc}")

                    meta = checks.get("metadata", {})
                    if meta:
                        meta_details = {}
                        gps = meta.get("gps")
                        if gps and isinstance(gps, dict):
                            meta_details["GPS Lat"] = gps.get("lat")
                            meta_details["GPS Lon"] = gps.get("lon")
                        ts = meta.get("timestamp")
                        if ts:
                            meta_details["Timestamp"] = ts
                        _check_card("Metadata", meta.get("passed", False), meta_details, meta.get("reason", ""))

                        if gps and gps.get("lat") is not None and gps.get("lon") is not None:
                            try:
                                lat = float(gps["lat"])
                                lon = float(gps["lon"])
                                map_df = pd.DataFrame({"lat": [lat], "lon": [lon]})
                                st.map(map_df, zoom=10)
                            except (ValueError, TypeError):
                                pass

                    reasons = data.get("rejection_reasons", [])
                    if reasons:
                        st.markdown("**Rejection Reasons:**")
                        for r in reasons:
                            st.markdown(f"- {r}")

                    ms = data.get("metadata", {}).get("processing_time_ms", 0)
                    if ms:
                        st.caption(f"Processing time: {ms} ms")

                    with st.expander("Raw JSON"):
                        st.json(data)

            if item.get("error"):
                st.error(item["error"])


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _check_card_from_raw(label: str, data: dict):
    """Fallback for older/different response shapes."""
    if not data:
        return
    passed = data.get("passed", data.get("pass"))
    reason = data.get("reason", "")

    if passed is True:
        st.success(f"**{label}: PASS**")
    elif passed is False:
        st.error(f"**{label}: FAIL**")
        if reason:
            st.caption(reason)
    else:
        st.info(f"**{label}**")

    details = data.get("details", {})
    if isinstance(details, dict) and details:
        cols = st.columns(min(len(details), 4))
        for i, (k, v) in enumerate(details.items()):
            col = cols[i % len(cols)]
            dk = k.replace("_", " ").title()
            if isinstance(v, dict):
                col.json(v)
            elif isinstance(v, float):
                col.metric(dk, f"{v:.2f}")
            elif isinstance(v, bool):
                col.metric(dk, "Yes" if v else "No")
            else:
                col.metric(dk, str(v)[:30])


def _poll(job_id: str, status_widget, timeout: float = 300.0) -> dict | None:
    start = time.time()
    while time.time() - start < timeout:
        job = api.get_job(job_id)
        if not job:
            return None
        pct = job.get("progress", 0)
        msg = job.get("progress_message", "")
        status_widget.update(label=f"Processing… {pct}% {msg}")
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(1.5)
    return api.get_job(job_id)
