@echo off
REM Install and start SMO with Podman (Windows, Podman machine on WSL).
REM Uses docker-compose.yml + docker-compose.podman.yml (host networking for the Podman VM).
setlocal
cd /d "%~dp0"

echo ==========================================
echo Installation de SMO avec Podman
echo ==========================================
echo.

podman --version >nul 2>&1
if errorlevel 1 (
    echo Erreur: Podman n'est pas installe.
    echo Installez Podman Desktop pour Windows, puis relancez ce script.
    exit /b 1
)
podman compose version >nul 2>&1
if errorlevel 1 (
    echo Erreur: la commande "podman compose" est indisponible.
    echo Installez un provider compose pour Podman, puis relancez ce script.
    exit /b 1
)

if not exist ".env" (
    echo Fichier .env absent: copie de .env.example.
    copy /y ".env.example" ".env" >nul
    echo Editez .env : SMO_MASTER_KEY, PUBLIC_BASE_URL et identifiants, puis relancez ce script.
    exit /b 1
)

if not exist "data" mkdir "data"

echo Construction et demarrage du conteneur...
podman compose -f docker-compose.yml -f docker-compose.podman.yml up -d --build
if errorlevel 1 (
    echo Erreur lors de la construction ou du demarrage.
    echo Consultez les messages ci-dessus.
    exit /b 1
)

echo.
echo SMO est demarre.
echo Healthcheck : http://localhost:8023/healthz
echo Documentation API : http://localhost:8023/docs
echo Sur une machine Podman WSL, l'adresse peut etre celle de la VM par exemple http://172.x.x.x:8023.
echo.
echo Commandes utiles :
echo   podman ps --filter name=smo        etat du conteneur
echo   podman logs -f smo                 logs
echo   podman compose -f docker-compose.yml -f docker-compose.podman.yml down   arret

endlocal
