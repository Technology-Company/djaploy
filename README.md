# djaploy

[![PyPI Version](https://badgen.net/pypi/v/djaploy)](https://pypi.org/project/djaploy/)
[![Python Versions](https://badgen.net/pypi/python/djaploy)](https://pypi.org/project/djaploy/)
[![License](https://badgen.net/badge/license/MIT/blue)](https://github.com/Technology-Company/djaploy/blob/main/LICENSE)
[![Last Commit](https://badgen.net/github/last-commit/Technology-Company/djaploy)](https://github.com/Technology-Company/djaploy/commits)

A modular Django deployment system based on [pyinfra](https://pyinfra.com/), designed to standardize and simplify infrastructure management across Django projects.

## Features

- **Modular Architecture** — Extensible plugin system for deployment components
- **Django Integration** — Seamless integration via Django management commands
- **Multiple Deployment Modes** — Support for `--local`, `--latest`, and `--release` deployments
- **Infrastructure as Code** — Define infrastructure using Python with pyinfra
- **Git-based Artifacts** — Automated artifact creation from git repository
- **SSL Management** — Built-in support for SSL certificates and Let's Encrypt
- **Python Compilation** — Optionally compile Python from source for specific versions

## Installation

```bash
pip install djaploy
```

Or with Poetry:

```bash
poetry add djaploy
```

### Optional extras

```bash
pip install djaploy[certificates]   # Let's Encrypt / certbot support
pip install djaploy[bunny]          # Bunny DNS certbot plugin
```

## Quick Start

### 1. Add to Django settings

```python
INSTALLED_APPS = [
    # ...
    "djaploy",
]

# Required paths
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR
GIT_DIR = PROJECT_DIR.parent
DJAPLOY_CONFIG_DIR = PROJECT_DIR / "infra"
```

### 2. Create project structure

```
your-django-project/
├── manage.py
├── your_app/
│   └── settings.py
└── infra/                          # Deployment configuration
    ├── config.py                   # Main configuration
    ├── inventory/                  # Host definitions per environment
    │   ├── production.py
    │   └── staging.py
    └── deploy_files/               # Environment-specific files
        ├── production/
        │   └── etc/systemd/system/app.service
        └── staging/
```

### 3. Configure deployment

**infra/config.py**:

```python
from djaploy.config import DjaployConfig
from pathlib import Path

config = DjaployConfig(
    project_name="myapp",
    djaploy_dir=Path(__file__).parent,
    manage_py_path=Path("manage.py"),

    python_version="3.11",
    app_user="app",
    ssh_user="deploy",

    modules=[
        "djaploy.modules.core",
        "djaploy.modules.nginx",
        "djaploy.modules.systemd",
    ],

    services=["myapp", "myapp-worker"],
)
```

### 4. Define inventory

**infra/inventory/production.py**:

```python
from djaploy.config import HostConfig

hosts = [
    HostConfig(
        name="web-1",
        ssh_host="192.168.1.100",
        ssh_user="deploy",
        app_user="app",
        env="production",
        services=["myapp", "myapp-worker"],
    ),
]
```

### 5. Deploy files

Place environment-specific configuration files in `deploy_files/` — these are copied to the server during deployment:

```ini
# deploy_files/production/etc/systemd/system/myapp.service
[Unit]
Description=My Django App
After=network.target

[Service]
Type=simple
User=app
WorkingDirectory=/home/app/apps/myapp
ExecStart=/home/app/.local/bin/poetry run gunicorn config.wsgi
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Usage

### Configure a server

```bash
python manage.py configureserver --env production
```

Sets up the application user, installs Python and Poetry, and prepares the directory structure.

### Deploy

```bash
# Deploy local changes (development)
python manage.py deploy --env production --local

# Deploy latest git commit
python manage.py deploy --env production --latest

# Deploy a specific release
python manage.py deploy --env production --release v1.0.0
```

Deployment flow:

1. Creates a tar.gz artifact from git
2. Uploads to servers
3. Extracts application code
4. Copies environment-specific deploy files (nginx, systemd, etc.)
5. Installs dependencies via Poetry
6. Runs migrations
7. Collects static files
8. Restarts services

### Certificate management

```bash
python manage.py update_certs           # Update certificate definitions
python manage.py sync_certs --env production  # Sync certificates
```

### Verify configuration

```bash
python manage.py verify --verbose
```

## Modules

djaploy uses a modular architecture — each component is a separate module that can be enabled or disabled per project.

### Built-in modules

| Module | Description |
|--------|-------------|
| `djaploy.modules.core` | Core setup: users, Python, Poetry, artifact deployment, migrations |
| `djaploy.modules.nginx` | Nginx web server configuration |
| `djaploy.modules.systemd` | Systemd service management |
| `djaploy.modules.sync_certs` | SSL certificate syncing |
| `djaploy.modules.cert_renewal` | Certificate renewal automation |
| `djaploy.modules.litestream` | Litestream database replication |
| `djaploy.modules.rclone` | Rclone-based backups |
| `djaploy.modules.tailscale` | Tailscale networking |

### Custom modules

Extend `BaseModule` to create project-specific deployment logic:

```python
from djaploy.modules.base import BaseModule

class MyModule(BaseModule):
    def configure_server(self, host):
        # Server configuration logic
        pass

    def deploy(self, host, artifact_path):
        # Deployment logic
        pass
```

Add it to your config:

```python
config = DjaployConfig(
    modules=[
        "djaploy.modules.core",
        "myproject.infra.modules.custom",
    ],
)
```

## Project Customization

### prepare.py

Projects can include a `prepare.py` file for local build steps that run before deployment:

```python
# prepare.py
from djaploy.prepare import run_command

def prepare():
    run_command("npm run build")
    run_command("python manage.py collectstatic --noinput")
```

### Custom deploy files

Projects can include environment-specific configuration files in a `deploy_files/` directory that will be copied to the server during deployment. The directory structure mirrors the target filesystem layout (e.g. `deploy_files/production/etc/nginx/sites-available/myapp` gets copied to `/etc/nginx/sites-available/myapp` on the server).

## Development

```bash
git clone https://github.com/Technology-Company/djaploy.git
cd djaploy
poetry install
```

To use a local development copy in another project:

```bash
pip install -e /path/to/djaploy
```

## License

[MIT](LICENSE)