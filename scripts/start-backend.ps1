# Start FastAPI backend

Write-Host "Starting Church Production Director Backend..." -ForegroundColor Green

cd backend

# Activate virtual environment (repo-root .venv; backend/venv does not exist)
Write-Host "Activating Python virtual environment..." -ForegroundColor Cyan
..\.venv\Scripts\Activate.ps1

# Start the server
Write-Host "Starting FastAPI server on http://localhost:8000" -ForegroundColor Cyan
Write-Host "API docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "Claude inference uses ANTHROPIC_API_KEY from backend/.env (default model claude-sonnet-5)." -ForegroundColor Yellow
Write-Host "Inference check: http://localhost:8000/health/anthropic (default timeout 120 seconds)" -ForegroundColor Cyan
Write-Host ""

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
