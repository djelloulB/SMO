@echo off
REM Installation script for SMO dependencies (Windows)
REM Usage: install.bat

echo Creating Python virtual environment...
python -m venv venv

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Upgrading pip...
pip install --upgrade pip --quiet

echo Installing dependencies from requirements.txt...
pip install -r requirements.txt --quiet

echo Installation complete!
echo.
echo To use SMO:
echo 1. Activate the environment: call venv\Scripts\activate.bat
echo 2. Configure .env (copy from .env.example)
echo 3. Set your SMO_MASTER_KEY environment variable
echo 4. Run tests: pytest
echo 5. Start server: python -m smo.main

pause
