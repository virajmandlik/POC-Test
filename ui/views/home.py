"""
Home / Dashboard — friendly overview with short metric labels.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider


def render():
    page_header("Dashboard", "System overview and recent activity")

    h = api.health()

    # ── Metric cards (short labels to avoid truncation) ───
    jd = api.list_jobs(limit=1)
    c = jd.get("counts", {}) if jd else {}

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", c.get("total", 0))
    m2.metric("Pending", c.get("pending", 0))
    m3.metric("Running", c.get("running", 0))
    m4.metric("Done", c.get("completed", 0))
    m5.metric("Failed", c.get("failed", 0))

    section_divider()

    # ── Two columns ───────────────────────────────────────
    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("##### Recent Jobs")
        jd = api.list_jobs(limit=8)
        jobs = jd.get("jobs", []) if jd else []
        if jobs:
            rows = []
            for j in jobs:
                status = j.get("status", "")
                rows.append({
                    "Status": status.upper(),
                    "Type": j.get("job_type", ""),
                    "User": j.get("user", ""),
                    "When": _relative_time(j.get("created_at")),
                })
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
                height=320,
            )
        else:
            st.info("No jobs yet — go to **Land Records** or **Photo Verification** to start.")

    with right:
        st.markdown("##### System")
        if h:
            c1, c2 = st.columns(2)
            c1.metric("API", f"v{h.get('version', '?')}")
            c2.metric("Mongo", "✓" if h.get("mongo") == "connected" else "✗")
            st.caption(f"Platform: `{h.get('platform', '?')}`")
        else:
            st.error("API offline")

        st.markdown("")
        st.markdown("##### Pipelines")
        if h:
            for jt in h.get("registered_job_types", []):
                icon = "📄" if "uc1" in jt else "🌳" if "uc2" in jt else "⚙️"
                st.caption(f"{icon}  `{jt}`")

    section_divider()

    # ── Offline Sync Queue ────────────────────────────────
    st.markdown("##### Offline Sync Queue")
    sync_data = api.get("/api/sync/status", timeout=5)
    if sync_data and isinstance(sync_data, dict):
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Pending", sync_data.get("pending", 0))
        sc2.metric("Synced", sync_data.get("synced", 0))
        sc3.metric("Failed", sync_data.get("failed", 0))
        online_data = api.get("/api/sync/online", timeout=5)
        is_online = online_data.get("online", False) if online_data else False
        sc4.metric("VPN/Internet", "🟢 Online" if is_online else "🔴 Offline")

        recent_synced = api.get("/api/sync/recent", params={"limit": 5}, timeout=5)
        if recent_synced and isinstance(recent_synced, dict):
            items = recent_synced.get("items", [])
            if items:
                st.markdown("**Recent Synced Results:**")
                for item in items:
                    jt = item.get("job_type", "").upper()
                    fp = Path(item.get("file_path", "")).name if item.get("file_path") else "—"
                    synced_at = _relative_time(item.get("synced_at"))
                    st.caption(f"🔄 **{jt}** — `{fp}` — synced {synced_at}")

                    combined = item.get("combined_result", {})
                    if combined and isinstance(combined, dict):
                        if jt == "UC1":
                            merged = combined.get("merged_extraction", {})
                            if merged:
                                village = merged.get("village", "—")
                                survey = merged.get("survey_number", "—")
                                st.markdown(f"  Survey: {survey} | Village: {village}")
                        elif jt == "UC2":
                            decision = combined.get("decision", "—")
                            st.markdown(f"  Verdict: **{decision}**")
    else:
        st.caption("Sync queue not available")

    section_divider()

    # ── Activity feed ─────────────────────────────────────
    st.markdown("##### Recent Activity")
    ad = api.get_audit_logs(limit=6)
    logs = ad.get("logs", []) if ad else []
    if logs:
        for entry in logs:
            ts = _relative_time(entry.get("timestamp"))
            action = entry.get("action", "")
            user = entry.get("user", "")
            lvl = entry.get("level", "info")
            icon = {"info": "ℹ️", "warn": "⚠️", "error": "🔴"}.get(lvl, "ℹ️")
            st.markdown(f"{icon}  **{action}** — *{user}* · {ts}")
    else:
        st.caption("No recent activity.")


def _relative_time(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        delta = datetime.now().astimezone() - dt if dt.tzinfo else datetime.now() - dt
        secs = delta.total_seconds()
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{int(secs // 60)}m ago"
        if secs < 86400:
            return f"{int(secs // 3600)}h ago"
        return f"{int(secs // 86400)}d ago"
    except Exception:
        return str(iso_str)[:16]
