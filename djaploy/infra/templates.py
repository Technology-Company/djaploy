"""
Config file templates for djaploy deployments.

Jinja2 templates rendered via pyinfra's files.template with StringIO.
Variables are derived from host_data at deploy time.
"""

# ---------------------------------------------------------------------------
# Systemd service templates
# ---------------------------------------------------------------------------

SYSTEMD_ZERO_DOWNTIME = """\
[Unit]
Description={{ project_name }} Gunicorn Django App
After=network.target

[Service]
Type=simple
User={{ app_user }}
Group={{ app_user }}
RuntimeDirectory={{ project_name }}
WorkingDirectory={{ app_path }}/current
ExecStart=/usr/local/bin/gunicornherder \\
    --pidfile /run/{{ project_name }}/gunicorn.pid \\
    --app-dir {{ app_path }}/current \\
    -- \\
    {{ app_path }}/current/.venv/bin/gunicorn \\
        --workers {{ workers }} \\
        --bind unix:/run/{{ project_name }}/{{ project_name }}.sock \\
        --umask {{ umask }} \\
        --access-logfile - \\
        --error-logfile - \\
        --timeout {{ timeout }} \\
        {{ wsgi_module }}
ExecReload=/bin/kill -HUP $MAINPID
KillMode=control-group
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

SYSTEMD_IN_PLACE = """\
[Unit]
Description={{ project_name }} Gunicorn Django App
After=network.target

[Service]
Type=simple
User={{ app_user }}
Group={{ app_user }}
RuntimeDirectory={{ project_name }}
WorkingDirectory={{ app_path }}
ExecStart=/home/{{ app_user }}/.local/bin/poetry run gunicorn \\
    --workers {{ workers }} \\
    --bind unix:/run/{{ project_name }}/{{ project_name }}.sock \\
    --pid /run/{{ project_name }}/gunicorn.pid \\
    --access-logfile - \\
    --error-logfile - \\
    --timeout {{ timeout }} \\
    {{ wsgi_module }}
ExecReload=/bin/kill -HUP $MAINPID
KillMode=control-group
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

# ---------------------------------------------------------------------------
# Nginx template
# ---------------------------------------------------------------------------

NGINX_SITE = """\
upstream {{ project_name }} {
    server unix:/run/{{ project_name }}/{{ project_name }}.sock fail_timeout=0;
}

server {
    listen {{ listen }};
    server_name {{ server_name }};

    client_max_body_size {{ client_max_body_size }};

    location /static/ {
        alias {{ static_path }}/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location /media/ {
        alias {{ media_path }}/;
        expires 30d;
    }

    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        proxy_pass http://{{ project_name }};
    }
}
"""


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_template_context(host_data):
    """Build the full Jinja2 context dict from host_data."""
    from djaploy.infra.utils import is_zero_downtime, get_app_path

    app_user = getattr(host_data, 'app_user', 'app')
    app_name = getattr(host_data, 'app_name', None)
    if not app_name:
        raise ValueError("app_name must be set on HostConfig")
    app_path = get_app_path(host_data)

    gunicorn_cfg = getattr(host_data, 'gunicorn_conf', None) or {}
    nginx_cfg = getattr(host_data, 'nginx_conf', None) or {}

    if is_zero_downtime(host_data):
        static_path = f"{app_path}/shared/staticfiles"
        media_path = f"{app_path}/shared/media"
    else:
        static_path = f"{app_path}/staticfiles"
        media_path = f"{app_path}/media"

    return {
        "project_name": app_name,
        "app_user": app_user,
        "app_path": app_path,
        # gunicorn
        "workers": gunicorn_cfg.get("workers", 2),
        "timeout": gunicorn_cfg.get("timeout", 30),
        "umask": gunicorn_cfg.get("umask", "002"),
        "wsgi_module": gunicorn_cfg.get(
            "wsgi_module", f"{app_name}.wsgi:application"
        ),
        # nginx
        "server_name": nginx_cfg.get("server_name", "_"),
        "listen": nginx_cfg.get("listen", "80 default_server"),
        "client_max_body_size": nginx_cfg.get("client_max_body_size", "10M"),
        "static_path": static_path,
        "media_path": media_path,
    }
