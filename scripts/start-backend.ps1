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
Write-Host "AI inference uses Ollama (default model qwen3.8:latest) on the EasyWorship PC: OLLAMA_BASE_URL, or port 11434 on EASYWORSHIP_REMOTE_HOST." -ForegroundColor Yellow
Write-Host "Inference check: http://localhost:8000/health/ollama (default timeout 120 seconds)" -ForegroundColor Cyan
Write-Host ""

# Bind address comes from API_HOST in .env (default 127.0.0.1). Use 0.0.0.0 only
# with API_KEY / API_ADMIN_KEY set -- the auth gate refuses LAN serving without them.
$apiHost = if ($env:API_HOST) { $env:API_HOST } else { "127.0.0.1" }
$envFile = Join-Path $PSScriptRoot "..\.env"
if (-not $env:API_HOST -and (Test-Path $envFile)) {
    $line = Select-String -Path $envFile -Pattern '^\s*API_HOST\s*=\s*(.+)$' | Select-Object -First 1
    if ($line) { $apiHost = $line.Matches[0].Groups[1].Value.Trim().Trim('"') }
}
& $python -m uvicorn app.main:app --reload --host $apiHost --port 8000
