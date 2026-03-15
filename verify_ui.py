"""
DocExtract — Verification & Approval UI
Loads extraction results, displays editable fields alongside the original document,
and produces egress-ready JSON on human approval.
"""

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from PIL import Image

import pypdfium2 as pdfium

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_DIR = BASE_DIR / "uploads"
VERIFIED_DIR = OUTPUT_DIR / "verified"
VERIFIED_DIR.mkdir(parents=True, exist_ok=True)


def _load_extraction(json_path: Path) -> dict:
    with json_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_document_image(source_file: str) -> Image.Image | None:
    """Attempt to load the original document as a PIL image."""
    p = Path(source_file)
    if not p.exists():
        for candidate in UPLOAD_DIR.glob("*"):
            if candidate.stem.startswith("raw_") or candidate.stem.startswith("preprocessed_"):
                p = candidate
                break
        if not p.exists():
            return None

    suffix = p.suffix.lower()
    if suffix == ".pdf":
        pdf = pdfium.PdfDocument(str(p))
        return pdf[0].render(scale=2.0).to_pil()
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        return Image.open(str(p)).convert("RGB")
    return None


def _build_egress_response(
    document_id: str,
    verified_data: dict,
    extraction_method: str,
    corrections: list[dict],
    verified_by: str,
    source_file: str,
    processing_duration_ms: int,
) -> dict:
    """Build the egress API response per the contract in tempPlan.txt."""
    filled = 0
    total = 0

    def _count(obj):
        nonlocal filled, total
        if isinstance(obj, dict):
            for v in obj.values():
                _count(v)
        elif isinstance(obj, list):
            total += 1
            if obj:
                filled += 1
        elif isinstance(obj, str):
            total += 1
            if obj:
                filled += 1

    _count(verified_data)
    confidence = round(filled / max(total, 1), 2)

    return {
        "document_id": document_id,
        "status": "COMPLETED",
        "extraction_method": extraction_method,
        "overall_confidence": confidence,
        "verified_by": verified_by,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "data": verified_data,
        "corrections_applied": corrections,
        "metadata": {
            "source_file": source_file,
            "ingress_timestamp": datetime.now(timezone.utc).isoformat(),
            "processing_duration_ms": processing_duration_ms,
            "pages_processed": 1,
            "retry_count": 0,
        },
    }


def _render_field_editor(
    data: dict, prefix: str = "", original: dict | None = None
) -> tuple[dict, list[dict]]:
    """
    Recursively render editable form fields for a nested dict.
    Returns (edited_data, list_of_corrections).
    """
    edited = {}
    corrections = []
    orig = original or {}

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        label = key.replace("_", " ").title()

        if isinstance(value, dict):
            st.markdown(f"**{label}**")
            with st.container():
                sub_edited, sub_corrections = _render_field_editor(
                    value, prefix=full_key, original=orig.get(key, {})
                )
            edited[key] = sub_edited
            corrections.extend(sub_corrections)
        elif isinstance(value, list):
            current_str = ", ".join(str(v) for v in value) if value else ""
            new_val = st.text_input(label, value=current_str, key=f"field_{full_key}")
            new_list = [v.strip() for v in new_val.split(",") if v.strip()] if new_val else []
            edited[key] = new_list
            old_str = ", ".join(str(v) for v in orig.get(key, []))
            if new_val != old_str and new_val != current_str:
                corrections.append({
                    "field_name": full_key,
                    "original_value": current_str,
                    "corrected_value": new_val,
                })
        else:
            str_value = str(value) if value is not None else ""
            new_val = st.text_input(label, value=str_value, key=f"field_{full_key}")
            edited[key] = new_val
            orig_val = str(orig.get(key, "")) if orig.get(key) is not None else ""
            if new_val != str_value and new_val != orig_val:
                corrections.append({
                    "field_name": full_key,
                    "original_value": str_value,
                    "corrected_value": new_val,
                })

    return edited, corrections


def main():
    st.set_page_config(
        page_title="DocExtract — Verification",
        page_icon="✅",
        layout="wide",
    )

    st.markdown(
        "<h1 style='text-align:center;'>DocExtract — Verification & Approval</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Review extracted data &rarr; Edit if needed &rarr; Approve &rarr; Send to Egress"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    for key, default in [
        ("doc_id", None),
        ("verified", False),
        ("egress_path", None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── Source selection ──────────────────────────────────────────
    st.subheader("1. Load Extraction Result")

    extraction_files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    extraction_files = [f for f in extraction_files if f.name != "documents.json"]

    if not extraction_files:
        st.warning(
            "No extraction results found in `output/`. "
            "Run the extraction pipeline first (via `pipeline_ui.py` or `combined.py`)."
        )
        return

    selected_file = st.selectbox(
        "Select extraction result",
        extraction_files,
        format_func=lambda p: f"{p.name} ({p.stat().st_size / 1024:.1f} KB)",
    )

    if not selected_file:
        return

    extraction = _load_extraction(selected_file)
    merged = extraction.get("merged_extraction", {})
    if not merged:
        raw_ext = extraction.get("raw_extraction", {})
        prep_ext = extraction.get("preprocessed_extraction", {})
        merged = prep_ext if prep_ext else raw_ext

    if not merged:
        st.error("No extraction data found in this file.")
        return

    source_file = extraction.get("source_file", "")
    pipeline_info = extraction.get("pipeline", {})
    timing = extraction.get("timing_seconds", {})

    extraction_method = "DUAL_MERGED"
    if pipeline_info:
        paddle_ok = pipeline_info.get("paddleocr", {}).get("status") == "ok"
        vision_ok = pipeline_info.get("vision_api", {}).get("status") == "ok"
        if paddle_ok and vision_ok:
            extraction_method = "DUAL_MERGED"
        elif paddle_ok:
            extraction_method = "OFFLINE_ONLY"
        elif vision_ok:
            extraction_method = "ONLINE_ONLY"

    total_ms = int(timing.get("total", 0) * 1000) if timing else 0

    if not st.session_state.doc_id:
        st.session_state.doc_id = str(uuid.uuid4())

    doc_id = st.session_state.doc_id

    st.markdown(
        f"**Document ID:** `{doc_id}` &nbsp;|&nbsp; "
        f"**Source:** `{Path(source_file).name if source_file else 'N/A'}` &nbsp;|&nbsp; "
        f"**Method:** `{extraction_method}`"
    )

    st.divider()

    # ── Two-column layout: Document image + Editable fields ──────
    st.subheader("2. Review & Edit Extracted Data")

    col_doc, col_fields = st.columns([1, 1])

    with col_doc:
        st.markdown("**Original Document**")
        doc_image = _load_document_image(source_file)
        if doc_image:
            st.image(doc_image, use_container_width=True)
        else:
            uploaded_files = sorted(UPLOAD_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if uploaded_files:
                fallback = uploaded_files[0]
                try:
                    fb_img = _load_document_image(str(fallback))
                    if fb_img:
                        st.image(fb_img, caption=f"Showing: {fallback.name}", use_container_width=True)
                    else:
                        st.info("Could not render document image. Upload the original to `uploads/`.")
                except Exception:
                    st.info("Could not render document image.")
            else:
                st.info("No document image found. Upload the original to `uploads/`.")

        if pipeline_info:
            st.markdown("**Pipeline Summary**")
            for engine, info in pipeline_info.items():
                status = info.get("status", "unknown")
                elapsed = info.get("elapsed_seconds", 0)
                icon = "✓" if status == "ok" else "✗"
                st.markdown(f"- {icon} **{engine}**: {status} ({elapsed:.1f}s)")

    with col_fields:
        st.markdown("**Extracted Fields** *(edit any field before approving)*")

        with st.form("verification_form"):
            verified_by = st.text_input(
                "Your Name / ID (verifier)",
                placeholder="e.g., farmer_67890 or official_12345",
            )

            st.divider()

            edited_data, corrections = _render_field_editor(merged, original=merged)

            st.divider()

            if corrections:
                st.info(f"You have made **{len(corrections)}** correction(s).")

            col_approve, col_reject = st.columns(2)

            with col_approve:
                approve_btn = st.form_submit_button(
                    "Approve & Send to Egress",
                    type="primary",
                    use_container_width=True,
                )

            with col_reject:
                reject_btn = st.form_submit_button(
                    "Reject — Request Re-extraction",
                    use_container_width=True,
                )

    # ── Handle approval ──────────────────────────────────────────
    if approve_btn:
        if not verified_by.strip():
            st.error("Enter your name or ID before approving.")
            return

        egress_response = _build_egress_response(
            document_id=doc_id,
            verified_data=edited_data,
            extraction_method=extraction_method,
            corrections=corrections,
            verified_by=verified_by.strip(),
            source_file=source_file,
            processing_duration_ms=total_ms,
        )

        egress_path = VERIFIED_DIR / f"{doc_id}.json"
        with egress_path.open("w", encoding="utf-8") as fp:
            json.dump(egress_response, fp, ensure_ascii=False, indent=2)

        _update_document_index(doc_id, egress_path, source_file, extraction_method)

        st.session_state.verified = True
        st.session_state.egress_path = str(egress_path)

        st.success(f"Document **approved** and saved to `{egress_path.name}`")
        if corrections:
            st.info(f"{len(corrections)} correction(s) recorded as training data.")
        st.balloons()

        st.divider()
        st.subheader("3. Egress API Response Preview")
        st.markdown(
            f"**`GET /egress/{doc_id}`** would return:"
        )
        st.json(egress_response)

        st.markdown(
            f"Start the FastAPI server (`python app.py`) and call:\n\n"
            f"```\nGET http://localhost:8000/egress/{doc_id}\n```"
        )

    if reject_btn:
        st.warning(
            "Document **rejected**. Re-run the extraction pipeline with adjusted "
            "preprocessing settings, then return here to verify."
        )
        st.session_state.verified = False
        st.session_state.doc_id = None


def _update_document_index(
    doc_id: str, egress_path: Path, source_file: str, method: str
):
    """Maintain a lightweight index of verified documents for the egress API."""
    index_path = OUTPUT_DIR / "documents.json"
    index = {}
    if index_path.exists():
        with index_path.open("r", encoding="utf-8") as f:
            try:
                index = json.load(f)
            except json.JSONDecodeError:
                index = {}

    index[doc_id] = {
        "egress_path": str(egress_path),
        "source_file": source_file,
        "extraction_method": method,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETED",
    }

    with index_path.open("w", encoding="utf-8") as fp:
        json.dump(index, fp, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
