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
        """Deploy systemd service files and reload daemon"""

        # Systemd files are provided by the project in deploy_files
        # No need to generate them here

        # Reload systemd daemon to pick up any new service files
        systemd.daemon_reload(
            name="Reload systemd daemon",
            _sudo=True,
        )

    def post_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Start/restart services after all modules have deployed (migrations complete)"""

        zero_downtime = getattr(project_config, 'deployment_strategy', 'in_place') == 'zero_downtime'

        for service in getattr(host_data, "services", []):
            if zero_downtime:
                # Restart the service so the new process resolves the current/
                # symlink to the new release directory.  A simple reload (USR2)
                # would fork a new master that inherits the old master's working
                # directory *inode*, meaning it would still load code from the
                # previous release even though the symlink has been swapped.
                systemd.service(
                    name=f"Restart and enable {service} (zero-downtime)",
                    service=service,
                    running=True,
                    enabled=True,
                    restarted=True,
                    _sudo=True,
                )
            else:
                systemd.service(
                    name=f"Restart and enable {service}",
                    service=service,
                    running=True,
                    enabled=True,
                    restarted=True,
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
    
    def rollback(self, host_data: Dict[str, Any], project_config: Any, release: str = None):
        """Restart services after a rollback so they pick up the rolled-back code"""
        zero_downtime = getattr(project_config, 'deployment_strategy', 'in_place') == 'zero_downtime'
        for service in getattr(host_data, "services", []):
            if zero_downtime:
                systemd.service(
                    name=f"Restart {service} after rollback",
                    service=service,
                    restarted=True,
                    _sudo=True,
                )
            else:
                systemd.service(
                    name=f"Restart {service} after rollback",
                    service=service,
                    restarted=True,
                    _sudo=True,
                )

    def get_services(self) -> List[str]:
        """Get services managed by this module"""
        # Return empty as services are project-specific
        return []


# Make the module class available for the loader
Module = SystemdModule