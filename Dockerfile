# ============================================================
# F4F POC — Multi-service Docker Image
#
# Builds a single image used by both the FastAPI backend (api)
# and the Streamlit frontend (ui). PaddleOCR is installed in
# the same environment; venv312 symlinks provide compatibility
# with the subprocess invocation paths in the existing code.
#
# Build:   docker compose build
# Run:     docker compose up -d
# ============================================================

FROM python:3.12-slim AS base

# ── System dependencies ──────────────────────────────────────
# libgl1 + libglib2.0  → OpenCV headless
# graphviz             → st.graphviz_chart (ownership graphs)
# libgomp1             → PaddlePaddle threading
# curl                 → healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        graphviz \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── App user (non-root) ─────────────────────────────────────
RUN groupadd -r f4f && useradd -r -g f4f -m -s /bin/bash f4f

WORKDIR /app

# ── Python dependencies ─────────────────────────────────────
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir paddlepaddle paddleocr

# ── venv312 compatibility layer ──────────────────────────────
# The codebase invokes PaddleOCR as a subprocess via:
#   Linux:   venv312/bin/python3
#   Windows: venv312/Scripts/python.exe
# We create both paths as symlinks to the container's Python
# so the existing code works without modification.
RUN mkdir -p /app/venv312/bin /app/venv312/Scripts \
    && ln -s /usr/local/bin/python3 /app/venv312/bin/python3 \
    && ln -s /usr/local/bin/python3 /app/venv312/Scripts/python.exe

# ── Application code ────────────────────────────────────────
COPY --chown=f4f:f4f . .

# ── Writable directories ────────────────────────────────────
RUN mkdir -p /app/uploads /app/output \
    && chown -R f4f:f4f /app/uploads /app/output /app/venv312

# ── Streamlit config (disable telemetry, set ports) ──────────
RUN mkdir -p /home/f4f/.streamlit \
    && printf '[server]\nheadless = true\nenableCORS = false\nenableXsrfProtection = false\naddress = "0.0.0.0"\n\n[browser]\ngatherUsageStats = false\n' \
       > /home/f4f/.streamlit/config.toml \
    && chown -R f4f:f4f /home/f4f/.streamlit

USER f4f

# ── Default: run the FastAPI backend ─────────────────────────
# Override CMD in docker-compose per service
EXPOSE 8000 8501

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
