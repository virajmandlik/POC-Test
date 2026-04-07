"""
Centralised configuration — loads .env and exposes typed settings.

All config is read from environment variables (with .env fallback).
Import `cfg` from this module to access settings anywhere.
"""

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BASE_DIR / ".env")


@dataclass(frozen=True)
class Config:
    """Immutable application configuration."""

    # ── Paths ────────────────────────────────────────────────────
    BASE_DIR: Path = _BASE_DIR
    UPLOAD_DIR: Path = field(default_factory=lambda: _BASE_DIR / "uploads")
    OUTPUT_DIR: Path = field(default_factory=lambda: _BASE_DIR / "output")

    # ── MongoDB ──────────────────────────────────────────────────
    MONGO_URI: str = field(
        default_factory=lambda: os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    )
    MONGO_DB: str = field(
        default_factory=lambda: os.environ.get("MONGO_DB", "f4f_poc")
    )

    # ── API keys ─────────────────────────────────────────────────
    CXAI_API_KEY: str = field(
        default_factory=lambda: os.environ.get("CXAI_API_KEY", "")
    )

    # ── Twilio WhatsApp Sandbox ──────────────────────────────────
    TWILIO_ACCOUNT_SID: str = field(
        default_factory=lambda: os.environ.get("TWILIO_ACCOUNT_SID", "")
    )
    TWILIO_AUTH_TOKEN: str = field(
        default_factory=lambda: os.environ.get("TWILIO_AUTH_TOKEN", "")
    )
    TWILIO_WHATSAPP_FROM: str = field(
        default_factory=lambda: os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    )

    # ── PaddleOCR subprocess ─────────────────────────────────────
    # Resolved at runtime per OS so the same config works on Win/Mac/Linux
    PADDLE_SCRIPT: Path = field(
        default_factory=lambda: _BASE_DIR / "paddleocr_pdf_to_json_demo.py"
    )

    # ── Job engine ───────────────────────────────────────────────
    JOB_WORKER_THREADS: int = field(
        default_factory=lambda: int(os.environ.get("JOB_WORKER_THREADS", "2"))
    )

    # ── Image Enhancement ─────────────────────────────────────
    ENHANCE_CONTRAST: float = field(
        default_factory=lambda: float(os.environ.get("ENHANCE_CONTRAST", "1.3"))
    )
    ENHANCE_BRIGHTNESS: float = field(
        default_factory=lambda: float(os.environ.get("ENHANCE_BRIGHTNESS", "1.05"))
    )
    ENHANCE_DENOISE: str = field(
        default_factory=lambda: os.environ.get("ENHANCE_DENOISE", "nlm")
    )
    ENHANCE_DESKEW: bool = field(
        default_factory=lambda: os.environ.get("ENHANCE_DESKEW", "true").lower() == "true"
    )
    ENHANCE_ADAPTIVE_THRESH: bool = field(
        default_factory=lambda: os.environ.get("ENHANCE_ADAPTIVE_THRESH", "false").lower() == "true"
    )

    # ── Platform ─────────────────────────────────────────────────
    PLATFORM: str = field(default_factory=lambda: platform.system().lower())

    def __post_init__(self):
        # Ensure writable directories exist
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def python_paddle(self) -> str:
        """Path to the Python interpreter for the PaddleOCR venv.

        Windows: venv312\\Scripts\\python.exe
        macOS/Linux: venv312/bin/python3
        """
        venv = self.BASE_DIR / "venv312"
        if self.PLATFORM == "windows":
            return str(venv / "Scripts" / "python.exe")
        return str(venv / "bin" / "python3")


# Singleton — import this everywhere
cfg = Config()
