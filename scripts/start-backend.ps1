# Start FastAPI backend

Write-Host "Starting Church Production Director Backend..." -ForegroundColor Green

cd backend

# Activate virtual environment
Write-Host "Activating Python virtual environment..." -ForegroundColor Cyan
.\venv\Scripts\Activate.ps1

# Start the server
Write-Host "Starting FastAPI server on http://localhost:8000" -ForegroundColor Cyan
Write-Host "API docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
