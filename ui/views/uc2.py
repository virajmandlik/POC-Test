"""
UC2 — Photo Verification (stepper wizard).

Steps:  Upload → Quality → Scene & GPS → Verdict
"""

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

    # ── Wizard state ──────────────────────────────────────
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
        skip_vision = st.checkbox("Skip GPT Vision", key="uc2_skip_vision")

    if uploaded:
        st.image(uploaded, caption=uploaded.name, width=480)

        if st.button("Run Full Verification", type="primary", key="uc2_run_verify", use_container_width=True):
            with st.spinner("Uploading…"):
                uploaded.seek(0)
                up_res = api.upload_file(uploaded, st.session_state.get("username", "ui"))
            if not up_res:
                st.error("Upload failed"); return

            with st.status("Verifying…", expanded=True) as status:
                resp = api.submit_job("/api/uc2/verify", {
                    "image_path": up_res["path"],
                    "skip_vision": skip_vision,
                    "user": st.session_state.get("username", "ui"),
                    "tags": ["ui", "single"],
                })
                if not resp:
                    st.error("Submission failed"); return

                job = _poll(resp["job_id"], status)

            if job and job.get("status") == "completed":
                st.session_state["uc2_result"] = job.get("result", {})
                st.session_state["uc2_img_bytes"] = uploaded.getvalue()
                _mark_done(0)
                st.success("Verification complete — use **Quality →** to review results")
            elif job:
                st.error(f"Job {job.get('status')}: {job.get('error', '')}")

    if "uc2_result" in st.session_state:
        st.caption("✅ Verification data available")


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

    checks = result.get("checks", result.get("quality", {}))
    if isinstance(checks, dict):
        qc = checks.get("quality", checks)
        _check_card("Image Quality", qc)
    elif isinstance(checks, list):
        for chk in checks:
            _check_card(chk.get("name", "Check"), chk)


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

    scene = result.get("scene", result.get("checks", {}).get("scene", {}))
    if scene:
        _check_card("Scene", scene)
        desc = scene.get("description", scene.get("scene_description", ""))
        if desc:
            st.markdown(f"**Description:** {desc}")

    section_divider()

    meta = result.get("metadata", result.get("checks", {}).get("metadata", {}))
    if meta:
        _check_card("GPS & Timestamp", meta)
        lat = meta.get("latitude") or meta.get("lat")
        lon = meta.get("longitude") or meta.get("lon")
        if lat and lon:
            try:
                st.map(pd.DataFrame({"lat": [float(lat)], "lon": [float(lon)]}), zoom=12)
            except Exception:
                st.caption(f"GPS: {lat}, {lon}")

    img = st.session_state.get("uc2_img_bytes")
    if img:
        section_divider()
        st.image(img, caption="Analysed photo", width=480)


# ═══════════════════════════════════════════════════════════════════════
# STEP 3 — Verdict
# ═══════════════════════════════════════════════════════════════════════

def _step_verdict():
    st.markdown("### Final Verdict")
    result = st.session_state.get("uc2_result", {})
    if not result:
        st.warning("Run verification first.")
        return

    _mark_done(3)

    decision = result.get("decision", result.get("verdict", "UNKNOWN")).upper()
    if decision == "ACCEPT":
        st.success("## ✅ ACCEPTED")
        st.balloons()
    elif decision == "REJECT":
        st.error("## ❌ REJECTED")
    else:
        st.warning(f"## ⚠️ {decision}")

    reasons = result.get("rejection_reasons", result.get("reasons", []))
    if reasons:
        st.markdown("#### Reasons")
        for r in reasons:
            st.markdown(f"- {r}")

    ms = result.get("processing_time_ms", result.get("elapsed_ms"))
    if ms:
        st.metric("Time", f"{ms} ms")

    section_divider()
    with st.expander("Raw API Response"):
        st.json(result)


# ═══════════════════════════════════════════════════════════════════════
# BATCH PDFs
# ═══════════════════════════════════════════════════════════════════════

def _batch_pdfs():
    section_divider()
    col_up, col_folder = st.columns(2)

    with col_up:
        batch_files = st.file_uploader(
            "Upload CC training PDFs", type=["pdf"],
            accept_multiple_files=True, key="uc2_batch_up",
        )
    with col_folder:
        folder = st.text_input("Or scan folder", value="cc_data_final", key="uc2_folder")
        scan = st.button("Scan", key="uc2_scan")

    file_paths: list[str] = []
    if batch_files:
        with st.spinner("Uploading…"):
            file_paths = [r["path"] for r in api.upload_files(batch_files, st.session_state.get("username", "ui"))]
            st.success(f"{len(file_paths)} PDFs uploaded")

    if scan and folder:
        p = Path(folder)
        if p.is_dir():
            file_paths = sorted(str(f) for f in p.iterdir() if f.suffix.lower() == ".pdf")
            st.success(f"Found {len(file_paths)} PDFs")
        else:
            st.error(f"Not found: {folder}")

    if not file_paths:
        st.info("Upload PDFs or scan a folder to begin.")
        return

    st.markdown(f"**{len(file_paths)}** PDFs ready")
    if st.button("Process All PDFs", type="primary", key="uc2_batch_go", use_container_width=True):
        resp = api.submit_job("/api/uc2/batch", {
            "pdf_paths": file_paths,
            "user": st.session_state.get("username", "ui"),
            "tags": ["ui", "batch"],
        })
        if not resp:
            st.error("Failed"); return

        prog = st.progress(0, text="Processing…")
        m1, m2, m3, m4 = st.columns(4)

        job = None
        start = time.time()
        while time.time() - start < 600:
            job = api.get_job(resp["job_id"])
            if not job: break
            prog.progress(min(job.get("progress", 0), 100), text=job.get("progress_message", "…"))
            partial = job.get("result", {})
            if isinstance(partial, dict):
                m1.metric("Processed", partial.get("processed", 0))
                m2.metric("Accepted", partial.get("accepted", 0))
                m3.metric("Rejected", partial.get("rejected", 0))
                m4.metric("Errors", partial.get("errors", 0))
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(1.5)
        prog.progress(100)

        if job and job["status"] == "completed":
            st.success("Batch complete!")
            _render_batch(job.get("result", {}))
        elif job:
            st.error(f"Batch {job['status']}: {job.get('error', '')}")


def _render_batch(result: dict):
    if not result: return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", result.get("total", result.get("processed", 0)))
    c2.metric("Accepted", result.get("accepted", 0))
    c3.metric("Rejected", result.get("rejected", 0))
    c4.metric("Errors", result.get("errors", 0))

    rows = result.get("rows", result.get("details", []))
    if rows and isinstance(rows, list):
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=400, hide_index=True)
        st.download_button("Download CSV", df.to_csv(index=False), "uc2_batch.csv", "text/csv")
    else:
        st.json(result)


# ═══════════════════════════════════════════════════════════════════════

def _poll(job_id: str, status_widget, timeout: float = 300.0) -> dict | None:
    start = time.time()
    while time.time() - start < timeout:
        job = api.get_job(job_id)
        if not job: return None
        pct = job.get("progress", 0)
        msg = job.get("progress_message", "")
        status_widget.update(label=f"Processing… {pct}% {msg}")
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(1.5)
    return api.get_job(job_id)


def _check_card(label: str, data: dict):
    if not data: return
    passed = data.get("passed", data.get("pass"))
    reason = data.get("reason", "")

    if passed is True:
        st.success(f"**{label}: PASS**")
    elif passed is False:
        st.error(f"**{label}: FAIL**")
        if reason: st.caption(reason)
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
