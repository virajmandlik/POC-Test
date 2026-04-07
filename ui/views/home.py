"""
Home / Dashboard — friendly overview with short metric labels.
Includes export for jobs, sync results, and audit logs.
Auto-polls every 5 seconds via st.fragment(run_every=) so stats stay live.
"""

import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider

_POLL_INTERVAL = 5  # seconds


def render():
    page_header("Dashboard", "System overview and recent activity")

    _live_metrics()

    section_divider()

    left, right = st.columns([3, 2], gap="large")

    with left:
        _live_jobs()

    with right:
        _system_info()

    section_divider()

    _live_sync()

    section_divider()

    _live_activity()


# ═══════════════════════════════════════════════════════════════════════
# AUTO-REFRESHING FRAGMENTS
# ═══════════════════════════════════════════════════════════════════════

@st.fragment(run_every=_POLL_INTERVAL)
def _live_metrics():
    """Job counters — auto-refresh every 5s."""
    jd = api.list_jobs(limit=1)
    c = jd.get("counts", {}) if jd else {}

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", c.get("total", 0))
    m2.metric("Pending", c.get("pending", 0))
    m3.metric("Running", c.get("running", 0))
    m4.metric("Done", c.get("completed", 0))
    m5.metric("Failed", c.get("failed", 0))


@st.fragment(run_every=_POLL_INTERVAL)
def _live_jobs():
    """Recent jobs table — auto-refresh."""
    st.markdown("##### Recent Jobs")
    jd = api.list_jobs(limit=50)
    jobs = jd.get("jobs", []) if jd else []
    if jobs:
        rows = []
        for j in jobs:
            status = j.get("status", "")
            rows.append({
                "Job ID": j.get("job_id", "")[:12],
                "Status": status.upper(),
                "Type": j.get("job_type", ""),
                "User": j.get("user", ""),
                "Created": j.get("created_at", "")[:19],
                "When": _relative_time(j.get("created_at")),
            })
        df_jobs = pd.DataFrame(rows)
        st.dataframe(
            df_jobs,
            use_container_width=True,
            hide_index=True,
            height=320,
        )
        csv_buf = io.StringIO()
        df_jobs.to_csv(csv_buf, index=False)
        st.download_button(
            "📥 Export Jobs CSV",
            data=csv_buf.getvalue(),
            file_name="digilekha_jobs.csv",
            mime="text/csv",
            key="export_jobs",
        )
    else:
        st.info("No jobs yet — go to **Land Records** or **Photo Verification** to start.")


def _system_info():
    """Static system info — no auto-refresh needed."""
    h = api.health()
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


@st.fragment(run_every=_POLL_INTERVAL)
def _live_sync():
    """Offline Sync Queue — auto-refresh."""
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

        recent_synced = api.get("/api/sync/recent", params={"limit": 20}, timeout=5)
        if recent_synced and isinstance(recent_synced, dict):
            items = recent_synced.get("items", [])
            if items:
                st.markdown("**Recent Synced Results:**")
                sync_rows = []
                for item in items:
                    jt = item.get("job_type", "").upper()
                    fp = Path(item.get("file_path", "")).name if item.get("file_path") else "—"
                    synced_at = _relative_time(item.get("synced_at"))
                    st.caption(f"🔄 **{jt}** — `{fp}` — synced {synced_at}")

                    combined = item.get("combined_result", {})
                    row = {"Type": jt, "File": fp, "Status": item.get("status", ""),
                           "Synced": item.get("synced_at", "")[:19], "User": item.get("user", "")}

                    if combined and isinstance(combined, dict):
                        if jt == "UC1":
                            merged = combined.get("merged_extraction", {})
                            if merged:
                                village = merged.get("village", "—")
                                survey = merged.get("survey_number", "—")
                                st.markdown(f"  Survey: {survey} | Village: {village}")
                                row["Survey No."] = survey
                                row["Village"] = village
                                row["District"] = merged.get("district", "")
                                row["Taluka"] = merged.get("taluka", "")
                                owners = merged.get("owners", [])
                                if owners and isinstance(owners[0], dict):
                                    row["Owner"] = owners[0].get("name", "")
                                    row["Area"] = owners[0].get("area_hectare", "")
                        elif jt == "UC2":
                            decision = combined.get("decision", "—")
                            st.markdown(f"  Verdict: **{decision}**")
                            row["Decision"] = decision
                            reasons = combined.get("rejection_reasons", [])
                            row["Reasons"] = "; ".join(reasons) if reasons else ""

                    sync_rows.append(row)

                with st.expander("View Synced Results Table"):
                    df_sync = pd.DataFrame(sync_rows)
                    st.dataframe(df_sync, use_container_width=True, hide_index=True)

                col_csv, col_json = st.columns(2)
                with col_csv:
                    csv_buf = io.StringIO()
                    pd.DataFrame(sync_rows).to_csv(csv_buf, index=False)
                    st.download_button(
                        "📥 Export Sync CSV",
                        data=csv_buf.getvalue(),
                        file_name="digilekha_sync_results.csv",
                        mime="text/csv",
                        key="export_sync_csv",
                    )
                with col_json:
                    st.download_button(
                        "📥 Export Sync JSON",
                        data=json.dumps(items, indent=2, default=str, ensure_ascii=False),
                        file_name="digilekha_sync_results.json",
                        mime="application/json",
                        key="export_sync_json",
                    )
    else:
        st.caption("Sync queue not available")


@st.fragment(run_every=_POLL_INTERVAL)
def _live_activity():
    """Recent audit activity — auto-refresh."""
    st.markdown("##### Recent Activity")
    ad = api.get_audit_logs(limit=20)
    logs = ad.get("logs", []) if ad else []
    if logs:
        for entry in logs[:8]:
            ts = _relative_time(entry.get("timestamp"))
            action = entry.get("action", "")
            user = entry.get("user", "")
            lvl = entry.get("level", "info")
            icon = {"info": "ℹ️", "warn": "⚠️", "error": "🔴"}.get(lvl, "ℹ️")
            st.markdown(f"{icon}  **{action}** — *{user}* · {ts}")

        audit_rows = []
        for entry in logs:
            audit_rows.append({
                "Timestamp": entry.get("timestamp", "")[:19],
                "Action": entry.get("action", ""),
                "User": entry.get("user", ""),
                "Level": entry.get("level", ""),
                "Job ID": entry.get("job_id", ""),
            })
        csv_buf = io.StringIO()
        pd.DataFrame(audit_rows).to_csv(csv_buf, index=False)
        st.download_button(
            "📥 Export Audit Log CSV",
            data=csv_buf.getvalue(),
            file_name="digilekha_audit_logs.csv",
            mime="text/csv",
            key="export_audit",
        )
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
