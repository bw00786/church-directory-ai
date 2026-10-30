# Church Production Director — developer targets (POSIX shells: macOS, Linux,
# Git Bash / WSL on Windows). Windows operators use the PowerShell launchers in
# scripts/ instead (setup-windows.ps1, start-all.ps1).
#
# Backend commands run inside backend/ with the project's virtualenv if present.

PY      ?= $(shell test -x backend/.venv/bin/python && echo backend/.venv/bin/python || echo python3)
PYTEST  ?= $(PY) -m pytest -q -p no:cacheprovider
NPM     ?= npm

.PHONY: help setup install dev backend frontend test test-backend test-frontend \
        lint lint-backend lint-frontend format typecheck build version clean \
        docker-up docker-down migrate

help:
	@echo "Church Production Director - Build Targets"
	@echo ""
	@echo "  setup            Create backend venv, install Python + Node dependencies, create .env"
	@echo "  install          Install Python and Node dependencies into existing environments"
	@echo "  backend          Start the FastAPI backend (reload) on API_HOST/API_PORT from .env"
	@echo "  frontend         Start the Vite dev server"
	@echo "  dev              Start backend and frontend (background jobs in this shell)"
	@echo "  test             Run backend + frontend tests"
	@echo "  lint             Ruff/black check + ESLint + tsc"
	@echo "  format           Auto-format Python (black, isort) and TypeScript (prettier)"
	@echo "  typecheck        mypy (backend) + tsc --noEmit (frontend)"
	@echo "  build            Build the frontend bundle and the backend wheel"
	@echo "  migrate          Create/upgrade the PostgreSQL schema"
	@echo "  version          Check app/version.py, pyproject.toml and package.json agree"
	@echo "  docker-up/down   Start/stop PostgreSQL (docker compose)"
	@echo "  clean            Remove build artifacts"

setup:
	cd backend && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt
	cd frontend && $(NPM) install
	@test -f .env || (cp .env.example .env && $(PY) scripts/generate_secrets.py .env && echo "Created .env with generated secrets; edit the hardware settings.")

install:
	cd backend && $(PY) -m pip install -r requirements.txt
	cd frontend && $(NPM) install

backend:
	cd backend && $(PY) -m uvicorn app.main:app --reload --host $${API_HOST:-127.0.0.1} --port $${API_PORT:-8000}

frontend:
	cd frontend && $(NPM) run dev

dev:
	$(MAKE) backend & $(MAKE) frontend

test: test-backend test-frontend

test-backend:
	cd backend && $(PYTEST)

test-frontend:
	cd frontend && $(NPM) run test

lint: lint-backend lint-frontend

lint-backend:
	cd backend && $(PY) -m black --check app tests && $(PY) -m isort --check-only app tests

lint-frontend:
	cd frontend && $(NPM) run lint && npx tsc --noEmit

typecheck:
	cd backend && $(PY) -m mypy app || true
	cd frontend && npx tsc --noEmit

format:
	cd backend && $(PY) -m black app tests && $(PY) -m isort app tests
	cd frontend && $(NPM) run format

build:
	cd frontend && $(NPM) run build
	cd backend && $(PY) -m pip install --upgrade build >/dev/null && $(PY) -m build

migrate:
	cd backend && $(PY) -m app.database.cli migrate

version:
	$(PY) scripts/check_version.py

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf backend/build backend/dist backend/*.egg-info backend/.pytest_cache backend/.coverage
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf frontend/dist
