# djaploy

A modular Django deployment system based on pyinfra, designed to standardize and simplify infrastructure management across Django projects.

## Features

- **Modular Architecture**: Extensible plugin system for deployment components
- **Django Integration**: Seamless integration via Django management commands  
- **Python Compilation Support**: Compile Python from source for specific versions
- **Multiple Deployment Modes**: Support for `--local`, `--latest`, and `--release` deployments
- **Infrastructure as Code**: Define infrastructure using Python with pyinfra
- **Git-based Artifacts**: Automated artifact creation from git repository

## Installation

### From PyPI (once published)

```bash
pip install djaploy
```

Or with Poetry:
```bash
poetry add djaploy
```

### Development Installation

For testing djaploy locally without publishing to PyPI:

```bash
# Clone the repository
git clone https://github.com/techco-fi/djaploy.git
cd djaploy

# Install in editable mode
pip install -e .

# Or with Poetry  
poetry install
```

Then in your Django project:
```bash
# Using pip
pip install -e /path/to/djaploy

# Or add as a local dependency in pyproject.toml
[tool.poetry.dependencies]
djaploy = {path = "../djaploy", develop = true}
```

## Quick Start

### 1. Project Structure

```
your-django-project/
├── manage.py
├── your_app/
│   └── settings.py
└── djaploy/                    # Deployment configuration
    ├── config.py              # Main configuration
    ├── inventory/             # Host definitions
    │   ├── production.py
    │   └── staging.py  
    └── deploy_files/          # Environment-specific files
        ├── production/
        │   └── etc/systemd/system/app.service
        └── staging/
```

### 2. Django Settings

Add to your Django `settings.py`:

```python
# Required: Set project paths
BASE_DIR = '/path/to/django'  
PROJECT_DIR = BASE_DIR # folder containing manage.py
DJAPLOY_CONFIG_DIR = BASE_DIR + '/djaploy'
GIT_DIR = BASE_DIR.parent  # Git repository root
```

### 3. Create Configuration

Create `djaploy/config.py`:

```python
from djaploy.config import DjaployConfig
from pathlib import Path

config = DjaployConfig(
    # Required fields
    project_name="myapp",
    djaploy_dir=Path(__file__).parent,  # REQUIRED: This djaploy directory
    manage_py_path=Path("manage.py"),   # REQUIRED: Path to manage.py (relative to project root)
    
    # Python settings
    python_version="3.11",
    python_compile=False,  # Set True to compile from source
    
    # Server settings
    app_user="app",
    ssh_user="deploy",
    
    # Modules to use
    modules=[
        "djaploy.modules.core",      # Core setup (required)
        "djaploy.modules.nginx",     # Web server
        "djaploy.modules.systemd",   # Service management
    ],
    
    # Services to manage
    services=["myapp", "myapp-worker"],
)
```

### 4. Define Inventory

Create `djaploy/inventory/production.py`:

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

### 5. Deploy Files

Place service files in `djaploy/deploy_files/production/`:

```ini
# etc/systemd/system/myapp.service
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

### Configure Server

```bash
python manage.py configureserver --env production
```

This will:
- Create application user
- Install/compile Python  
- Install Poetry
- Set up directory structure

### Deploy Application

```bash
# Deploy local changes (for development)
python manage.py deploy --env production --local

# Deploy latest git commit
python manage.py deploy --env production --latest

# Deploy specific release
python manage.py deploy --env production --release v1.0.0
```

Deployment process:
1. Creates tar.gz artifact from git
2. Uploads to servers
3. Extracts application code
4. Installs dependencies
5. Runs migrations
6. Collects static files  
7. Restarts services

## Module System

Djaploy uses a modular architecture where each component (nginx, systemd, backups, etc.) is a separate module that can be enabled or disabled per project.

### Available Modules

- `nginx`: NGINX web server configuration
- `systemd`: Systemd service management
- `litestream`: Litestream database backups
- `rclone`: Rclone-based backup system
- `tailscale`: Tailscale networking
- `ssl`: SSL certificate management
- `python_build`: Python compilation from source

### Creating Custom Modules

Projects can create their own modules by extending the base module class:

```python
from djaploy.modules.base import BaseModule

class MyCustomModule(BaseModule):
    def configure_server(self, host):
        # Server configuration logic
        pass
    
    def deploy(self, host, artifact_path):
        # Deployment logic
        pass
```

## Project Customization

### prepare.py

Projects can include a `prepare.py` file for local build steps before deployment:

```python
# prepare.py
from djaploy.prepare import run_command

def prepare():
    run_command("npm run build")
    run_command("python manage.py collectstatic --noinput")
```

### Custom Deploy Files

Projects can include environment-specific configuration files in a `deploy_files/` directory that will be copied to the server during deployment.

## License

MIT