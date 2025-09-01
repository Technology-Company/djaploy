"""
Systemd module for djaploy
"""

from pathlib import Path
from typing import Dict, Any, List

from pyinfra import host
from pyinfra.operations import files, systemd

from .base import BaseModule


class SystemdModule(BaseModule):
    """Module for managing systemd services"""
    
    name = "systemd"
    description = "Systemd service configuration and management"
    version = "0.1.0"
    
    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Configure systemd for the application"""
        # Configuration happens during deploy when we have the service files
        pass
    
    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Deploy systemd service configurations"""
        
        # Systemd files are provided by the project in deploy_files
        # No need to generate them here
        
        # Reload systemd daemon to pick up any new service files
        systemd.daemon_reload(
            name="Reload systemd daemon",
            _sudo=True,
        )
        
        # Start and enable services
        for service in getattr(host_data, "services", []):
            systemd.service(
                name=f"Start and enable {service}",
                service=service,
                running=True,
                enabled=True,
                restarted=True,  # Restart on deploy
                _sudo=True,
            )
        
        # Start and enable timer services
        for timer in getattr(host_data, "timer_services", []):
            systemd.service(
                name=f"Start and enable {timer}.timer",
                service=f"{timer}.timer",
                running=True,
                enabled=True,
                _sudo=True,
            )
    
    def get_services(self) -> List[str]:
        """Get services managed by this module"""
        # Return empty as services are project-specific
        return []


# Make the module class available for the loader
Module = SystemdModule