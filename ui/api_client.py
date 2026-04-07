"""
Shared API client for the unified F4F UI.

All pages use these helpers to talk to the FastAPI backend.
No direct lib imports — everything goes through HTTP.
"""

import os
import time
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
_TIMEOUT = int(os.environ.get("API_TIMEOUT", "30"))


# ─────────────────────────────────────────────────────────────────────
# LOW-LEVEL
# ─────────────────────────────────────────────────────────────────────


def _handle_error(exc: Exception) -> None:
    st.toast(f"API request failed: {exc}", icon="🚨")


def get(path: str, params: dict | None = None, timeout: int = _TIMEOUT) -> dict | list | None:
    try:
        r = requests.get(f"{API_URL}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        _handle_error(exc)
        return None


def post(path: str, json_data: dict | None = None, params: dict | None = None,
         timeout: int = _TIMEOUT) -> dict | None:
    try:
        r = requests.post(f"{API_URL}{path}", json=json_data, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        _handle_error(exc)
        return None


def delete(path: str, params: dict | None = None, timeout: int = _TIMEOUT) -> dict | None:
    try:
        r = requests.delete(f"{API_URL}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        _handle_error(exc)
        return None


def upload_file(file_obj, user: str = "ui") -> dict | None:
    """Upload a file via multipart form. Returns {file_id, filename, path, size_bytes}."""
    try:
        r = requests.post(
            f"{API_URL}/api/upload",
            files={"file": (file_obj.name, file_obj.getvalue(), file_obj.type)},
            params={"user": user},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        _handle_error(exc)
        return None


def upload_files(file_objs: list, user: str = "ui") -> list[dict]:
    """Upload multiple files. Returns list of upload responses."""
    results = []
    for f in file_objs:
        result = upload_file(f, user=user)
        if result:
            results.append(result)
    return results


# ─────────────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────────────


# @st.cache_data(ttl=10)
def health() -> dict | None:
    return get("/api/health")


# ─────────────────────────────────────────────────────────────────────
# JOBS — convenience helpers
# ─────────────────────────────────────────────────────────────────────


def submit_job(endpoint: str, payload: dict) -> dict | None:
    """POST to a job-submission endpoint. Returns {job_id, ...}."""
    return post(endpoint, json_data=payload)


def get_job(job_id: str) -> dict | None:
    return get(f"/api/jobs/{job_id}")


def list_jobs(**kwargs) -> dict | None:
    return get("/api/jobs", params=kwargs)


def cancel_job(job_id: str, user: str = "ui") -> dict | None:
    return post(f"/api/jobs/{job_id}/cancel", params={"user": user})


def retry_job(job_id: str, user: str = "ui") -> dict | None:
    return post(f"/api/jobs/{job_id}/retry", params={"user": user})


def remove_job(job_id: str, user: str = "ui") -> dict | None:
    return delete(f"/api/jobs/{job_id}", params={"user": user})


def purge_jobs(older_than_hours: int = 24, user: str = "ui") -> dict | None:
    return post("/api/jobs/purge", json_data={"older_than_hours": older_than_hours},
                params={"user": user})


def poll_job(job_id: str, poll_interval: float = 1.0, timeout: float = 300.0) -> dict | None:
    """Block until job finishes or timeout.  Returns final job dict."""
    start = time.time()
    while time.time() - start < timeout:
        job = get_job(job_id)
        if not job:
            return None
        if job.get("status") in ("completed", "failed", "cancelled"):
            return job
        time.sleep(poll_interval)
    return get_job(job_id)


# ─────────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────────


def get_audit_logs(**kwargs) -> dict | None:
    return get("/api/audit", params=kwargs)
