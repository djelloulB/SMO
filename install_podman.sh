#!/bin/bash
# Installation script for SMO using Podman

set -e

echo "=========================================="
echo "Installation de SMO avec Podman"
echo "=========================================="
echo ""

# Vérifier que podman est installé
if ! command -v podman &> /dev/null; then
    echo "Erreur: Podman n'est pas installé."
    echo "Installez Podman Desktop pour Windows ou Podman sur Linux."
    exit 1
fi

echo "✓ Podman est installé: $(podman --version)"
echo ""

# Construire l'image
echo "Constrution de l'image Docker..."
podman build -f Dockerfile.final -t smo:latest .

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Image construite avec succès!"
    echo ""

    # Lancer le conteneur
    echo "Lancement du conteneur SMO..."
    podman run -d \
        --name smo \
        --restart unless-stopped \
        -p 8023:8023 \
        -v "$(pwd)/data":/app/data \
        -e SMO_MASTER_KEY="demo-key-please-replace-in-production" \
        smo:latest

    echo ""
    echo "✓ Conteneur démarré avec succès!"
    echo ""
    echo "=== Information de connexion ==="
    echo "Port: 8023"
    echo "Healthcheck: http://localhost:8023/healthz"
    echo "Documentation API: http://localhost:8023/docs"
    echo ""
    echo "=== Vérifier le conteneur ==="
    podman logs smo
    echo ""
    echo "=== Arrêter le conteneur ==="
    echo "podman stop smo"
    echo ""
    echo "=== Démarrer le conteneur ==="
    echo "podman start smo"
    echo ""
    echo "=== Voir les logs ==="
    echo "podman logs -f smo"
else
    echo ""
    echo "Erreur lors de la construction de l'image."
    exit 1
fi
