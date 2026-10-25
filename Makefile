# Convenience targets for local development (macOS/Linux).
.PHONY: setup backend frontend test lint e2e up down logs

PYTHON ?= python3.12

setup:            ## Install backend and frontend dependencies
	cd backend && $(PYTHON) -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	cd frontend && npm ci

backend:          ## Run the API with auto-reload on http://127.0.0.1:8000
	cd backend && .venv/bin/uvicorn app.main:create_app --factory --reload --port 8000

frontend:         ## Run the Vite dev server on http://localhost:5173 (proxies /api)
	cd frontend && npm run dev

test:             ## Backend + frontend unit tests
	cd backend && .venv/bin/pytest
	cd frontend && npm test

lint:
	cd backend && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests
	cd frontend && npm run typecheck

e2e:              ## Playwright end-to-end tests (mocked API)
	cd frontend && npm run test:e2e

up:               ## Production-like stack in Docker on http://127.0.0.1:8080
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail 100
