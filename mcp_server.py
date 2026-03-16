"""
F4F MCP Server — exposes UC1 and UC2 as tools for AI agents.

AI assistants (Claude, Cursor, etc.) can discover and call these tools
via the Model Context Protocol to process land records and verify photos.

Run standalone:
    python mcp_server.py

Configure in Cursor:  .cursor/mcp.json
Configure in Claude:  claude_desktop_config.json

Both tools talk to the existing FastAPI backend — the same pipeline
that the Streamlit UI and WhatsApp bot use.
"""

import json
import os
import time
from pathlib import Path

import httpx
from fastmcp import FastMCP

API_URL = os.environ.get("API_URL", "http://localhost:8000")
POLL_INTERVAL = 3
MAX_POLL_SECONDS = 300

mcp = FastMCP(
    "F4F Document Automation",
    instructions=(
        "Farmers for Forests document automation tools. "
        "Use extract_land_record for Maharashtra 7/12 land record PDFs/images. "
        "Use verify_photo for carbon credit training session photos."
    ),
)


def _upload_file(file_path: str) -> dict:
    """Upload a file to the F4F API and return the response."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with httpx.Client(timeout=60) as client:
        with open(path, "rb") as f:
            resp = client.post(
                f"{API_URL}/api/upload",
                files={"file": (path.name, f)},
                params={"user": "mcp-agent"},
            )
        resp.raise_for_status()
    return resp.json()


def _submit_and_poll(endpoint: str, payload: dict) -> dict:
    """Submit a job and poll until completion or failure."""
    with httpx.Client(timeout=60) as client:
        resp = client.post(f"{API_URL}{endpoint}", json=payload)
        resp.raise_for_status()
        submit_data = resp.json()

    job_id = submit_data["job_id"]
    deadline = time.time() + MAX_POLL_SECONDS

    while time.time() < deadline:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{API_URL}/api/jobs/{job_id}")
            resp.raise_for_status()
            job = resp.json()

        status = job["status"]
        if status == "completed":
            return job.get("result", {})
        if status == "failed":
            raise RuntimeError(f"Job failed: {job.get('error', 'unknown')}")
        if status == "cancelled":
            raise RuntimeError("Job was cancelled")

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Job {job_id} did not complete within {MAX_POLL_SECONDS}s")


@mcp.tool()
def extract_land_record(file_path: str, mode: str = "combined") -> str:
    """Extract structured data from a Maharashtra 7/12 (Saat-Baara) land record document.

    Uploads the file, runs OCR + AI extraction, and returns structured data
    including survey number, owners, area, village, mutations, and encumbrances.

    Args:
        file_path: Absolute path to a PDF or image file (JPG, PNG) on the local machine.
        mode: OCR mode — 'combined' (PaddleOCR + GPT-4 Vision merged, best accuracy),
              'vision' (GPT-4 Vision only), or 'paddle' (offline PaddleOCR only).

    Returns:
        JSON string with extracted land record fields.
    """
    upload = _upload_file(file_path)
    server_path = upload["path"]

    result = _submit_and_poll(
        "/api/uc1/extract",
        {
            "file_path": server_path,
            "mode": mode,
            "lang": "mr",
            "user": "mcp-agent",
            "tags": ["channel:mcp"],
        },
    )

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def verify_photo(image_path: str) -> str:
    """Verify a carbon credit training session photo for authenticity.

    Checks image quality (blur, brightness, contrast), analyzes the scene
    using AI vision (are people present? is it a training session? GPS/date
    overlay?), and returns an ACCEPT or REJECT decision with reasons.

    Args:
        image_path: Absolute path to a JPG, PNG, or WEBP photo on the local machine.

    Returns:
        JSON string with decision (ACCEPT/REJECT), individual check results,
        and rejection reasons if applicable.
    """
    upload = _upload_file(image_path)
    server_path = upload["path"]

    result = _submit_and_poll(
        "/api/uc2/verify",
        {
            "image_path": server_path,
            "skip_vision": False,
            "user": "mcp-agent",
            "tags": ["channel:mcp"],
        },
    )

    return json.dumps(result, indent=2, default=str)


if __name__ == "__main__":
    mcp.run()
