"""
Core deployment module for djaploy
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from django.conf import settings
from pyinfra import host
from pyinfra.operations import apt, server, pip, files
from pyinfra.facts.server import Which

from .base import BaseModule


class CoreModule(BaseModule):
    """Core module for basic server setup and deployment"""

    name = "core"
    description = "Core server configuration and deployment"
    version = "0.1.0"

    def get_required_imports(self) -> List[str]:
        """Get required import statements for this module"""
        return [
            "from pyinfra import host",
            "from pyinfra.operations import apt, server, pip, files",
            "from pyinfra.facts.server import Which",
            "from pathlib import Path",
        ]

    def _is_zero_downtime(self, project_config) -> bool:
        return getattr(project_config, 'deployment_strategy', 'in_place') == 'zero_downtime'

    def _get_app_path(self, host_data, project_config) -> str:
        app_user = getattr(host_data, 'app_user', None) or project_config.app_user
        app_name = getattr(host_data, 'project_name', project_config.project_name)
        return f"/home/{app_user}/apps/{app_name}"

    def configure_server(self, host_data: Dict[str, Any], project_config: Dict[str, Any]):
        """Configure basic server requirements"""

        # Get app_user from host data or fallback to project config
        app_user = getattr(host_data, 'app_user', None) or project_config.app_user
        ssh_user = getattr(host_data, 'ssh_user')

        # Create application user
        server.user(
            name="Create application user",
            user=app_user,
            shell="/bin/bash",
            create_home=True,
            _sudo=True,
        )

        # For zero-downtime deploys, gunicorn owns the socket instead of systemd.
        # Add www-data (nginx) to the app user's group so it can access
        # the gunicorn-owned unix socket via group permissions.
        if self._is_zero_downtime(project_config):
            server.shell(
                name="Add www-data to app user group",
                commands=[f"usermod -aG {app_user} www-data"],
                _sudo=True,
            )

        # Update apt repositories
        apt.update(
            name="Update apt repositories",
            _sudo=True,
        )

        # Configure ownership for HTTP challenge operations (Let's Encrypt)
        self._configure_http_challenge_sudo(ssh_user, project_config)

        # Install Python
        self._install_python(host_data, project_config)

        # Install Poetry
        pip.packages(
            name="Install poetry",
            packages=["poetry"],
            extra_install_args="--break-system-packages",
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
        )

        # Install basic packages
        apt.packages(
            name="Install basic packages",
            packages=["git", "curl", "wget", "build-essential"],
            _sudo=True,
        )

        # Create external database directory if configured
        db_dir = getattr(project_config, 'db_dir', None)
        if db_dir:
            resolved_db_dir = project_config.resolve_db_dir(app_user)
            parent_dir = str(Path(resolved_db_dir).parent)
            for directory in [parent_dir, resolved_db_dir]:
                files.directory(
                    name=f"Create {directory}",
                    path=directory,
                    user=app_user,
                    group=app_user,
                    _sudo=True,
                )

        # Set up zero-downtime directory structure if configured
        if self._is_zero_downtime(project_config):
            self._configure_zero_downtime_dirs(host_data, project_config)

    def _configure_zero_downtime_dirs(self, host_data, project_config):
        """Create the releases/, shared/, current directory structure"""
        app_user = getattr(host_data, 'app_user', None) or project_config.app_user
        app_path = self._get_app_path(host_data, project_config)

        apps_dir = f"/home/{app_user}/apps"
        for directory in [apps_dir, app_path]:
            files.directory(
                name=f"Create {directory}",
                path=directory,
                user=app_user,
                group=app_user,
                _sudo=True,
            )

        for subdir in ["releases", "shared"]:
            files.directory(
                name=f"Create {subdir} directory",
                path=f"{app_path}/{subdir}",
                user=app_user,
                group=app_user,
                _sudo=True,
            )

        # Create shared resource directories
        shared_resources = getattr(project_config, 'shared_resources', [])
        for resource in shared_resources:
            if not resource.startswith('.'):
                files.directory(
                    name=f"Create shared/{resource} directory",
                    path=f"{app_path}/shared/{resource}",
                    user=app_user,
                    group=app_user,
                    _sudo=True,
                )

    def _install_python(self, host_data: Dict[str, Any], project_config: Dict[str, Any]):
        """Install Python based on configuration"""

        python_version = project_config.python_version

        if getattr(project_config, 'python_compile', False):
            self._compile_python(python_version, host_data)
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

    def _compile_python(self, version: str, host_data: Dict[str, Any]):
        """Compile Python from source"""

        # Parse version into major.minor and look up full version
        # You can customize these or get from config
        major_minor = version

        # Map major.minor to full version (can be made configurable)
        version_map = {
            "3.11": "3.11.9",
            "3.12": "3.12.7",
            "3.13": "3.13.3",
        }

        full_version = version_map.get(major_minor, f"{major_minor}.0")

        python_download_url = f"https://www.python.org/ftp/python/{full_version}/Python-{full_version}.tar.xz"
        python_source_dir = f"/tmp/Python-{full_version}"
        python_install_path = f"/usr/local/bin/python{major_minor}"

        # Check if Python is already compiled and installed
        if host.get_fact(Which, python_install_path) is None:
            # Install build dependencies
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

            # Download Python source
            server.shell(
                name=f"Download Python {full_version} source",
                commands=[
                    f"wget -P /tmp {python_download_url}",
                    f"tar -xf /tmp/Python-{full_version}.tar.xz -C /tmp"
                ],
                _sudo=True,
            )

            # Configure and compile Python
            server.shell(
                name=f"Configure and compile Python {full_version}",
                commands=[
                    f"./configure --enable-optimizations --with-ensurepip=install",
                    "make -j$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))"  # use one less core for stability
                ],
                _chdir=python_source_dir,
                _sudo=True,
            )

            # Install Python using altinstall (doesn't override system python)
            server.shell(
                name=f"Install Python {full_version} using altinstall",
                commands=[
                    "make altinstall"
                ],
                _chdir=python_source_dir,
                _sudo=True,
            )

            # Clean up source files
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

    def deploy(self, host_data: Dict[str, Any], project_config: Dict[str, Any], artifact_path: Path):
        """Deploy the application"""
        if self._is_zero_downtime(project_config):
            self._deploy_zero_downtime(host_data, project_config, artifact_path)
        else:
            self._deploy_in_place(host_data, project_config, artifact_path)

    def post_deploy(self, host_data: Dict[str, Any], project_config: Dict[str, Any], artifact_path: Path):
        """Run migrations and collectstatic after all modules have deployed their files"""
        app_user = getattr(host_data, 'app_user', None) or project_config.app_user
        if self._is_zero_downtime(project_config):
            app_path = f"{self._get_app_path(host_data, project_config)}/current"
        else:
            app_path = self._get_app_path(host_data, project_config)

        self._run_migrations(app_user, app_path, project_config)
        self._collect_static(app_user, app_path, project_config)

    def rollback(self, host_data: Dict[str, Any], project_config: Dict[str, Any], release: str = None):
        """Roll back to a previous release by swapping the current symlink"""
        app_user = getattr(host_data, 'app_user', None) or project_config.app_user
        app_path = self._get_app_path(host_data, project_config)
        releases_path = f"{app_path}/releases"

        if release:
            server.shell(
                name=f"Roll back to release {release}",
                commands=[
                    f'test -d {releases_path}/{release} || (echo "Release {release} not found" && exit 1)',
                    f'ln -sfn {releases_path}/{release} {app_path}/current.tmp && mv -Tf {app_path}/current.tmp {app_path}/current',
                    f'echo "Rolled back to {release}"',
                ],
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
            )
        else:
            server.shell(
                name="Roll back to previous release",
                commands=[
                    f'PREV=$(cd {releases_path} && ls -1t | sed -n "2p") && '
                    f'test -n "$PREV" || (echo "No previous release to roll back to" && exit 1) && '
                    f'ln -sfn {releases_path}/$PREV {app_path}/current.tmp && mv -Tf {app_path}/current.tmp {app_path}/current && '
                    f'echo "Rolled back to $PREV"',
                ],
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
            )

    def _deploy_in_place(self, host_data: Dict[str, Any], project_config: Dict[str, Any], artifact_path: Path):
        """Deploy by overwriting in place (original behavior)"""

        # Get app_user from host data or fallback to project config
        app_user = getattr(host_data, 'app_user', None) or project_config.app_user
        ssh_user = getattr(host_data, 'ssh_user', 'deploy')
        app_path = self._get_app_path(host_data, project_config)

        # Create necessary directories
        files.directory(
            name="Create tars directory",
            path=f"/home/{ssh_user}/tars",
            _sudo=False,
        )

        files.directory(
            name="Create application directory",
            path=app_path,
            user=app_user,
            group=app_user,
            _sudo=True,
        )

        # Upload artifact
        artifact_filename = artifact_path.name
        files.put(
            name="Upload deployment artifact",
            src=str(artifact_path),
            dest=f"/home/{ssh_user}/tars/{artifact_filename}",
        )

        # Extract artifact
        server.shell(
            name="Extract artifact and set permissions",
            commands=[
                f"tar -C {app_path} -xf /home/{ssh_user}/tars/{artifact_filename}",
                f"chown -R {app_user}:{app_user} {app_path}",
            ],
            _sudo=True,
        )

        # Deploy configuration files
        self._deploy_config_files(host_data, project_config, app_path)

        # Generate SSL certificates if enabled
        if getattr(host_data, 'pregenerate_certificates', False):
            self._generate_ssl_certificates(host_data, app_user)

        # Install dependencies (migrations and collectstatic deferred to post_deploy)
        self._install_dependencies(app_user, app_path, project_config)

    def _deploy_zero_downtime(self, host_data: Dict[str, Any], project_config: Dict[str, Any], artifact_path: Path):
        """Deploy using release directories and symlink swap"""

        app_user = getattr(host_data, 'app_user', None) or project_config.app_user
        ssh_user = getattr(host_data, 'ssh_user', 'deploy')
        app_path = self._get_app_path(host_data, project_config)
        releases_path = f"{app_path}/releases"
        shared_path = f"{app_path}/shared"

        # Determine release name from artifact filename
        # Artifact names are like: project.abc1234.tar.gz or project.v1.2.0.tar.gz
        artifact_filename = artifact_path.name
        # Extract the ref part: "project.REF.tar.gz" -> "REF"
        parts = artifact_filename.rsplit('.tar.gz', 1)[0]  # remove .tar.gz
        ref = parts.split('.', 1)[1] if '.' in parts else parts  # remove project name prefix

        # For "local" deploys (no git ref), append a timestamp so each deploy
        # gets its own release directory and rollback history is preserved.
        if ref == "local":
            ref = f"local-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        release_name = f"app-{ref}"
        release_path = f"{releases_path}/{release_name}"

        # Create tars directory and upload artifact
        files.directory(
            name="Create tars directory",
            path=f"/home/{ssh_user}/tars",
            _sudo=False,
        )

        files.put(
            name="Upload deployment artifact",
            src=str(artifact_path),
            dest=f"/home/{ssh_user}/tars/{artifact_filename}",
        )

        # Create release directory and extract
        files.directory(
            name=f"Create release directory {release_name}",
            path=release_path,
            user=app_user,
            group=app_user,
            _sudo=True,
        )

        server.shell(
            name=f"Extract artifact into {release_name}",
            commands=[
                f"tar -C {release_path} -xf /home/{ssh_user}/tars/{artifact_filename}",
                f"chown -R {app_user}:{app_user} {release_path}",
            ],
            _sudo=True,
        )

        # Symlink shared resources into the release.
        # mkdir -p the parent dir first in case the artifact doesn't contain it
        # (e.g. shared_resources=["public/media"] needs releases/app-x/public/).
        shared_resources = getattr(project_config, 'shared_resources', [])
        if shared_resources:
            symlink_commands = []
            for resource in shared_resources:
                parent = str(Path(resource).parent)
                if parent and parent != '.':
                    symlink_commands.append(f"mkdir -p {release_path}/{parent}")
                symlink_commands.append(
                    f"ln -sfn {shared_path}/{resource} {release_path}/{resource}"
                )
            server.shell(
                name="Symlink shared resources into release",
                commands=symlink_commands,
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
            )

        # Deploy configuration files (nginx, systemd) from the release
        self._deploy_config_files(host_data, project_config, release_path)

        # Generate SSL certificates if enabled
        if getattr(host_data, 'pregenerate_certificates', False):
            self._generate_ssl_certificates(host_data, app_user)

        # Atomic symlink swap BEFORE installing deps so Poetry sees a stable
        # path (current/) and reuses the same virtualenv across releases.
        # Use mv -Tf for a truly atomic swap (ln -sfn unlinks then creates,
        # leaving a brief window where current doesn't exist).
        server.shell(
            name=f"Swap current symlink to {release_name}",
            commands=[
                f"ln -sfn {release_path} {app_path}/current.tmp && mv -Tf {app_path}/current.tmp {app_path}/current",
            ],
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
        )

        # Install deps via the stable current/ path.
        # Poetry keys virtualenvs by directory — using current/ means it
        # reuses the same venv across deploys (shared venv behavior).
        # Migrations and collectstatic are deferred to post_deploy so that
        # other modules (e.g. local_settings) can place config files first.
        current_path = f"{app_path}/current"
        self._install_dependencies(app_user, current_path, project_config)

        # Clean up old releases
        keep_releases = getattr(project_config, 'keep_releases', 5)
        server.shell(
            name=f"Clean up old releases (keeping {keep_releases})",
            commands=[
                f"cd {releases_path} && ls -1t | tail -n +{keep_releases + 1} | xargs -r rm -rf --",
            ],
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
        )

    def _deploy_config_files(self, host_data, project_config, app_path: str):
        """Deploy configuration files (nginx, systemd) from the artifact"""
        env_name = getattr(host_data, 'env', 'production')

        if getattr(project_config, 'djaploy_dir', None) and getattr(project_config, 'project_dir', None):
            djaploy_dir = Path(project_config.djaploy_dir)
            project_dir = Path(project_config.project_dir)
            try:
                config_rel_path = djaploy_dir.relative_to(project_dir.parent)
            except ValueError:
                config_rel_path = "infra"
        else:
            config_rel_path = "infra"

        deploy_files_path = f"{app_path}/{config_rel_path}/deploy_files/{env_name}"

        server.shell(
            name="Put deploy files (NGINX, systemd) in place on remote",
            commands=[
                f"if [ -d {deploy_files_path} ]; then cp -r {deploy_files_path}/* /; fi",
            ],
            _sudo=True,
        )

        server.shell(
            name="Clear default NGINX sites",
            commands=[
                "rm -f /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default",
            ],
            _sudo=True,
        )

        server.shell(
            name="Enable NGINX sites",
            commands=[
                "for f in /etc/nginx/sites-available/*; do [ -f \"$f\" ] && ln -fs \"$f\" /etc/nginx/sites-enabled/; done",
            ],
            _sudo=True,
        )

    def _install_dependencies(self, app_user: str, app_path: str, project_config: Dict[str, Any]):
        """Install Python dependencies using Poetry"""

        # Get core module configuration
        core_config = getattr(project_config, 'module_configs', {}).get("core", {})

        # Check Poetry-specific settings from module config
        poetry_no_root = core_config.get("poetry_no_root", True)  # Default to True for applications
        exclude_groups = core_config.get("exclude_groups", [])

        # Build Poetry command with appropriate flags
        poetry_cmd = f"/home/{app_user}/.local/bin/poetry install"

        if poetry_no_root:
            poetry_cmd += " --no-root"

        if exclude_groups:
            if isinstance(exclude_groups, str):
                exclude_groups = [exclude_groups]
            for group in exclude_groups:
                poetry_cmd += f" --without {group}"

        python_version = project_config.python_version

        commands = [
            # First configure Poetry to not use in-project virtualenvs on the server
            f"/home/{app_user}/.local/bin/poetry config virtualenvs.in-project false",
            # Ensure the virtualenv uses the configured Python version
            f"/home/{app_user}/.local/bin/poetry env use python{python_version}",
        ]

        # Optionally regenerate the lock file before installation
        poetry_lock_enabled = core_config.get("poetry_lock", False)
        poetry_lock_args = core_config.get("poetry_lock_args", None)

        if poetry_lock_enabled:
            poetry_bin = f"/home/{app_user}/.local/bin/poetry"
            if poetry_lock_args:
                lock_cmd = f"{poetry_bin} lock {poetry_lock_args}"
            else:
                # --no-update (Poetry 1.x) was renamed to --no-upgrade (Poetry 2.x)
                lock_cmd = (
                    f"{poetry_bin} lock --no-upgrade 2>/dev/null"
                    f" || {poetry_bin} lock --no-update"
                )
            commands.append(lock_cmd)

        # Finally install the dependencies
        commands.append(poetry_cmd)

        server.shell(
            name="Install Python dependencies",
            commands=commands,
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
            _chdir=app_path,
        )

    def _run_migrations(self, app_user: str, app_path: str, project_config: Dict[str, Any]):
        """Run Django database migrations"""

        manage_py = self._get_manage_py_path(app_path, project_config)
        if manage_py:
            # Get core module configuration
            core_config = getattr(project_config, 'module_configs', {}).get("core", {})

            # Get list of databases from module config
            databases = core_config.get("databases", ["default"])

            # Ensure databases is a list
            if isinstance(databases, str):
                databases = [databases]

            # Run migrations for each database
            migration_commands = []
            for db in databases:
                migration_commands.append(
                    f"/home/{app_user}/.local/bin/poetry run python {manage_py} migrate --database={db} --noinput"
                )

            server.shell(
                name="Run database migrations",
                commands=migration_commands,
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
                _chdir=app_path,
            )

    def _collect_static(self, app_user: str, app_path: str, project_config: Dict[str, Any]):
        """Collect static files"""

        manage_py = self._get_manage_py_path(app_path, project_config)
        if manage_py:
            # For zero-downtime, static files are in a shared directory symlinked
            # into each release. Don't use --clear so old hashed filenames survive
            # across releases (browsers may still reference them during the handoff).
            clear_flag = "" if self._is_zero_downtime(project_config) else " --clear"
            server.shell(
                name="Collect static files",
                commands=[
                    f"/home/{app_user}/.local/bin/poetry run python {manage_py} collectstatic --noinput{clear_flag}",
                ],
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
                _chdir=app_path,
            )

    def _generate_ssl_certificates(self, host_data, app_user: str):
        """Generate SSL certificates for testing/development purposes"""

        # Install openssl if not already installed
        apt.packages(
            name="Install OpenSSL for certificate generation",
            packages=["openssl"],
            _sudo=True,
        )

        # Create SSL directory
        files.directory(
            name="Create SSL directory",
            path=f"/home/{app_user}/.ssl",
            user=app_user,
            group=app_user,
            _sudo=True,
        )

        # Generate domains to create certificates for
        domains = getattr(host_data, 'domains', [])

        if not domains:
            # Default to app_hostname if no domains specified
            app_hostname = getattr(host_data, 'app_hostname', 'localhost')
            domains = [app_hostname]

        for domain in domains:
            # Handle different domain formats (string, dict, or certificate object)
            if hasattr(domain, 'domains') and hasattr(domain, 'identifier'):
                domain_name = domain.identifier if hasattr(domain, 'identifier') else str(domain.domains[0])
                alt_names = domain.domains if hasattr(domain, 'domains') else [domain_name]
            elif isinstance(domain, dict):
                # pyinfra serializes objects with __class__ and __dict__ keys
                inner = domain.get('__dict__', domain)
                domain_name = inner.get('identifier', inner.get('name', 'localhost'))
                alt_names = inner.get('domains', [domain_name])
            else:
                domain_name = str(domain)
                alt_names = [domain_name]

            # Create certificate and key paths
            cert_path = f"/home/{app_user}/.ssl/{domain_name}.crt"
            key_path = f"/home/{app_user}/.ssl/{domain_name}.key"

            # Generate self-signed certificate if it doesn't exist or is expired
            # openssl x509 -checkend 0 returns 1 if cert is expired
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

    def _get_manage_py_path(self, app_path: str, project_config: Dict[str, Any]) -> str:
        """Get the manage.py path from config"""

        if getattr(project_config, 'manage_py_path', None):
            return str(project_config.manage_py_path)

        return None

    def _configure_http_challenge_sudo(self, ssh_user: str, project_config: Dict[str, Any]):
        """Create ACME challenge directory with correct ownership for Let's Encrypt"""

        # Get webroot path from config or use default
        http_hook_config = getattr(project_config, 'module_configs', {}).get('http_hook', {})
        webroot = http_hook_config.get('webroot_path', '/var/www/challenges')

        # Create the webroot directory owned by ssh_user
        files.directory(
            name="Create ACME challenge directory",
            path=webroot,
            user=ssh_user,
            group='www-data',
            mode='775',
            _sudo=True,
        )

    def get_required_packages(self) -> List[str]:
        """Get required system packages"""
        return ["curl", "wget", "build-essential"]


# Make the module class available for the loader
Module = CoreModule
