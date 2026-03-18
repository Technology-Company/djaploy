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
WorkingDirectory={{ app_path }}/current{% if manage_subdir %}/{{ manage_subdir }}{% endif %}
ExecStart=/usr/local/bin/gunicornherder \\
    --pidfile /run/{{ project_name }}/gunicorn.pid \\
    --app-dir {{ app_path }}/current{% if manage_subdir %}/{{ manage_subdir }}{% endif %}{% if health_check_url %} \\
    --health-check-url {{ health_check_url }}{% endif %} \\
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
WorkingDirectory={{ app_path }}{% if manage_subdir %}/{{ manage_subdir }}{% endif %}
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

NGINX_SITE_SSL = """\
upstream {{ project_name }} {
    server unix:/run/{{ project_name }}/{{ project_name }}.sock fail_timeout=0;
}

server {
    listen 443 ssl;
    server_name {{ server_name }};

    ssl_certificate {{ ssl_certificate }};
    ssl_certificate_key {{ ssl_certificate_key }};

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

server {
    listen 80;
    server_name {{ server_name }};

    location /.well-known/acme-challenge/ {
        alias /var/www/challenges/;
        try_files $uri =404;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}
"""


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_template_context(host_data):
    """Build the full Jinja2 context dict from host_data."""
    from djaploy.infra.utils import is_zero_downtime, get_app_path
    import posixpath

    app_user = getattr(host_data, 'app_user', 'app')
    app_name = getattr(host_data, 'app_name', None)
    if not app_name:
        raise ValueError("app_name must be set on HostConfig")
    app_path = get_app_path(host_data)

    gunicorn_cfg = getattr(host_data, 'gunicorn_conf', None) or {}
    nginx_cfg = getattr(host_data, 'nginx_conf', None) or {}

    # Derive working directory from manage_py_path.
    # "manage.py" → "" (release root), "bostad/manage.py" → "bostad"
    manage_py_path = getattr(host_data, 'manage_py_path', 'manage.py')
    manage_subdir = posixpath.dirname(manage_py_path)

    # Derive WSGI module: check gunicorn_conf, then Django's WSGI_APPLICATION,
    # then fall back to {app_name}.wsgi:application.
    wsgi_module = gunicorn_cfg.get("wsgi_module")
    if not wsgi_module:
        try:
            from django.conf import settings as django_settings
            wsgi_app = getattr(django_settings, 'WSGI_APPLICATION', None)
            if wsgi_app:
                # "bostad.wsgi.application" → "bostad.wsgi:application"
                parts = wsgi_app.rsplit('.', 1)
                wsgi_module = f"{parts[0]}:{parts[1]}" if len(parts) == 2 else wsgi_app
        except Exception:
            pass
    if not wsgi_module:
        wsgi_module = f"{app_name}.wsgi:application"

    if is_zero_downtime(host_data):
        static_path = f"{app_path}/shared/staticfiles"
        media_path = f"{app_path}/shared/media"
    else:
        static_path = f"{app_path}/staticfiles"
        media_path = f"{app_path}/media"

    # Derive SSL cert paths from domains if available
    domains = getattr(host_data, 'domains', None) or []
    ssl_identifier = None
    if domains:
        first_domain = domains[0]
        if isinstance(first_domain, dict):
            ssl_identifier = first_domain.get('identifier')
        elif hasattr(first_domain, 'identifier'):
            ssl_identifier = first_domain.identifier

    # Derive server_name from domains or nginx_conf
    server_name = nginx_cfg.get("server_name")
    if not server_name and ssl_identifier:
        server_name = ssl_identifier
    if not server_name:
        app_hostname = getattr(host_data, 'app_hostname', None)
        server_name = app_hostname or "_"

    ctx = {
        "project_name": app_name,
        "app_user": app_user,
        "app_path": app_path,
        "manage_subdir": manage_subdir,
        # gunicorn
        "workers": gunicorn_cfg.get("workers", 2),
        "timeout": gunicorn_cfg.get("timeout", 30),
        "umask": gunicorn_cfg.get("umask", "002"),
        "wsgi_module": wsgi_module,
        "health_check_url": gunicorn_cfg.get("health_check_url"),
        # nginx
        "server_name": server_name,
        "listen": nginx_cfg.get("listen", "80 default_server"),
        "client_max_body_size": nginx_cfg.get("client_max_body_size", "10M"),
        "static_path": static_path,
        "media_path": media_path,
    }

    if ssl_identifier:
        ctx["ssl_certificate"] = f"/home/{app_user}/.ssl/{ssl_identifier}.crt"
        ctx["ssl_certificate_key"] = f"/home/{app_user}/.ssl/{ssl_identifier}.key"

    return ctx
