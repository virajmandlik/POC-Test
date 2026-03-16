# Codebase Structure & Style Guide

> Authored by Viraj — documented for future reference (March 2026)

---

## Repository Layout

```
POC-Test/
├── usecase1_land_record_ocr.py          # UC1: Land Record OCR (Streamlit app, ~1924 lines)
├── usecase2_photo_verification.py       # UC2: CC Photo Verification (Streamlit app, ~1055 lines)
├── paddleocr_pdf_to_json_demo.py        # PaddleOCR CLI subprocess worker (~336 lines)
├── README.md                            # Project overview & architecture docs
├── rues.txt                             # Custom system prompt / coding rules for AI tools
├── test.pdf                             # Sample document for testing
├── .env                                 # CXAI_API_KEY (not committed)
├── .gitignore                           # Ignores venvs, outputs, IDE, cache files
├── cc_data_final/
│   └── final_annotations.csv            # Ground truth CC training annotations (manual labels)
├── docs/
│   └── Meeting01.txt                    # Meeting notes from F4F kickoff (09 Mar 2026)
├── venv312/                             # Python 3.12 venv for PaddleOCR (gitignored)
├── uploads/                             # Uploaded documents (auto-created, gitignored)
└── output/                              # Pipeline outputs (auto-created, gitignored)
```

---

## Architecture Philosophy

### Self-Contained Files (No Cross-Imports)

Each use case is a **single monolithic Python file** with zero imports from the other app files. This is a deliberate design choice:

- **UC1** (`usecase1_land_record_ocr.py`) — fully self-contained Streamlit app for land record extraction.
- **UC2** (`usecase2_photo_verification.py`) — fully self-contained Streamlit app for photo verification.
- **PaddleOCR worker** (`paddleocr_pdf_to_json_demo.py`) — standalone CLI script invoked as a subprocess from UC1.

The only inter-file dependency is UC1 calling the PaddleOCR worker via `subprocess.run()`, deliberately keeping it process-isolated (the worker runs in a separate Python 3.12 venv).

### POC / Hackathon Style

This is a proof-of-concept for the Cisco x F4F Hackathon. Code prioritises:
- **Speed of iteration** over abstraction
- **Visible demos** (Streamlit UI) over API endpoints
- **Complete vertical slices** (upload → process → display → export) per use case

---

## Code Style & Conventions

### Python Version & Typing

- Python 3.10+ type hints used throughout: `list[str]`, `dict[str, Any]`, `str | None`
- No use of `typing.List` / `typing.Dict` (PEP 585 style)
- `from __future__ import annotations` is **not** used — relies on Python 3.10+ natively
- `@dataclass` used extensively for structured data (`QualityReport`, `CheckResult`, `VerificationResult`, `DocumentJob`, etc.)
- `@dataclass(frozen=True)` used for immutable value types (e.g. `PDFIdentifiers`)

### Naming Conventions

| Element | Convention | Examples |
|---------|-----------|----------|
| Classes | PascalCase | `QualityChecker`, `ExtractionEngine`, `SemanticAnalyzer` |
| Functions/methods | snake_case | `run_ocr()`, `extract_photo()`, `_parse_llm_json()` |
| Private helpers | Leading underscore | `_fix_text()`, `_normalize_digits()`, `_run_subprocess()` |
| Constants | UPPER_SNAKE_CASE | `API_URL`, `UPLOAD_DIR`, `OUTPUT_TEMPLATE`, `CSV_COLUMNS` |
| Module-level regex | Leading underscore + UPPER | `_FILENAME_RE`, `_DEVANAGARI_FIXES`, `_TRAINING_PHOTO_PAGE` |
| Streamlit UI functions | Leading underscore + descriptive | `_single_mode()`, `_batch_mode()`, `_render_check_card()` |

### Module Structure Pattern

Each use case file follows this consistent layout (separated by box-drawing section headers):

```
1. Module docstring (with run instructions)
2. Imports (stdlib → third-party → dotenv/streamlit)
3. Constants and config (API URLs, paths, templates, CSV column lists)
4. Utility functions (image conversion, base64, field counting)
5. Data classes
6. Processing classes (checkers, analyzers, engines) — each with its own section
7. Batch processing (Queue-based: Job dataclass → CSVResultStore → BatchProcessor)
8. Streamlit UI helpers
9. Streamlit UI — Single mode
10. Streamlit UI — Batch mode
11. main() entry point
```

### Section Dividers

Viraj uses a distinctive box-drawing style for section headers:

```python
# ═══════════════════════════════════════════════════════════════════════
# SECTION NAME IN CAPS
# ═══════════════════════════════════════════════════════════════════════
```

These are used consistently in both UC1 and UC2 to separate logical sections (Utility Functions, Extraction Engines, Batch Processing, Streamlit UI, etc.).

### Docstrings

- **Module-level** docstrings are detailed, including run instructions and requirements.
- **Class-level** docstrings are brief one-liners describing purpose.
- **Method-level** docstrings are used selectively (complex/public methods only).
- Style: plain text, not reStructuredText or Google/NumPy format.

### Logging

- Uses `logging.getLogger()` with descriptive logger names per module:
  - `"paddleocr_demo"`, `"pipeline_ui"`, `"cc_verify"`
- `log.info()` / `log.warning()` / `log.error()` used throughout
- PaddleOCR worker uses `logging.basicConfig()` with a custom format: `%(asctime)s | %(levelname)-7s | %(message)s`
- UI apps rely on Streamlit's built-in log handling

### Error Handling

- `try/except` around all external calls (subprocess, API, file I/O)
- Graceful degradation: if one pipeline fails in combined mode, the other's result is still used
- CSV writing has Windows file-lock retry logic (3 attempts with 1s delay, then writes to timestamped fallback file)
- API errors return structured dicts with `"status": "failed"` and `"error"` keys rather than raising

### API / LLM Integration Pattern

All LLM calls follow the same pattern:

1. Build `headers` dict with Bearer token auth
2. Build `payload` dict with model, temperature, messages
3. `requests.post()` with `timeout=120`
4. Parse response: extract `choices[0].message.content`
5. Strip markdown fences if present, `json.loads()` the content
6. Return structured dict or `{"raw_llm_response": content}` on parse failure

LLM system prompts are stored as **class-level string constants** (e.g. `SceneAnalyzer.SYSTEM_PROMPT`, `ExtractionEngine.VISION_SYSTEM_PROMPT`) and the JSON output templates are module-level dicts (e.g. `OUTPUT_TEMPLATE`, `SemanticAnalyzer.SEMANTIC_SCHEMA`).

### Streamlit UI Patterns

- `st.session_state` is used extensively for multi-step workflows (step tracking, result caching)
- Session state keys are initialized with default values using a `for key, default` loop:
  ```python
  for key, default in [("step", 1), ("approved", False), ...]:
      if key not in st.session_state:
          st.session_state[key] = default
  ```
- Tabs are used for step-by-step wizards (UC1 has 5 tabs, UC2 has 4 tabs)
- `st.columns()` for layout, `st.metrics()` for KPIs, `st.dataframe()` for tables
- `st.status()` context manager for operations with progress feedback
- `st.expander()` for drill-down into individual batch results
- Live-updating batch tables via placeholder `.dataframe()` calls in callbacks
- `st.balloons()` on successful completion

### Batch Processing Architecture

Both use cases follow the same queue-based batch pattern:

```
Job dataclass       →  enqueue into Queue
BatchProcessor      →  sequential dequeue, process, callback
CSVResultStore      →  append result row to CSV (with lock retry)
Streamlit callback  →  live-update progress bar + table
```

Key classes per use case:
- **UC1:** `DocumentJob` → `UC1CSVResultStore` → `UC1BatchProcessor`
- **UC2:** `VerificationJob` → `CSVResultStore` → `BatchProcessor`

Factory pattern is used in UC2: `PipelineFactory.create()` wires up the pipeline components.

---

## Dependencies & Runtime

### Third-Party Libraries

| Library | Purpose |
|---------|---------|
| `streamlit` | Web UI framework |
| `opencv-python-headless` (`cv2`) | Image quality checks, enhancement, deskew |
| `numpy` | Array ops for OpenCV |
| `Pillow` (`PIL`) | Image I/O and manipulation |
| `pypdfium2` (`pdfium`) | PDF page rendering to PIL images |
| `pandas` | DataFrames for batch results and CSV |
| `requests` | HTTP calls to CXAI API |
| `python-dotenv` | `.env` file loading |
| `paddleocr` + `paddlepaddle` | Offline OCR (runs in venv312 subprocess) |
| `graphviz` (Streamlit built-in) | Ownership knowledge graph rendering |

### Virtual Environment Setup

PaddleOCR requires Python 3.12 and runs in a dedicated venv (`venv312/`). The Streamlit apps invoke it via subprocess:

```python
PYTHON_312 = str(BASE_DIR / "venv312" / "Scripts" / "python.exe")
cmd = [PYTHON_312, PADDLE_SCRIPT, "--input", ..., "--output", ..., "--lang", lang]
subprocess.run(cmd, ...)
```

> **Note:** The `venv312` path uses Windows `Scripts/python.exe`. On macOS/Linux this would need to be `bin/python`.

### External API

All vision/LLM calls go to `https://cxai-playground.cisco.com/chat/completions` using:
- `gpt-4-vision-playground` for image analysis
- `gpt-4o-mini` for text merging, comparison, and semantic analysis

Auth via `CXAI_API_KEY` environment variable (loaded from `.env`).

---

## Domain-Specific Notes

### Devanagari / Marathi OCR

The PaddleOCR worker includes:
- A hand-curated **correction table** (`_DEVANAGARI_FIXES`) mapping common OCR misreads to correct Marathi text
- Latin-to-Devanagari digit translation (`_LATIN_TO_DEVANAGARI`)
- Regex-based field extraction for 7/12 form fields (taluka, district, survey number, etc.)

### Data Schemas

- **UC1 output:** Defined by `OUTPUT_TEMPLATE` — a deeply nested dict covering location, owners, area, assessment, mutation, encumbrances, water, and digital signature fields
- **UC1 semantic:** Defined by `SemanticAnalyzer.SEMANTIC_SCHEMA` — ownership chain, encumbrances mapped to owners, wells, key dates
- **UC2 output:** Defined by `VerificationResult.to_dict()` and `CSV_COLUMNS` — image quality, scene analysis, GPS/timestamp overlay, accept/reject decision

### Ground Truth Data

`cc_data_final/final_annotations.csv` contains manual verification labels (approved/rejected/pending) for CC training documents, with columns for LID, status, comments, check date, and reviewer name.

---

## Key Design Decisions (from README)

1. **Quality gate before extraction** — prevents wasting API calls on unreadable documents
2. **Parallel extraction** — PaddleOCR and Vision API run concurrently via `ThreadPoolExecutor` in combined mode
3. **Preprocessing is optional** — quality gate decides; over-processing clean docs degrades accuracy
4. **Semantic analysis is on-demand** — user-triggered to avoid unnecessary API costs
5. **CSV with file-lock retry** — handles Windows file-locking when CSV is open in Excel during batch runs
6. **Subprocess isolation for PaddleOCR** — runs in its own venv/process to avoid dependency conflicts
