import os
import re
import time

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("paddleocr_demo")

BLOAT_KEYS = {"output_img", "doc_preprocessor_res"}

# Common Devanagari OCR misreads: wrong → correct
_DEVANAGARI_FIXES: list[tuple[str, str]] = [
    ("महाराषट्र", "महाराष्ट्र"),
    ("अमिलेख", "अभिलेख"),
    ("महवतुल", "महसूल"),
    ("भुधारणा", "भूधारणा"),
    ("प्रलवित", "प्रलंबित"),
    ("क्रमोंक", "क्रमांक"),
    ("शेताधे", "शेतजमिनीचे"),
    ("इत्तर", "इतर"),
    ("९एिष", "नियम"),
    ("आभि५७", "अभि.५७"),
    ("ई महाभमा", "ई-महाभूमी"),
    ("्ेत्र", "क्षेत्र"),
    ("क्ेत्र", "क्षेत्र"),
    ("क्ेत्,", "क्षेत्र,"),
    ("क्षत्र", "क्षेत्र"),
    ("PU-D :", "PU-ID:"),
    ("PU-D:", "PU-ID:"),
]

# Latin digit → Devanagari digit
_LATIN_TO_DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")


def _fix_text(text: str) -> str:
    """Apply Devanagari-specific corrections to raw OCR text."""
    for wrong, correct in _DEVANAGARI_FIXES:
        text = text.replace(wrong, correct)
    return text


def _normalize_digits(text: str, to_devanagari: bool = False) -> str:
    """Optionally normalize stray Latin digits in mostly-Devanagari text."""
    if not to_devanagari:
        return text
    devanagari_chars = sum(1 for c in text if "\u0900" <= c <= "\u097F")
    if devanagari_chars > len(text) * 0.3:
        return text.translate(_LATIN_TO_DEVANAGARI)
    return text


def _extract_structured_fields(text_blocks: list[dict]) -> dict[str, Any]:
    """Best-effort extraction of labeled fields from Maharashtra 7/12 form."""
    full_text = " ".join(b["text"] for b in text_blocks)
    fields: dict[str, Any] = {}

    patterns: list[tuple[str, str, int]] = [
        ("taluka", r"तालुका\s*[:\-]?\s*(.+?)(?:\s{2,}|$)", 1),
        ("district", r"जिल्हा\s*[:\-]?\s*(.+?)(?:\s{2,}|$)", 1),
        ("village", r"गाव\s*[:\-]?\s*(.+?)(?:\s{2,}|$)", 1),
        ("pu_id", r"PU-ID\s*[:\-]?\s*(\d+)", 1),
        ("survey_number", r"भूमापन क्रमांक", 0),
        ("owner_name", r"([\u0900-\u097F]+\s+[\u0900-\u097F]+\s+[\u0900-\u097F]+)", 0),
        ("last_ferfer_no", r"फेरफार क्रमांक\s*[:\-]?\s*([\d\u0966-\u096F]+)", 1),
        ("last_ferfer_date", r"दिनांक\s*[:\-]?\s*([\d\u0966-\u096F/]+)", 1),
    ]

    for name, pattern, group in patterns:
        match = re.search(pattern, full_text)
        if match:
            fields[name] = match.group(group).strip() if group else match.group(0).strip()

    for block in text_blocks:
        txt = block["text"]
        if re.match(r"^[\d\u0966-\u096F]+\.\d[\d.]*$", txt):
            if "area_hectare" not in fields:
                fields["area_hectare"] = txt
        if "भोगवटादार" in txt and "वर्ग" in txt:
            fields["tenure_class"] = txt
        if re.match(r"^[\d\u0966-\u096F]{2}/[\d\u0966-\u096F]{2}/[\d\u0966-\u096F]{4}$", txt):
            fields["date"] = txt

    for block in text_blocks:
        txt = block["text"]
        conf = block.get("confidence", 0)
        dev_count = sum(1 for c in txt if "\u0900" <= c <= "\u097F")
        latin_count = sum(1 for c in txt if c.isascii() and c.isalpha())
        if dev_count > 3 and latin_count == 0 and conf > 0.85:
            name_candidate = txt.strip()
            words = name_candidate.split()
            if 2 <= len(words) <= 5 and all(len(w) > 1 for w in words):
                if not any(kw in name_candidate for kw in [
                    "शासन", "नमुना", "तालुका", "जिल्हा", "गाव", "अधिकार",
                    "क्षेत्र", "आकार", "फेरफार", "वारस", "एकूण", "एकुण",
                    "पोख", "सीमा", "वर्ग", "बागायत", "जिरायत", "लागवड",
                    "भूमापन", "भूधारणा", "भोगवटादार", "खाते", "नाव",
                ]):
                    fields["owner_name"] = name_candidate
                    break

    return fields


def _to_serializable(obj: Any) -> Any:
    """Best-effort conversion for numpy arrays and other non-JSON types."""
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_serializable(v) for k, v in obj.items() if k not in BLOAT_KEYS}
    if isinstance(obj, (list, tuple, set)):
        return [_to_serializable(v) for v in obj]
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


def _normalize_result_item(result_item: Any) -> dict[str, Any]:
    """Extract structured OCR data from a PaddleOCR result object."""
    if isinstance(result_item, dict):
        data = result_item
    elif hasattr(result_item, "res"):
        data = getattr(result_item, "res")
    elif hasattr(result_item, "to_json"):
        data = json.loads(result_item.to_json())
    else:
        data = {"raw": str(result_item)}

    data = _to_serializable(data)
    rec_texts = data.get("rec_texts", []) if isinstance(data, dict) else []
    rec_scores = data.get("rec_scores", []) if isinstance(data, dict) else []
    rec_boxes = data.get("rec_boxes", []) if isinstance(data, dict) else []

    text_blocks = []
    for i, text in enumerate(rec_texts):
        if not isinstance(text, str) or not text.strip():
            continue
        corrected = _fix_text(text)
        block = {"text": corrected, "text_raw": text}
        if corrected != text:
            block["corrected"] = True
        if i < len(rec_scores):
            block["confidence"] = round(rec_scores[i], 4) if isinstance(rec_scores[i], (int, float)) else rec_scores[i]
        if i < len(rec_boxes):
            block["bbox"] = rec_boxes[i]
        text_blocks.append(block)

    low_conf = [b for b in text_blocks if b.get("confidence", 1) < 0.65]
    corrected_count = sum(1 for b in text_blocks if b.get("corrected"))

    combined_text = "\n".join(b["text"] for b in text_blocks)
    structured_fields = _extract_structured_fields(text_blocks)

    return {
        "page_index": data.get("page_index") if isinstance(data, dict) else None,
        "combined_text": combined_text,
        "structured_fields": structured_fields,
        "stats": {
            "total_text_blocks": len(text_blocks),
            "corrected_blocks": corrected_count,
            "low_confidence_blocks": len(low_conf),
            "avg_confidence": round(
                sum(b.get("confidence", 0) for b in text_blocks) / max(len(text_blocks), 1), 4
            ),
        },
        "text_blocks": text_blocks,
        "low_confidence_blocks": low_conf,
    }


def run_ocr(input_pdf: Path, output_json: Path, lang: str) -> dict[str, float]:
    timings: dict[str, float] = {}

    log.info("Step 1/4 — Importing PaddleOCR library...")
    t0 = time.perf_counter()
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        raise RuntimeError(
            "Failed to import PaddleOCR.\n"
            "  python -m pip install paddleocr\n"
            "  python -m pip install paddlepaddle==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/"
        ) from exc
    timings["import"] = time.perf_counter() - t0
    log.info("Import complete (%.1fs)", timings["import"])

    log.info("Step 2/4 — Initializing OCR engine (lang=%s)...", lang)
    log.info("  First run will download models (~85 MB) — subsequent runs use cache.")
    t0 = time.perf_counter()
    try:
        ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang=lang,
        )
    except Exception as exc:
        raise RuntimeError(
            "PaddleOCR initialization failed.\n"
            "Install paddlepaddle==3.0.0 in a Python 3.10-3.12 venv."
        ) from exc
    timings["model_init"] = time.perf_counter() - t0
    log.info("OCR engine ready (%.1fs)", timings["model_init"])

    log.info("Step 3/4 — Running OCR on: %s", input_pdf.name)
    t0 = time.perf_counter()
    results = ocr.predict(str(input_pdf))
    timings["inference"] = time.perf_counter() - t0
    log.info("OCR inference complete (%.1fs)", timings["inference"])

    log.info("Step 4/4 — Post-processing (corrections + field extraction)...")
    t0 = time.perf_counter()
    pages = []
    for idx, item in enumerate(results):
        try:
            page = _normalize_result_item(item)
            pages.append(page)
            stats = page["stats"]
            log.info(
                "  Page %d: %d blocks | %d corrected | %d low-conf | avg conf %.1f%%",
                idx,
                stats["total_text_blocks"],
                stats["corrected_blocks"],
                stats["low_confidence_blocks"],
                stats["avg_confidence"] * 100,
            )
            if page["structured_fields"]:
                for k, v in page["structured_fields"].items():
                    log.info("    %-20s = %s", k, v)
        except Exception as exc:
            log.warning("  Page %d: failed to parse — %s", idx, exc)
            pages.append({"page_index": None, "combined_text": "", "text_blocks": []})

    payload = {
        "source_file": str(input_pdf.resolve()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "PaddleOCR (PP-OCRv5)",
        "lang": lang,
        "page_count": len(pages),
        "pages": pages,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    with output_json.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    timings["post_process"] = time.perf_counter() - t0

    timings["total"] = sum(v for k, v in timings.items() if k != "total")
    payload["timing_seconds"] = {k: round(v, 2) for k, v in timings.items()}
    with output_json.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    file_size_kb = output_json.stat().st_size / 1024
    log.info("Output written: %.1f KB", file_size_kb)

    return timings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quick PaddleOCR PDF -> JSON demo (PoC)."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to input PDF file (example: ./test.pdf)",
    )
    parser.add_argument(
        "--output",
        default="./output/ocr_output.json",
        help="Path to output JSON file (default: ./output/ocr_output.json)",
    )
    parser.add_argument(
        "--lang",
        default="en",
        help="OCR language code: en, hi, ch, fr, mr, etc. (default: en)",
    )
    args = parser.parse_args()

    input_pdf = Path(args.input)
    output_json = Path(args.output)

    if not input_pdf.exists():
        log.error("Input file not found: %s", input_pdf)
        return 1
    if input_pdf.suffix.lower() != ".pdf":
        log.error("Input must be a PDF file.")
        return 1

    log.info("=" * 50)
    log.info("PaddleOCR PDF -> JSON Demo")
    log.info("  Input:    %s", input_pdf)
    log.info("  Output:   %s", output_json)
    log.info("  Language: %s", args.lang)
    log.info("=" * 50)

    try:
        timings = run_ocr(input_pdf=input_pdf, output_json=output_json, lang=args.lang)
    except Exception as exc:
        log.error(str(exc))
        return 1

    print()
    print("=" * 52)
    print("  TIMING SUMMARY")
    print("=" * 52)
    print(f"  {'Library import':<22} {timings.get('import', 0):>8.2f}s")
    print(f"  {'Model init/load':<22} {timings.get('model_init', 0):>8.2f}s")
    print(f"  {'OCR inference':<22} {timings.get('inference', 0):>8.2f}s")
    print(f"  {'Post-processing':<22} {timings.get('post_process', 0):>8.2f}s")
    print(f"  {'-' * 32}")
    print(f"  {'TOTAL':<22} {timings.get('total', 0):>8.2f}s")
    print("=" * 52)
    print(f"\n  JSON output written to: {output_json.resolve()}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
