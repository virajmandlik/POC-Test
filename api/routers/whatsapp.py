"""
WhatsApp router — Twilio Sandbox webhook for farmer-facing chatbot.

Farmers send a land record photo/PDF or a training session photo via
WhatsApp. The bot uploads it, runs UC1 or UC2, and replies with the
result in plain text.

POST /api/whatsapp/webhook  — Twilio forwards incoming messages here
GET  /api/whatsapp/webhook  — Twilio verification (echo challenge)

Prerequisites:
    - Twilio account (free sandbox) with TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
    - ngrok exposing localhost:8000 to the internet
    - Twilio Sandbox webhook pointed at https://<ngrok>/api/whatsapp/webhook
"""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Form, Response
from twilio.rest import Client as TwilioClient

from lib.audit import audit_log
from lib.config import cfg
from lib.db import get_db
from lib.jobs import job_manager

log = logging.getLogger("f4f.whatsapp")

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

_SESSIONS_COL = "whatsapp_sessions"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DOC_EXTENSIONS = {".pdf"}

_cxai_reachable: bool | None = None


def _detect_ocr_mode() -> str:
    """Return 'combined' if CXAI Vision API is reachable, else 'paddle' (offline)."""
    global _cxai_reachable
    if _cxai_reachable is not None:
        return "combined" if _cxai_reachable else "paddle"

    if not cfg.CXAI_API_KEY:
        _cxai_reachable = False
        return "paddle"

    import socket
    try:
        socket.create_connection(("cxai-playground.cisco.com", 443), timeout=3)
        _cxai_reachable = True
        log.info("CXAI API reachable — using combined mode")
        return "combined"
    except OSError:
        _cxai_reachable = False
        log.info("CXAI API unreachable (no VPN?) — falling back to paddle mode")
        return "paddle"


def _get_twilio_client() -> TwilioClient | None:
    if not cfg.TWILIO_ACCOUNT_SID or not cfg.TWILIO_AUTH_TOKEN:
        return None
    return TwilioClient(cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN)


def _twiml_reply(text: str) -> Response:
    """Return a TwiML XML response with a text message."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Message>{text}</Message>"
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")


def _send_whatsapp(to: str, body: str) -> None:
    """Send a WhatsApp message via Twilio REST API."""
    client = _get_twilio_client()
    if not client:
        log.error("Twilio not configured — cannot send reply to %s", to)
        return
    try:
        client.messages.create(
            from_=cfg.TWILIO_WHATSAPP_FROM,
            to=to,
            body=body,
        )
        log.info("WhatsApp reply sent to %s (%d chars)", to, len(body))
    except Exception as exc:
        log.error("Failed to send WhatsApp to %s: %s", to, exc)


def _save_session(phone: str, job_id: str, job_type: str) -> None:
    col = get_db()[_SESSIONS_COL]
    col.update_one(
        {"phone": phone},
        {"$set": {
            "phone": phone,
            "last_job_id": job_id,
            "last_job_type": job_type,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


def _get_session(phone: str) -> dict | None:
    col = get_db()[_SESSIONS_COL]
    return col.find_one({"phone": phone})


def _format_uc1_result(result: dict) -> str:
    """Format UC1 extraction result as farmer-friendly plain text."""
    if not result:
        return "Extraction completed but no data was found."

    merged = result.get("merged_extraction") or result
    lines = ["*Land Record Extracted*\n"]

    field_map = [
        ("document_type", "Document Type"),
        ("survey_number", "Survey No"),
        ("sub_division", "Sub-Division"),
        ("village", "Village"),
        ("taluka", "Taluka"),
        ("district", "District"),
        ("local_name", "Local Name"),
        ("tenure", "Tenure"),
        ("area", "Area"),
    ]

    # Combined/vision mode: flat keys at top level
    flat_found = False
    for key, label in field_map:
        val = merged.get(key)
        if val and str(val).strip() and str(val).strip() != "N/A":
            lines.append(f"  {label}: {val}")
            flat_found = True

    owners = merged.get("owners")
    if owners and isinstance(owners, list) and len(owners) > 0:
        names = []
        for o in owners[:5]:
            if isinstance(o, dict):
                names.append(o.get("name", str(o)))
            else:
                names.append(str(o))
        lines.append(f"  Owners: {', '.join(names)}")
        flat_found = True

    if flat_found:
        return "\n".join(lines)

    # Paddle mode: data is inside pages[].structured_fields and pages[].combined_text
    pages = merged.get("pages", [])
    if not pages:
        return "Extraction completed but no data was found in the document."

    for page in pages:
        page_idx = page.get("page_index", 0) + 1
        sf = page.get("structured_fields", {})
        page_has_fields = False

        if isinstance(sf, dict):
            for key, val in sf.items():
                val_str = str(val).strip()
                if not val_str:
                    continue
                # Truncate long values — PaddleOCR regex can capture trailing text
                if len(val_str) > 80:
                    val_str = val_str[:80] + "..."
                label = key.replace("_", " ").title()
                lines.append(f"  {label}: {val_str}")
                page_has_fields = True

        raw_text = page.get("combined_text", "")
        if raw_text and not page_has_fields:
            snippet = raw_text[:600]
            if len(raw_text) > 600:
                snippet += "..."
            lines.append(f"\n--- Page {page_idx} ---\n{snippet}")

    if len(lines) == 1:
        lines.append("Document scanned but structured extraction was limited in paddle mode.")

    return "\n".join(lines)


def _format_uc2_result(result: dict) -> str:
    """Format UC2 verification result as farmer-friendly plain text."""
    if not result:
        return "Verification completed but no result was returned."

    decision = result.get("decision", "UNKNOWN")
    lines = [f"*Photo Verification: {decision}*\n"]

    checks = result.get("checks", {})
    if isinstance(checks, dict):
        for check_name, passed in checks.items():
            status = "Pass" if passed else "Fail"
            label = check_name.replace("_", " ").title()
            lines.append(f"  {label}: {status}")

    reasons = result.get("rejection_reasons") or []
    if reasons:
        lines.append(f"\nReasons: {', '.join(reasons)}")

    return "\n".join(lines)


def _format_result(result: dict, job_type: str) -> str:
    if job_type.startswith("uc1"):
        return _format_uc1_result(result)
    return _format_uc2_result(result)


HELP_TEXT = (
    "*F4F Document Automation Bot*\n\n"
    "Send me a document and I'll process it:\n"
    "  - *Land record PDF/photo* -> I'll extract the data (UC1)\n"
    "  - *Training session photo* -> I'll verify it (UC2)\n\n"
    "Commands:\n"
    "  *status* — Check your last job\n"
    "  *help* — Show this message\n\n"
    "Tip: Send a photo directly from your camera for fastest results."
)


async def _download_media(media_url: str) -> tuple[bytes, str]:
    """Download media from Twilio and return (content, filename)."""
    auth = (cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN)
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        resp = await client.get(media_url, auth=auth)
        resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "pdf" in content_type:
        ext = ".pdf"
    elif "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    else:
        ext = ".jpg"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    filename = f"wa_{ts}_{short_id}{ext}"
    return resp.content, filename


def _save_upload(content: bytes, filename: str) -> str:
    """Save downloaded media to uploads dir and return the path string."""
    dest = cfg.UPLOAD_DIR / filename
    dest.write_bytes(content)
    file_id = hashlib.sha256(content).hexdigest()[:16]
    audit_log(
        "whatsapp.file_saved",
        user="whatsapp",
        detail={"filename": filename, "size_bytes": len(content), "file_id": file_id},
    )
    return str(dest)


def _poll_and_reply(job_id: str, phone: str, job_type: str) -> None:
    """Synchronous polling loop (runs in background thread). Sends result via Twilio."""
    max_polls = 120
    for _ in range(max_polls):
        job = job_manager.get(job_id)
        if not job:
            _send_whatsapp(phone, "Something went wrong — job not found.")
            return
        if job["status"] in ("completed", "failed", "cancelled"):
            break
        import time
        time.sleep(3)
    else:
        _send_whatsapp(phone, "Processing is taking too long. Please try again later.")
        return

    if job["status"] == "completed":
        message = _format_result(job.get("result", {}), job_type)
    elif job["status"] == "cancelled":
        message = "Your job was cancelled."
    else:
        message = f"Processing failed: {job.get('error', 'unknown error')}"

    _send_whatsapp(phone, message)
    audit_log(
        "whatsapp.result_sent",
        user="whatsapp",
        job_id=job_id,
        detail={"phone_hash": hashlib.sha256(phone.encode()).hexdigest()[:12], "status": job["status"]},
    )


@router.post("/webhook")
async def webhook(
    background_tasks: BackgroundTasks,
    From: str = Form(""),
    Body: str = Form(""),
    NumMedia: int = Form(0),
    MediaUrl0: str = Form(""),
    MediaContentType0: str = Form(""),
) -> Response:
    """Twilio WhatsApp webhook — receives incoming messages from farmers."""
    phone = From
    text = Body.strip().lower()

    log.info("WhatsApp from %s: text=%r, media=%d", phone[:15], text[:50], NumMedia)

    if not cfg.TWILIO_ACCOUNT_SID:
        return _twiml_reply("Bot is not configured. Please set Twilio credentials.")

    if NumMedia > 0 and MediaUrl0:
        try:
            content, filename = await _download_media(MediaUrl0)
        except Exception as exc:
            log.error("Media download failed: %s", exc)
            return _twiml_reply("Could not download your file. Please try again.")

        file_path = _save_upload(content, filename)
        ext = Path(filename).suffix.lower()

        if ext in DOC_EXTENSIONS:
            job_type = "uc1.extract"
            ocr_mode = _detect_ocr_mode()
            params: dict[str, Any] = {"file_path": file_path, "mode": ocr_mode, "lang": "mr"}
            reply = f"Received your land record document. Processing ({ocr_mode} mode)..."
        elif ext in IMAGE_EXTENSIONS:
            job_type = "uc2.verify"
            skip_vision = _detect_ocr_mode() == "paddle"
            params = {"image_path": file_path, "skip_vision": skip_vision}
            reply = "Received your photo. Verifying..."
        else:
            return _twiml_reply(f"Unsupported file type: {ext}\nPlease send a PDF, JPG, or PNG.")

        try:
            job_id = job_manager.submit(
                job_type=job_type,
                params=params,
                user=f"whatsapp:{phone}",
                tags=["channel:whatsapp"],
            )
        except Exception as exc:
            log.error("Job submission failed: %s", exc)
            return _twiml_reply("Could not start processing. Please try again later.")

        _save_session(phone, job_id, job_type)
        audit_log(
            "whatsapp.job_submitted",
            user=f"whatsapp:{phone}",
            job_id=job_id,
            detail={"job_type": job_type, "filename": filename},
        )

        background_tasks.add_task(_poll_and_reply, job_id, phone, job_type)
        return _twiml_reply(reply)

    if text == "status":
        session = _get_session(phone)
        if not session:
            return _twiml_reply("No recent jobs found. Send a document to get started.")

        job = job_manager.get(session["last_job_id"])
        if not job:
            return _twiml_reply("Your last job could not be found.")

        status = job["status"]
        progress = job.get("progress", 0)
        if status == "completed":
            msg = _format_result(job.get("result", {}), session["last_job_type"])
        elif status == "failed":
            msg = f"Last job failed: {job.get('error', 'unknown')}"
        elif status in ("pending", "running"):
            msg = f"Still processing... ({progress}% complete)"
        else:
            msg = f"Last job status: {status}"
        return _twiml_reply(msg)

    if text in ("help", "hi", "hello", "start"):
        return _twiml_reply(HELP_TEXT)

    if text and NumMedia == 0:
        return _twiml_reply(
            "I process documents and photos. "
            "Send me a land record PDF/image or a training session photo.\n\n"
            "Type *help* for more info."
        )

    return _twiml_reply(HELP_TEXT)


@router.get("/webhook")
async def webhook_verify():
    """Twilio webhook verification endpoint."""
    return Response(content="OK", media_type="text/plain")
