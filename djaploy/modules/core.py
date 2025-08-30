"""
Core deployment module for djaploy
"""

import os
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
    
    def configure_server(self, host_data: Dict[str, Any], project_config: Dict[str, Any]):
        """Configure basic server requirements"""
        
        # Get app_user from host data or fallback to project config
        app_user = host_data.get('app_user') or project_config["app_user"]
        
        # Create application user
        server.user(
            name="Create application user",
            user=app_user,
            shell="/bin/bash",
            create_home=True,
            _sudo=True,
        )
        
        # Update apt repositories
        apt.update(
            name="Update apt repositories",
            _sudo=True,
        )
        
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
    
    def _install_python(self, host_data: Dict[str, Any], project_config: Dict[str, Any]):
        """Install Python based on configuration"""
        
        python_version = project_config["python_version"]
        
        if project_config.get("python_compile", False):
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
        
        # Get app_user from host data or fallback to project config
        app_user = host_data.get('app_user') or project_config["app_user"]
        ssh_user = host_data.get('ssh_user', 'deploy')
        # Use host-specific project name if available, otherwise use global project name
        app_name = host_data.get('project_name', project_config["project_name"])
        app_path = f"/home/{app_user}/apps/{app_name}"
        
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
        
        # Deploy configuration files if specified
        server.shell(
            name="Put deploy files (NGINX, systemd) in place on remote",
            commands=[
                f"cp -r {settings.DJAPLOY_CONFIG_DIR}/deploy_files/{host_data.env}/* /",
            ],
            _sudo=True,
        )

        server.shell(
            name="Clear default NGINX site and enable application sites",
            commands=[
                "rm -f /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default",
                "ln -fs /etc/nginx/sites-available/* /etc/nginx/sites-enabled/",
            ],
            _sudo=True,
        )
        
        # Generate SSL certificates if enabled
        if getattr(host_data, 'pregenerate_certificates', False):
            self._generate_ssl_certificates(host_data, app_user)
        
        # Install dependencies and run migrations
        self._install_dependencies(app_user, app_path, project_config)
        self._run_migrations(app_user, app_path, project_config)
        self._collect_static(app_user, app_path, project_config)
    
    def _install_dependencies(self, app_user: str, app_path: str, project_config: Dict[str, Any]):
        """Install Python dependencies using Poetry"""
        
        server.shell(
            name="Install Python dependencies",
            commands=[
                f"/home/{app_user}/.local/bin/poetry install --without dev",
            ],
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
            _chdir=app_path,
        )
    
    def _run_migrations(self, app_user: str, app_path: str, project_config: Dict[str, Any]):
        """Run Django database migrations"""
        
        manage_py = self._get_manage_py_path(app_path, project_config)
        if manage_py:
            server.shell(
                name="Run database migrations",
                commands=[
                    f"/home/{app_user}/.local/bin/poetry run python {manage_py} migrate --noinput",
                ],
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
                _chdir=app_path,
            )
    
    def _collect_static(self, app_user: str, app_path: str, project_config: Dict[str, Any]):
        """Collect static files"""
        
        manage_py = self._get_manage_py_path(app_path, project_config)
        if manage_py:
            server.shell(
                name="Collect static files",
                commands=[
                    f"/home/{app_user}/.local/bin/poetry run python {manage_py} collectstatic --noinput --clear",
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
            # Handle different domain formats (string or dict)
            if isinstance(domain, dict):
                domain_name = domain.get('identifier', domain.get('name', 'localhost'))
                alt_names = domain.get('domains', [domain_name])
            else:
                domain_name = str(domain)
                alt_names = [domain_name]
            
            # Create certificate and key paths
            cert_path = f"/home/{app_user}/.ssl/{domain_name}.crt"
            key_path = f"/home/{app_user}/.ssl/{domain_name}.key"
            
            # Generate self-signed certificate
            server.shell(
                name=f"Generate self-signed SSL certificate for {domain_name}",
                commands=[
                    f"openssl req -x509 -newkey rsa:4096 -keyout {key_path} -out {cert_path} "
                    f"-days 365 -nodes -subj '/CN={domain_name}' "
                    f"-addext 'subjectAltName=DNS:{',DNS:'.join(alt_names)}'",
                    f"chown {app_user}:{app_user} {cert_path} {key_path}",
                    f"chmod 600 {key_path}",
                    f"chmod 644 {cert_path}",
                ],
                _sudo=True,
            )

    def _get_manage_py_path(self, app_path: str, project_config: Dict[str, Any]) -> str:
        """Get the manage.py path from config"""
        
        if project_config.get("manage_py_path"):
            return str(project_config["manage_py_path"])
        
        return None
    
    def get_required_packages(self) -> List[str]:
        """Get required system packages"""
        return ["curl", "wget", "build-essential"]


# Make the module class available for the loader
Module = CoreModule