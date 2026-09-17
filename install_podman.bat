@echo off
REM Installation script for SMO using Podman (Windows)

setlocal enabledelayedexpansion

echo ==========================================
echo Installation de SMO avec Podman
echo ==========================================
echo.

REM Vérifier que podman est installé
podman --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Erreur: Podman n'est pas installé.
    echo Installez Podman Desktop pour Windows.
    exit /b 1
)

echo ✓ Podman est installé: %podman --version%
echo.

REM Construire l'image
echo Construction de l'image Docker...
podman build -f Dockerfile.final -t smo:latest .
if %errorlevel% neq 0 (
    echo Erreur lors de la construction de l'image.
    exit /b 1
)

echo.
echo ✓ Image construite avec succes!
echo.

REM Lancer le conteneur
echo Lancement du conteneur SMO...
podman run -d --name smo --restart unless-stopped -p 8023:8023 -v "%cd%\data":/app/data -e SMO_MASTER_KEY=demo-key-please-replace-in-production smo:latest

echo.
echo ✓ Conteneur demarre avec succes!
echo.
echo === Information de connexion ===
echo Port: 8023
echo Healthcheck: http://localhost:8023/healthz
echo Documentation API: http://localhost:8023/docs
echo.
echo === Verifier le conteneur ===
podman logs smo
echo.
echo === Arreter le conteneur ===
echo podman stop smo
echo.
echo === Démarrer le conteneur ===
echo podman start smo
echo.
echo === Voir les logs ===
echo podman logs -f smo
