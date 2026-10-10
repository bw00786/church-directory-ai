# Start FastAPI backend

Write-Host "Starting Church Production Director Backend..." -ForegroundColor Green

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "backend\venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
	$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path $python)) {
	throw "Backend Python virtual environment not found."
}
Set-Location (Join-Path $repoRoot "backend")
$env:SD_ENABLE_ASIO = "1"

# Start the server
Write-Host "Starting FastAPI server on http://localhost:8000" -ForegroundColor Cyan
Write-Host "API docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "Claude inference uses ANTHROPIC_API_KEY from backend/.env (default model claude-sonnet-5)." -ForegroundColor Yellow
Write-Host "Inference check: http://localhost:8000/health/anthropic (default timeout 120 seconds)" -ForegroundColor Cyan
Write-Host ""

& $python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
