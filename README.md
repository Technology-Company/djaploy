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
ExecStart=/home/app/.local/bin/poetry run gunicorn --chdir /home/app/apps/myapp/current config.wsgi
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

> **Note:** The `--chdir` flag is important for zero-downtime deployments. Without it,
> gunicorn resolves the `current` symlink once at startup and all forked workers
> (including after USR2 reload) continue using the old release directory. With `--chdir`,
> gunicorn re-evaluates the symlink path when the new master starts.

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

## Release Notifications & Versioning

djaploy includes built-in support for semantic versioning, changelog generation, and deployment notifications. When enabled, deployments automatically:

- Calculate the next semantic version based on git tags
- Generate a changelog from commit messages (simple or AI-powered)
- Send notifications to Slack or custom webhooks
- Create and push git tags after successful deployments
- Deploy a `VERSION` file to the server

### Enabling the feature

Add the `versioning` module to your config and configure notifications:

```python
# infra/config.py
from djaploy.config import DjaployConfig

config = DjaployConfig(
    project_name="myapp",
    # ...

    modules=[
        "djaploy.modules.core",
        "djaploy.modules.nginx",
        "djaploy.modules.systemd",
        "djaploy.modules.versioning",  # Enable versioning
    ],

    module_configs={
        "versioning": {
            "tag_environments": ["production"],  # Create tags only for these envs
            "increment_type": "patch",           # Default: patch (v1.0.0 -> v1.0.1)
            "push_tags": True,                   # Push tags to remote
        },
        "notifications": {
            "display_name": "My App",            # Name shown in notifications
            "notify_environments": ["production", "staging"],
            "notify_on_failure": True,
            "changelog_generator": "llm",        # "simple" or "llm"
            "changelog_config": {
                "api_key": "op://vault/mistral/api-key",  # 1Password reference or plain key
                "model": "devstral-small-latest",
                "api_url": "https://api.mistral.ai/v1/chat/completions",
            },
            "backend_config": {
                "webhook_url": "op://vault/slack/webhook-url",
            },
        },
    },
)
```

### Configuration options

**Versioning (`module_configs["versioning"]`)**

| Option | Default | Description |
|--------|---------|-------------|
| `tag_environments` | `["production"]` | Environments that create git tags |
| `increment_type` | `"patch"` | Default version bump: `major`, `minor`, or `patch` |
| `push_tags` | `True` | Push created tags to remote |
| `version_file_path` | `"VERSION"` | Path for VERSION file on server |

**Notifications (`module_configs["notifications"]`)**

| Option | Default | Description |
|--------|---------|-------------|
| `display_name` | `project_name` | Name shown in notification messages |
| `notify_environments` | `tag_environments` | Environments that send notifications |
| `notify_on_failure` | `True` | Send notification on deployment failure |
| `changelog_generator` | `"simple"` | Generator type: `simple` or `llm` |
| `changelog_config` | `{}` | Config passed to changelog generator |
| `backend_config.webhook_url` | — | Slack webhook URL (required) |

### Changelog generators

**Simple** — Concatenates commit messages into a brief summary:
```python
"changelog_generator": "simple"
```

**LLM** — Uses an AI model to generate natural language summaries:
```python
"changelog_generator": "llm",
"changelog_config": {
    "api_key": "your-api-key",           # Required
    "api_url": "https://api.mistral.ai/v1/chat/completions",  # OpenAI-compatible
    "model": "devstral-small-latest",
}
```

### Version bump override

Override the default increment type per deployment:

```bash
python manage.py deploy --env production --bump-major   # v1.0.0 -> v2.0.0
python manage.py deploy --env production --bump-minor   # v1.0.0 -> v1.1.0
python manage.py deploy --env production --bump-patch   # v1.0.0 -> v1.0.1 (default)
```

### How it works

```
Deploy to dev (tag_environments: ["production"])
├─ Calculates version from commits since last tag
├─ Generates changelog
├─ Sends notification ✓
└─ Does NOT create tag (dev not in tag_environments)

Deploy to production
├─ Same version/changelog calculation
├─ Sends notification ✓
├─ Creates tag v1.0.5 and pushes to remote ✓
└─ Deploys VERSION file to server
```

When redeploying the same version (no new commits), the changelog is extracted from the existing git tag message to ensure consistent notifications across environments.

### VERSION file

The versioning module deploys a `VERSION` file to the server containing:

```
VERSION=v1.0.5
COMMIT=abc1234
DEPLOYED_AT=2024-01-15T10:30:00Z
ENVIRONMENT=production
```

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