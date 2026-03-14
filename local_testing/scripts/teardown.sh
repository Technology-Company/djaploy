#!/bin/bash
#
# Tear down the local testing environment.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo ">>> Stopping containers and removing volumes..."
docker compose down -v

echo ">>> Cleaning up SSH keys..."
rm -rf keys/

echo "=== Teardown complete ==="
