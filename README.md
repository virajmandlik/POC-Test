# Farmers for Forests - Document Automation POC

> Cisco x F4F Hackathon | March 2026

Automation toolkit for **Farmers for Forests (F4F)** farmer onboarding workflows. Two Streamlit apps that replace manual document verification with AI-powered pipelines.

---

## Quick Start

```bash
# Activate the virtual environment
venv312\Scripts\activate

# Run Use Case 1 - Land Record OCR
streamlit run usecase1_land_record_ocr.py

# Run Use Case 2 - CC Photo Verification
streamlit run usecase2_photo_verification.py
```

**Requirement:** Set `CXAI_API_KEY` in `.env` for Vision and Combined modes.

---

## Use Case 1 - Land Record OCR & Extraction

**File:** `usecase1_land_record_ocr.py`

Extracts structured data from Maharashtra 7/12 (Saat-Baara) land record documents (PDFs or images) into a standardised JSON schema.

### Three Extraction Modes

- **PaddleOCR (Offline)** - Runs `paddleocr_pdf_to_json_demo.py` as a subprocess. No API key needed.
- **Vision (Online)** - Sends document image to GPT-4 Vision API for direct extraction.
- **Combined (Recommended)** - PaddleOCR + Vision run in parallel. GPT-4o-mini merges both results into a single output.

### How It Works (Single Document)

1. **Upload** a PDF or image
2. **Quality Gate** checks blur, brightness, contrast, resolution, and skew
   - If PASS: skip preprocessing and go straight to extraction
   - If FAIL: interactive enhancement panel (contrast, denoise, deskew, threshold)
3. **Run extraction** on raw document (and preprocessed version if applicable)
4. **Comparative analysis** via GPT-4o-mini (raw vs preprocessed accuracy)
5. **Final output** saved as structured JSON

### Batch Processing

- Upload multiple PDFs/images or scan a local folder
- Queue-based processing with live progress table
- Choose extraction mode per batch (combined / paddle / vision)
- CSV export with key fields: district, taluka, village, survey number, owner, area
- Expand any row to inspect full extraction JSON

### Extracted Fields

`document_type`, `state`, `district`, `taluka`, `village`, `village_code`, `survey_number`, `owner` (name, account), `area` (cultivable, uncultivable, total), `tenure`, `mutation`, `rights`, `digital_signature`, `source_comparison`

---

## Use Case 2 - CC Training Photo Verification

**File:** `usecase2_photo_verification.py`

Verifies photographic evidence submitted for carbon credit training sessions. Each photo must prove a training event occurred with identifiable participants, location, and timestamp.

### Three Verification Checks

1. **Image Quality** (OpenCV) - Blur score, brightness, contrast
2. **Scene Analysis** (GPT-4 Vision) - People count, F4F representative present, training context, outdoor/rural setting
3. **Metadata Extraction** (GPT-4 Vision) - GPS coordinates and date/time from photo overlay (e.g. GPS Map Camera app)

### Accept / Reject Logic

A photo is **ACCEPTED** only if ALL of these pass:
- Image quality is acceptable (not blurry, not too dark/bright)
- Multiple people are visible in the frame
- Scene looks like a training session (not a selfie, not indoors)
- GPS coordinates found in photo overlay
- Date/time found in photo overlay

If any check fails, the photo is **REJECTED** with specific reasons.

### Single Photo Mode

Upload a JPEG/PNG and walk through 4 tabs:
1. Upload Photo
2. Quality Check results
3. Scene & Metadata analysis (with GPS map)
4. Final Verdict (Accept/Reject with full breakdown)

### Batch PDF Mode

- Upload CC training PDFs (filename pattern: `{surrogate_key}-{FID}-{LID}.pdf`)
- Automatically extracts training photo from page 3 of each PDF
- Live-updating results table with Accept / Reject / Error counts
- CSV export with all verification fields
- Click any row to see the extracted photo and full result JSON

---

## Project Structure

```
usecase1_land_record_ocr.py                  --> UC1: Land Record OCR (Streamlit app)
usecase2_photo_verification.py                    --> UC2: CC Photo Verification (Streamlit app)
paddleocr_pdf_to_json_demo.py   --> PaddleOCR subprocess worker (Python 3.12)
.env                            --> CXAI_API_KEY (not committed)

venv312/                        --> Python 3.12 virtual environment
cc_data_final/                  --> Sample CC training PDFs
uploads/                        --> Uploaded documents (auto-created)

output/
  ocr_output.json               --> PaddleOCR raw output
  raw_combined.json             --> UC1 raw pipeline result
  prep_combined.json            --> UC1 preprocessed pipeline result
  comparative_output.json       --> UC1 final merged output
  cc_verification_results.csv   --> UC2 batch results
  uc1_extraction_results.csv    --> UC1 batch results

docs/
  artect.png                    --> Architecture diagram
  usecase.png                   --> Use case diagram
  Meeting01.txt                 --> F4F kickoff meeting notes
```

---

## Architecture

![Architecture Diagram](docs/artect.png)

### UC1 Pipeline Flow

```
PDF/Image
  |
  v
Quality Gate (OpenCV)
  |
  +--> [Enhancement if needed]
  |
  +--> PaddleOCR (subprocess, offline)  --+
  |                                       |
  +--> GPT-4 Vision (API, online)  -------+
                                          |
                                          v
                                  GPT-4o-mini Merge
                                          |
                                          v
                                  Structured JSON Output
```

### UC2 Pipeline Flow

```
PDF
  |
  v
Extract Page 3 (training photo)
  |
  v
OpenCV Quality Check
  |
  v
GPT-4 Vision (scene analysis + overlay extraction)
  |
  v
Accept / Reject Decision
```

---

## Tech Stack

- **UI:** Streamlit
- **OCR (offline):** PaddleOCR via Python 3.12 subprocess
- **Vision AI:** GPT-4 Vision via CXAI Playground API
- **Data merge:** GPT-4o-mini
- **Image processing:** OpenCV, Pillow
- **PDF rendering:** pypdfium2
- **Languages:** Marathi, Hindi, English (Devanagari script)

---

## Setup

### Prerequisites

- Python 3.12+ (for PaddleOCR subprocess)
- Python 3.10+ (for Streamlit UI)
- `CXAI_API_KEY` in `.env`

### Install Dependencies

```bash
python -m venv venv312
venv312\Scripts\activate

pip install streamlit pandas opencv-python-headless numpy Pillow pypdfium2 requests python-dotenv paddleocr paddlepaddle
```

---

## Design Decisions

- **Self-contained files** - Each use case is a single .py file with zero cross-imports. Easy to deploy independently.
- **Quality gate before extraction** - Prevents wasting API calls on unreadable documents.
- **Parallel extraction** - PaddleOCR and Vision API run concurrently in Combined mode to cut wall-clock time.
- **CSV with file-lock retry** - Handles Windows file-locking when CSV is open in Excel during batch runs.
- **Preprocessing is optional** - The quality gate decides. Over-processing clean documents actually degrades accuracy.
