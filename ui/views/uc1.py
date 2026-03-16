"""
UC1 — Land Record OCR & Extraction (stepper wizard).

Steps:  Upload → Quality → Extract → Semantic → Output
Each step has Back/Next navigation with colour-coded progress.

Rich semantic view includes:
  - Land Summary metrics
  - Original Owner info
  - Ownership & Encumbrance Graphviz chart
  - Current Owners / Encumbrances / Water Resources tables
  - Key Dates
"""

import io
import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider, stepper, step_nav

_STEPS = ["Upload", "Quality", "Extract", "Semantic", "Output"]
_SS = "uc1_step"
_DONE = "uc1_done"


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

    if _SS not in st.session_state:
        st.session_state[_SS] = 0
    if _DONE not in st.session_state:
        st.session_state[_DONE] = set()

    cur = st.session_state[_SS]
    done = st.session_state[_DONE]

    stepper(_STEPS, cur, done)

    [_step_upload, _step_quality, _step_extract, _step_semantic, _step_output][cur]()

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
            st.info(f"**{uploaded.name}** — {uploaded.size / 1024:.1f} KB")
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
        st.caption(f"File ready: `{st.session_state.get('uc1_file_name', '')}`")


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
            st.success("Quality gate **PASSED**")
        else:
            reasons = qc.get("gate_reasons") or qc.get("issues") or []
            st.warning("Quality gate **FAILED**")
            for r in reasons:
                st.markdown(f"- {r}")

        mc = st.columns(5)
        mc[0].metric("Width", qc.get("width", 0))
        mc[1].metric("Height", qc.get("height", 0))
        mc[2].metric("Sharp", qc.get("sharpness", "—"))
        mc[3].metric("Bright", f"{qc.get('mean_brightness', 0):.0f}")
        mc[4].metric("Contrast", f"{qc.get('contrast_ratio', 0):.2f}")

        extra = st.columns(4)
        if qc.get("orientation"):
            extra[0].metric("Orientation", qc["orientation"])
        if qc.get("text_density_pct") is not None:
            extra[1].metric("Text Density", f"{qc['text_density_pct']:.1f}%")
        if qc.get("skew_angle_deg") is not None:
            extra[2].metric("Skew", f"{qc['skew_angle_deg']:.2f}°")
        if qc.get("readability"):
            extra[3].metric("Readability", qc["readability"])

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
        st.caption("Extraction data available")
        _render_extraction_summary(result)


def _render_extraction_summary(result: dict):
    """Render a structured summary of the extraction result."""
    merged = result.get("merged_extraction", result)
    timing = result.get("timing_seconds", {})
    pipeline = result.get("pipeline", {})

    if timing:
        tc = st.columns(min(len(timing), 4))
        for i, (k, v) in enumerate(timing.items()):
            tc[i % len(tc)].metric(k.replace("_", " ").title(), f"{v:.1f}s")

    if pipeline:
        pc = st.columns(len(pipeline))
        for i, (name, info) in enumerate(pipeline.items()):
            label = name.replace("_", " ").title()
            status = info.get("status", "—")
            elapsed = info.get("elapsed_seconds", 0)
            pc[i].metric(label, f"{status} ({elapsed:.1f}s)")

    section_divider()

    if isinstance(merged, dict):
        doc_type = merged.get("document_type", "")
        report_date = merged.get("report_date", "")
        state = merged.get("state", "")
        district = merged.get("district", "")
        taluka = merged.get("taluka", "")
        village = merged.get("village", "")
        survey = merged.get("survey_number", "")

        if any([doc_type, district, taluka, village, survey]):
            st.markdown("#### Document Overview")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Document Type", doc_type or "—")
            c2.metric("Report Date", report_date or "—")
            c3.metric("State", state or "—")
            c4.metric("Survey No.", survey or "—")

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("District", district or "—")
            c6.metric("Taluka", taluka or "—")
            c7.metric("Village", village or "—")
            c8.metric("Sub Division", merged.get("sub_division", "—"))

        owners = merged.get("owners", [])
        if isinstance(owners, list) and owners:
            st.markdown("#### Owners")
            owner_rows = []
            for o in owners:
                if isinstance(o, dict):
                    owner_rows.append({
                        "Name": o.get("name", ""),
                        "Account No.": o.get("account_number", ""),
                        "Area (ha)": o.get("area_hectare", ""),
                        "Assessment (Rs)": o.get("assessment_rupees", ""),
                        "Mutation Ref": o.get("mutation_ref", ""),
                    })
            if owner_rows:
                st.dataframe(pd.DataFrame(owner_rows), use_container_width=True, hide_index=True)

        area = merged.get("area", {})
        if isinstance(area, dict) and area.get("total_area_hectare"):
            st.markdown("#### Area Details")
            ac = st.columns(4)
            ac[0].metric("Total Area", f"{area.get('total_area_hectare', '—')} ha")
            cultivable = area.get("cultivable", {})
            if isinstance(cultivable, dict):
                ac[1].metric("Jirayat", f"{cultivable.get('jirayat_hectare', '—')} ha")
                ac[2].metric("Bagayat", f"{cultivable.get('bagayat_hectare', '—')} ha")
            ac[3].metric("Pot Kharab", f"{area.get('pot_kharab_hectare', '—')} ha")

        encumbrances = merged.get("encumbrances", [])
        if isinstance(encumbrances, list) and encumbrances:
            st.markdown("#### Encumbrances")
            enc_rows = []
            for e in encumbrances:
                if isinstance(e, dict):
                    enc_rows.append({
                        "Type": e.get("type", ""),
                        "Bank": e.get("bank_name", ""),
                        "Branch": e.get("branch", ""),
                        "Amount (Rs)": e.get("amount_rupees", ""),
                        "Borrower": e.get("borrower_name", ""),
                        "Mutation Ref": e.get("mutation_ref", ""),
                    })
            if enc_rows:
                st.dataframe(pd.DataFrame(enc_rows), use_container_width=True, hide_index=True)

    with st.expander("View Full Extracted JSON"):
        st.json(merged)


# ═══════════════════════════════════════════════════════════════════════
# STEP 3 — Semantic Analysis
# ═══════════════════════════════════════════════════════════════════════

def _step_semantic():
    st.markdown("### Semantic Analysis & Knowledge Graph")
    result = st.session_state.get("uc1_result")
    if not result:
        st.warning("Run extraction first (step 3).")
        return

    st.markdown(
        "Analyze the extracted data to build an **ownership chain**, "
        "identify **current vs original owners**, and visualize "
        "**encumbrances and land relationships** as a graph."
    )

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
        st.caption("Semantic analysis complete")
        semantic_data = sem.get("semantic_knowledge_graph", sem)
        _render_semantic_view(semantic_data)


# ═══════════════════════════════════════════════════════════════════════
# SEMANTIC VIEW — full knowledge graph visualization
# ═══════════════════════════════════════════════════════════════════════

def _render_semantic_view(semantic: dict):
    """Render the full semantic knowledge graph visualization with
    land summary, ownership chain, Graphviz graph, encumbrances,
    water resources, and key dates."""
    if "raw_llm_response" in semantic:
        st.warning("Semantic analysis returned non-JSON.")
        st.code(semantic["raw_llm_response"])
        return

    summary = semantic.get("land_summary", {})
    if summary:
        st.markdown("#### Land Summary")
        cols = st.columns(4)
        cols[0].metric("Survey No.", summary.get("survey_number", "—"))
        cols[1].metric("Village", summary.get("village", "—"))
        cols[2].metric("Taluka", summary.get("taluka", "—"))
        cols[3].metric("District", summary.get("district", "—"))

        cols2 = st.columns(4)
        cols2[0].metric("Total Area", f"{summary.get('total_area_hectare', '—')} ha")
        cols2[1].metric("Cultivable", f"{summary.get('cultivable_hectare', '—')} ha")
        cols2[2].metric("Uncultivable", f"{summary.get('uncultivable_hectare', '—')} ha")
        cols2[3].metric("Tenure", summary.get("tenure_type", "—"))

    section_divider()

    orig = semantic.get("original_owner", {})
    if orig.get("name"):
        st.markdown("#### Original Owner")
        st.info(f"**{orig['name']}** — {orig.get('notes', '')}")

    chain = semantic.get("ownership_chain", [])
    current = semantic.get("current_owners", [])

    if chain or current:
        st.markdown("#### Ownership & Encumbrance Graph")
        dot = _build_ownership_dot(semantic)
        st.graphviz_chart(dot, use_container_width=True)

    if current:
        st.markdown("#### Current Owners")
        owner_rows = [
            {
                "Name": o.get("name", ""),
                "Account No.": o.get("account_number", ""),
                "Area (ha)": o.get("area_hectare", ""),
                "Assessment (Rs)": o.get("assessment_rupees", ""),
            }
            for o in current
        ]
        st.dataframe(pd.DataFrame(owner_rows), use_container_width=True, hide_index=True)

    enc = semantic.get("encumbrances_mapped", [])
    if enc:
        st.markdown("#### Encumbrances (Loans & Mortgages)")
        enc_rows = [
            {
                "Owner": e.get("owner_name", ""),
                "Bank / Institution": e.get("bank_name", ""),
                "Amount (Rs)": e.get("amount_rupees", ""),
                "Type": e.get("type", ""),
                "Mutation Ref": e.get("mutation_ref", ""),
            }
            for e in enc
        ]
        st.dataframe(pd.DataFrame(enc_rows), use_container_width=True, hide_index=True)

    wells = semantic.get("wells", [])
    if wells:
        st.markdown("#### Water Resources")
        for w in wells:
            st.markdown(
                f"- Well owned by **{w.get('owner', '—')}** "
                f"(Mutation: {w.get('mutation_ref', '—')})"
            )

    dates = semantic.get("key_dates", {})
    if dates and any(dates.values()):
        st.markdown("#### Key Dates")
        dc = st.columns(3)
        dc[0].metric("Report Date", dates.get("report_date", "—"))
        dc[1].metric("Last Mutation No.", dates.get("last_mutation_number", "—"))
        dc[2].metric("Last Mutation Date", dates.get("last_mutation_date", "—"))

    with st.expander("View Full Semantic JSON"):
        st.json(semantic)


def _build_ownership_dot(semantic: dict) -> str:
    """Build a Graphviz DOT string for the ownership knowledge graph."""
    summary = semantic.get("land_summary", {})
    original = semantic.get("original_owner", {})
    chain = semantic.get("ownership_chain", [])
    current = semantic.get("current_owners", [])
    encumbrances = semantic.get("encumbrances_mapped", [])
    wells = semantic.get("wells", [])

    survey = summary.get("survey_number", "?")
    village = summary.get("village", "")
    total_area = summary.get("total_area_hectare", "?")

    def esc(s: str) -> str:
        return s.replace('"', '\\"').replace("\n", " ")

    lines = [
        "digraph ownership {",
        "  rankdir=TB;",
        '  graph [fontname="Arial", label="", labelloc=t, fontsize=14];',
        '  node [shape=box, style="rounded,filled", fontname="Arial", fontsize=11];',
        '  edge [fontname="Arial", fontsize=9];',
        "",
        f'  land [label="{esc(village)}\\nSurvey: {esc(survey)} | {esc(str(total_area))} ha",'
        '  shape=ellipse, fillcolor="#C8E6C9", penwidth=2];',
        "",
    ]

    node_ids: dict[str, str] = {}
    counter = [0]

    def nid(name: str) -> str:
        if name not in node_ids:
            node_ids[name] = f"n{counter[0]}"
            counter[0] += 1
        return node_ids[name]

    declared: set[str] = set()

    def declare(name: str, extra_label: str = "", color: str = "#BBDEFB", bold: bool = False):
        n = nid(name)
        if n in declared:
            return
        declared.add(n)
        label = esc(name)
        if extra_label:
            label += f"\\n{esc(extra_label)}"
        style = '"rounded,filled,bold"' if bold else '"rounded,filled"'
        lines.append(f'  {n} [label="{label}", fillcolor="{color}", style={style}];')

    if original.get("name"):
        declare(original["name"], "(Original Owner)", "#FFF9C4")

    current_names = {o.get("name", "") for o in current if o.get("name")}

    for transfer in chain:
        fr = transfer.get("from_owner", "")
        to = transfer.get("to_owner", "")
        if not fr or not to:
            continue
        declare(fr, color="#FFF9C4")
        is_current = to in current_names
        declare(to, color="#C8E6C9" if is_current else "#BBDEFB", bold=is_current)

        parts = []
        if transfer.get("transfer_type"):
            parts.append(transfer["transfer_type"])
        if transfer.get("mutation_ref"):
            parts.append(f"Mut#{transfer['mutation_ref']}")
        if transfer.get("area_hectare"):
            parts.append(f"{transfer['area_hectare']} ha")
        edge_label = esc("\\n".join(parts))
        lines.append(f'  {nid(fr)} -> {nid(to)} [label="{edge_label}", color="#1565C0"];')

    for owner in current:
        name = owner.get("name", "")
        if not name:
            continue
        acct = owner.get("account_number", "")
        area = owner.get("area_hectare", "")
        extra = []
        if acct:
            extra.append(f"Acct#{acct}")
        if area:
            extra.append(f"{area} ha")
        declare(name, " | ".join(extra), "#C8E6C9", bold=True)
        lines.append(
            f'  {nid(name)} -> land [style=dashed, color="#388E3C",'
            f' label="owns", arrowhead=none];'
        )

    for i, enc in enumerate(encumbrances):
        bank = enc.get("bank_name", f"Institution {i + 1}")
        amount = enc.get("amount_rupees", "")
        bank_label = esc(str(bank))
        if amount:
            bank_label += f"\\n\\u20b9{esc(str(amount))}"
        bid = f"bank_{i}"
        lines.append(
            f'  {bid} [label="{bank_label}", shape=hexagon,'
            f' fillcolor="#FFCDD2", style=filled];'
        )
        owner_name = enc.get("owner_name", "")
        if owner_name in node_ids:
            mut = enc.get("mutation_ref", "")
            elabel = esc(enc.get("type", "encumbrance"))
            if mut:
                elabel += f"\\nMut#{esc(str(mut))}"
            lines.append(
                f'  {node_ids[owner_name]} -> {bid}'
                f' [style=dotted, color="#D32F2F", label="{elabel}"];'
            )

    for i, well in enumerate(wells):
        wowner = well.get("owner", "")
        if not wowner:
            continue
        wid = f"well_{i}"
        wlabel = "Well"
        if well.get("mutation_ref"):
            wlabel += f"\\nMut#{esc(str(well['mutation_ref']))}"
        lines.append(
            f'  {wid} [label="{wlabel}", shape=diamond,'
            f' fillcolor="#B3E5FC", style=filled];'
        )
        if wowner in node_ids:
            lines.append(
                f'  {node_ids[wowner]} -> {wid}'
                f' [style=dashed, color="#0288D1", label="well owner"];'
            )

    lines.append("}")
    return "\n".join(lines)


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

    merged = result.get("merged_extraction", {})
    sem = st.session_state.get("uc1_semantic", {})
    sem_data = sem.get("semantic_knowledge_graph", sem) if sem else {}

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("#### Extraction Summary")
        if isinstance(merged, dict):
            summary_items = {
                "Document Type": merged.get("document_type", "—"),
                "Report Date": merged.get("report_date", "—"),
                "District": merged.get("district", "—"),
                "Taluka": merged.get("taluka", "—"),
                "Village": merged.get("village", "—"),
                "Survey No.": merged.get("survey_number", "—"),
            }
            for k, v in summary_items.items():
                st.markdown(f"- **{k}:** {v}")

    with col_r:
        st.markdown("#### Semantic Summary")
        if sem_data:
            land = sem_data.get("land_summary", {})
            st.markdown(f"- **Total Area:** {land.get('total_area_hectare', '—')} ha")
            st.markdown(f"- **Tenure:** {land.get('tenure_type', '—')}")
            owners = sem_data.get("current_owners", [])
            st.markdown(f"- **Current Owners:** {len(owners)}")
            enc = sem_data.get("encumbrances_mapped", [])
            st.markdown(f"- **Encumbrances:** {len(enc)}")
        else:
            st.caption("No semantic analysis run yet")

    section_divider()

    with st.expander("View Full JSON Output"):
        st.json(final)

    json_bytes = json.dumps(final, indent=2, default=str, ensure_ascii=False)
    st.download_button(
        "Download JSON",
        data=json_bytes,
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
            st.error("Submission failed")
            return

        prog = st.progress(0, text="Processing…")
        m_col1, m_col2, m_col3 = st.columns(3)
        m_total = m_col1.empty()
        m_ok = m_col2.empty()
        m_err = m_col3.empty()

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
                m_total.metric("Processed", f"{partial.get('succeeded', 0) + partial.get('failed', 0)}/{partial.get('total', '?')}")
                m_ok.metric("Succeeded", partial.get("succeeded", 0))
                m_err.metric("Failed", partial.get("failed", 0))

            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(1.5)
        prog.progress(1.0, text="Done")

        if job and job["status"] == "completed":
            st.success("Batch complete!")
            st.session_state["uc1_batch_result"] = job.get("result", {})
        elif job:
            st.error(f"Batch {job['status']}: {job.get('error', '')}")

    batch_result = st.session_state.get("uc1_batch_result")
    if batch_result:
        _render_batch_results(batch_result)


def _render_batch_results(result: dict):
    """Render batch results with summary metrics, table, CSV download,
    and individual result inspection with semantic analysis."""
    if not result:
        return

    section_divider()
    st.subheader("Extraction Results")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total", result.get("total", 0))
    c2.metric("Succeeded", result.get("succeeded", 0))
    c3.metric("Failed", result.get("failed", 0))

    rows = result.get("rows", [])
    if rows and isinstance(rows, list):
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=400, hide_index=True)

        csv_buf = io.StringIO()
        df.to_csv(csv_buf, index=False)
        st.download_button(
            "Download Results CSV",
            data=csv_buf.getvalue(),
            file_name="uc1_batch_results.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        with st.expander("View Raw Result"):
            st.json(result)

    output_path = result.get("output_path")
    if output_path:
        try:
            p = Path(output_path)
            if p.exists():
                with p.open("r", encoding="utf-8") as f:
                    batch_details = json.load(f)

                if isinstance(batch_details, list):
                    section_divider()
                    st.subheader("Inspect Individual Results")
                    for idx, item in enumerate(batch_details):
                        fname = Path(item.get("file", f"doc_{idx}")).name
                        status = item.get("status", "unknown")
                        icon = "+" if status == "ok" else "-"

                        with st.expander(f"[{icon}] {fname} — {status}"):
                            data = item.get("data", {})
                            merged = data.get("merged_extraction", data) if isinstance(data, dict) else data

                            view_tab, graph_tab = st.tabs(["Extracted Data", "Semantic Graph"])

                            with view_tab:
                                if isinstance(merged, dict):
                                    _render_extraction_summary(data if isinstance(data, dict) else {"merged_extraction": merged})
                                else:
                                    st.json(merged)

                            with graph_tab:
                                sem_key = f"batch_sem_{idx}"
                                if sem_key not in st.session_state:
                                    st.session_state[sem_key] = None

                                if st.button(
                                    "Run Semantic Analysis",
                                    key=f"batch_sem_btn_{idx}",
                                    use_container_width=True,
                                ):
                                    user = st.session_state.get("username", "ui")
                                    extraction_data = merged if isinstance(merged, dict) else {}
                                    sem_resp = api.submit_job("/api/uc1/semantic", {
                                        "extraction_data": extraction_data,
                                        "user": user,
                                        "tags": ["ui", "batch-semantic"],
                                    })
                                    if sem_resp:
                                        with st.spinner("Building knowledge graph…"):
                                            sem_job = api.poll_job(sem_resp["job_id"], timeout=120)
                                        if sem_job and sem_job.get("status") == "completed":
                                            sem_result = sem_job.get("result", {})
                                            st.session_state[sem_key] = sem_result.get("semantic_knowledge_graph", sem_result)
                                            st.success("Semantic analysis complete")
                                        elif sem_job:
                                            st.error(f"Failed: {sem_job.get('error', '')}")
                                    else:
                                        st.error("Submission failed")

                                if st.session_state[sem_key]:
                                    _render_semantic_view(st.session_state[sem_key])

                            if item.get("error"):
                                st.error(item["error"])
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════

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
