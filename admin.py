"""
Admin Dashboard — Job Management & Audit Log Viewer

Streamlit UI that calls the FastAPI backend via HTTP.
This is a thin client — all business logic lives in the API.

Run:
    streamlit run admin.py --server.port 8502

Requires:
    FastAPI running at http://localhost:8000 (or API_URL env)
"""

import json
import os
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_URL = os.environ.get("API_URL", "http://localhost:8000")


# ═══════════════════════════════════════════════════════════════════════
# API CLIENT HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _api_get(path: str, params: dict | None = None) -> dict | list | None:
    """GET request to the API. Returns parsed JSON or None on error."""
    try:
        resp = requests.get(f"{API_URL}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as exc:
        st.error(f"API error: {exc}")
        return None


def _api_post(path: str, json_data: dict | None = None, params: dict | None = None) -> dict | None:
    try:
        resp = requests.post(f"{API_URL}{path}", json=json_data, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as exc:
        st.error(f"API error: {exc}")
        return None


def _api_delete(path: str, params: dict | None = None) -> dict | None:
    try:
        resp = requests.delete(f"{API_URL}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as exc:
        st.error(f"API error: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════════════


def main():
    st.set_page_config(
        page_title="F4F Admin — Jobs & Logs",
        page_icon="⚙️",
        layout="wide",
    )

    st.markdown(
        "<h1 style='text-align:center;'>F4F Admin Dashboard</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Job management &bull; Audit logs &bull; System status"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    if "admin_user" not in st.session_state:
        st.session_state.admin_user = os.environ.get("USER", os.environ.get("USERNAME", "admin"))

    with st.sidebar:
        st.markdown("### Settings")
        st.session_state.admin_user = st.text_input(
            "Your username (for audit)",
            value=st.session_state.admin_user,
        )
        st.markdown(f"**API:** `{API_URL}`")
        st.divider()
        _render_system_status()

    tab_jobs, tab_submit, tab_logs = st.tabs([
        "Job Management",
        "Submit Job",
        "Audit Logs",
    ])

    with tab_jobs:
        _render_job_management()

    with tab_submit:
        _render_job_submission()

    with tab_logs:
        _render_audit_logs()


# ═══════════════════════════════════════════════════════════════════════
# SIDEBAR — SYSTEM STATUS
# ═══════════════════════════════════════════════════════════════════════


def _render_system_status():
    st.markdown("### System Status")
    health = _api_get("/api/health")
    if health:
        if health.get("status") == "ok":
            st.success(f"API: Connected | MongoDB: {health.get('mongo')}")
        else:
            st.warning(f"API: Degraded | MongoDB: {health.get('mongo')}")

        st.markdown(f"**Platform:** `{health.get('platform')}`")

        st.divider()
        st.markdown("### Registered Job Types")
        for jt in health.get("registered_job_types", []):
            st.code(jt, language=None)
    else:
        st.error("Cannot reach API server")


# ═══════════════════════════════════════════════════════════════════════
# TAB 1 — JOB MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════


ALL_STATUSES = ["pending", "running", "completed", "failed", "cancelled"]


def _render_job_management():
    st.subheader("Job Overview")

    data = _api_get("/api/jobs", params={"limit": 1})
    counts = data.get("counts", {}) if data else {}

    cols = st.columns(6)
    cols[0].metric("Total", counts.get("total", 0))
    cols[1].metric("Pending", counts.get("pending", 0))
    cols[2].metric("Running", counts.get("running", 0))
    cols[3].metric("Completed", counts.get("completed", 0))
    cols[4].metric("Failed", counts.get("failed", 0))
    cols[5].metric("Cancelled", counts.get("cancelled", 0))

    st.divider()

    # Filters
    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        filter_status = st.multiselect("Filter by status", ALL_STATUSES, default=ALL_STATUSES)
    with col_f2:
        health = _api_get("/api/health")
        types = ["All"] + (health.get("registered_job_types", []) if health else [])
        filter_type = st.selectbox("Filter by type", types)
    with col_f3:
        filter_user = st.text_input("Filter by user", value="")
    with col_f4:
        filter_limit = st.number_input("Max results", min_value=10, max_value=500, value=50)

    params = {"limit": filter_limit}
    if filter_status:
        params["status"] = ",".join(filter_status)
    if filter_type != "All":
        params["job_type"] = filter_type
    if filter_user:
        params["user"] = filter_user

    data = _api_get("/api/jobs", params=params)
    jobs = data.get("jobs", []) if data else []

    if not jobs:
        st.info("No jobs found matching filters.")
        return

    rows = []
    for j in jobs:
        started = j.get("started_at", "")
        ended = j.get("completed_at", "")
        duration = "—"
        if started:
            try:
                s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                e = datetime.fromisoformat(ended.replace("Z", "+00:00")) if ended else datetime.now()
                secs = (e - s).total_seconds()
                duration = f"{secs:.1f}s" if secs < 60 else f"{secs/60:.1f}m"
            except Exception:
                pass

        rows.append({
            "ID": j.get("id", ""),
            "Type": j.get("job_type", ""),
            "Status": j.get("status", ""),
            "User": j.get("user", ""),
            "Progress": j.get("progress", 0),
            "Message": j.get("progress_message", ""),
            "Created": j.get("created_at", ""),
            "Duration": duration,
            "Tags": ", ".join(j.get("tags", [])),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=400)

    # Job Actions
    st.divider()
    st.subheader("Job Actions")

    col_id, col_action = st.columns([3, 1])
    with col_id:
        selected_id = st.text_input("Job ID", placeholder="Paste a job ID", key="action_job_id")
    with col_action:
        action = st.selectbox("Action", ["View Details", "Cancel", "Retry", "Remove"])

    if st.button("Execute Action", type="primary", disabled=not selected_id):
        user = st.session_state.admin_user
        if action == "View Details":
            job = _api_get(f"/api/jobs/{selected_id}")
            if job:
                st.json(job)

        elif action == "Cancel":
            result = _api_post(f"/api/jobs/{selected_id}/cancel", params={"user": user})
            if result and result.get("success"):
                st.success(f"Job {selected_id} cancelled.")

        elif action == "Retry":
            result = _api_post(f"/api/jobs/{selected_id}/retry", params={"user": user})
            if result and result.get("job_id"):
                st.success(f"Retried → new job: `{result['job_id']}`")

        elif action == "Remove":
            result = _api_delete(f"/api/jobs/{selected_id}", params={"user": user})
            if result and result.get("success"):
                st.success(f"Job {selected_id} removed.")

    # Bulk actions
    st.divider()
    st.subheader("Bulk Actions")
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        purge_hours = st.number_input("Purge jobs older than (hours)", min_value=1, value=24)
    with col_p2:
        st.markdown("")
        st.markdown("")
        if st.button("Purge Old Jobs"):
            result = _api_post(
                "/api/jobs/purge",
                json_data={"older_than_hours": purge_hours},
                params={"user": st.session_state.admin_user},
            )
            if result:
                st.success(f"Purged {result.get('deleted', 0)} old jobs.")

    # Auto-refresh
    st.divider()
    if st.checkbox("Auto-refresh (every 5s)", value=False):
        import time
        time.sleep(5)
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# TAB 2 — SUBMIT JOB
# ═══════════════════════════════════════════════════════════════════════


_DEFAULT_PARAMS = {
    "uc1.extract": {"file_path": "uploads/example.pdf", "mode": "combined", "lang": "mr"},
    "uc1.batch": {"file_paths": ["uploads/doc1.pdf"], "mode": "combined", "lang": "mr"},
    "uc1.semantic": {"extraction_data": {}},
    "uc2.verify": {"image_path": "uploads/photo.jpg", "skip_vision": False},
    "uc2.batch": {"pdf_paths": ["cc_data_final/123-FID-LID.pdf"]},
}

_ENDPOINT_MAP = {
    "uc1.extract": "/api/uc1/extract",
    "uc1.batch": "/api/uc1/batch",
    "uc1.semantic": "/api/uc1/semantic",
    "uc2.verify": "/api/uc2/verify",
    "uc2.batch": "/api/uc2/batch",
}


def _render_job_submission():
    st.subheader("Submit a New Job")

    # File upload section
    st.markdown("#### 1. Upload files (optional)")
    uploaded_files = st.file_uploader(
        "Upload documents to process",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="admin_upload",
    )

    uploaded_paths = []
    if uploaded_files:
        for uf in uploaded_files:
            files = {"file": (uf.name, uf.getvalue(), uf.type)}
            try:
                resp = requests.post(
                    f"{API_URL}/api/upload",
                    files=files,
                    params={"user": st.session_state.admin_user},
                    timeout=30,
                )
                resp.raise_for_status()
                result = resp.json()
                uploaded_paths.append(result["path"])
                st.success(f"Uploaded: `{result['filename']}` → `{result['path']}`")
            except Exception as exc:
                st.error(f"Upload failed: {exc}")

    st.divider()

    # Job submission
    st.markdown("#### 2. Submit job")
    health = _api_get("/api/health")
    available_types = health.get("registered_job_types", []) if health else list(_ENDPOINT_MAP.keys())

    job_type = st.selectbox("Job Type", options=available_types)
    endpoint = _ENDPOINT_MAP.get(job_type, "")

    default = _DEFAULT_PARAMS.get(job_type, {})
    default_req = {**default, "user": st.session_state.admin_user, "tags": []}

    # Auto-fill uploaded paths
    if uploaded_paths:
        if job_type == "uc1.extract" and len(uploaded_paths) == 1:
            default_req["file_path"] = uploaded_paths[0]
        elif job_type == "uc1.batch":
            default_req["file_paths"] = uploaded_paths
        elif job_type == "uc2.verify" and len(uploaded_paths) == 1:
            default_req["image_path"] = uploaded_paths[0]
        elif job_type == "uc2.batch":
            default_req["pdf_paths"] = uploaded_paths

    params_str = st.text_area(
        "Parameters (JSON)",
        value=json.dumps(default_req, indent=2),
        height=200,
    )

    if st.button("Submit Job", type="primary", use_container_width=True):
        if not endpoint:
            st.error(f"No API endpoint mapped for job type: {job_type}")
            return

        try:
            params = json.loads(params_str)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
            return

        result = _api_post(endpoint, json_data=params)
        if result:
            st.success(f"Job submitted: `{result.get('job_id', '')}` — {result.get('message', '')}")


# ═══════════════════════════════════════════════════════════════════════
# TAB 3 — AUDIT LOGS
# ═══════════════════════════════════════════════════════════════════════


def _render_audit_logs():
    st.subheader("Audit Trail")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        log_action = st.text_input("Filter by action (regex)", value="", key="log_action")
    with col2:
        log_user = st.text_input("Filter by user", value="", key="log_user")
    with col3:
        log_level = st.selectbox("Level", ["All", "info", "warn", "error"], key="log_level")
    with col4:
        log_limit = st.number_input("Max results", min_value=10, max_value=1000, value=100, key="log_limit")

    col_since, col_until = st.columns(2)
    with col_since:
        log_since = st.date_input("Since", value=datetime.now() - timedelta(days=7), key="log_since")
    with col_until:
        log_until = st.date_input("Until", value=datetime.now(), key="log_until")

    params: dict = {"limit": log_limit}
    if log_action:
        params["action"] = log_action
    if log_user:
        params["user"] = log_user
    if log_level != "All":
        params["level"] = log_level
    if log_since:
        params["since"] = log_since.isoformat()
    if log_until:
        params["until"] = log_until.isoformat()

    data = _api_get("/api/audit", params=params)
    if not data:
        return

    logs = data.get("logs", [])
    if not logs:
        st.info("No audit logs found matching filters.")
        return

    rows = []
    for entry in logs:
        rows.append({
            "Timestamp": entry.get("timestamp", ""),
            "Action": entry.get("action", ""),
            "User": entry.get("user", ""),
            "Level": entry.get("level", ""),
            "Job ID": entry.get("job_id", ""),
            "Detail": _truncate(json.dumps(entry.get("detail", {}), default=str), 100),
            "Result": _truncate(json.dumps(entry.get("result", {}), default=str), 100),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=500)

    st.divider()
    st.subheader("Log Detail")
    if logs:
        selected_idx = st.number_input(
            "Select row (0-indexed)",
            min_value=0, max_value=len(logs) - 1, value=0,
        )
        st.json(logs[selected_idx])


def _truncate(s: str, max_len: int) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s


if __name__ == "__main__":
    main()
