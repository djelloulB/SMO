#!/bin/bash
# Installation script for SMO dependencies
# Usage: ./install.sh

echo "Creating Python virtual environment..."
python -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip --quiet

echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt --quiet

echo "Installation complete!"
echo ""
echo "To use SMO:"
echo "1. Activate the environment: source venv/bin/activate"
echo "2. Configure .env (copy from .env.example)"
echo "3. Set your SMO_MASTER_KEY environment variable"
echo "4. Run tests: pytest"
echo "5. Start server: python -m smo.main"
