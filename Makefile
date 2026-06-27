# Ensure tools are in PATH
SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:$(HOME)/.cargo/bin:$(PATH)

.PHONY: help setup install run dev dev-backend dev-frontend test lint build-frontend setup-chrome

# Helper function to find node/npm via nvm or system
define find_node
	@(export NVM_DIR="$HOME/.nvm"; \
	if [ -s "$NVM_DIR/nvm.sh" ]; then \
		. "$NVM_DIR/nvm.sh" >/dev/null 2>&1; \
		nvm use default >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || true; \
	fi; \
	$(1))
endef

help:
	@echo "Condor - Available Commands"
	@echo ""
	@echo "  make setup       - Interactive setup wizard"
	@echo "  make install     - Setup + install all dependencies"
	@echo "  make run         - Run locally (production: built UI on :8088)"
	@echo "  make dev         - Dev mode: Vite HMR (:5173) + API/Telegram (:8088)"
	@echo "  make test        - Run tests"
	@echo "  make lint        - Run black + isort"

setup:
	@chmod +x setup-environment.sh && ./setup-environment.sh

install: setup
	uv sync --extra dev
	@bash -c ' \
		export NVM_DIR="$$HOME/.nvm"; \
		[ -s "$$NVM_DIR/nvm.sh" ] && . "$$NVM_DIR/nvm.sh"; \
		cd frontend && npm install \
	'
	@$(MAKE) setup-chrome

setup-chrome:
	@echo "Setting up Chrome for chart rendering..."
	@uv run python -c "import kaleido; kaleido.get_chrome_sync()" 2>/dev/null || \
		echo "Chrome setup skipped (not required for basic usage)"

build-frontend:
	@bash -c ' \
		export NVM_DIR="$$HOME/.nvm"; \
		[ -s "$$NVM_DIR/nvm.sh" ] && . "$$NVM_DIR/nvm.sh"; \
		cd frontend && npm run build \
	'

run: build-frontend
	uv run python main.py

dev-backend:
	CONDOR_DEV=1 WEB_URL=http://localhost:5173 WEB_PORT=8088 uv run python main.py

dev-frontend:
	@bash -c ' \
		export NVM_DIR="$$HOME/.nvm"; \
		[ -s "$$NVM_DIR/nvm.sh" ] && . "$$NVM_DIR/nvm.sh"; \
		cd frontend && npm run dev \
	'

dev:
	@trap 'kill 0' INT TERM; \
	$(MAKE) dev-backend & \
	$(MAKE) dev-frontend & \
	wait

test:
	uv run pytest

lint:
	uv run black .
	uv run isort .
