"""
Utility functions for the core djaploy app.

Extracted from CoreModule's private methods.  Every function imports pyinfra
inside its body so the module can be imported at discovery time without pyinfra
being available.
"""


def is_zero_downtime(host_data) -> bool:
    return getattr(host_data, 'deployment_strategy', 'zero_downtime') == 'zero_downtime'


def get_app_path(host_data) -> str:
    app_user = getattr(host_data, 'app_user', 'app')
    app_name = getattr(host_data, 'app_name', None)
    if not app_name:
        raise ValueError("app_name must be set on HostConfig")
    return f"/home/{app_user}/apps/{app_name}"


def get_core_config(host_data) -> dict:
    return getattr(host_data, 'core_conf', None) or {}


def install_python(host_data):
    """Install Python via apt or compile from source."""
    from pyinfra.operations import apt

    python_version = getattr(host_data, 'python_version', '3.11')
    python_compile = getattr(host_data, 'python_compile', False)

    if python_compile:
        compile_python(python_version, host_data)
    else:
        apt.packages(
            name=f"Install Python {python_version}",
            packages=[
                f"python{python_version}",
                f"python{python_version}-dev",
                f"python{python_version}-venv",
                "python3-pip",
            ],
            _sudo=True,
        )


def compile_python(version: str, host_data):
    """Compile Python from source with optimizations."""
    from pyinfra import host
    from pyinfra.operations import apt, server
    from pyinfra.facts.server import Which

    major_minor = version
    version_map = {
        "3.11": "3.11.9",
        "3.12": "3.12.7",
        "3.13": "3.13.3",
    }
    full_version = version_map.get(major_minor, f"{major_minor}.0")

    python_download_url = f"https://www.python.org/ftp/python/{full_version}/Python-{full_version}.tar.xz"
    python_source_dir = f"/tmp/Python-{full_version}"
    python_install_path = f"/usr/local/bin/python{major_minor}"

    if host.get_fact(Which, python_install_path) is None:
        apt.packages(
            name="Install Python build dependencies",
            packages=[
                'build-essential', 'zlib1g-dev', 'libncurses5-dev', 'libncursesw5-dev',
                'libgdbm-dev', 'libnss3-dev', 'libssl-dev', 'libreadline-dev',
                'libffi-dev', 'libsqlite3-dev', 'wget', 'curl', 'llvm',
                'xz-utils', 'tk-dev', 'libxml2-dev', 'libxmlsec1-dev', 'liblzma-dev',
                'libbz2-dev'
            ],
            _sudo=True,
        )

        server.shell(
            name=f"Download Python {full_version} source",
            commands=[
                f"wget -P /tmp {python_download_url}",
                f"tar -xf /tmp/Python-{full_version}.tar.xz -C /tmp"
            ],
            _sudo=True,
        )

        server.shell(
            name=f"Configure and compile Python {full_version}",
            commands=[
                "./configure --enable-optimizations --with-ensurepip=install",
                "make -j$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))"
            ],
            _chdir=python_source_dir,
            _sudo=True,
        )

        server.shell(
            name=f"Install Python {full_version} using altinstall",
            commands=["make altinstall"],
            _chdir=python_source_dir,
            _sudo=True,
        )

        server.shell(
            name=f"Clean up Python {full_version} source files",
            commands=[
                f"rm -f /tmp/Python-{full_version}.tar.xz",
                f"rm -rf {python_source_dir}"
            ],
            _sudo=True,
        )
    else:
        server.shell(
            name=f"Python {full_version} already installed at {python_install_path}",
            commands=[f"echo 'Python {full_version} already installed.'"],
            _sudo=False,
        )


def deploy_config_files(host_data, app_path: str):
    """Render and deploy config file templates (systemd, nginx) to the server."""
    from io import StringIO
    from pyinfra.operations import files
    from djaploy.infra.templates import (
        SYSTEMD_ZERO_DOWNTIME, SYSTEMD_IN_PLACE, NGINX_SITE,
        build_template_context,
    )

    app_name = getattr(host_data, 'app_name', None)
    if not app_name:
        raise ValueError("app_name must be set")
    ctx = build_template_context(host_data)

    # Select systemd template based on deployment strategy
    if is_zero_downtime(host_data):
        systemd_tpl = SYSTEMD_ZERO_DOWNTIME
    else:
        systemd_tpl = SYSTEMD_IN_PLACE

    files.template(
        name=f"Render {app_name} systemd service",
        src=StringIO(systemd_tpl),
        dest=f"/etc/systemd/system/{app_name}.service",
        _sudo=True,
        **ctx,
    )

    files.template(
        name=f"Render {app_name} nginx config",
        src=StringIO(NGINX_SITE),
        dest=f"/etc/nginx/sites-available/{app_name}",
        _sudo=True,
        **ctx,
    )


def install_dependencies(app_user: str, app_path: str, host_data):
    """Install Python dependencies using Poetry."""
    from pyinfra.operations import server

    core_config = get_core_config(host_data)

    poetry_no_root = core_config.get("poetry_no_root", True)
    exclude_groups = core_config.get("exclude_groups", [])

    poetry_cmd = f"/home/{app_user}/.local/bin/poetry install"

    if poetry_no_root:
        poetry_cmd += " --no-root"

    if exclude_groups:
        if isinstance(exclude_groups, str):
            exclude_groups = [exclude_groups]
        for group in exclude_groups:
            poetry_cmd += f" --without {group}"

    python_version = getattr(host_data, 'python_version', '3.11')
    poetry_bin = f"/home/{app_user}/.local/bin/poetry"
    poetry_lock_enabled = core_config.get("poetry_lock", False)
    poetry_lock_args = core_config.get("poetry_lock_args", None)

    if is_zero_downtime(host_data):
        # Hash poetry.lock + python version to create/reuse shared venvs.
        # Each release gets a .venv symlink pointing to the shared venv,
        # so current/.venv/bin/... always resolves correctly.
        app_name = getattr(host_data, 'app_name', None)
        if not app_name:
            raise ValueError("app_name must be set on HostConfig")
        base_path = f"/home/{app_user}/apps/{app_name}"
        shared_path = f"{base_path}/shared"
        releases_path = f"{base_path}/releases"

        commands = []

        # Poetry lock if configured (runs without venv)
        if poetry_lock_enabled:
            if poetry_lock_args:
                commands.append(f"{poetry_bin} lock {poetry_lock_args}")
            else:
                commands.append(
                    f"{poetry_bin} lock --no-upgrade 2>/dev/null"
                    f" || {poetry_bin} lock --no-update"
                )

        # Set HOME explicitly — sudo without login shell doesn't set it.
        home_prefix = f'HOME=/home/{app_user}'

        # Ensure poetry.lock exists (needed for hashing)
        commands.append(
            f'test -f poetry.lock || {home_prefix} {poetry_bin} lock'
        )

        # Compute lock hash, create shared venv if missing, symlink into release.
        # If the shared venv already exists, just symlink — no install needed.
        # NOTE: use $VAR not ${VAR} — pyinfra interprets {…} as format placeholders.
        commands.append(
            f'LOCK_HASH=$(sha256sum poetry.lock | cut -c1-12) && '
            f'VENV_DIR="{shared_path}/venv-$LOCK_HASH-py{python_version}" && '
            f'rm -rf .venv && '
            f'if [ ! -d "$VENV_DIR/bin" ]; then '
            f'python{python_version} -m venv "$VENV_DIR" && '
            f'ln -sfn "$VENV_DIR" .venv && '
            f'{home_prefix} POETRY_VIRTUALENVS_IN_PROJECT=true {poetry_cmd} && '
            f'for s in "$VENV_DIR"/bin/*; do '
            f'head -1 "$s" 2>/dev/null | grep -q python && '
            f'sed -i "1s|#\\!.*|#!$VENV_DIR/bin/python{python_version}|" "$s"; '
            f'done; '
            f'else '
            f'ln -sfn "$VENV_DIR" .venv; '
            f'fi'
        )

        # Clean up shared venvs no longer referenced by any release
        commands.append(
            f'for v in {shared_path}/venv-*-py*; do '
            f'[ -d "$v" ] || continue; '
            f'USED=false; '
            f'for r in {releases_path}/*/; do '
            f'[ "$(readlink "$r.venv" 2>/dev/null)" = "$v" ] && USED=true && break; '
            f'done; '
            f'$USED || rm -rf "$v"; '
            f'done'
        )

        # NOTE: _use_sudo_login=False here — sudo -i re-parses command
        # strings through the login shell, which breaks && chains and
        # shell variable expansion.  All binaries use absolute paths so
        # a login shell is not required.
        server.shell(
            name="Install Python dependencies",
            commands=commands,
            _sudo=True,
            _sudo_user=app_user,
            _chdir=app_path,
        )
        return

    # In-place deployment: use poetry's own venv management
    commands = [
        f"{poetry_bin} config virtualenvs.in-project false",
        f"{poetry_bin} env use python{python_version}",
    ]

    if poetry_lock_enabled:
        if poetry_lock_args:
            commands.append(f"{poetry_bin} lock {poetry_lock_args}")
        else:
            commands.append(
                f"{poetry_bin} lock --no-upgrade 2>/dev/null"
                f" || {poetry_bin} lock --no-update"
            )

    commands.append(poetry_cmd)

    server.shell(
        name="Install Python dependencies",
        commands=commands,
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
        _chdir=app_path,
    )


def run_migrations(app_user: str, app_path: str, host_data):
    """Run Django database migrations."""
    from pyinfra.operations import server

    manage_py = get_manage_py_path(host_data)
    if not manage_py:
        return

    core_config = get_core_config(host_data)
    databases = core_config.get("databases", ["default"])

    if isinstance(databases, str):
        databases = [databases]

    python_cmd = get_python_cmd(app_user, app_path, host_data)
    migration_commands = []
    for db in databases:
        migration_commands.append(
            f"{python_cmd} {manage_py} migrate --database={db} --noinput"
        )

    server.shell(
        name="Run database migrations",
        commands=migration_commands,
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
        _chdir=app_path,
    )


def collect_static(app_user: str, app_path: str, host_data):
    """Collect static files."""
    from pyinfra.operations import server

    manage_py = get_manage_py_path(host_data)
    if not manage_py:
        return

    python_cmd = get_python_cmd(app_user, app_path, host_data)
    clear_flag = "" if is_zero_downtime(host_data) else " --clear"
    server.shell(
        name="Collect static files",
        commands=[
            f"{python_cmd} {manage_py} collectstatic --noinput{clear_flag}",
        ],
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
        _chdir=app_path,
    )


def generate_ssl_certificates(host_data, app_user: str):
    """Generate self-signed SSL certificates for testing/development."""
    from pyinfra.operations import apt, server, files

    apt.packages(
        name="Install OpenSSL for certificate generation",
        packages=["openssl"],
        _sudo=True,
    )

    files.directory(
        name="Create SSL directory",
        path=f"/home/{app_user}/.ssl",
        user=app_user,
        group=app_user,
        _sudo=True,
    )

    domains = getattr(host_data, 'domains', [])
    if not domains:
        app_hostname = getattr(host_data, 'app_hostname', 'localhost')
        domains = [app_hostname]

    for domain in domains:
        if hasattr(domain, 'domains') and hasattr(domain, 'identifier'):
            domain_name = domain.identifier if hasattr(domain, 'identifier') else str(domain.domains[0])
            alt_names = domain.domains if hasattr(domain, 'domains') else [domain_name]
        elif isinstance(domain, dict):
            inner = domain.get('__dict__', domain)
            domain_name = inner.get('identifier', inner.get('name', 'localhost'))
            alt_names = inner.get('domains', [domain_name])
        else:
            domain_name = str(domain)
            alt_names = [domain_name]

        cert_path = f"/home/{app_user}/.ssl/{domain_name}.crt"
        key_path = f"/home/{app_user}/.ssl/{domain_name}.key"

        server.shell(
            name=f"Generate self-signed SSL certificate for {domain_name}",
            commands=[
                f"if [ ! -f {cert_path} ] || ! openssl x509 -checkend 0 -noout -in {cert_path} 2>/dev/null; then "
                f"openssl req -x509 -newkey rsa:4096 -keyout {key_path} -out {cert_path} "
                f"-days 365 -nodes -subj '/CN={domain_name}' "
                f"-addext 'subjectAltName=DNS:{',DNS:'.join(alt_names)}' && "
                f"chown {app_user}:{app_user} {cert_path} {key_path} && "
                f"chmod 600 {key_path} && "
                f"chmod 644 {cert_path}; "
                f"else echo 'Valid certificate exists at {cert_path}, skipping'; fi",
            ],
            _sudo=True,
        )


def get_manage_py_path(host_data) -> str:
    """Get the manage.py path from host_data."""
    return getattr(host_data, 'manage_py_path', 'manage.py')


def get_python_cmd(app_user: str, app_path: str, host_data) -> str:
    """Get the python command prefix for running management commands.

    For zero-downtime deploys, uses the release's .venv/bin/python directly.
    For in-place deploys, uses 'poetry run python'.
    """
    if is_zero_downtime(host_data):
        return f"{app_path}/.venv/bin/python"
    return f"/home/{app_user}/.local/bin/poetry run python"


def configure_http_challenge_sudo(ssh_user: str, host_data):
    """Create ACME challenge directory with correct ownership for Let's Encrypt."""
    from pyinfra.operations import files

    http_hook_config = getattr(host_data, 'http_hook_conf', None) or {}
    webroot = http_hook_config.get('webroot_path', '/var/www/challenges')

    files.directory(
        name="Create ACME challenge directory",
        path=webroot,
        user=ssh_user,
        group='www-data',
        mode='775',
        _sudo=True,
    )
