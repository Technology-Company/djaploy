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

## Deployment Strategies

djaploy supports three deployment strategies, configured via `deployment_strategy` on `HostConfig`.

### In-place (`"in_place"`)

The simplest strategy. Code is extracted directly into the app directory and services are restarted. Has brief downtime during restart.

### Zero-downtime (`"zero_downtime"`)

Uses a `releases/` directory with a `current` symlink. Each deploy creates a new immutable release, swaps the symlink atomically, and sends USR2 via gunicornherder to reload gunicorn. No downtime, but no pre-activation testing.

### Blue-green (`"bluegreen"`)

Two independent slots (blue and green), each running its own gunicorn process on a separate Unix socket. Traffic switching happens via nginx reload. Supports staging a release for testing before switching.

```python
HostConfig(
    "my-server",
    ssh_hostname="192.168.1.100",
    app_name="myapp",
    app_user="myapp-api",
    deployment_strategy="bluegreen",
    # ...
)
```

#### Blue-green commands

```bash
# Deploy to inactive slot (does NOT switch traffic)
python manage.py djaploy deploy --env production --latest

# Activate: switch nginx to the staged slot (zero downtime)
python manage.py djaploy activate --env production

# Deploy + activate in one step
python manage.py djaploy deploy --env production --latest --activate

# Show both slots with release info, paths, service status
python manage.py djaploy status --env production

# Rollback: switch back to previous slot (instant)
python manage.py djaploy rollback --env production
```

#### Blue-green deployment flow

1. **Deploy** -- extracts artifact to inactive slot, installs dependencies, runs migrations, starts the slot's gunicorn service
2. **Test** -- the staged slot is running and reachable via its socket (e.g. `curl --unix-socket /run/myapp-green/myapp.sock http://localhost/health/`)
3. **Activate** -- rewrites nginx upstream to point to the new slot, reloads nginx
4. **Rollback** (if needed) -- switches nginx back to the previous slot, which is still running

> **Note:** Migrations run during Step 1, before traffic switches. Both the old and new slots share the same database, so migrations must be **backward-compatible** (use the expand/contract pattern). Deploy new code that handles both old and new schema, activate, then clean up in a subsequent deploy.

### Server directory layout comparison

For `app_user="myapp-api"`, `app_name="myapp"`:

| Path | `in_place` | `zero_downtime` | `bluegreen` |
|------|-----------|-----------------|-------------|
| App code | `.../apps/myapp/` | `.../apps/myapp/current/` | `.../apps/myapp/slots/{blue\|green}/` |
| Virtualenv | Managed by Poetry | `.../shared/venv-{HASH}-py{ver}/` | `.../shared/venv-{HASH}-py{ver}/` |
| Static files | `.../apps/myapp/staticfiles/` | `.../apps/myapp/shared/staticfiles/` | `.../apps/myapp/shared/staticfiles/` |
| Media files | `.../apps/myapp/media/` | `.../apps/myapp/shared/media/` | `.../apps/myapp/shared/media/` |
| Database | via `db_dir` | via `db_dir` | via `db_dir` |

All paths are relative to `/home/{app_user}/`.

#### Shared directory (`zero_downtime` and `bluegreen`)

Both strategies use a `shared/` directory for resources that persist across deployments:

| Content | Purpose |
|---------|---------|
| `venv-{HASH}-py{version}/` | Virtualenvs keyed by `poetry.lock` hash. Reused when dependencies haven't changed. |
| `staticfiles/` | Output of `collectstatic`. Served by nginx. |
| `media/` | User-uploaded files. Served by nginx. |
| Custom paths via `shared_resources` | Project-specific shared directories (e.g. `bostad/public`). |

#### Systemd services comparison

| Strategy | Service name | Socket path | Process |
|----------|-------------|-------------|---------|
| `in_place` | `{app}.service` | `/run/{app}/{app}.sock` | `poetry run gunicorn` |
| `zero_downtime` | `{app}.service` | `/run/{app}/{app}.sock` | gunicornherder wrapping gunicorn |
| `bluegreen` | `{app}-blue.service`, `{app}-green.service` | `/run/{app}-blue/{app}.sock`, `/run/{app}-green/{app}.sock` | Plain gunicorn (`Type=notify`) |

Blue-green uses `Type=notify` -- gunicorn has native systemd-notify support, so systemd knows when the process is ready without needing gunicornherder.

#### Nginx configuration (bluegreen)

Blue-green deploys the nginx upstream as a separate include file so it can be rewritten during activation without touching the site config:

- Site config: `/etc/nginx/sites-available/{app_name}` (no inline upstream block)
- Upstream config: `/etc/nginx/sites-available/{app_name}-upstream.conf`

Activation rewrites the upstream file to point to the new slot's socket and reloads nginx.

#### State tracking (bluegreen)

Blue-green maintains a `state.json` file at `/home/{app_user}/apps/{app_name}/state.json` that tracks the active slot and deployment metadata (release name, commit, venv path, python interpreter) for each slot. This is printed during deploy, activate, and status commands.

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

Configure `versioning_conf` and `notifications_conf` on your `HostConfig`:

```python
# infra/inventory/production.py
from djaploy import HostConfig

hosts = [
    HostConfig(
        "web-1",
        ssh_hostname="192.168.1.100",
        app_name="myapp",
        # ...
        versioning_conf={
            "tag_environments": ["production"],  # Create tags only for these envs
            "increment_type": "patch",           # Default: patch (v1.0.0 -> v1.0.1)
            "push_tags": True,                   # Push tags to remote
        },
        notifications_conf={
            "display_name": "My App",            # Name shown in notifications
            "notify": True,                      # Enable Slack notifications for this env
            "notify_on_failure": True,
            "webhook_url": "op://vault/slack/webhook-url",
            "changelog_generator": "llm",        # "simple" or "llm"
            "changelog_config": {
                "api_key": "op://vault/mistral/api-key",  # 1Password reference or plain key
                "model": "devstral-small-latest",
                "api_url": "https://api.mistral.ai/v1/chat/completions",
            },
        },
    ),
]
```

### Configuration options

**Versioning (`versioning_conf`)**

| Option | Default | Description |
|--------|---------|-------------|
| `tag_environments` | `["production"]` | Environments that create git tags |
| `increment_type` | `"patch"` | Default version bump: `major`, `minor`, or `patch` |
| `push_tags` | `True` | Push created tags to remote |
| `version_file_path` | `"VERSION"` | Path for VERSION file on server |

**Notifications (`notifications_conf`)**

| Option | Default | Description |
|--------|---------|-------------|
| `display_name` | `app_name` | Name shown in notification messages |
| `notify` | `False` | Enable notifications for this environment |
| `notify_on_failure` | `True` | Send notification on deployment failure |
| `webhook_url` | — | Slack webhook URL (required) |
| `changelog_generator` | `"simple"` | Generator type: `simple` or `llm` |
| `changelog_config` | `{}` | Config passed to changelog generator |

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