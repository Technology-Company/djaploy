#!/bin/bash
#
# Quick deploy script — runs configure + deploy against the local Docker target.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR/helloworld"

echo ">>> Configuring server..."
python manage.py djaploy configure --env local

echo ""
echo ">>> Deploying application..."
python manage.py djaploy deploy --env local --local

echo ""
echo "=== Deployment complete! ==="
echo "Visit http://localhost:8080 to see the app."
