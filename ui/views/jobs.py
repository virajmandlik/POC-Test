"""
Jobs page — monitor, manage, and act on background jobs.

Replaced multiselect with checkbox row for status filtering (no truncation).
"""

import time
from datetime import datetime

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider


def render():
    page_header("Job Management", "Monitor and control background processing jobs")

    # ── Metrics row ───────────────────────────────────────
    jd = api.list_jobs(limit=1)
    c = jd.get("counts", {}) if jd else {}

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", c.get("total", 0))
    m2.metric("Pending", c.get("pending", 0))
    m3.metric("Running", c.get("running", 0))
    m4.metric("Done", c.get("completed", 0))
    m5.metric("Failed", c.get("failed", 0))

    section_divider()

    # ── Filters — checkbox row instead of multiselect ─────
    with st.expander("Filters", expanded=False):
        st.markdown("**Status:**")
        fc = st.columns(5)
        f_pending   = fc[0].checkbox("Pending",   value=True, key="jf_pend")
        f_running   = fc[1].checkbox("Running",   value=True, key="jf_run")
        f_completed = fc[2].checkbox("Completed", value=True, key="jf_comp")
        f_failed    = fc[3].checkbox("Failed",    value=True, key="jf_fail")
        f_cancelled = fc[4].checkbox("Cancelled", value=True, key="jf_canc")

        filter_status = []
        if f_pending:   filter_status.append("pending")
        if f_running:   filter_status.append("running")
        if f_completed: filter_status.append("completed")
        if f_failed:    filter_status.append("failed")
        if f_cancelled: filter_status.append("cancelled")

        fc2a, fc2b, fc2c = st.columns(3)
        with fc2a:
            h = api.health()
            types = ["All"] + (h.get("registered_job_types", []) if h else [])
            filter_type = st.selectbox("Job Type", types, key="jf_type")
        with fc2b:
            filter_user = st.text_input("User", key="jf_user")
        with fc2c:
            filter_limit = st.number_input("Max", min_value=10, max_value=500, value=50, key="jf_lim")

    params = {"limit": filter_limit}
    if filter_status:
        params["status"] = ",".join(filter_status)
    if filter_type != "All":
        params["job_type"] = filter_type
    if filter_user:
        params["user"] = filter_user

    data = api.list_jobs(**params)
    jobs = data.get("jobs", []) if data else []

    if not jobs:
        st.info("No jobs match these filters.")
        return

    # ── Table ─────────────────────────────────────────────
    rows = []
    for j in jobs:
        rows.append({
            "Status": j.get("status", "").upper(),
            "Type": j.get("job_type", ""),
            "User": j.get("user", ""),
            "Progress": f'{j.get("progress", 0)}%',
            "Created": _fmt_time(j.get("created_at")),
            "Duration": _duration(j.get("started_at"), j.get("completed_at")),
            "ID": j.get("id", ""),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=380, hide_index=True)

    section_divider()

    # ── Actions ───────────────────────────────────────────
    st.markdown("##### Actions")
    ac1, ac2, ac3 = st.columns([4, 2, 1])
    with ac1:
        ids = [j.get("id", "") for j in jobs]
        sel = st.selectbox("Job", ids, key="ja_id")
    with ac2:
        act = st.selectbox("Action", ["Details", "Cancel", "Retry", "Remove"], key="ja_act")
    with ac3:
        st.markdown("")
        go = st.button("Go", type="primary", key="ja_go")

    if go and sel:
        user = st.session_state.get("username", "ui")
        if act == "Details":
            st.json(api.get_job(sel) or {})
        elif act == "Cancel":
            r = api.cancel_job(sel, user)
            if r and r.get("success"):
                st.success("Cancelled"); st.rerun()
        elif act == "Retry":
            r = api.retry_job(sel, user)
            if r and r.get("job_id"):
                st.success(f"New job: `{r['job_id']}`"); st.rerun()
        elif act == "Remove":
            r = api.remove_job(sel, user)
            if r and r.get("success"):
                st.success("Removed"); st.rerun()

    section_divider()

    # ── Purge ─────────────────────────────────────────────
    pc1, pc2 = st.columns([3, 1])
    with pc1:
        hrs = st.number_input("Purge older than (hours)", min_value=1, value=24, key="jp_hrs")
    with pc2:
        st.markdown("")
        if st.button("Purge", key="jp_btn"):
            r = api.purge_jobs(int(hrs), st.session_state.get("username", "ui"))
            if r:
                st.success(f"Purged {r.get('deleted', 0)} jobs"); st.rerun()

    # ── Auto-refresh ──────────────────────────────────────
    if st.checkbox("Auto-refresh (5s)", key="ja_auto"):
        time.sleep(5); st.rerun()


def _fmt_time(iso_str: str | None) -> str:
    if not iso_str: return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M")
    except Exception:
        return str(iso_str)[:16]


def _duration(started: str | None, ended: str | None) -> str:
    if not started: return "—"
    try:
        s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(ended).replace("Z", "+00:00")) if ended else datetime.now()
        sec = (e - s).total_seconds()
        return f"{sec:.1f}s" if sec < 60 else f"{sec / 60:.1f}m"
    except Exception:
        return "—"
