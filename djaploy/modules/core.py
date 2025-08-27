"""
Core deployment module for djaploy
"""

import os
from pathlib import Path
from typing import Dict, Any, List

from pyinfra import host
from pyinfra.operations import apt, server, pip, files
from pyinfra.facts.server import Which

from .base import BaseModule


class CoreModule(BaseModule):
    """Core module for basic server setup and deployment"""
    
    name = "core"
    description = "Core server configuration and deployment"
    version = "0.1.0"
    
    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Configure basic server requirements"""
        
        # Create application user
        server.user(
            name="Create application user",
            user=host_data["app_user"],
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
            _sudo_user=host_data["app_user"],
            _use_sudo_login=True,
        )
        
        # Install basic packages
        apt.packages(
            name="Install basic packages",
            packages=["git", "curl", "wget", "build-essential"],
            _sudo=True,
        )
    
    def _install_python(self, host_data: Dict[str, Any], project_config: Any):
        """Install Python based on configuration"""
        
        python_version = project_config.python_version
        
        if project_config.python_compile:
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
    
    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Deploy the application"""
        
        app_user = host_data["app_user"]
        ssh_user = host_data["ssh_user"]
        app_name = project_config.project_name
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
        if project_config.deploy_files_dir:
            deploy_files_path = Path(app_path) / project_config.deploy_files_dir / host_data["env"]
            if deploy_files_path.exists():
                server.shell(
                    name="Deploy configuration files",
                    commands=[
                        f"cp -r {deploy_files_path}/* /",
                    ],
                    _sudo=True,
                )
        
        # Install dependencies and run migrations
        self._install_dependencies(app_user, app_path, project_config)
        self._run_migrations(app_user, app_path, project_config)
        self._collect_static(app_user, app_path, project_config)
    
    def _install_dependencies(self, app_user: str, app_path: str, project_config: Any):
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
    
    def _run_migrations(self, app_user: str, app_path: str, project_config: Any):
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
    
    def _collect_static(self, app_user: str, app_path: str, project_config: Any):
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
    
    def _get_manage_py_path(self, app_path: str, project_config: Any) -> str:
        """Get the manage.py path from config"""
        
        if hasattr(project_config, 'manage_py_path') and project_config.manage_py_path:
            return str(project_config.manage_py_path)
        
        return None
    
    def get_required_packages(self) -> List[str]:
        """Get required system packages"""
        return ["curl", "wget", "build-essential"]


# Make the module class available for the loader
Module = CoreModule