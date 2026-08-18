.PHONY: help setup install dev test build clean docker-up docker-down format lint

help:
	@echo "Church Production Director - Build Targets"
	@echo ""
	@echo "setup            Setup development environment"
	@echo "install          Install Python and Node dependencies"
	@echo "dev              Start development servers"
	@echo "backend          Start FastAPI backend"
	@echo "frontend         Start React frontend"
	@echo "test             Run all tests"
	@echo "test-backend     Run backend tests"
	@echo "test-frontend    Run frontend tests"
	@echo "lint             Run linters (Python + TypeScript)"
	@echo "format           Auto-format code"
	@echo "build            Build production artifacts"
	@echo "clean            Clean build artifacts"
	@echo "docker-up        Start PostgreSQL and Ollama via docker-compose"
	@echo "docker-down      Stop docker services"
	@echo ""

setup:
	@echo "Setting up development environment..."
	cd backend && python -m venv venv
	.\backend\venv\Scripts\activate && pip install -r backend\requirements.txt
	cd frontend && npm install

install:
	@echo "Installing dependencies..."
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

dev:
	@echo "Starting development servers..."
	@start powershell -NoExit -Command "cd backend && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
	@timeout /t 2
	@start powershell -NoExit -Command "cd frontend && npm run dev"

backend:
	cd backend && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

test:
	@echo "Running all tests..."
	cd backend && pytest
	cd frontend && npm run test

test-backend:
	cd backend && pytest -v

test-frontend:
	cd frontend && npm run test

lint:
	@echo "Linting code..."
	cd backend && pylint app tests
	cd frontend && npm run lint

format:
	@echo "Formatting code..."
	cd backend && black app tests
	cd frontend && npm run format

build:
	@echo "Building production artifacts..."
	cd backend && python -m pip install --upgrade build && python -m build
	cd frontend && npm run build

clean:
	@echo "Cleaning build artifacts..."
	cd backend && rm -r build dist *.egg-info __pycache__ .pytest_cache .coverage
	cd frontend && rm -r dist node_modules

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

.PHONY: setup install dev backend frontend test test-backend test-frontend lint format build clean docker-up docker-down
