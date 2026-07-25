# OpenWorker Makefile
# Convenience tasks for local setup, development, testing, and packaging.

.PHONY: help setup setup-gui setup-all server gui desktop test test-gui e2e test-all dmg clean check-python check-node check-venv

PORT ?= 8765
CWD ?= .
VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
SERVER ?= $(VENV)/bin/openworker-server
PYTEST ?= $(VENV)/bin/pytest
GUI_DIR ?= surfaces/gui

## help: Display available Makefile targets
help:
	@echo "OpenWorker Development Commands:"
	@echo ""
	@sed -n 's/^## //p' $(MAKEFILE_LIST) | column -t -s ':' | sed 's/^/  /'

## check-python: Check if python3 is installed
check-python:
	@command -v python3 >/dev/null 2>&1 || { echo "Error: python3 is required but not installed." >&2; exit 1; }

## check-node: Check if node and npm are installed
check-node:
	@command -v node >/dev/null 2>&1 || { echo "Error: node is required but not installed." >&2; exit 1; }
	@command -v npm >/dev/null 2>&1 || { echo "Error: npm is required but not installed." >&2; exit 1; }

## check-venv: Check if Python virtual environment exists
check-venv:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Error: $(VENV) directory not found. Please run 'make setup' first." >&2; \
		exit 1; \
	fi

## setup: One-time Python backend venv setup
setup: check-python
	@bash packaging/setup_dev_env.sh

## setup-gui: One-time GUI npm dependencies installation
setup-gui: check-node
	@cd $(GUI_DIR) && npm install

## setup-all: Run both backend setup and GUI setup
setup-all: setup setup-gui

## server: Start the local agent server (use CWD=/path PORT=8765 to override)
server: check-venv
	@$(SERVER) --cwd $(CWD) --port $(PORT)

## gui: Start the React/Vite web UI in dev mode
gui: check-node
	@cd $(GUI_DIR) && npm run dev

## desktop: Start the desktop app using Tauri dev
desktop: check-node
	@cd $(GUI_DIR) && npm run tauri dev

## test: Run Python backend test suite
test: check-venv
	@$(PYTEST) tests -q

## test-gui: Run GUI unit tests (vitest)
test-gui: check-node
	@cd $(GUI_DIR) && npm test

## e2e: Run Playwright end-to-end tests
e2e: check-node
	@cd $(GUI_DIR) && npm run e2e

## test-all: Run all tests (backend, GUI unit, and e2e)
test-all: test test-gui e2e

## dmg: Build macOS DMG package (macOS only)
dmg:
	@bash packaging/build_dmg.sh

## clean: Remove virtualenv, node_modules, build artifacts, and caches
clean:
	@rm -rf $(VENV)
	@rm -rf $(GUI_DIR)/node_modules $(GUI_DIR)/dist $(GUI_DIR)/src-tauri/target
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type d -name ".pytest_cache" -exec rm -rf {} +
	@echo "Cleaned build artifacts, venv, and caches."
