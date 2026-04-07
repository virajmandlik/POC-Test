"""
Field App — Mobile-responsive Streamlit view for field engineers.

Single-column layout optimised for phone browsers.
Supports UC1 (land record extraction) and UC2 (training photo capture).
Offline mode enqueues results into the sync queue for later enrichment.
"""

import io
import json
import time
from pathlib import Path

import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider

_MOBILE_CSS = """
<style>
.mobile-card {
    background: #FFFFFF;
    border: 1px solid #C8E6C9;
    border-radius: 16px;
    padding: 1.25rem;
    margin-bottom: 1rem;
    box-shadow: 0 2px 8px rgba(46,125,50,0.08);
}
.mobile-card h4 {
    color: #1B3A1B;
    margin: 0 0 0.75rem;
    font-size: 1.1rem;
}
.result-field {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #E8F5E9;
    font-size: 0.95rem;
}
.result-field:last-child { border-bottom: none; }
.result-label { color: #5D7A5D; font-weight: 500; }
.result-value { color: #1B3A1B; font-weight: 600; text-align: right; max-width: 60%; }
.sync-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
}
.sync-pending { background: #FFF8E1; color: #F57F17; }
.sync-done    { background: #E8F5E9; color: #2E7D32; }
.verdict-accept {
    background: #E8F5E9; border: 2px solid #2E7D32;
    border-radius: 12px; padding: 1rem; text-align: center;
    color: #1B5E20; font-size: 1.2rem; font-weight: 700;
}
.verdict-reject {
    background: #FFEBEE; border: 2px solid #C62828;
    border-radius: 12px; padding: 1rem; text-align: center;
    color: #C62828; font-size: 1.2rem; font-weight: 700;
}
</style>
"""


def render():
    st.markdown(_MOBILE_CSS, unsafe_allow_html=True)
    page_header("📱 Field App", "Mobile view for field engineers")

    sync_counts = _get_sync_counts()
    if sync_counts:
        pending = sync_counts.get("pending", 0)
        if pending > 0:
            st.markdown(
                f'<span class="sync-badge sync-pending">🔄 {pending} pending sync</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<span class="sync-badge sync-done">✓ All synced</span>',
                unsafe_allow_html=True,
            )

    force_offline = st.toggle("📴 Simulate Offline Mode", key="field_force_offline",
                               help="For demo: forces offline behavior without disconnecting internet")

    online = False if force_offline else _check_online()
    if force_offline:
        st.warning("📴 Offline (simulated) — using PaddleOCR local extraction")
    elif online:
        st.success("🌐 Online — full AI extraction available")
    else:
        st.warning("📴 Offline — using PaddleOCR local extraction")

    section_divider()

    use_case = st.radio(
        "What do you need?",
        ["📄 Scan Land Record (UC1)", "📷 Training Photo (UC2)"],
        horizontal=True,
        key="field_uc",
    )

    if "UC1" in use_case:
        _uc1_flow(online)
    else:
        _uc2_flow(online)


# ═══════════════════════════════════════════════════════════════════════
# UC1 — Land Record Scanning
# ═══════════════════════════════════════════════════════════════════════

def _uc1_flow(online: bool):
    st.markdown('<div class="mobile-card"><h4>📄 Scan Land Record</h4></div>', unsafe_allow_html=True)

    tab_cam, tab_file = st.tabs(["📷 Camera", "📁 Upload File"])

    with tab_cam:
        cam_img = st.camera_input("Take photo of land record", key="field_cam_uc1")
        if cam_img:
            st.session_state["field_uc1_bytes"] = cam_img.getvalue()
            st.session_state["field_uc1_name"] = "camera_capture.jpg"
            st.session_state["field_uc1_type"] = "image/jpeg"

    with tab_file:
        uploaded = st.file_uploader(
            "Upload document",
            type=["pdf", "png", "jpg", "jpeg"],
            key="field_file_uc1",
        )
        if uploaded:
            st.session_state["field_uc1_bytes"] = uploaded.getvalue()
            st.session_state["field_uc1_name"] = uploaded.name
            st.session_state["field_uc1_type"] = uploaded.type

    if "field_uc1_bytes" not in st.session_state:
        return

    mode = "paddle" if not online else "combined"
    st.caption(f"Mode: **{'PaddleOCR (offline)' if mode == 'paddle' else 'Combined (online)'}**")

    if st.button("🔍 Extract Data", type="primary", key="field_uc1_go", use_container_width=True):
        _run_uc1_extraction(mode)

    result = st.session_state.get("field_uc1_result")
    if result:
        _render_uc1_result(result, mode)


def _run_uc1_extraction(mode: str):
    name = st.session_state.get("field_uc1_name", "capture.jpg")
    raw = st.session_state["field_uc1_bytes"]
    ftype = st.session_state.get("field_uc1_type", "image/jpeg")
    user = st.session_state.get("username", "field_engineer")

    with st.spinner("Uploading..."):
        try:
            import requests as req
            r = req.post(
                f"{api.API_URL}/api/upload",
                files={"file": (name, raw, ftype)},
                params={"user": user},
                timeout=30,
            )
            r.raise_for_status()
            up = r.json()
        except Exception as exc:
            st.error(f"Upload failed: {exc}")
            return

    fp = up["path"]
    prog = st.progress(0, text="Extracting...")

    resp = api.submit_job("/api/uc1/extract", {
        "file_path": fp, "mode": mode, "lang": "mr",
        "user": user, "tags": ["field_app"],
    })
    if not resp:
        st.error("Extraction submission failed")
        return

    job_id = resp["job_id"]
    start = time.time()
    while time.time() - start < 300:
        job = api.get_job(job_id)
        if not job:
            break
        pct = min(job.get("progress", 0), 100)
        prog.progress(pct / 100, text=f"Extracting... {pct}%")
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(1.5)

    prog.progress(1.0, text="Done")

    if job and job.get("status") == "completed":
        st.session_state["field_uc1_result"] = job.get("result", {})
        st.session_state["field_uc1_file_path"] = fp

        if mode == "paddle":
            _enqueue_sync("uc1", fp, job.get("result", {}), user)
    elif job:
        st.error(f"Extraction {job.get('status')}: {job.get('error', '')}")


def _render_uc1_result(result: dict, mode: str):
    merged = result.get("merged_extraction", result)
    if not isinstance(merged, dict):
        st.json(result)
        return

    st.markdown('<div class="mobile-card"><h4>📋 Extracted Data</h4>', unsafe_allow_html=True)

    fields = [
        ("Survey No.", merged.get("survey_number", "—")),
        ("Village", merged.get("village", "—")),
        ("Taluka", merged.get("taluka", "—")),
        ("District", merged.get("district", "—")),
    ]

    owners = merged.get("owners", [])
    if owners and isinstance(owners, list):
        for o in owners:
            if isinstance(o, dict):
                fields.append(("Owner", o.get("name", "—")))
                fields.append(("Area", f"{o.get('area_hectare', '—')} ha"))

    for label, value in fields:
        st.markdown(
            f'<div class="result-field">'
            f'<span class="result-label">{label}</span>'
            f'<span class="result-value">{value}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown('</div>', unsafe_allow_html=True)

    if mode == "paddle":
        st.info("📤 Queued for auto-sync — GPT-4 Vision will enrich when online")

    with st.expander("View full JSON"):
        st.json(result)


# ═══════════════════════════════════════════════════════════════════════
# UC2 — Training Photo Verification
# ═══════════════════════════════════════════════════════════════════════

def _uc2_flow(online: bool):
    st.markdown('<div class="mobile-card"><h4>📷 Training Photo Verification</h4></div>', unsafe_allow_html=True)

    tab_cam, tab_file = st.tabs(["📷 Camera", "📁 Upload"])

    with tab_cam:
        cam_img = st.camera_input("Take training session photo", key="field_cam_uc2")
        if cam_img:
            st.session_state["field_uc2_bytes"] = cam_img.getvalue()
            st.session_state["field_uc2_name"] = "training_photo.jpg"
            st.session_state["field_uc2_type"] = "image/jpeg"

    with tab_file:
        uploaded = st.file_uploader(
            "Upload training photo",
            type=["jpg", "jpeg", "png", "webp"],
            key="field_file_uc2",
        )
        if uploaded:
            st.session_state["field_uc2_bytes"] = uploaded.getvalue()
            st.session_state["field_uc2_name"] = uploaded.name
            st.session_state["field_uc2_type"] = uploaded.type

    if "field_uc2_bytes" not in st.session_state:
        return

    if not online:
        st.warning("📴 Offline — photo will be queued for verification when internet returns")

    if st.button("🔍 Verify Photo", type="primary", key="field_uc2_go", use_container_width=True):
        _run_uc2_verification(online)

    result = st.session_state.get("field_uc2_result")
    if result:
        _render_uc2_result(result)


def _run_uc2_verification(online: bool):
    name = st.session_state.get("field_uc2_name", "photo.jpg")
    raw = st.session_state["field_uc2_bytes"]
    ftype = st.session_state.get("field_uc2_type", "image/jpeg")
    user = st.session_state.get("username", "field_engineer")

    with st.spinner("Uploading..."):
        try:
            import requests as req
            r = req.post(
                f"{api.API_URL}/api/upload",
                files={"file": (name, raw, ftype)},
                params={"user": user},
                timeout=30,
            )
            r.raise_for_status()
            up = r.json()
        except Exception as exc:
            st.error(f"Upload failed: {exc}")
            return

    fp = up["path"]

    if not online:
        _enqueue_sync("uc2", fp, {}, user)
        st.session_state["field_uc2_result"] = {"decision": "QUEUED", "message": "Photo queued for verification when internet returns"}
        return

    with st.spinner("Verifying..."):
        resp = api.submit_job("/api/uc2/verify", {
            "image_path": fp,
            "skip_vision": False,
            "user": user,
            "tags": ["field_app"],
        })
        if not resp:
            st.error("Submission failed")
            return

        job = api.poll_job(resp["job_id"], timeout=120)

    if job and job.get("status") == "completed":
        st.session_state["field_uc2_result"] = job.get("result", {})
    elif job:
        st.error(f"Verification {job.get('status')}: {job.get('error', '')}")


def _render_uc2_result(result: dict):
    decision = result.get("decision", "UNKNOWN").upper()

    if decision == "ACCEPT":
        st.markdown('<div class="verdict-accept">✅ ACCEPTED</div>', unsafe_allow_html=True)
    elif decision == "REJECT":
        st.markdown('<div class="verdict-reject">❌ REJECTED</div>', unsafe_allow_html=True)
    elif decision == "QUEUED":
        st.info(f"📤 {result.get('message', 'Queued for processing')}")
        return
    else:
        st.warning(f"⚠️ {decision}")

    checks = result.get("checks", {})
    scene = checks.get("scene_analysis", {})
    if scene:
        desc = scene.get("scene_description", "")
        if desc:
            st.markdown(f"**Scene:** {desc}")

        st.markdown('<div class="mobile-card">', unsafe_allow_html=True)
        details = [
            ("People Count", scene.get("people_count", "—")),
            ("Training Scene", "Yes" if scene.get("is_training_scene") else "No"),
            ("Outdoor/Rural", "Yes" if scene.get("is_outdoor_rural") else "No"),
        ]
        for label, value in details:
            st.markdown(
                f'<div class="result-field">'
                f'<span class="result-label">{label}</span>'
                f'<span class="result-value">{value}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    reasons = result.get("rejection_reasons", [])
    if reasons:
        st.markdown("**Rejection Reasons:**")
        for r in reasons:
            st.markdown(f"- {r}")

    with st.expander("View full JSON"):
        st.json(result)


# ═══════════════════════════════════════════════════════════════════════
# SYNC HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _check_online() -> bool:
    try:
        resp = api.get("/api/sync/online", timeout=5)
        if resp and isinstance(resp, dict):
            return resp.get("online", False)
    except Exception:
        pass
    h = api.health()
    return h is not None and h.get("status") == "ok"


def _get_sync_counts() -> dict | None:
    try:
        return api.get("/api/sync/status", timeout=5)
    except Exception:
        return None


def _enqueue_sync(use_case: str, file_path: str, offline_result: dict, user: str):
    try:
        api.post("/api/sync/enqueue", json_data={
            "job_type": use_case,
            "file_path": file_path,
            "offline_result": offline_result,
            "user": user,
        }, timeout=10)
    except Exception:
        pass
