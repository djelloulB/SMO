#!/bin/bash
# Synology Deployment Script for SMO
# Usage: ./deploy_synology.sh

set -e

echo "=== SMO Synology Deployment Script ==="
echo ""

# Check if SSH connection details are provided
if [ -z "$SYNO_USER" ] || [ -z "$SYNO_HOST" ]; then
    echo "Error: SYNO_USER and SYNO_HOST environment variables must be set"
    echo ""
    echo "Example:"
    echo "  SYNO_USER=admin SYNO_HOST=192.168.1.100 ./deploy_synology.sh"
    echo ""
    exit 1
fi

SYNO_PASS=${SYNO_PASS:-}  # Optional: if not set, will prompt

# Setup SSH connection
if [ -z "$SYNO_PASS" ]; then
    echo "Enter Synology admin password:"
    read -s SYNO_PASS
fi

echo "Connecting to $SYNO_HOST..."
echo ""

# Create SSH key pair for non-interactive deployment (one-time setup)
if [ ! -f ~/.ssh/id_rsa_smo ]; then
    echo "Creating SSH key pair for SMO deployment..."
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_smo -N "" -C "smo-deploy"
    ssh-copy-id -i ~/.ssh/id_rsa_smo.pub $SYNO_USER@$SYNO_HOST
fi

# Copy files to Synology
echo "Copying project files to Synology..."
scp -r . $SYNO_USER@$SYNO_HOST:~/Media-Orchestrator/

# Execute deployment commands on Synology
echo "Deploying on Synology..."
ssh $SYNO_USER@$SYNO_HOST << 'ENDSSH'
    cd ~/Media-Orchestrator

    # Check if docker-compose is available
    if ! command -v docker &> /dev/null; then
        echo "Error: Docker is not installed on Synology"
        exit 1
    fi

    # Check if docker-compose is available
    if ! command -v docker-compose &> /dev/null; then
        echo "Error: docker-compose is not installed on Synology"
        echo "Please install docker-compose from Synology Package Center"
        exit 1
    fi

    # Create data directory
    mkdir -p data

    # Set up environment file
    if [ ! -f .env ]; then
        echo "Creating .env file..."
        cp .env.example .env
    fi

    # Stop existing container if running
    docker-compose down

    # Build and start
    echo "Building and starting SMO container..."
    docker-compose up -d --build

    echo "SMO is now running!"
    echo ""
    echo "To check logs:"
    echo "  docker-compose logs -f"

    echo ""
    echo "To stop SMO:"
    echo "  docker-compose down"
ENDSSH

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "SMO is now running on your Synology NAS"
echo ""
echo "Access points:"
echo "  - Healthcheck: http://localhost:8023/healthz"
echo "  - API Docs: http://localhost:8023/docs"
echo ""
echo "Next steps:"
echo "  1. Set up your OAuth tokens: smo auth --platform linkedin"
echo "  2. Configure your brand voice in .env"
echo "  3. Review logs: docker-compose logs -f"
