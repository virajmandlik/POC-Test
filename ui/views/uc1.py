"""
UC1 — Land Record OCR & Extraction (stepper wizard).

Steps:  Upload & Quality → Extract → Semantic → Output
Each step has Back/Next navigation with colour-coded progress.

Changes from CHANGES_TODO [1][2][3]:
  - Merged Upload + Quality into single step with auto-enhance before/after
  - Enhancement params read from .env via lib/config
  - Output shows 4 tabs: Merged | PaddleOCR | GPT Vision | Semantic
"""

import io
import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import api_client as api
from ui.theme import page_header, section_divider, stepper, step_nav

_STEPS_FULL = ["Upload & Quality", "Extract", "Semantic", "Output"]
_STEPS_OFFLINE = ["Upload & Quality", "Extract", "Output"]
_SS = "uc1_step"
_DONE = "uc1_done"


def _get_steps() -> list[str]:
    """Semantic step requires VPN/internet — skip it in paddle-only mode."""
    ext_mode = st.session_state.get("uc1_mode_locked", "combined")
    return _STEPS_OFFLINE if ext_mode == "paddle" else _STEPS_FULL


def _get_step_handlers() -> list:
    ext_mode = st.session_state.get("uc1_mode_locked", "combined")
    if ext_mode == "paddle":
        return [_step_upload_quality, _step_extract, _step_output]
    return [_step_upload_quality, _step_extract, _step_semantic, _step_output]


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

    steps = _get_steps()
    handlers = _get_step_handlers()
    cur = min(st.session_state[_SS], len(steps) - 1)
    done = st.session_state[_DONE]

    stepper(steps, cur, done)
    handlers[cur]()

    section_divider()
    new = step_nav(
        cur, len(steps), "uc1",
        next_disabled=_next_disabled(cur),
        next_label=_next_label(cur, steps),
    )
    if new is not None:
        st.session_state[_SS] = new
        st.rerun()


def _next_disabled(cur: int) -> bool:
    if cur == 0:
        return "uc1_file_path" not in st.session_state
    if cur == 1:
        return "uc1_result" not in st.session_state
    return False


def _next_label(cur: int, steps: list[str] | None = None) -> str:
    steps = steps or _get_steps()
    if cur + 1 < len(steps):
        return f"{steps[cur + 1]} →"
    return "Next →"


def _mark_done(step: int):
    st.session_state.setdefault(_DONE, set()).add(step)


# ═══════════════════════════════════════════════════════════════════════
# STEP 0 — Upload & Quality (merged)
# ═══════════════════════════════════════════════════════════════════════

def _step_upload_quality():
    st.markdown("### Upload & Quality Check")
    col_up, col_opts = st.columns([3, 1])

    with col_up:
        uploaded = st.file_uploader(
            "PDF or scanned image",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            key="uc1_upload",
        )
    with col_opts:
        mode_options = ["combined", "paddle", "vision"]
        saved_mode = st.session_state.get("uc1_mode_locked")
        default_idx = mode_options.index(saved_mode) if saved_mode in mode_options else 0

        selected_mode = st.selectbox(
            "Extraction mode",
            mode_options,
            index=default_idx,
            format_func=lambda m: {"combined": "Combined (best)", "paddle": "PaddleOCR", "vision": "GPT-4 Vision"}[m],
            key="uc1_extract_mode",
        )
        # Persist mode outside of widget key so it survives step navigation
        st.session_state["uc1_mode_locked"] = selected_mode

        lang_options = ["mr", "hi", "en"]
        saved_lang = st.session_state.get("uc1_lang_locked", "mr")
        lang_idx = lang_options.index(saved_lang) if saved_lang in lang_options else 0
        selected_lang = st.selectbox("Language", lang_options, index=lang_idx, key="uc1_lang")
        st.session_state["uc1_lang_locked"] = selected_lang

    if uploaded:
        is_new_file = st.session_state.get("uc1_file_name") != uploaded.name

        # Auto-upload + auto-quality on file selection
        if "uc1_file_path" not in st.session_state or is_new_file:
            if is_new_file:
                # Clear previous quality results when file changes
                for k in ["uc1_quality_done", "uc1_original_report", "uc1_enhanced_report",
                           "uc1_original_bytes", "uc1_enhanced_bytes", "uc1_is_pdf_render"]:
                    st.session_state.pop(k, None)

            with st.spinner("Uploading…"):
                uploaded.seek(0)
                res = api.upload_file(uploaded, user=st.session_state.get("username", "ui"))
            if res:
                st.session_state["uc1_file_path"] = res["path"]
                st.session_state["uc1_file_name"] = res["filename"]
            else:
                st.error("Upload failed")
                return

        # Auto-run quality check + enhancement (no manual button)
        if "uc1_quality_done" not in st.session_state:
            _run_quality_enhancement()
        else:
            _show_quality_results()

    if "uc1_file_path" in st.session_state:
        st.caption(f"File ready: `{st.session_state.get('uc1_file_name', '')}`")
        _mark_done(0)


def _compute_adaptive_params(report) -> dict:
    """Adapt enhancement params based on quality check results instead of blindly applying .env defaults."""
    from lib.config import cfg

    brightness = report.get("mean_brightness", 128)
    contrast = report.get("contrast_ratio", 0.3)
    blur = report.get("blur_score", 200)

    # Brightness: pull toward ideal range (100-160)
    if brightness > 200:
        bright_factor = max(0.75, 1.0 - (brightness - 160) / 400)
    elif brightness < 80:
        bright_factor = min(1.25, 1.0 + (80 - brightness) / 300)
    else:
        bright_factor = 1.0

    # Contrast: only boost if actually low
    if contrast < 0.2:
        contrast_factor = min(cfg.ENHANCE_CONTRAST, 1.4)
    elif contrast < 0.3:
        contrast_factor = min(cfg.ENHANCE_CONTRAST, 1.2)
    else:
        contrast_factor = 1.0

    return {
        "contrast": contrast_factor,
        "brightness": bright_factor,
        "denoise_method": cfg.ENHANCE_DENOISE if blur < 150 else "none",
        "deskew": cfg.ENHANCE_DESKEW,
        "adaptive_thresh": cfg.ENHANCE_ADAPTIVE_THRESH,
    }


def _run_quality_enhancement():
    fp = st.session_state.get("uc1_file_path")
    if not fp:
        return

    p = Path(fp)
    if not p.exists():
        return

    with st.spinner("Analysing document quality…"):
        from PIL import Image
        from usecase1_land_record_ocr import QualityChecker, ImageEnhancer

        is_pdf = p.suffix.lower() == ".pdf"
        if is_pdf:
            import pypdfium2 as pdfium
            pdf_doc = pdfium.PdfDocument(str(p))
            if len(pdf_doc) == 0:
                st.error("PDF has no pages")
                return
            bitmap = pdf_doc[0].render(scale=2.0)
            img = bitmap.to_pil()
            st.session_state["uc1_is_pdf_render"] = True
        else:
            img = Image.open(fp)
            st.session_state["uc1_is_pdf_render"] = False

        if img.mode == "RGBA":
            img = img.convert("RGB")

        checker = QualityChecker()
        enhancer = ImageEnhancer()

        original_report = checker.check(img)
        orig_dict = original_report.__dict__ if hasattr(original_report, '__dict__') else vars(original_report)
        st.session_state["uc1_original_report"] = orig_dict

        # Adaptive enhancement — adjust params based on current image state
        params = _compute_adaptive_params(orig_dict)
        st.session_state["uc1_enhance_params"] = params

        enhanced_img = enhancer.enhance(
            img,
            contrast=params["contrast"],
            brightness=params["brightness"],
            denoise_method=params["denoise_method"],
            deskew=params["deskew"],
            adaptive_thresh=params["adaptive_thresh"],
        )

        enhanced_report = checker.check(enhanced_img)
        st.session_state["uc1_enhanced_report"] = enhanced_report.__dict__ if hasattr(enhanced_report, '__dict__') else vars(enhanced_report)

        orig_buf = io.BytesIO()
        img.save(orig_buf, format="JPEG", quality=92)
        st.session_state["uc1_original_bytes"] = orig_buf.getvalue()

        enh_buf = io.BytesIO()
        enhanced_img.save(enh_buf, format="JPEG", quality=92)
        st.session_state["uc1_enhanced_bytes"] = enh_buf.getvalue()
        st.session_state["uc1_quality_done"] = True
        st.rerun()


def _show_quality_results():
    orig = st.session_state.get("uc1_original_report", {})
    enh = st.session_state.get("uc1_enhanced_report", {})

    if not orig or not enh:
        return

    is_pdf = st.session_state.get("uc1_is_pdf_render", False)
    if is_pdf:
        st.caption("PDF page 1 rendered at 2× scale for quality analysis")

    col_orig, col_enh = st.columns(2)

    with col_orig:
        st.markdown("**Original**")
        orig_bytes = st.session_state.get("uc1_original_bytes")
        if orig_bytes:
            st.image(orig_bytes, width=350)
        mc = st.columns(3)
        mc[0].metric("Blur", f"{orig.get('blur_score', 0):.0f}")
        mc[1].metric("Bright", f"{orig.get('mean_brightness', 0):.0f}")
        mc[2].metric("Contrast", f"{orig.get('contrast_ratio', 0):.3f}")
        issues = orig.get("issues", [])
        if issues:
            for iss in issues:
                st.caption(f"⚠️ {iss}")
        else:
            st.caption("✅ All checks passed")

    with col_enh:
        st.markdown("**Enhanced** (auto)")
        enh_bytes = st.session_state.get("uc1_enhanced_bytes")
        if enh_bytes:
            st.image(enh_bytes, width=350)
        mc = st.columns(3)

        blur_delta = enh.get("blur_score", 0) - orig.get("blur_score", 0)
        bright_delta = enh.get("mean_brightness", 0) - orig.get("mean_brightness", 0)
        contrast_delta = enh.get("contrast_ratio", 0) - orig.get("contrast_ratio", 0)

        mc[0].metric("Blur", f"{enh.get('blur_score', 0):.0f}", delta=f"{blur_delta:+.0f}")
        mc[1].metric("Bright", f"{enh.get('mean_brightness', 0):.0f}", delta=f"{bright_delta:+.0f}")
        mc[2].metric("Contrast", f"{enh.get('contrast_ratio', 0):.3f}", delta=f"{contrast_delta:+.3f}")
        enh_issues = enh.get("issues", [])
        if enh_issues:
            for iss in enh_issues:
                st.caption(f"⚠️ {iss}")
        else:
            st.caption("✅ All checks passed")

    params = st.session_state.get("uc1_enhance_params", {})
    with st.expander("Enhancement Parameters (adaptive)"):
        st.markdown(
            f"- Contrast: `{params.get('contrast', '—')}` | "
            f"Brightness: `{params.get('brightness', '—')}`"
        )
        st.markdown(
            f"- Denoise: `{params.get('denoise_method', '—')}` | "
            f"Deskew: `{params.get('deskew', '—')}`"
        )
        st.caption("Parameters auto-adjusted based on document quality analysis")


# ═══════════════════════════════════════════════════════════════════════
# STEP 1 — Extract
# ═══════════════════════════════════════════════════════════════════════

def _step_extract():
    st.markdown("### Run Extraction Pipeline")
    fp = st.session_state.get("uc1_file_path")
    if not fp:
        st.warning("Upload a document first.")
        return

    mode = st.session_state.get("uc1_mode_locked", "combined")
    lang = st.session_state.get("uc1_lang_locked", "mr")
    mode_label = {"combined": "Combined", "paddle": "PaddleOCR", "vision": "GPT-4 Vision"}.get(mode, mode)
    st.caption(f"File: `{st.session_state.get('uc1_file_name', fp)}`  ·  Mode: `{mode_label}`")

    if st.button("Run Extraction", type="primary", key="uc1_run_ext"):
        user = st.session_state.get("username", "ui")

        resp = api.submit_job("/api/uc1/extract", {
            "file_path": fp, "mode": mode, "lang": lang,
            "user": user, "tags": ["ui", "single"],
        })
        if not resp:
            st.error("Submission failed")
            return
        job_id = resp["job_id"]

        prog = st.progress(0, text="Extracting…")
        start = time.time()
        last_pct = 0
        while time.time() - start < 300:
            job = api.get_job(job_id)
            if not job:
                break
            server_pct = min(job.get("progress", 0), 100)
            if server_pct > last_pct:
                last_pct = server_pct
            else:
                last_pct = min(last_pct + 1, 95)
            prog.progress(last_pct / 100, text=f"Extracting… {last_pct}%")
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(1.5)
        prog.progress(1.0, text="Done")

        if job and job.get("status") == "completed":
            st.session_state["uc1_result"] = job.get("result", {})
            _mark_done(1)
            st.success("Extraction complete!")
        elif job:
            st.error(f"Job {job.get('status')}: {job.get('error', '')}")

    result = st.session_state.get("uc1_result")
    if result:
        st.caption("Extraction data available")
        _render_extraction_summary(result)


def _render_extraction_summary(result: dict):
    merged = result.get("merged_extraction", result)
    pipeline = result.get("pipeline", {})

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

    with st.expander("View Full Extracted JSON"):
        st.json(merged)


# ═══════════════════════════════════════════════════════════════════════
# STEP 2 — Semantic Analysis
# ═══════════════════════════════════════════════════════════════════════

def _step_semantic():
    st.markdown("### Semantic Analysis & Knowledge Graph")

    ext_mode = st.session_state.get("uc1_mode_locked", "combined")
    if ext_mode == "paddle":
        st.warning("Semantic analysis requires internet/VPN (GPT-4o-mini). "
                    "Switch to Combined mode or skip to Output.")
        return

    result = st.session_state.get("uc1_result")
    if not result:
        st.warning("Run extraction first (step 2).")
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
            _mark_done(2)
        elif job:
            st.error(f"Job {job.get('status')}: {job.get('error', '')}")

    sem = st.session_state.get("uc1_semantic")
    if sem:
        st.caption("Semantic analysis complete")
        semantic_data = sem.get("semantic_knowledge_graph", sem)
        _render_semantic_view(semantic_data)


def _render_semantic_view(semantic: dict):
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
# TAB RENDERERS — PaddleOCR / Vision individual results
# ═══════════════════════════════════════════════════════════════════════

def _render_paddle_tab(paddle_data: dict):
    pages = paddle_data.get("pages", [])
    if pages:
        for pg in pages:
            idx = pg.get("page_index", 0)
            st.markdown(f"**Page {idx + 1}**")
            text = pg.get("combined_text", "")
            if text:
                st.text_area("Raw OCR Text", text, height=250, key=f"paddle_raw_{idx}")
            sf = pg.get("structured_fields", {})
            if sf:
                st.markdown("**Structured Fields:**")
                st.json(sf)
            stats = pg.get("stats", {})
            if stats:
                sc = st.columns(4)
                sc[0].metric("Text Blocks", stats.get("total_text_blocks", "—"))
                sc[1].metric("Corrected", stats.get("corrected_blocks", "—"))
                sc[2].metric("Low Confidence", stats.get("low_confidence_blocks", "—"))
                sc[3].metric("Avg Confidence", f"{stats.get('avg_confidence', 0):.2%}")
    else:
        st.json(paddle_data)


def _render_paddle_fallback(result: dict):
    """Load raw PaddleOCR data only — no cross-pollination from merged result."""
    from lib.config import cfg
    ocr_file = cfg.OUTPUT_DIR / "ocr_output.json"
    if ocr_file.exists():
        try:
            ocr_data = json.loads(ocr_file.read_text(encoding="utf-8"))
            if ocr_data and isinstance(ocr_data, dict):
                st.caption("Raw PaddleOCR output (local OCR engine)")
                _render_paddle_tab(ocr_data)
                return
        except Exception:
            pass

    st.info("PaddleOCR raw data not available. Re-run extraction to generate.")


def _render_vision_tab(vision_data: dict):
    content = vision_data.get("extracted_content", "")
    if content:
        try:
            parsed = json.loads(content)
            st.json(parsed)
        except (json.JSONDecodeError, TypeError):
            st.text_area("Vision API Response", content, height=300, key="vision_raw_content")
    else:
        st.json(vision_data)


def _render_vision_fallback(result: dict):
    """Load raw Vision data only — no cross-pollination from merged result."""
    mode = st.session_state.get("uc1_mode_locked", "combined")
    if mode == "paddle":
        st.info("Vision API was not used (PaddleOCR-only mode).")
        return

    from lib.config import cfg
    vision_file = cfg.OUTPUT_DIR / "vision_output.json"
    if vision_file.exists():
        try:
            vision_data = json.loads(vision_file.read_text(encoding="utf-8"))
            if vision_data and isinstance(vision_data, dict):
                st.caption("Raw GPT-4 Vision output (cloud API)")
                _render_vision_tab(vision_data)
                return
        except Exception:
            pass

    st.info("GPT-4 Vision raw data not available. Re-run extraction to generate.")


# ═══════════════════════════════════════════════════════════════════════
# STEP 3 — Final Output (4 tabs)
# ═══════════════════════════════════════════════════════════════════════

def _step_output():
    st.markdown("### Final Output")
    result = st.session_state.get("uc1_result")
    if not result:
        st.warning("Run extraction first.")
        return

    _mark_done(3)

    tab_merged, tab_paddle, tab_vision, tab_semantic = st.tabs(
        ["📊 Merged (Final)", "🔤 PaddleOCR", "👁️ GPT Vision", "🧠 Semantic"]
    )

    merged = result.get("merged_extraction", {})
    pipeline = result.get("pipeline", {})

    with tab_merged:
        st.markdown("#### Combined Extraction Result")
        if isinstance(merged, dict) and merged:
            _render_extraction_summary(result)
        else:
            st.info("No merged extraction available")

        final = {
            "extraction": result,
            "semantic": st.session_state.get("uc1_semantic"),
            "metadata": {
                "file": st.session_state.get("uc1_file_name", ""),
                "mode": st.session_state.get("uc1_mode_locked", ""),
            },
        }
        section_divider()
        json_bytes = json.dumps(final, indent=2, default=str, ensure_ascii=False)
        st.download_button(
            "📥 Download Full JSON",
            data=json_bytes,
            file_name="uc1_output.json",
            mime="application/json",
            type="primary",
        )

    with tab_paddle:
        st.markdown("#### PaddleOCR Extraction")
        paddle_info = pipeline.get("paddleocr", {})
        if paddle_info:
            pc = st.columns(2)
            pc[0].metric("Status", paddle_info.get("status", "—"))
            pc[1].metric("Time", f"{paddle_info.get('elapsed_seconds', 0):.1f}s")

        paddle_data = result.get("paddle_extraction")
        if paddle_data and isinstance(paddle_data, dict):
            _render_paddle_tab(paddle_data)
        else:
            # Fallback: reconstruct from merged_extraction or saved output file
            _render_paddle_fallback(result)

    with tab_vision:
        st.markdown("#### GPT-4 Vision Extraction")
        vision_info = pipeline.get("vision_api", {})
        if vision_info:
            vc = st.columns(2)
            vc[0].metric("Status", vision_info.get("status", "—"))
            vc[1].metric("Time", f"{vision_info.get('elapsed_seconds', 0):.1f}s")

        vision_data = result.get("vision_extraction")
        if vision_data:
            _render_vision_tab(vision_data)
        else:
            _render_vision_fallback(result)

    with tab_semantic:
        st.markdown("#### Semantic Analysis")
        sem = st.session_state.get("uc1_semantic")
        if sem:
            semantic_data = sem.get("semantic_knowledge_graph", sem)
            _render_semantic_view(semantic_data)
        else:
            st.info("Run semantic analysis in step 3 first.")


# ═══════════════════════════════════════════════════════════════════════
# BATCH PROCESSING
# ═══════════════════════════════════════════════════════════════════════

def _batch_processing():
    section_divider()
    batch_files = st.file_uploader(
        "Upload PDFs / images", type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True, key="uc1_batch_up",
    )

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

    if not file_paths:
        st.info("Upload files to begin.")
        return

    st.markdown(f"**{len(file_paths)}** documents ready")
    if st.button("Process All", type="primary", key="uc1_batch_go", use_container_width=True):
        resp = api.submit_job("/api/uc1/batch", {
            "file_paths": file_paths, "mode": mode,
            "lang": st.session_state.get("uc1_lang_locked", "mr"),
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
            "📥 Download Results CSV",
            data=csv_buf.getvalue(),
            file_name="uc1_batch_results.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        with st.expander("View Raw Result"):
            st.json(result)


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
