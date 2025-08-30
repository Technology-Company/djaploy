"""
NGINX module for djaploy
"""

from pathlib import Path
from typing import Dict, Any, List

from pyinfra import host
from pyinfra.operations import apt, server, files, systemd

from .base import BaseModule


class NginxModule(BaseModule):
    """Module for managing NGINX web server"""
    
    name = "nginx"
    description = "NGINX web server configuration and management"
    version = "0.1.0"
    
    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Install and configure NGINX"""
        
        # Install NGINX
        apt.packages(
            name="Install NGINX",
            packages=["nginx"],
            _sudo=True,
        )
        
        # Create SSL directory if needed
        domains = getattr(host_data, 'domains', [])
        if domains:
            app_user = getattr(host_data, 'app_user', 'app')
            files.directory(
                name="Create SSL certificates directory",
                path=f"/home/{app_user}/.ssl",
                user=app_user,
                group=app_user,
                _sudo=True,
            )
    
    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Deploy NGINX configuration"""
        
        # Clear default sites
        server.shell(
            name="Clear default NGINX sites",
            commands=[
                "rm -f /etc/nginx/sites-available/default",
                "rm -f /etc/nginx/sites-enabled/default",
            ],
            _sudo=True,
        )
        
        # Deploy SSL certificates if configured
        domains = getattr(host_data, 'domains', [])
        app_user = getattr(host_data, 'app_user', 'app')
        
        for domain_conf in domains:
            if "cert_file" in domain_conf and "key_file" in domain_conf:
                files.put(
                    name=f"Deploy SSL certificate for {domain_conf['identifier']}",
                    src=domain_conf["cert_file"],
                    dest=f"/home/{app_user}/.ssl/{domain_conf['identifier']}.crt",
                    mode="644",
                    force=True,
                    _sudo=True,
                )
                files.put(
                    name=f"Deploy SSL key for {domain_conf['identifier']}",
                    src=domain_conf["key_file"],
                    dest=f"/home/{app_user}/.ssl/{domain_conf['identifier']}.key",
                    mode="644",
                    force=True,
                    _sudo=True,
                )
        
        # NGINX configurations are provided by the project in deploy_files
        # Just enable the sites that were copied
        server.shell(
            name="Enable NGINX sites",
            commands=[
                "ln -fs /etc/nginx/sites-available/* /etc/nginx/sites-enabled/",
            ],
            _sudo=True,
        )
        
        # Reload NGINX
        systemd.service(
            name="Reload NGINX",
            service="nginx",
            running=True,
            reloaded=True,
            enabled=True,
            _sudo=True,
        )
    
    def get_required_packages(self) -> List[str]:
        """Get required system packages"""
        return ["nginx"]
    
    def get_services(self) -> List[str]:
        """Get services managed by this module"""
        return ["nginx"]


# Make the module class available for the loader
Module = NginxModule