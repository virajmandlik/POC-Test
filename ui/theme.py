"""
Theme & CSS for the unified F4F UI.

Modern, clean design inspired by Catalyst Center but friendlier.
Fixes: metric label truncation, multiselect tag readability,
stepper/wizard support, proper sidebar nav.
"""

import streamlit as st

# ─── Palette ──────────────────────────────────────────────────────────
PRIMARY   = "#0D6EFD"
ACCENT    = "#198754"
WARN      = "#FFC107"
DANGER    = "#DC3545"
BG_DARK   = "#0F1B2D"
BG_LIGHT  = "#F8F9FA"
CARD_BG   = "#FFFFFF"
TEXT      = "#212529"
TEXT_MUTED= "#6C757D"

LOGO_TEXT = "🌳 Farmers for Forests"

_CSS = """
<style>
/* ── Global ───────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* ── Sidebar ──────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0F1B2D 0%, #162A46 100%);
    border-right: 1px solid rgba(255,255,255,0.06);
    min-width: 260px;
}

section[data-testid="stSidebar"] * {
    color: #E0E6ED !important;
}

/* Nav items — hide radio circles, style as menu items */
section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] {
    gap: 2px !important;
}

section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label {
    padding: 0.6rem 1rem !important;
    border-radius: 8px !important;
    margin: 1px 0 !important;
    transition: all 0.15s ease !important;
    cursor: pointer !important;
    font-size: 0.9rem !important;
    font-weight: 500 !important;
    display: flex !important;
    align-items: center !important;
}

section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label:hover {
    background: rgba(255,255,255,0.08) !important;
}

section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label[data-checked="true"],
section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label:has(input:checked) {
    background: rgba(13, 110, 253, 0.2) !important;
    border-left: 3px solid #5B9BFF !important;
    color: #ffffff !important;
    font-weight: 600 !important;
}

section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label > div:first-child {
    display: none !important;
}

/* ── Metric cards — FIXED truncation ──────────────── */
div[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E9ECEF;
    border-radius: 12px;
    padding: 0.85rem 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    overflow: visible !important;
}

div[data-testid="stMetric"] label {
    color: #6C757D !important;
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    white-space: nowrap !important;
    overflow: visible !important;
    text-overflow: unset !important;
}

div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-size: 1.5rem !important;
    font-weight: 700 !important;
    color: #212529 !important;
}

/* ── Multiselect tags — FIXED readability ─────────── */
span[data-baseweb="tag"] {
    background: #E8F0FE !important;
    border: 1px solid #BFDBFE !important;
    border-radius: 6px !important;
    color: #1E40AF !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    padding: 2px 8px !important;
    max-width: none !important;
    white-space: nowrap !important;
    overflow: visible !important;
}

span[data-baseweb="tag"] span {
    color: #1E40AF !important;
}

span[data-baseweb="tag"] svg {
    color: #6B7280 !important;
}

/* ── Buttons ──────────────────────────────────────── */
.stButton > button[kind="primary"],
button[kind="primary"] {
    background: linear-gradient(135deg, #0D6EFD, #0B5ED7) !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    padding: 0.5rem 1.5rem !important;
    transition: all 0.15s ease !important;
    color: white !important;
}

.stButton > button[kind="primary"]:hover,
button[kind="primary"]:hover {
    box-shadow: 0 4px 12px rgba(13,110,253,0.35) !important;
    transform: translateY(-1px) !important;
}

/* ── Tabs ─────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 2px solid #E9ECEF;
}

.stTabs [data-baseweb="tab"] {
    padding: 0.7rem 1.1rem;
    font-weight: 500;
    font-size: 0.88rem;
    border-radius: 8px 8px 0 0;
}

.stTabs [aria-selected="true"] {
    border-bottom: 3px solid #0D6EFD;
    font-weight: 600;
}

/* ── Expanders ────────────────────────────────────── */
details[data-testid="stExpander"] {
    border: 1px solid #E9ECEF;
    border-radius: 12px;
    margin-bottom: 0.5rem;
    overflow: hidden;
}

/* ── Dataframes ───────────────────────────────────── */
.stDataFrame {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #E9ECEF;
}

/* ── Progress bars ────────────────────────────────── */
.stProgress > div > div {
    background: linear-gradient(90deg, #0D6EFD 0%, #198754 100%);
    border-radius: 6px;
}

/* ── Page header ──────────────────────────────────── */
.page-header {
    padding: 0.25rem 0 0.75rem;
    border-bottom: 2px solid #E9ECEF;
    margin-bottom: 1.25rem;
}
.page-header h1 {
    font-size: 1.6rem;
    font-weight: 700;
    color: #212529;
    margin: 0;
}
.page-header p {
    color: #6C757D;
    margin: 0.2rem 0 0;
    font-size: 0.9rem;
}

/* ── Sidebar branding ─────────────────────────────── */
.sidebar-logo {
    font-size: 1.2rem;
    font-weight: 700;
    color: #FFFFFF !important;
    padding: 0.5rem 0;
    text-align: center;
    border-bottom: 1px solid rgba(255,255,255,0.1);
    margin-bottom: 0.25rem;
}

.sidebar-status {
    font-size: 0.7rem;
    padding: 4px 8px;
    border-radius: 8px;
    text-align: center;
    margin: 0.25rem 0 0.75rem;
}
.sidebar-status.online  { background: rgba(25,135,84,0.2); color: #75B798 !important; }
.sidebar-status.offline { background: rgba(220,53,69,0.2); color: #EA868F !important; }

/* ── Section divider ──────────────────────────────── */
.section-divider {
    border: 0;
    height: 1px;
    background: #E9ECEF;
    margin: 1.25rem 0;
}

/* ════════════════════════════════════════════════════
   STEPPER – wizard-style step indicator
   ════════════════════════════════════════════════════ */
.stepper {
    display: flex;
    align-items: center;
    gap: 0;
    margin: 0.75rem 0 1.25rem;
    padding: 0;
}

.step {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 500;
    color: #9CA3AF;
    background: transparent;
    transition: all 0.2s ease;
    white-space: nowrap;
    cursor: default;
    position: relative;
}

.step .step-num {
    width: 26px;
    height: 26px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    font-weight: 700;
    flex-shrink: 0;
    border: 2px solid #D1D5DB;
    color: #9CA3AF;
    background: transparent;
    transition: all 0.2s ease;
}

.step-connector {
    width: 32px;
    height: 2px;
    background: #E5E7EB;
    flex-shrink: 0;
}

/* Done */
.step.done {
    color: #059669;
}
.step.done .step-num {
    background: #059669;
    border-color: #059669;
    color: white;
}
.step-connector.done {
    background: #059669;
}

/* Active */
.step.active {
    color: #0D6EFD;
    background: rgba(13, 110, 253, 0.06);
    font-weight: 600;
}
.step.active .step-num {
    background: #0D6EFD;
    border-color: #0D6EFD;
    color: white;
    box-shadow: 0 0 0 3px rgba(13, 110, 253, 0.2);
}

/* ── Step nav buttons ─────────────────────────────── */
.step-nav {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 1.5rem;
    padding-top: 1rem;
    border-top: 1px solid #E9ECEF;
}

/* ── Dashboard cards ──────────────────────────────── */
.dash-card {
    background: white;
    border: 1px solid #E9ECEF;
    border-radius: 12px;
    padding: 1.25rem;
    height: 100%;
}

.dash-card h4 {
    font-size: 0.95rem;
    font-weight: 600;
    color: #374151;
    margin: 0 0 0.75rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #F3F4F6;
}

/* ── Activity feed ────────────────────────────────── */
.activity-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid #F3F4F6;
    font-size: 0.85rem;
}

.activity-item:last-child {
    border-bottom: none;
}

.activity-icon {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    font-size: 0.75rem;
}

.activity-icon.info  { background: #EFF6FF; }
.activity-icon.warn  { background: #FFFBEB; }
.activity-icon.error { background: #FEF2F2; }

/* ── Status pills ─────────────────────────────────── */
.status-pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 10px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.3px;
}
.pill-pending   { background: #FEF3C7; color: #92400E; }
.pill-running   { background: #DBEAFE; color: #1E40AF; }
.pill-completed { background: #D1FAE5; color: #065F46; }
.pill-failed    { background: #FEE2E2; color: #991B1B; }
.pill-cancelled { background: #F3F4F6; color: #4B5563; }

/* ── Hide Streamlit chrome ────────────────────────── */
#MainMenu { visibility: hidden; }
header { visibility: hidden; }
footer { visibility: hidden; }

/* ── Hide the top multipage nav Streamlit auto-generates */
div[data-testid="stSidebarNav"] { display: none !important; }
</style>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    sub = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(f'<div class="page-header"><h1>{title}</h1>{sub}</div>', unsafe_allow_html=True)


def status_badge(status: str) -> str:
    cls = f"badge-{status}" if status in ("pending", "running", "completed", "failed", "cancelled") else ""
    return f'<span class="status-badge {cls}">{status.upper()}</span>'


def section_divider():
    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)


def sidebar_brand(api_ok: bool = True):
    st.markdown(f'<div class="sidebar-logo">{LOGO_TEXT}</div>', unsafe_allow_html=True)
    cls = "online" if api_ok else "offline"
    txt = "● API Connected" if api_ok else "○ API Offline"
    st.markdown(f'<div class="sidebar-status {cls}">{txt}</div>', unsafe_allow_html=True)


def stepper(steps: list[str], current: int, completed: set[int] | None = None):
    """Render a horizontal stepper/wizard indicator.

    Args:
        steps: list of step labels, e.g. ["Upload", "Extract", "Analyse"]
        current: 0-based index of the active step
        completed: set of 0-based indices that are done (green check)
    """
    completed = completed or set()
    parts = []
    for i, label in enumerate(steps):
        if i > 0:
            conn_cls = "done" if i - 1 in completed else ""
            parts.append(f'<div class="step-connector {conn_cls}"></div>')

        if i in completed and i != current:
            cls = "done"
            num = "✓"
        elif i == current:
            cls = "active"
            num = str(i + 1)
        else:
            cls = ""
            num = str(i + 1)

        parts.append(
            f'<div class="step {cls}">'
            f'<span class="step-num">{num}</span>'
            f'{label}'
            f'</div>'
        )

    st.markdown(f'<div class="stepper">{"".join(parts)}</div>', unsafe_allow_html=True)


def step_nav(current: int, total: int, key_prefix: str,
             next_label: str = "Next →", back_label: str = "← Back",
             next_disabled: bool = False) -> int | None:
    """Render Back / Next buttons. Returns new step index if changed, else None."""
    cols = st.columns([1, 3, 1])

    new_step = None
    with cols[0]:
        if current > 0:
            if st.button(back_label, key=f"{key_prefix}_back"):
                new_step = current - 1

    with cols[2]:
        if current < total - 1:
            if st.button(next_label, type="primary", key=f"{key_prefix}_next", disabled=next_disabled):
                new_step = current + 1

    return new_step
