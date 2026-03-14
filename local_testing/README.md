# Local Testing Environment

A self-contained environment for testing djaploy features locally using Docker.

## What's included

- **Docker target server** — Ubuntu 22.04 container with SSH, nginx, and Python, simulating a real deployment target
- **Hello World Django app** — Minimal project pre-configured with djaploy (zero-downtime deployment strategy)
- **Nginx config** — Proxies to gunicorn via Unix socket
- **Systemd service** — Gunicorn service unit for the Django app
- **Helper scripts** — One-command setup, deploy, and teardown

## Prerequisites

- Docker and Docker Compose
- Python 3.10+ with djaploy installed (`pip install -e ..` from the repo root)

## Quick start

```bash
# 1. Install djaploy in development mode (from repo root)
cd ..
pip install -e .
cd local_testing

# 2. Set up the Docker target server
bash scripts/setup.sh

# 3. Deploy the hello world app
bash scripts/deploy.sh

# 4. Visit the app
curl http://localhost:8080
# → {"message": "Hello from djaploy!", "status": "deployed"}
```

## Manual usage

You can also run djaploy commands directly to test specific features:

```bash
cd helloworld

# Configure the server (install packages, create users, set up directories)
python manage.py djaploy configure --env local

# Deploy (creates artifact from local files, uploads, extracts, runs migrations)
python manage.py djaploy deploy --env local --local

# Verify configuration
python manage.py djaploy verify

# Deploy again to test zero-downtime release switching
python manage.py djaploy deploy --env local --local

# Rollback to previous release
python manage.py djaploy rollback --env local

# List available commands
python manage.py djaploy --list
```

## SSH access

To SSH into the target server manually:

```bash
ssh -i keys/id_ed25519 -p 2222 deploy@localhost

# Inspect the deployment
sudo ls -la /home/app/apps/helloworld/
sudo ls -la /home/app/apps/helloworld/releases/
sudo readlink /home/app/apps/helloworld/current
```

## Structure

```
local_testing/
├── docker-compose.yml          # Docker services definition
├── Dockerfile.server           # Target server image
├── helloworld/                 # Django project
│   ├── manage.py
│   ├── pyproject.toml
│   ├── helloworld/
│   │   ├── settings.py         # Django settings (includes djaploy)
│   │   ├── urls.py             # URL routing
│   │   ├── views.py            # Hello world views
│   │   └── wsgi.py             # WSGI entry point
│   └── infra/                  # djaploy configuration
│       ├── config.py           # DjaployConfig (zero_downtime strategy)
│       ├── inventory/
│       │   └── local.py        # Points to localhost:2222 Docker container
│       └── deploy_files/
│           └── local/
│               └── etc/
│                   ├── nginx/sites-available/helloworld
│                   └── systemd/system/helloworld.service
├── scripts/
│   ├── setup.sh                # Build containers, extract SSH keys
│   ├── deploy.sh               # Run configure + deploy
│   └── teardown.sh             # Stop containers, clean up
├── keys/                       # (generated) SSH keys for deploy user
└── README.md
```

## Teardown

```bash
bash scripts/teardown.sh
```
