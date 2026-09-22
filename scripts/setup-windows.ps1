# Setup Windows development environment for Church Production Director

param(
    [string]$PythonVersion = "3.11",
    [string]$NodeVersion = "18"
)

$ErrorActionPreference = "Stop"

Write-Host "Church Production Director - Windows Setup" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green

# Check Python
Write-Host "`nChecking Python installation..." -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Python not found. Please install Python 3.11+" -ForegroundColor Red
    Write-Host "Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

$pythonVersion = python --version
Write-Host "Found: $pythonVersion" -ForegroundColor Green

# Check Node.js
Write-Host "`nChecking Node.js installation..." -ForegroundColor Cyan
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Node.js not found. Please install Node.js 18+" -ForegroundColor Red
    Write-Host "Download from: https://nodejs.org/" -ForegroundColor Yellow
    exit 1
}

$nodeVersion = node --version
Write-Host "Found: $nodeVersion" -ForegroundColor Green

# Create .env if it doesn't exist
Write-Host "`nSetting up environment..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example" -ForegroundColor Green
    Write-Host "  Edit .env with your configuration!" -ForegroundColor Yellow
} else {
    Write-Host ".env already exists" -ForegroundColor Green
}

# Anthropic Claude is used for inference; the API key lives in .env.
Write-Host "`nAI inference uses Anthropic Claude (default model claude-sonnet-5)." -ForegroundColor Cyan
Write-Host "  Set ANTHROPIC_API_KEY in .env; optionally override ANTHROPIC_MODEL/FAST_MODEL/VISION_MODEL." -ForegroundColor Gray
Write-Host "  Existing .env values are preserved; nothing is downloaded or installed for the LLM." -ForegroundColor Gray
Write-Host "  RAG embeddings are separate. This migration does not enable voice playback." -ForegroundColor Gray

# Backend setup
Write-Host "`nSetting up backend..." -ForegroundColor Cyan
cd backend

if (-not (Test-Path "venv")) {
    Write-Host "  Creating Python virtual environment..." -ForegroundColor Gray
    python -m venv venv
    Write-Host "  Virtual environment created" -ForegroundColor Green
}

Write-Host "  Activating virtual environment..." -ForegroundColor Gray
.\venv\Scripts\Activate.ps1

Write-Host "  Installing dependencies..." -ForegroundColor Gray
pip install -r requirements.txt
Write-Host "  Backend dependencies installed" -ForegroundColor Green

cd ..

# Frontend setup
Write-Host "`nSetting up frontend..." -ForegroundColor Cyan
cd frontend

Write-Host "  Installing dependencies..." -ForegroundColor Gray
npm install
Write-Host "  Frontend dependencies installed" -ForegroundColor Green

cd ..

# Docker check
Write-Host "`nChecking Docker installation..." -ForegroundColor Cyan
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "Docker found" -ForegroundColor Green
    Write-Host "  Run 'docker-compose up -d' to start PostgreSQL" -ForegroundColor Gray
} else {
    Write-Host "Docker not found (optional)" -ForegroundColor Yellow
    Write-Host "  For development, install Docker Desktop: https://www.docker.com/products/docker-desktop" -ForegroundColor Gray
}

Write-Host "`n✅ Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Edit .env with your configuration" -ForegroundColor Gray
Write-Host "2. Run services:" -ForegroundColor Gray
Write-Host "   - Backend: .\scripts\start-backend.ps1" -ForegroundColor Gray
Write-Host "   - Frontend: .\scripts\start-frontend.ps1" -ForegroundColor Gray
Write-Host "   - All:      .\scripts\start-all.ps1" -ForegroundColor Gray
Write-Host "3. Set ANTHROPIC_API_KEY in .env (create a key at https://console.anthropic.com/)." -ForegroundColor Gray
Write-Host "4. Check http://localhost:8000/health/anthropic (bounded inference, default budget 120 seconds)." -ForegroundColor Gray
Write-Host "   Or, from backend with its environment active: python scripts/test_claude.py" -ForegroundColor Gray
