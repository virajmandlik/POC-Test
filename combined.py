import argparse
import copy
import json
import os
import subprocess
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")

import requests
from dotenv import load_dotenv

load_dotenv()

PYTHON = str(Path(__file__).parent / "venv312" / "Scripts" / "python.exe")
BASE_DIR = Path(__file__).parent
API_URL = "https://cxai-playground.cisco.com/chat/completions"

# Fixed schema for गाव नमुना सात (7/12 extract).
# GPT-4o-mini is instructed to populate exactly this structure.
OUTPUT_TEMPLATE = {
    "document_type": "",
    "state": "",
    "taluka": "",
    "district": "",
    "village": "",
    "village_code": "",
    "pu_id": "",
    "survey_number": "",
    "sub_division": "",
    "local_name": "",
    "tenure": {
        "class": "",
        "type": "",
    },
    "owner": {
        "name": "",
        "account_number": "",
    },
    "area": {
        "cultivable": {
            "jirayat_hectare": "",
            "bagayat_hectare": "",
            "total_hectare": "",
        },
        "uncultivable": {
            "class_a_hectare": "",
            "class_b_hectare": "",
            "total_hectare": "",
        },
        "pot_kharab_hectare": "",
        "total_area_hectare": "",
        "unit": "हे.आर.चौ.मी.",
    },
    "assessment": {
        "base_rupees": "",
        "special_rupees": "",
    },
    "mutation": {
        "last_number": "",
        "last_date": "",
        "pending": "",
        "old_numbers": [],
    },
    "rights": {
        "tenant_name": "",
        "other_rights": "",
    },
    "heir_info": "",
    "boundary_marks": "",
    "digital_signature": {
        "date": "",
        "verification_url": "",
        "reference_number": "",
    },
    "source_comparison": {
        "fields_differing": {},
        "paddle_only": [],
        "vision_only": [],
    },
}


def run_subprocess(label: str, cmd: list[str]) -> tuple[str, int, str, str, float]:
    """Run a command, return (label, exit_code, stdout, stderr, elapsed)."""
    print(f"  [{label}] Starting...")
    t0 = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(BASE_DIR),
        env=env,
    )
    elapsed = time.perf_counter() - t0
    status = "OK" if proc.returncode == 0 else "FAILED"
    print(f"  [{label}] {status} ({elapsed:.1f}s)")
    return label, proc.returncode, proc.stdout, proc.stderr, elapsed


def call_gpt4o_mini(api_key: str, system_prompt: str, user_prompt: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PaddleOCR + Vision in parallel, merge via GPT-4o-mini."
    )
    parser.add_argument("--input", required=True, help="Input PDF/image path")
    parser.add_argument(
        "--output",
        default="./output/combined_output.json",
        help="Final merged JSON output",
    )
    parser.add_argument("--lang", default="mr", help="PaddleOCR language (default: mr)")
    parser.add_argument(
        "--vision-input",
        default=None,
        help="Separate input for Vision API (e.g. preprocessed image). "
             "PaddleOCR still uses --input.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("CXAI_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        print("ERROR: Set CXAI_API_KEY in .env", file=sys.stderr)
        return 1

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        return 1

    vision_input = Path(args.vision_input).resolve() if args.vision_input else input_path

    ocr_out = BASE_DIR / "output" / "ocr_output.json"
    vision_out = BASE_DIR / "output" / "vision_output.json"
    combined_out = Path(args.output)

    print("=" * 56)
    print("  Combined Pipeline — PaddleOCR + Vision + GPT-4o-mini")
    print("=" * 56)
    print(f"  PaddleOCR input: {input_path}")
    if vision_input != input_path:
        print(f"  Vision input:    {vision_input}")
    print()

    paddle_cmd = [
        PYTHON, str(BASE_DIR / "paddleocr_pdf_to_json_demo.py"),
        "--input", str(input_path),
        "--output", str(ocr_out),
        "--lang", args.lang,
    ]
    vision_cmd = [
        PYTHON, str(BASE_DIR / "vision.py"),
        "--input", str(vision_input),
        "--output", str(vision_out),
    ]

    print("[1/3] Running PaddleOCR & Vision API in parallel...")
    t_total = time.perf_counter()
    results = {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(run_subprocess, "PaddleOCR", paddle_cmd): "paddle",
            pool.submit(run_subprocess, "Vision", vision_cmd): "vision",
        }
        for future in as_completed(futures):
            label, rc, stdout, stderr, elapsed = future.result()
            results[label] = {
                "exit_code": rc,
                "elapsed": elapsed,
                "stdout": stdout,
                "stderr": stderr,
            }

    t_parallel = time.perf_counter() - t_total

    paddle_ok = results.get("PaddleOCR", {}).get("exit_code") == 0
    vision_ok = results.get("Vision", {}).get("exit_code") == 0

    if not paddle_ok:
        print(f"\n  WARNING: PaddleOCR failed. stderr tail:")
        print(results["PaddleOCR"]["stderr"][-500:])
    if not vision_ok:
        print(f"\n  WARNING: Vision API failed. stderr tail:")
        print(results["Vision"]["stderr"][-500:])

    if not paddle_ok and not vision_ok:
        print("ERROR: Both extractions failed. Nothing to merge.", file=sys.stderr)
        return 1

    ocr_data = {}
    vision_data = {}

    if paddle_ok and ocr_out.exists():
        with ocr_out.open("r", encoding="utf-8") as f:
            ocr_data = json.load(f)

    if vision_ok and vision_out.exists():
        with vision_out.open("r", encoding="utf-8") as f:
            vision_data = json.load(f)

    ocr_text = ""
    if ocr_data.get("pages"):
        page = ocr_data["pages"][0]
        ocr_text = json.dumps(
            {
                "combined_text": page.get("combined_text", ""),
                "structured_fields": page.get("structured_fields", {}),
                "stats": page.get("stats", {}),
            },
            ensure_ascii=False,
            indent=2,
        )

    vision_text = vision_data.get("extracted_content", "")

    print(f"\n[2/3] Sending both extractions to GPT-4o-mini for merge...")
    t0 = time.perf_counter()

    template_str = json.dumps(OUTPUT_TEMPLATE, ensure_ascii=False, indent=2)

    system_prompt = (
        "You are a Maharashtra land record (गाव नमुना सात / 7-12 extract) data reconciliation expert.\n"
        "You receive two OCR extractions of the SAME document — one from PaddleOCR (offline) "
        "and one from GPT-4 Vision (online).\n\n"
        "RULES:\n"
        "1. Fill the EXACT JSON template below. Do NOT add, remove, or rename any keys.\n"
        "2. For each field, pick the most accurate/complete value from either source.\n"
        "3. If a field is not found in either source, set it to empty string \"\" or empty list [].\n"
        "4. In 'source_comparison.fields_differing', list fields where the two sources disagree. "
        "Format: {\"field_name\": {\"paddle\": \"value\", \"vision\": \"value\"}}.\n"
        "5. In 'source_comparison.paddle_only', list data found ONLY in PaddleOCR.\n"
        "6. In 'source_comparison.vision_only', list data found ONLY in Vision.\n"
        "7. Respond ONLY with valid JSON matching the template. No markdown fences, no explanation.\n\n"
        "TEMPLATE:\n"
        f"{template_str}"
    )

    user_prompt = (
        "=== PaddleOCR Extraction ===\n"
        f"{ocr_text}\n\n"
        "=== GPT-4 Vision Extraction ===\n"
        f"{vision_text}\n\n"
        "Fill the template with merged data from both sources."
    )

    try:
        gpt_resp = call_gpt4o_mini(api_key, system_prompt, user_prompt)
    except requests.exceptions.HTTPError as exc:
        print(f"ERROR: GPT API {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 1
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        print(f"ERROR: GPT API connection issue: {exc}", file=sys.stderr)
        return 1

    t_gpt = time.perf_counter() - t0

    raw_content = ""
    if "choices" in gpt_resp and gpt_resp["choices"]:
        raw_content = gpt_resp["choices"][0].get("message", {}).get("content", "")

    merged = raw_content.strip()
    if merged.startswith("```"):
        merged = merged.split("\n", 1)[1] if "\n" in merged else merged
        if merged.endswith("```"):
            merged = merged[:-3]
        merged = merged.strip()

    try:
        merged_json = json.loads(merged)
    except json.JSONDecodeError:
        merged_json = {"raw_llm_response": raw_content}

    validated = copy.deepcopy(OUTPUT_TEMPLATE)

    def _fill(template: dict, source: dict) -> None:
        """Recursively fill template keys from source, keeping template structure."""
        for key in template:
            if key not in source:
                continue
            if isinstance(template[key], dict) and isinstance(source[key], dict):
                _fill(template[key], source[key])
            else:
                template[key] = source[key]

    if isinstance(merged_json, dict) and "raw_llm_response" not in merged_json:
        _fill(validated, merged_json)
    else:
        validated = merged_json

    filled_count = 0
    empty_count = 0

    def _count_fields(obj: dict | list, depth: int = 0) -> None:
        nonlocal filled_count, empty_count
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, dict):
                    _count_fields(v, depth + 1)
                elif isinstance(v, list):
                    if v:
                        filled_count += 1
                    else:
                        empty_count += 1
                elif isinstance(v, str):
                    if v:
                        filled_count += 1
                    else:
                        empty_count += 1

    _count_fields(validated)

    print(f"\n[3/3] Writing combined output...")
    print(f"  Template fields filled: {filled_count}/{filled_count + empty_count}")
    t_total_end = time.perf_counter() - t_total

    final = {
        "source_file": str(input_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline": {
            "paddleocr": {
                "status": "ok" if paddle_ok else "failed",
                "elapsed_seconds": round(results.get("PaddleOCR", {}).get("elapsed", 0), 2),
            },
            "vision_api": {
                "status": "ok" if vision_ok else "failed",
                "elapsed_seconds": round(results.get("Vision", {}).get("elapsed", 0), 2),
            },
            "gpt4o_mini_merge": {
                "elapsed_seconds": round(t_gpt, 2),
                "usage": gpt_resp.get("usage", {}),
            },
        },
        "merged_extraction": validated,
        "timing_seconds": {
            "parallel_extraction": round(t_parallel, 2),
            "gpt_merge": round(t_gpt, 2),
            "total": round(t_total_end, 2),
        },
    }

    combined_out.parent.mkdir(parents=True, exist_ok=True)
    with combined_out.open("w", encoding="utf-8") as fp:
        json.dump(final, fp, ensure_ascii=False, indent=2)

    print()
    print("=" * 56)
    print("  TIMING SUMMARY")
    print("=" * 56)
    print(f"  {'PaddleOCR':<28} {results.get('PaddleOCR', {}).get('elapsed', 0):>8.2f}s")
    print(f"  {'Vision API':<28} {results.get('Vision', {}).get('elapsed', 0):>8.2f}s")
    print(f"  {'(parallel wall-clock)':<28} {t_parallel:>8.2f}s")
    print(f"  {'GPT-4o-mini merge':<28} {t_gpt:>8.2f}s")
    print(f"  {'-' * 38}")
    print(f"  {'TOTAL':<28} {t_total_end:>8.2f}s")
    print("=" * 56)
    print(f"\n  Output: {combined_out.resolve()}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
