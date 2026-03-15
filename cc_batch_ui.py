"""
Carbon Credit Training — Batch PDF Verification UI

Upload one or multiple CC training PDFs, process them through the
verification pipeline, and download results as CSV.

Run:
    streamlit run cc_batch_ui.py
"""

import io
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from cc_pipeline import (
    BatchProcessor,
    CSVResultStore,
    PDFExtractor,
    PDFIdentifiers,
    VerificationJob,
)
from cc_verify import TrainingPhotoVerifier

load_dotenv()


def main():
    st.set_page_config(
        page_title="CC Training — Batch Verification",
        page_icon="🌳",
        layout="wide",
    )

    st.markdown(
        "<h1 style='text-align:center;'>Carbon Credit Training — Batch Verification</h1>"
        "<p style='text-align:center;color:#666;'>"
        "Upload CC training PDFs &rarr; Queue processing &rarr; CSV results"
        "</p><hr>",
        unsafe_allow_html=True,
    )

    api_key = os.environ.get("CXAI_API_KEY", "")
    if not api_key:
        st.error("**CXAI_API_KEY** not found in `.env`. GPT Vision analysis requires this key.")
        return

    for key in ("jobs", "results_df", "pdf_sources", "processing"):
        if key not in st.session_state:
            st.session_state[key] = None

    # ── Source Selection ──────────────────────────────────────────
    col_upload, col_folder = st.columns(2)

    with col_upload:
        st.subheader("Upload PDFs")
        uploaded_files = st.file_uploader(
            "Upload one or more CC training PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            key="batch_upload",
        )

    with col_folder:
        st.subheader("Or scan a local folder")
        folder_path = st.text_input(
            "Folder path containing PDFs",
            value="cc_data_final",
            help="Relative or absolute path to a folder with CC training PDFs",
        )
        if st.button("Scan Folder"):
            fp = Path(folder_path)
            if fp.is_dir():
                found = []
                for p in sorted(fp.glob("*.pdf")):
                    if PDFIdentifiers.from_filename(p.name):
                        found.append((p.name, p))
                st.session_state.pdf_sources = found
                st.session_state.results_df = None
                st.session_state.jobs = None
            else:
                st.error(f"Folder not found: `{folder_path}`")

    if uploaded_files:
        upload_list = []
        for uf in uploaded_files:
            upload_list.append((uf.name, uf.read()))
        st.session_state.pdf_sources = upload_list

    st.divider()

    pdf_sources = st.session_state.pdf_sources
    if pdf_sources:
        st.success(f"**{len(pdf_sources)} PDFs** ready for processing")

        if st.button("Process All PDFs", type="primary", use_container_width=True):
            _run_batch(pdf_sources, api_key)

    # ── Results (live during processing + final) ──────────────────
    if st.session_state.results_df is not None:
        st.divider()
        _render_results()


def _run_batch(pdf_sources: list[tuple[str, "Path | bytes"]], api_key: str):
    """Process all PDFs with a live-updating results table."""
    extractor = PDFExtractor()
    verifier = TrainingPhotoVerifier(api_key=api_key)

    output_path = Path("output/cc_verification_results.csv")
    store = CSVResultStore(csv_path=output_path)
    processor = BatchProcessor(extractor=extractor, verifier=verifier, store=store)

    tmp_dir = Path(tempfile.mkdtemp(prefix="cc_batch_"))
    file_paths: list[Path] = []

    for name, source in pdf_sources:
        if isinstance(source, Path):
            file_paths.append(source)
        else:
            dest = tmp_dir / name
            dest.write_bytes(source)
            file_paths.append(dest)

    for fp in file_paths:
        processor.enqueue_file(fp)

    total = processor.queue_size
    progress_bar = st.progress(0, text=f"Processing 0/{total}...")

    # Live metrics row
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_total = m_col1.empty()
    m_accept = m_col2.empty()
    m_reject = m_col3.empty()
    m_error = m_col4.empty()

    # Live results table
    table_placeholder = st.empty()

    live_rows: list[dict] = []
    completed_jobs: list[VerificationJob] = []

    def on_complete(job: VerificationJob, index: int, total: int):
        decision = job.result.decision if job.result else "ERROR"
        progress_bar.progress(
            index / total,
            text=f"Processing {index}/{total} — {job.identifiers.lid}: {decision}",
        )

        row = CSVResultStore._job_to_row(job)
        live_rows.append(row)
        completed_jobs.append(job)

        df = pd.DataFrame(live_rows)

        accepted = len(df[df["decision"] == "ACCEPT"])
        rejected = len(df[df["decision"] == "REJECT"])
        errors = len(df[df["decision"] == "ERROR"])

        m_total.metric("Processed", f"{index}/{total}")
        m_accept.metric("Accepted", accepted)
        m_reject.metric("Rejected", rejected)
        m_error.metric("Errors", errors)

        # Show compact live table with key columns
        display_cols = ["lid", "decision", "people_count", "has_multiple_people",
                        "is_training_scene", "overlay_latitude", "overlay_longitude",
                        "scene_description"]
        available = [c for c in display_cols if c in df.columns]
        table_placeholder.dataframe(df[available], use_container_width=True, height=min(400, 50 + 35 * len(df)))

    processor.process_all(callback=on_complete)

    progress_bar.progress(1.0, text=f"Done — {total} PDFs processed")

    df = pd.DataFrame(live_rows)
    st.session_state.results_df = df
    st.session_state.jobs = completed_jobs

    st.success(
        f"Batch complete: **{len(df[df['decision'] == 'ACCEPT'])}** accepted, "
        f"**{len(df[df['decision'] == 'REJECT'])}** rejected. "
        f"Scroll down for full results and CSV download."
    )


def _render_results():
    """Render the final results with filtering, CSV download, and inspection."""
    st.subheader("Verification Results")

    df = st.session_state.results_df

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total", len(df))
    col2.metric("Accepted", len(df[df["decision"] == "ACCEPT"]))
    col3.metric("Rejected", len(df[df["decision"] == "REJECT"]))
    col4.metric("Errors", len(df[df.get("decision", "") == "ERROR"]) if "decision" in df.columns else 0)

    st.divider()

    filter_decision = st.multiselect(
        "Filter by decision",
        options=["ACCEPT", "REJECT", "ERROR"],
        default=["ACCEPT", "REJECT", "ERROR"],
    )
    filtered = df[df["decision"].isin(filter_decision)]

    st.dataframe(filtered, use_container_width=True, height=400)

    st.divider()

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    st.download_button(
        label="Download Full Results CSV",
        data=csv_buffer.getvalue(),
        file_name="cc_verification_results.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.divider()
    st.subheader("Inspect Individual Results")

    jobs = st.session_state.jobs
    if jobs:
        for job in jobs:
            decision = job.result.decision if job.result else "ERROR"
            icon = "+" if decision == "ACCEPT" else "-"
            with st.expander(f"[{icon}] {job.identifiers.lid} -- {decision}"):
                if job.image:
                    st.image(job.image, caption=job.identifiers.filename, width=400)
                if job.result:
                    st.json(job.result.to_dict())
                if job.error:
                    st.error(job.error)


if __name__ == "__main__":
    main()
