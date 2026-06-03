# djaploy

[![PyPI Version](https://badgen.net/pypi/v/djaploy)](https://pypi.org/project/djaploy/)
[![Python Versions](https://badgen.net/pypi/python/djaploy)](https://pypi.org/project/djaploy/)
[![License](https://badgen.net/badge/license/MIT/blue)](https://github.com/Technology-Company/djaploy/blob/main/LICENSE)
[![Last Commit](https://badgen.net/github/last-commit/Technology-Company/djaploy)](https://github.com/Technology-Company/djaploy/commits)

A modular Django deployment system based on [pyinfra](https://pyinfra.com/), designed to standardize and simplify infrastructure management across Django projects.

## Features

- **App-based, modular architecture** — deployment behaviour ships as Django apps you add to `INSTALLED_APPS`
- **Django integration** — drive everything through `manage.py` commands
- **Multiple deployment strategies** — `in_place`, `zero_downtime`, and `bluegreen`
- **Generated config** — systemd units and nginx sites are rendered from templates (no hand-maintained config files)
- **Generated local settings** — optionally write a `local.py` with production values on the server
- **Infrastructure as code** — define hosts in Python with pyinfra
- **Git-based artifacts** — automated artifact creation from your git repository
- **SSL management** — issue/renew certificates (Let's Encrypt, Bunny DNS, Tailscale) and sync them to servers
- **Release notifications & versioning** — semantic version tags, changelogs, and Slack/webhook notifications

## Installation

```bash
pip install djaploy
# or: poetry add djaploy
```

### Optional extras

```bash
pip install djaploy[certificates]   # Let's Encrypt / certbot support
pip install djaploy[bunny]          # Bunny DNS certbot plugin
```

## Quick Start

### 1. Add djaploy to Django settings

Add the base `djaploy` app plus the feature apps you want. Each feature is its own Django app
that contributes deploy hooks when present in `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ... your apps ...
    "djaploy",                 # management commands + core deploy hooks (required)
    "djaploy.apps.nginx",      # generate + deploy nginx config, manage SSL, reload
    "djaploy.apps.systemd",    # reload systemd, manage services
    "djaploy.apps.sync_certs", # sync certs from 1Password to servers
    # Other available apps:
    # "djaploy.apps.versioning", "djaploy.apps.borg", "djaploy.apps.rclone",
    # "djaploy.apps.tailscale", "djaploy.apps.janitor",
]

# Required paths (plain strings or Path objects both work)
import os

BASE_DIR = os.path.dirname(...)          # your Django project dir (contains manage.py's package)
GIT_DIR = os.path.dirname(BASE_DIR)      # repo root (where .git lives) — used for artifacts/versioning
# ARTIFACT_DIR = "deployment"            # optional; where artifacts are written (default: "deployment")
```

> **Migrating from 0.x?** `DjaployConfig`, `module_configs`, `modules=[...]`, the `infra/config.py`
> file, and the `deploy_files/` copy mechanism have been removed. All deployment config now lives on
> `HostConfig`, features are enabled via `INSTALLED_APPS`, and systemd/nginx are generated from
> templates. See [Configuration](#configuration) below.

### 2. Create the project structure

djaploy discovers infrastructure by scanning each installed app's `infra/` directory (in
`INSTALLED_APPS` order, first match wins). Put your deployment config inside one of your Django apps:

```
your_app/
├── infra/
│   ├── inventory/
│   │   ├── production.py      # hosts = [HostConfig(...), ...]
│   │   └── staging.py
│   ├── certificates.py        # all_certificates = [...]  (optional, for SSL)
│   ├── prepare.py             # optional local pre-deploy build steps
│   └── djaploy_hooks.py       # optional project-specific @deploy_hook functions
└── ...
```

There is **no** `infra/config.py` — host and deployment settings live entirely on `HostConfig`.

### 3. Define inventory

```python
# your_app/infra/inventory/production.py
from djaploy.config import HostConfig

hosts = [
    HostConfig(
        "web-1",
        ssh_hostname="192.168.1.100",
        ssh_user="deploy",
        app_name="myapp",                  # deployment name == your Django package (see note below)
        app_user="myapp",
        deployment_strategy="zero_downtime",
        python_version="3.11",
        manage_py_path="manage.py",        # relative path to manage.py inside the artifact
        services=["myapp"],
        gunicorn_conf={"workers": 3, "timeout": 30},
        nginx_conf={"client_max_body_size": "25M"},
    ),
]
```

> **`app_name` and your Django package.** `app_name` drives the server app dir
> (`/home/{app_user}/apps/{app_name}`), the systemd service/socket names, and the nginx upstream.
> If you use [`generate_local_settings`](#generated-local-settings), `app_name` must match your
> Django package name, since the generated `local.py` is written to
> `{manage_subdir}/{app_name}/settings/local.py`.

### 4. Configure and deploy

```bash
python manage.py djaploy configure --env production   # one-time server setup
python manage.py djaploy deploy --env production       # deploy latest git HEAD
```

## Configuration

All deployment configuration lives on `djaploy.config.HostConfig`. Commonly used fields:

| Field | Default | Purpose |
|-------|---------|---------|
| `ssh_hostname` | — (required) | SSH host |
| `ssh_user` / `ssh_port` / `ssh_key` | `deploy` / `22` / — | SSH connection |
| `ssh_known_hosts_file` | — | known_hosts for strict host verification |
| `app_name` | — (required) | Deployment name; drives dir/service/socket/nginx names |
| `app_user` | `app` | OS user the app runs as |
| `app_hostname` | — | Public hostname (used for `server_name` / ALLOWED_HOSTS) |
| `deployment_strategy` | `zero_downtime` | `in_place`, `zero_downtime`, or `bluegreen` |
| `python_version` / `python_compile` | `3.11` / `False` | Python on the server (apt or compiled) |
| `manage_py_path` | `manage.py` | Path to `manage.py` within the artifact |
| `services` / `timer_services` | — | systemd services/timers to manage |
| `domains` | — | Certificates/domains for SSL (see [Certificates](#certificate-management)) |
| `keep_releases` | `5` | Releases retained (zero_downtime) |
| `generate_local_settings` | `False` | Write `local.py` on the server (see below) |
| `shared_resources` | — | Extra paths symlinked from `shared/` |
| `db_dir` | — | External database directory template |
| `gunicorn_conf` | — | `workers`, `timeout`, `umask`, `wsgi_module`, `health_check_*` |
| `nginx_conf` | — | `server_name`, `listen`, `client_max_body_size`, `static_path`, `media_path`, `custom` |
| `core_conf` | — | `poetry_no_root`, `exclude_groups`, `poetry_lock`, `databases` |
| `versioning_conf` / `notifications_conf` | — | See [Release Notifications & Versioning](#release-notifications--versioning) |
| `backup` / `borg_backup` | — | `BackupConfig` / `BorgBackupConfig` |

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

> **Note:** Migrations run during deploy, before traffic switches. Both slots share the same
> database, so migrations must be **backward-compatible** (expand/contract pattern).

### Server directory layout comparison

For `app_user="myapp-api"`, `app_name="myapp"`:

| Path | `in_place` | `zero_downtime` | `bluegreen` |
|------|-----------|-----------------|-------------|
| App code | `.../apps/myapp/` | `.../apps/myapp/current/` | `.../apps/myapp/slots/{blue\|green}/` |
| Virtualenv | Managed by Poetry | `.../shared/venv-{HASH}-py{ver}/` | `.../shared/venv-{HASH}-py{ver}/` |
| Static files | `.../apps/myapp/static/` | `.../apps/myapp/shared/static/` | `.../apps/myapp/shared/static/` |
| Media files | `.../apps/myapp/media/` | `.../apps/myapp/shared/media/` | `.../apps/myapp/shared/media/` |

All paths are relative to `/home/{app_user}/`.

#### Systemd services comparison

| Strategy | Service name | Socket path | Process |
|----------|-------------|-------------|---------|
| `in_place` | `{app}.service` | `/run/{app}/{app}.sock` | `poetry run gunicorn` |
| `zero_downtime` | `{app}.service` | `/run/{app}/{app}.sock` | gunicornherder wrapping gunicorn |
| `bluegreen` | `{app}-blue.service`, `{app}-green.service` | `/run/{app}-{slot}/{app}.sock` | gunicorn (`Type=notify`) |

## Generated configuration

In 1.x, djaploy **generates** systemd units and nginx sites from templates
(`djaploy/infra/templates.py`) and writes them to the server during `deploy`/`configure` — there is
no `deploy_files/` directory to maintain.

### systemd

A unit is rendered for the host's strategy (`SYSTEMD_IN_PLACE`, `SYSTEMD_ZERO_DOWNTIME`, or a
per-slot `SYSTEMD_BLUEGREEN`) to `/etc/systemd/system/{app_name}.service`. Workers, timeout, umask,
and the WSGI module come from `gunicorn_conf` (the WSGI module otherwise derives from Django's
`WSGI_APPLICATION`, falling back to `{app_name}.wsgi:application`).

### nginx

The `djaploy.apps.nginx` app installs nginx, deploys SSL certs, symlinks the site, and reloads.
The site config is rendered from:

- `NGINX_SITE` / `NGINX_SITE_SSL` for `in_place` / `zero_downtime`
- `NGINX_SITE_BLUEGREEN` / `NGINX_SITE_SSL_BLUEGREEN` (+ a separate upstream file rewritten on
  activation) for `bluegreen`

The SSL variants are selected automatically when the host has `domains` with certificates. Template
values are derived from `HostConfig`:

- `server_name` — `nginx_conf["server_name"]`, else the first domain's identifier, else `app_hostname`, else `_`
- `ssl_certificate` / `ssl_certificate_key` — `/home/{app_user}/.ssl/{identifier}.{crt,key}`
- static/media aliases — `{app_path}/static` and `{app_path}/media` (or `shared/...` for zero_downtime/bluegreen), overridable via `nginx_conf["static_path"]` / `nginx_conf["media_path"]`
- `client_max_body_size` — `nginx_conf["client_max_body_size"]` (default `10M`), `listen` — `nginx_conf["listen"]`

**Custom static/media locations:** set `nginx_conf={"static_path": ..., "media_path": ...}` to point
nginx (and, for zero_downtime/bluegreen, the generated `local.py` `STATIC_ROOT`/`MEDIA_ROOT`) at a
custom directory. Each value may be absolute (leading `/`) or relative to `{app_path}`. Make sure
your Django `STATIC_ROOT`/`MEDIA_ROOT` resolve to the same paths. Example — serve from a `public/`
dir next to `manage.py`:

```python
nginx_conf={
    "static_path": "myproject/public/static",   # -> {app_path}/myproject/public/static
    "media_path":  "myproject/public/media",
}
```

**Bring your own nginx:** set `nginx_conf={"custom": True}` to skip built-in nginx generation and
manage the config yourself (e.g. via a custom `deploy:configure` / `activate:post` hook).

### Generated local settings

Set `generate_local_settings=True` to have djaploy write
`{manage_subdir}/{app_name}/settings/local.py` on the server during deploy, containing `DEBUG=False`,
`ALLOWED_HOSTS` (from `app_hostname`), `DATABASES` (when `db_dir` is set), and — for
`zero_downtime`/`bluegreen` — `STATIC_ROOT`/`MEDIA_ROOT`. Your project settings must import it:

```python
try:
    from .local import *  # noqa
except ImportError:
    pass
```

Because the path is keyed on `app_name`, `app_name` must equal your Django settings package name.

## Commands

```bash
# Deployment lifecycle
python manage.py djaploy deploy   --env <env> [--local | --latest | --release TAG] [--activate]
python manage.py djaploy configure --env <env>
python manage.py djaploy rollback  --env <env> [--release NAME]
python manage.py djaploy activate  --env <env>     # bluegreen
python manage.py djaploy status    --env <env>     # bluegreen
python manage.py djaploy --list                    # list available commands

# Certificates
python manage.py update_certs --email admin@example.com [--staging] [--force]
python manage.py sync_certs   --env <env>

# Diagnostics / backups
python manage.py verify --verbose
python manage.py restore_backup --env <env>
```

Deploy modes: `--local` (uncommitted working tree), `--latest` (git HEAD, default), `--release TAG`.
Version bumps: `--bump-major | --bump-minor | --bump-patch`.

## Certificate management

Define certificates in `<app>/infra/certificates.py`:

```python
from djaploy.certificates import BunnyDnsCertificate, LetsEncryptCertificate, TailscaleDnsCertificate

all_certificates = [
    prod_cert := BunnyDnsCertificate(
        "example.com", "www.example.com",
        op_crt="/MyProject/example.com/fullchain.pem",   # 1Password item field for the cert
        op_key="/MyProject/example.com/privkey.pem",     # 1Password item field for the key
        bunny_api_key_secret="/MyProject/Bunny - API Key/credential",
    ),
]
```

Reference certificates from a host via `domains=[prod_cert]`. Then:

```bash
python manage.py update_certs --email admin@example.com   # issue/renew (to 1Password)
python manage.py sync_certs --env production              # push certs to /home/{app_user}/.ssl/
```

`update_certs` discovers `certificates.py` via app discovery and uses `settings.OP_ACCOUNT` for the
1Password account. Other certificate types: `LetsEncryptCertificate` (HTTP-01, optionally via an
SSH `SshHttpHook`) and `TailscaleDnsCertificate`.

## Project customization

### Hooks

Add `<app>/infra/djaploy_hooks.py` with `@deploy_hook(<phase>)` functions. They're auto-discovered
and run at the matching lifecycle phase. Remote (`deploy:*`) hooks receive `(host_data, artifact_path)`:

```python
from djaploy.hooks import deploy_hook

@deploy_hook("deploy:configure")
def my_step(host_data, artifact_path):
    from pyinfra.operations import server
    server.shell(name="example", commands=["echo hello"], _sudo=True)
```

Phases (in order): `configure`, then per-deploy `deploy:upload` → `deploy:configure` → `deploy:pre`
→ `deploy:start`; plus `activate`/`rollback` (and their `:pre`/`:post`) for those commands. The
management command also runs `{command}:precommand` / `precommand` / `{command}:postcommand` /
`postcommand` locally around the pyinfra run.

### prepare.py

Add `<app>/infra/prepare.py` for local build steps run before the artifact is created (skipped with
`--skip-prepare`):

```python
from pyinfra import local
local.shell("npm run build")
```

## Release Notifications & Versioning

djaploy includes built-in support for semantic versioning, changelog generation, and deployment notifications. When enabled, deployments automatically:

- Calculate the next semantic version based on git tags
- Generate a changelog from commit messages (simple or AI-powered)
- Send notifications to Slack or custom webhooks
- Create and push git tags after successful deployments
- Deploy a `VERSION` file to the server

### Enabling the feature

Configure `versioning_conf` and `notifications_conf` on your `HostConfig` (requires
`djaploy.apps.versioning` in `INSTALLED_APPS`):

```python
from djaploy.config import HostConfig

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
            "display_name": "My App",
            "notify": True,
            "notify_on_failure": True,
            "webhook_url": "op://vault/slack/webhook-url",
            "changelog_generator": "llm",        # "simple" or "llm"
            "changelog_config": {
                "api_key": "op://vault/mistral/api-key",
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

### Version bump override

```bash
python manage.py djaploy deploy --env production --bump-minor   # v1.0.0 -> v1.1.0
```

### VERSION file

The versioning app deploys a `VERSION` file to the server:

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
