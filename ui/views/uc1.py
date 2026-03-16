"""
UC1 — Land Record OCR & Extraction (stepper wizard).

Steps:  Upload → Quality → Extract → Semantic → Output
Each step has Back/Next navigation with colour-coded progress.
"""

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider, stepper, step_nav

_STEPS = ["Upload", "Quality", "Extract", "Semantic", "Output"]
_SS = "uc1_step"          # session-state key for current step
_DONE = "uc1_done"        # set of completed step indices


def render():
    page_header(
        "Land Record OCR & Extraction",
        "Upload Maharashtra 7/12 documents — extract, compare, and analyse",
    )

    mode = st.radio(
        "Mode", ["Single Document", "Batch Processing"], horizontal=True, key="uc1_mode",
    )

    if mode == "Batch Processing":
        _batch_processing()
        return

    # ── Initialise wizard state ───────────────────────────
    if _SS not in st.session_state:
        st.session_state[_SS] = 0
    if _DONE not in st.session_state:
        st.session_state[_DONE] = set()

    cur = st.session_state[_SS]
    done = st.session_state[_DONE]

    # ── Stepper bar ───────────────────────────────────────
    stepper(_STEPS, cur, done)

    # ── Render current step ───────────────────────────────
    [_step_upload, _step_quality, _step_extract, _step_semantic, _step_output][cur]()

    # ── Back / Next ───────────────────────────────────────
    section_divider()
    new = step_nav(
        cur, len(_STEPS), "uc1",
        next_disabled=_next_disabled(cur),
        next_label=_next_label(cur),
    )
    if new is not None:
        st.session_state[_SS] = new
        st.rerun()


def _next_disabled(cur: int) -> bool:
    """Disable Next when the current step's prerequisite isn't met."""
    if cur == 0:
        return "uc1_file_path" not in st.session_state
    if cur == 2:
        return "uc1_result" not in st.session_state
    return False


def _next_label(cur: int) -> str:
    labels = {0: "Quality →", 1: "Extract →", 2: "Semantic →", 3: "Output →"}
    return labels.get(cur, "Next →")


def _mark_done(step: int):
    st.session_state.setdefault(_DONE, set()).add(step)


# ═══════════════════════════════════════════════════════════════════════
# STEP 0 — Upload
# ═══════════════════════════════════════════════════════════════════════

def _step_upload():
    st.markdown("### Upload Document")
    col_up, col_opts = st.columns([3, 1])

    with col_up:
        uploaded = st.file_uploader(
            "PDF or scanned image",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            key="uc1_upload",
        )
    with col_opts:
        st.selectbox(
            "Extraction mode",
            ["combined", "paddle", "vision"],
            format_func=lambda m: {"combined": "Combined (best)", "paddle": "PaddleOCR", "vision": "GPT-4 Vision"}[m],
            key="uc1_extract_mode",
        )
        st.selectbox("Language", ["mr", "hi", "en"], key="uc1_lang")

    if uploaded:
        if uploaded.type == "application/pdf":
            st.info(f"📄 **{uploaded.name}** — {uploaded.size / 1024:.1f} KB")
        else:
            st.image(uploaded, caption=uploaded.name, width=480)

        if st.button("Upload to Server", type="primary", key="uc1_do_upload"):
            with st.spinner("Uploading…"):
                uploaded.seek(0)
                res = api.upload_file(uploaded, user=st.session_state.get("username", "ui"))
            if res:
                st.session_state["uc1_file_path"] = res["path"]
                st.session_state["uc1_file_name"] = res["filename"]
                _mark_done(0)
                st.success(f"**{res['filename']}** uploaded — click **Quality →** to continue")
            else:
                st.error("Upload failed")

    if "uc1_file_path" in st.session_state:
        st.caption(f"✅ File ready: `{st.session_state.get('uc1_file_name', '')}`")


# ═══════════════════════════════════════════════════════════════════════
# STEP 1 — Quality Check
# ═══════════════════════════════════════════════════════════════════════

def _step_quality():
    st.markdown("### Quality Check")
    fp = st.session_state.get("uc1_file_path")
    if not fp:
        st.warning("Upload a document first (step 1).")
        return

    st.caption(f"File: `{st.session_state.get('uc1_file_name', fp)}`")

    uploaded = st.session_state.get("uc1_upload")
    if uploaded and st.button("Run Quality Gate", type="primary", key="uc1_qc_run"):
        with st.spinner("Analysing…"):
            uploaded.seek(0)
            try:
                import requests
                r = requests.post(
                    f"{api.API_URL}/api/uc1/quality-check",
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                    params={"user": st.session_state.get("username", "ui")},
                    timeout=30,
                )
                r.raise_for_status()
                qc = r.json()
            except Exception as exc:
                st.error(f"Failed: {exc}")
                return

        st.session_state["uc1_qc"] = qc
        _mark_done(1)

    qc = st.session_state.get("uc1_qc")
    if qc:
        passed = qc.get("gate_passed", qc.get("passed", False))
        if passed:
            st.success("✅ Quality gate **PASSED**")
        else:
            reasons = qc.get("gate_reasons") or qc.get("issues") or []
            st.warning("⚠️ Quality gate **FAILED**")
            for r in reasons:
                st.markdown(f"- {r}")

        mc = st.columns(5)
        mc[0].metric("Width", qc.get("width", 0))
        mc[1].metric("Height", qc.get("height", 0))
        mc[2].metric("Sharp", qc.get("sharpness", "—"))
        mc[3].metric("Bright", f"{qc.get('mean_brightness', 0):.0f}")
        mc[4].metric("Contrast", f"{qc.get('contrast_ratio', 0):.2f}")
    elif not uploaded:
        st.info("The uploaded file is no longer in memory. You can skip to Extract or re-upload.")
        _mark_done(1)


# ═══════════════════════════════════════════════════════════════════════
# STEP 2 — Extract
# ═══════════════════════════════════════════════════════════════════════

def _step_extract():
    st.markdown("### Run Extraction Pipeline")
    fp = st.session_state.get("uc1_file_path")
    if not fp:
        st.warning("Upload a document first.")
        return

    st.caption(f"File: `{st.session_state.get('uc1_file_name', fp)}`  ·  "
               f"Mode: `{st.session_state.get('uc1_extract_mode', 'combined')}`")

    if st.button("Run Extraction", type="primary", key="uc1_run_ext"):
        mode = st.session_state.get("uc1_extract_mode", "combined")
        lang = st.session_state.get("uc1_lang", "mr")
        user = st.session_state.get("username", "ui")

        with st.status("Running extraction…", expanded=True) as status:
            resp = api.submit_job("/api/uc1/extract", {
                "file_path": fp, "mode": mode, "lang": lang,
                "user": user, "tags": ["ui", "single"],
            })
            if not resp:
                st.error("Submission failed")
                return
            job_id = resp["job_id"]
            st.write(f"Job `{job_id}` submitted")

            job = _poll(job_id, status)

        if job and job.get("status") == "completed":
            st.session_state["uc1_result"] = job.get("result", {})
            _mark_done(2)
            st.success("Extraction complete!")
        elif job:
            st.error(f"Job {job.get('status')}: {job.get('error', '')}")

    result = st.session_state.get("uc1_result")
    if result:
        st.caption("✅ Extraction data available")
        with st.expander("View extracted data"):
            st.json(result)


# ═══════════════════════════════════════════════════════════════════════
# STEP 3 — Semantic Analysis
# ═══════════════════════════════════════════════════════════════════════

def _step_semantic():
    st.markdown("### Semantic Analysis & Knowledge Graph")
    result = st.session_state.get("uc1_result")
    if not result:
        st.warning("Run extraction first (step 3).")
        return

    if st.button("Run Semantic Analysis", type="primary", key="uc1_run_sem"):
        user = st.session_state.get("username", "ui")
        with st.status("Analysing…", expanded=True) as status:
            resp = api.submit_job("/api/uc1/semantic", {
                "extraction_data": result,
                "user": user, "tags": ["ui", "semantic"],
            })
            if not resp:
                st.error("Submission failed")
                return
            job = _poll(resp["job_id"], status)

        if job and job.get("status") == "completed":
            sem = job.get("result", {})
            st.session_state["uc1_semantic"] = sem
            _mark_done(3)
        elif job:
            st.error(f"Job {job.get('status')}: {job.get('error', '')}")

    sem = st.session_state.get("uc1_semantic")
    if sem:
        st.caption("✅ Semantic analysis complete")
        _render_semantic(sem)


def _render_semantic(sem: dict):
    land = sem.get("land", sem.get("land_summary", {}))
    if land:
        st.markdown("#### Land Summary")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Survey", land.get("survey_number", "—"))
        c2.metric("Village", land.get("village", "—"))
        c3.metric("Taluka", land.get("taluka", "—"))
        c4.metric("District", land.get("district", "—"))

    owners = sem.get("owners", sem.get("ownership_chain", []))
    if owners:
        st.markdown("#### Ownership Chain")
        st.dataframe(pd.DataFrame(owners), use_container_width=True, hide_index=True)

    dot = sem.get("dot_graph", sem.get("graphviz", ""))
    if dot:
        st.markdown("#### Ownership Graph")
        st.graphviz_chart(dot, use_container_width=True)

    with st.expander("Full semantic JSON"):
        st.json(sem)


# ═══════════════════════════════════════════════════════════════════════
# STEP 4 — Final Output
# ═══════════════════════════════════════════════════════════════════════

def _step_output():
    st.markdown("### Final Output")
    result = st.session_state.get("uc1_result")
    if not result:
        st.warning("Run extraction first.")
        return

    _mark_done(4)

    final = {
        "extraction": result,
        "semantic": st.session_state.get("uc1_semantic"),
        "metadata": {
            "file": st.session_state.get("uc1_file_name", ""),
            "mode": st.session_state.get("uc1_extract_mode", ""),
        },
    }

    st.json(final)
    st.download_button(
        "⬇ Download JSON",
        data=json.dumps(final, indent=2, default=str),
        file_name="uc1_output.json",
        mime="application/json",
        type="primary",
    )


# ═══════════════════════════════════════════════════════════════════════
# BATCH PROCESSING
# ═══════════════════════════════════════════════════════════════════════

def _batch_processing():
    section_divider()
    col_up, col_folder = st.columns(2)

    with col_up:
        batch_files = st.file_uploader(
            "Upload PDFs / images", type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True, key="uc1_batch_up",
        )
    with col_folder:
        folder = st.text_input("Or scan a folder", value="uploads", key="uc1_folder")
        scan = st.button("Scan", key="uc1_scan")

    mode = st.radio(
        "Extraction", ["combined", "paddle", "vision"], horizontal=True,
        format_func=lambda m: {"combined": "Combined", "paddle": "PaddleOCR", "vision": "GPT-4 Vision"}[m],
        key="uc1_batch_mode",
    )

    file_paths: list[str] = []
    if batch_files:
        with st.spinner("Uploading…"):
            file_paths = [r["path"] for r in api.upload_files(batch_files, st.session_state.get("username", "ui"))]
            st.success(f"{len(file_paths)} files uploaded")

    if scan and folder:
        p = Path(folder)
        if p.is_dir():
            file_paths = sorted(str(f) for f in p.iterdir() if f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"))
            st.success(f"Found {len(file_paths)} docs")
        else:
            st.error(f"Not found: {folder}")

    if not file_paths:
        st.info("Upload files or scan a folder to begin.")
        return

    st.markdown(f"**{len(file_paths)}** documents ready")
    if st.button("Process All", type="primary", key="uc1_batch_go", use_container_width=True):
        resp = api.submit_job("/api/uc1/batch", {
            "file_paths": file_paths, "mode": mode,
            "lang": st.session_state.get("uc1_lang", "mr"),
            "user": st.session_state.get("username", "ui"),
            "tags": ["ui", "batch"],
        })
        if not resp:
            st.error("Submission failed"); return

        prog = st.progress(0, text="Processing…")
        job = None
        start = time.time()
        while time.time() - start < 600:
            job = api.get_job(resp["job_id"])
            if not job: break
            prog.progress(min(job.get("progress", 0), 100), text=job.get("progress_message", "…"))
            if job["status"] in ("completed", "failed", "cancelled"): break
            time.sleep(1.5)
        prog.progress(100)

        if job and job["status"] == "completed":
            st.success("Batch complete!")
            res = job.get("result", {})
            if isinstance(res, dict) and "rows" in res:
                df = pd.DataFrame(res["rows"])
                st.dataframe(df, use_container_width=True, height=400, hide_index=True)
                st.download_button("Download CSV", df.to_csv(index=False), "uc1_batch.csv", "text/csv")
            else:
                st.json(res)
        elif job:
            st.error(f"Batch {job['status']}: {job.get('error', '')}")


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
