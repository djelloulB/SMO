.PHONY: help install test lint clean run security-check db-reset setup demo deploy

help: ## Show this help message
	@echo "SMO (Social Media Orchestrator) - Makefile"
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	@echo "Installing dependencies..."
	python -m venv venv
	@echo "Creating virtual environment... done"
	@echo "Activating virtual environment... done"
	@echo "Installing dependencies... done"
	@echo ""
	@echo "To activate the environment:"
	@echo "  Windows: venv\\Scripts\\activate.bat"
	@echo "  Linux/Mac: source venv/bin/activate"
	@echo ""
	@echo "Then run: python test.py"

test: ## Run tests
	@echo "Running tests..."
	python test.py

test-verbose: ## Run tests with verbose output
	@echo "Running tests with verbose output..."
	python test.py -v

lint: ## Run linter
	@echo "Running linter..."
	python -m ruff check .

format: ## Format code
	@echo "Formatting code..."
	python -m black .
	python -m ruff check --fix .

clean: ## Clean up artifacts
	@echo "Cleaning up..."
	rm -rf __pycache__
	rm -rf *.pyc
	rm -rf venv
	rm -rf *.db
	rm -rf *.db-*
	rm -rf logs/
	rm -rf data/
	rm -rf .pytest_cache/
	@echo "Clean complete"

run: ## Run the server
	@echo "Starting SMO server..."
	python -m smo.main

demo: ## Run demonstration
	@echo "Running demonstration..."
	python demonstration.py

security-check: ## Check security layer configuration
	@echo "Checking security layer..."
	python -c "from smo.config.security import security_layer; print(f'Dry run: {security_layer.dry_run}'); print(f'Human gate: {security_layer.human_gate}'); print(f'Budget: ${security_layer.budget_per_day_usd:.2f}')"

db-reset: ## Reset database
	@echo "Resetting database..."
	rm -f data/smoomo.db
	@echo "Database reset complete"

setup: ## Setup environment (install dependencies + create .env)
	@echo "Setting up environment..."
	python -m venv venv
	source venv/bin/activate || call venv\Scripts\activate.bat
	python -m pip install --upgrade pip --quiet
	pip install -r requirements.txt --quiet
	@echo "Setting up environment... done"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Copy .env.example to .env: cp .env.example .env"
	@echo "  2. Set SMO_MASTER_KEY environment variable"
	@echo "  3. Run: make test"

all: install test ## Install dependencies and run tests
