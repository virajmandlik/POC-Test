#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# dev.sh — Setup, start, and stop F4F POC apps (macOS / Linux)
#
# Usage:
#   ./dev.sh setup     — Create venv and install dependencies
#   ./dev.sh start     — Start all Streamlit apps (UC1 + UC2 + Admin)
#   ./dev.sh stop      — Stop all running Streamlit processes
#   ./dev.sh status    — Check what's running
#   ./dev.sh restart   — Stop then start
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
PID_DIR="$SCRIPT_DIR/.pids"
LOG_DIR="$SCRIPT_DIR/logs"

# Ports for each app
PORT_API=8000
PORT_UC1=8501
PORT_UC2=8503
PORT_ADMIN=8502

# ── Detect Python ────────────────────────────────────────────────
find_python() {
    # Prefer Python 3.11+ from Homebrew, then system python3
    for candidate in \
        /opt/homebrew/bin/python3.12 \
        /opt/homebrew/bin/python3.11 \
        /opt/homebrew/bin/python3 \
        /usr/local/bin/python3.12 \
        /usr/local/bin/python3.11 \
        /usr/local/bin/python3 \
        python3.12 \
        python3.11 \
        python3; do
        if command -v "$candidate" &>/dev/null; then
            version=$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    echo ""
    return 1
}

# ── Setup ────────────────────────────────────────────────────────
cmd_setup() {
    echo "=== F4F POC — Setup ==="

    PYTHON=$(find_python)
    if [[ -z "$PYTHON" ]]; then
        echo "ERROR: Python 3.10+ not found. Install via:"
        echo "  brew install python@3.11"
        echo "  # or: sudo apt install python3.11 python3.11-venv"
        exit 1
    fi
    echo "Using Python: $PYTHON ($($PYTHON --version))"

    # Create venv
    if [[ ! -d "$VENV_DIR" ]]; then
        echo "Creating virtual environment at $VENV_DIR ..."
        "$PYTHON" -m venv "$VENV_DIR"
    else
        echo "Virtual environment already exists at $VENV_DIR"
    fi

    # Activate and install deps
    source "$VENV_DIR/bin/activate"
    echo "Installing dependencies..."
    pip install --upgrade pip -q
    pip install -r requirements.txt -q

    echo ""
    echo "Setup complete. Activate the venv with:"
    echo "  source .venv/bin/activate"
    echo ""
    echo "Then run:  ./dev.sh start"

    # Create .env if it doesn't exist
    if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
        echo "Creating .env from template..."
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env" 2>/dev/null || \
        cat > "$SCRIPT_DIR/.env" <<EOF
# F4F POC Environment Configuration
CXAI_API_KEY=
MONGO_URI=mongodb://localhost:27017
MONGO_DB=f4f_poc
JOB_WORKER_THREADS=2
EOF
        echo "Edit .env to set your CXAI_API_KEY"
    fi

    # Check MongoDB
    if command -v mongosh &>/dev/null; then
        if mongosh --eval "db.version()" --quiet &>/dev/null; then
            echo "MongoDB: OK ($(mongosh --eval 'db.version()' --quiet))"
        else
            echo "WARNING: MongoDB is installed but not running."
            echo "  Start with: brew services start mongodb-community"
            echo "  Or:         mongod --dbpath /tmp/mongod"
        fi
    else
        echo "WARNING: mongosh not found. Install MongoDB:"
        echo "  brew tap mongodb/brew && brew install mongodb-community"
        echo "  # or: sudo apt install mongodb-org"
    fi
}

# ── Start ────────────────────────────────────────────────────────
cmd_start() {
    echo "=== F4F POC — Starting apps ==="

    mkdir -p "$PID_DIR" "$LOG_DIR"

    if [[ ! -d "$VENV_DIR" ]]; then
        echo "ERROR: Virtual environment not found. Run ./dev.sh setup first."
        exit 1
    fi

    source "$VENV_DIR/bin/activate"

    # Start FastAPI backend (must be first — UI depends on it)
    _start_api

    # Start UC1
    _start_app "uc1" "usecase1_land_record_ocr.py" "$PORT_UC1"

    # Start UC2
    _start_app "uc2" "usecase2_photo_verification.py" "$PORT_UC2"

    # Start Admin
    _start_app "admin" "admin.py" "$PORT_ADMIN"

    echo ""
    echo "All apps started:"
    echo "  API Server (FastAPI):      http://localhost:$PORT_API"
    echo "  API Docs (Swagger):        http://localhost:$PORT_API/docs"
    echo "  UC1 (Land Record OCR):     http://localhost:$PORT_UC1"
    echo "  Admin Dashboard:           http://localhost:$PORT_ADMIN"
    echo "  UC2 (Photo Verification):  http://localhost:$PORT_UC2"
    echo ""
    echo "Stop with: ./dev.sh stop"
}

_start_api() {
    local pidfile="$PID_DIR/api.pid"
    local logfile="$LOG_DIR/api.log"

    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "  api is already running (PID $(cat "$pidfile"))"
        return
    fi

    echo "  Starting FastAPI on port $PORT_API..."
    nohup uvicorn api.app:app \
        --host 0.0.0.0 \
        --port "$PORT_API" \
        --log-level info \
        > "$logfile" 2>&1 &

    local pid=$!
    echo "$pid" > "$pidfile"
    echo "  api started (PID $pid, log: $logfile)"
    # Give API a moment to boot before starting UIs
    sleep 2
}

_start_app() {
    local name="$1"
    local script="$2"
    local port="$3"
    local pidfile="$PID_DIR/$name.pid"
    local logfile="$LOG_DIR/$name.log"

    # Check if already running
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "  $name is already running (PID $(cat "$pidfile"))"
        return
    fi

    echo "  Starting $name on port $port..."
    nohup streamlit run "$script" \
        --server.port "$port" \
        --server.headless true \
        --browser.gatherUsageStats false \
        > "$logfile" 2>&1 &

    local pid=$!
    echo "$pid" > "$pidfile"
    echo "  $name started (PID $pid, log: $logfile)"
}

# ── Stop ─────────────────────────────────────────────────────────
cmd_stop() {
    echo "=== F4F POC — Stopping apps ==="
    mkdir -p "$PID_DIR"

    local stopped=0
    for pidfile in "$PID_DIR"/*.pid; do
        [[ -f "$pidfile" ]] || continue
        local name
        name="$(basename "$pidfile" .pid)"
        local pid
        pid="$(cat "$pidfile")"

        if kill -0 "$pid" 2>/dev/null; then
            echo "  Stopping $name (PID $pid)..."
            kill "$pid" 2>/dev/null || true
            # Wait up to 5s for graceful shutdown
            for i in {1..10}; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.5
            done
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
            stopped=$((stopped + 1))
        else
            echo "  $name was not running"
        fi
        rm -f "$pidfile"
    done

    # Also kill any orphaned streamlit processes in this directory
    pkill -f "streamlit run.*$(basename "$SCRIPT_DIR")" 2>/dev/null || true

    echo "  Stopped $stopped app(s)."
}

# ── Status ───────────────────────────────────────────────────────
cmd_status() {
    echo "=== F4F POC — Status ==="
    mkdir -p "$PID_DIR"

    for name in api uc1 uc2 admin; do
        local pidfile="$PID_DIR/$name.pid"
        if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            local pid
            pid="$(cat "$pidfile")"
            echo "  $name: RUNNING (PID $pid)"
        else
            echo "  $name: STOPPED"
        fi
    done

    # MongoDB status
    if command -v mongosh &>/dev/null && mongosh --eval "1" --quiet &>/dev/null 2>&1; then
        echo "  mongodb: RUNNING"
    else
        echo "  mongodb: STOPPED or UNREACHABLE"
    fi
}

# ── Restart ──────────────────────────────────────────────────────
cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

# ── Main ─────────────────────────────────────────────────────────
case "${1:-help}" in
    setup)   cmd_setup   ;;
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    status)  cmd_status  ;;
    restart) cmd_restart ;;
    *)
        echo "Usage: $0 {setup|start|stop|status|restart}"
        echo ""
        echo "Commands:"
        echo "  setup    — Create venv, install deps, check MongoDB"
        echo "  start    — Start UC1 + UC2 + Admin as background processes"
        echo "  stop     — Stop all running apps"
        echo "  status   — Check what's running"
        echo "  restart  — Stop then start"
        exit 1
        ;;
esac
