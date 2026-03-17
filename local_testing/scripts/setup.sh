#!/bin/bash
#
# Set up the local testing environment.
#
# This script:
#   1. Builds and starts the Docker target server
#   2. Extracts SSH keys for passwordless access
#   3. Adds the server to known_hosts
#   4. Verifies SSH connectivity
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo ">>> Building and starting the target server..."
docker compose up -d server

echo ">>> Waiting for SSH to be ready..."
for i in $(seq 1 30); do
    if ssh-keyscan -p 2222 localhost 2>/dev/null | grep -q .; then
        break
    fi
    sleep 1
done

echo ">>> Extracting SSH keys..."
mkdir -p keys
docker compose run --rm ssh-keygen

echo ">>> Adding server to known_hosts..."
mkdir -p ~/.ssh
ssh-keyscan -p 2222 localhost 2>/dev/null >> ~/.ssh/known_hosts 2>/dev/null || true

echo ">>> Testing SSH connection..."
if ssh -i keys/id_ed25519 -p 2222 -o StrictHostKeyChecking=no deploy@localhost echo "SSH OK"; then
    echo ""
    echo "=== Setup complete! ==="
    echo ""
    echo "Target server is running. You can now deploy with:"
    echo ""
    echo "  cd helloworld"
    echo "  python manage.py djaploy configure --env local"
    echo "  python manage.py djaploy deploy --env local --local"
    echo ""
    echo "Then visit http://localhost:8080 to see the app."
    echo ""
else
    echo "SSH connection failed. Check Docker logs:"
    echo "  docker compose logs server"
    exit 1
fi
