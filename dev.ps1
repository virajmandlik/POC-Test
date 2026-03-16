<#
.SYNOPSIS
    F4F POC — Setup, start, and stop apps (Windows PowerShell)

.DESCRIPTION
    Usage:
        .\dev.ps1 setup     — Create venv and install dependencies
        .\dev.ps1 start     — Start all Streamlit apps (UC1 + UC2 + Admin)
        .\dev.ps1 stop      — Stop all running Streamlit processes
        .\dev.ps1 status    — Check what's running
        .\dev.ps1 restart   — Stop then start
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "start", "stop", "status", "restart", "help")]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvDir = Join-Path $ScriptDir ".venv"
$PidDir = Join-Path $ScriptDir ".pids"
$LogDir = Join-Path $ScriptDir "logs"

$PortAPI = 8000
$PortUC1 = 8501
$PortUC2 = 8503
$PortAdmin = 8502

# ── Find Python ──────────────────────────────────────────────────
function Find-Python {
    # Check for Python 3.10+ — prefer py launcher, then python3, then python
    $candidates = @("py -3.12", "py -3.11", "py -3", "python3", "python")
    foreach ($cmd in $candidates) {
        try {
            $parts = $cmd -split " "
            $output = & $parts[0] $parts[1..($parts.Length-1)] --version 2>&1
            if ($output -match "Python (\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 10) {
                    return $cmd
                }
            }
        } catch {}
    }
    return $null
}

# ── Setup ────────────────────────────────────────────────────────
function Invoke-Setup {
    Write-Host "=== F4F POC — Setup ===" -ForegroundColor Cyan

    $python = Find-Python
    if (-not $python) {
        Write-Host "ERROR: Python 3.10+ not found. Install from https://www.python.org/downloads/" -ForegroundColor Red
        exit 1
    }
    Write-Host "Using Python: $python"

    # Create venv
    if (-not (Test-Path $VenvDir)) {
        Write-Host "Creating virtual environment at $VenvDir ..."
        $parts = $python -split " "
        & $parts[0] $parts[1..($parts.Length-1)] -m venv $VenvDir
    } else {
        Write-Host "Virtual environment already exists at $VenvDir"
    }

    # Activate and install
    $activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
    . $activateScript
    Write-Host "Installing dependencies..."
    pip install --upgrade pip -q
    pip install -r requirements.txt -q

    # Create .env if needed
    $envFile = Join-Path $ScriptDir ".env"
    if (-not (Test-Path $envFile)) {
        Write-Host "Creating .env from template..."
        @"
# F4F POC Environment Configuration
CXAI_API_KEY=
MONGO_URI=mongodb://localhost:27017
MONGO_DB=f4f_poc
JOB_WORKER_THREADS=2
"@ | Out-File -FilePath $envFile -Encoding utf8
        Write-Host "Edit .env to set your CXAI_API_KEY"
    }

    # Check MongoDB
    try {
        $mongoVersion = & mongosh --eval "db.version()" --quiet 2>&1
        Write-Host "MongoDB: OK ($mongoVersion)" -ForegroundColor Green
    } catch {
        Write-Host "WARNING: MongoDB not found or not running." -ForegroundColor Yellow
        Write-Host "  Install: https://www.mongodb.com/try/download/community"
        Write-Host "  Or via winget: winget install MongoDB.Server"
    }

    Write-Host ""
    Write-Host "Setup complete. Activate the venv with:" -ForegroundColor Green
    Write-Host "  .\.venv\Scripts\Activate.ps1"
    Write-Host ""
    Write-Host "Then run: .\dev.ps1 start"
}

# ── Start ────────────────────────────────────────────────────────
function Invoke-Start {
    Write-Host "=== F4F POC — Starting apps ===" -ForegroundColor Cyan

    New-Item -ItemType Directory -Force -Path $PidDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    if (-not (Test-Path $VenvDir)) {
        Write-Host "ERROR: Virtual environment not found. Run .\dev.ps1 setup first." -ForegroundColor Red
        exit 1
    }

    $activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
    . $activateScript

    # Start FastAPI backend first
    Start-ApiServer

    Start-App "uc1" "usecase1_land_record_ocr.py" $PortUC1
    Start-App "uc2" "usecase2_photo_verification.py" $PortUC2
    Start-App "admin" "admin.py" $PortAdmin

    Write-Host ""
    Write-Host "All apps started:" -ForegroundColor Green
    Write-Host "  API Server (FastAPI):      http://localhost:$PortAPI"
    Write-Host "  API Docs (Swagger):        http://localhost:$PortAPI/docs"
    Write-Host "  UC1 (Land Record OCR):     http://localhost:$PortUC1"
    Write-Host "  Admin Dashboard:           http://localhost:$PortAdmin"
    Write-Host "  UC2 (Photo Verification):  http://localhost:$PortUC2"
    Write-Host ""
    Write-Host "Stop with: .\dev.ps1 stop"
}

function Start-ApiServer {
    $pidFile = Join-Path $PidDir "api.pid"
    $logFile = Join-Path $LogDir "api.log"

    if (Test-Path $pidFile) {
        $existingPid = Get-Content $pidFile
        try {
            Get-Process -Id $existingPid -ErrorAction Stop | Out-Null
            Write-Host "  api is already running (PID $existingPid)"
            return
        } catch {}
    }

    Write-Host "  Starting FastAPI on port $PortAPI..."

    $uvicornPath = Join-Path $VenvDir "Scripts\uvicorn.exe"
    $proc = Start-Process -FilePath $uvicornPath `
        -ArgumentList "api.app:app", "--host", "0.0.0.0", "--port", $PortAPI, "--log-level", "info" `
        -WorkingDirectory $ScriptDir `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError (Join-Path $LogDir "api.err.log") `
        -WindowStyle Hidden `
        -PassThru

    $proc.Id | Out-File -FilePath $pidFile -Encoding ascii
    Write-Host "  api started (PID $($proc.Id), log: $logFile)"
    Start-Sleep -Seconds 2
}

function Start-App {
    param($Name, $Script, $Port)

    $pidFile = Join-Path $PidDir "$Name.pid"
    $logFile = Join-Path $LogDir "$Name.log"

    # Check if already running
    if (Test-Path $pidFile) {
        $existingPid = Get-Content $pidFile
        try {
            $proc = Get-Process -Id $existingPid -ErrorAction Stop
            Write-Host "  $Name is already running (PID $existingPid)"
            return
        } catch {}
    }

    Write-Host "  Starting $Name on port $Port..."

    $streamlitPath = Join-Path $VenvDir "Scripts\streamlit.exe"
    $proc = Start-Process -FilePath $streamlitPath `
        -ArgumentList "run", $Script, "--server.port", $Port, "--server.headless", "true", "--browser.gatherUsageStats", "false" `
        -WorkingDirectory $ScriptDir `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError (Join-Path $LogDir "$Name.err.log") `
        -WindowStyle Hidden `
        -PassThru

    $proc.Id | Out-File -FilePath $pidFile -Encoding ascii
    Write-Host "  $Name started (PID $($proc.Id), log: $logFile)"
}

# ── Stop ─────────────────────────────────────────────────────────
function Invoke-Stop {
    Write-Host "=== F4F POC — Stopping apps ===" -ForegroundColor Cyan

    New-Item -ItemType Directory -Force -Path $PidDir | Out-Null
    $stopped = 0

    foreach ($pidFile in Get-ChildItem -Path $PidDir -Filter "*.pid" -ErrorAction SilentlyContinue) {
        $name = $pidFile.BaseName
        $pid = Get-Content $pidFile.FullName

        try {
            $proc = Get-Process -Id $pid -ErrorAction Stop
            Write-Host "  Stopping $name (PID $pid)..."
            Stop-Process -Id $pid -Force
            $stopped++
        } catch {
            Write-Host "  $name was not running"
        }
        Remove-Item $pidFile.FullName -Force
    }

    # Kill any orphaned streamlit/uvicorn processes
    Get-Process -Name "streamlit" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

    Write-Host "  Stopped $stopped app(s)."
}

# ── Status ───────────────────────────────────────────────────────
function Invoke-Status {
    Write-Host "=== F4F POC — Status ===" -ForegroundColor Cyan

    foreach ($name in @("api", "uc1", "uc2", "admin")) {
        $pidFile = Join-Path $PidDir "$name.pid"
        if (Test-Path $pidFile) {
            $pid = Get-Content $pidFile
            try {
                Get-Process -Id $pid -ErrorAction Stop | Out-Null
                Write-Host "  ${name}: RUNNING (PID $pid)" -ForegroundColor Green
            } catch {
                Write-Host "  ${name}: STOPPED" -ForegroundColor Yellow
            }
        } else {
            Write-Host "  ${name}: STOPPED" -ForegroundColor Yellow
        }
    }

    # MongoDB
    try {
        $null = & mongosh --eval "1" --quiet 2>&1
        Write-Host "  mongodb: RUNNING" -ForegroundColor Green
    } catch {
        Write-Host "  mongodb: STOPPED or UNREACHABLE" -ForegroundColor Yellow
    }
}

# ── Main ─────────────────────────────────────────────────────────
switch ($Command) {
    "setup"   { Invoke-Setup }
    "start"   { Invoke-Start }
    "stop"    { Invoke-Stop }
    "status"  { Invoke-Status }
    "restart" { Invoke-Stop; Start-Sleep -Seconds 1; Invoke-Start }
    default {
        Write-Host "Usage: .\dev.ps1 {setup|start|stop|status|restart}" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Commands:"
        Write-Host "  setup    — Create venv, install deps, check MongoDB"
        Write-Host "  start    — Start UC1 + UC2 + Admin as background processes"
        Write-Host "  stop     — Stop all running apps"
        Write-Host "  status   — Check what's running"
        Write-Host "  restart  — Stop then start"
    }
}
