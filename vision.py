import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
import pypdfium2 as pdfium
from dotenv import load_dotenv

load_dotenv()


def pdf_page_to_base64(pdf_path: str, page_index: int = 0, scale: float = 2.0) -> str:
    """Render a single PDF page to a base64-encoded JPEG string."""
    pdf = pdfium.PdfDocument(pdf_path)
    if page_index >= len(pdf):
        raise ValueError(f"Page {page_index} does not exist (PDF has {len(pdf)} pages)")
    page = pdf[page_index]
    bitmap = page.render(scale=scale)
    pil_image = bitmap.to_pil()
    buffer = io.BytesIO()
    pil_image.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def image_to_base64(image_path: str) -> str:
    """Read an image file and return its base64-encoded string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def call_vision_api(
    image_base64: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    api_url: str = "https://cxai-playground.cisco.com/chat/completions",
    model: str = "gpt-4-vision-playground",
    temperature: float = 0.2,
) -> dict:
    """Send a base64-encoded image to the vision API and return the response."""
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}",
                    },
                },
            ],
        },
    ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }

    response = requests.post(api_url, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="GPT-4 Vision API — extract text/data from PDF or image."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to input file (PDF or image: jpg/png/jpeg)",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=0,
        help="PDF page index to process (default: 0, first page)",
    )
    parser.add_argument(
        "--output",
        default="./output/vision_output.json",
        help="Path to output JSON file (default: ./output/vision_output.json)",
    )
    parser.add_argument(
        "--system-prompt",
        default=(
            "You are a document OCR and data extraction expert. "
            "Extract ALL text and structured data from the provided document image. "
            "Return the result as valid JSON with fields and their values."
        ),
        help="System prompt for the vision model",
    )
    parser.add_argument(
        "--user-prompt",
        default=(
            "Extract all text and data from this document image. "
            "Return a JSON object with all fields, labels, values, "
            "numbers, dates, and names found in the document. "
            "Preserve the original language (Marathi/Hindi/English)."
        ),
        help="User prompt for the vision model",
    )
    args = parser.parse_args()

    api_key = os.environ.get("CXAI_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        print("ERROR: Set your API key in .env file (CXAI_API_KEY=...)", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        return 1

    print("=" * 52)
    print("  GPT-4 Vision — Document Extraction")
    print("=" * 52)
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_path}")

    suffix = input_path.suffix.lower()
    print(f"\n[1/3] Converting input to base64 image...")
    t0 = time.perf_counter()

    if suffix == ".pdf":
        print(f"  PDF detected — rendering page {args.page} to JPEG...")
        image_b64 = pdf_page_to_base64(str(input_path), page_index=args.page)
    elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
        print(f"  Image file detected — encoding directly...")
        image_b64 = image_to_base64(str(input_path))
    else:
        print(f"ERROR: Unsupported file type: {suffix}", file=sys.stderr)
        return 1

    img_size_kb = len(image_b64) * 3 / 4 / 1024
    print(f"  Image encoded: ~{img_size_kb:.0f} KB")
    t_encode = time.perf_counter() - t0

    print(f"\n[2/3] Calling GPT-4 Vision API...")
    t0 = time.perf_counter()
    try:
        response = call_vision_api(
            image_base64=image_b64,
            api_key=api_key,
            system_prompt=args.system_prompt,
            user_prompt=args.user_prompt,
        )
    except requests.exceptions.HTTPError as exc:
        print(f"ERROR: API returned {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 1
    except requests.exceptions.ConnectionError:
        print("ERROR: Could not connect to the API. Check your network/VPN.", file=sys.stderr)
        return 1
    except requests.exceptions.Timeout:
        print("ERROR: API request timed out (120s).", file=sys.stderr)
        return 1
    t_api = time.perf_counter() - t0

    content = ""
    if "choices" in response and response["choices"]:
        content = response["choices"][0].get("message", {}).get("content", "")

    print(f"\n[3/3] Writing output...")
    result = {
        "source_file": str(input_path.resolve()),
        "generated_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "model": response.get("model", "gpt-4-vision-playground"),
        "extracted_content": content,
        "usage": response.get("usage", {}),
        "timing_seconds": {
            "image_encode": round(t_encode, 2),
            "api_call": round(t_api, 2),
            "total": round(t_encode + t_api, 2),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)

    print()
    print("=" * 52)
    print("  TIMING SUMMARY")
    print("=" * 52)
    print(f"  {'Image encoding':<22} {t_encode:>8.2f}s")
    print(f"  {'API call':<22} {t_api:>8.2f}s")
    print(f"  {'-' * 32}")
    print(f"  {'TOTAL':<22} {t_encode + t_api:>8.2f}s")
    print("=" * 52)

    print(f"\n  JSON output written to: {output_path.resolve()}")

    print("\n--- Extracted Content Preview ---")
    preview = content[:500] + "..." if len(content) > 500 else content
    print(preview)
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
