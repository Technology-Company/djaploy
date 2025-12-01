"""
Tailscale module for djaploy - manages Tailscale VPN and certificate generation
"""
from pathlib import Path
from typing import Dict, Any, List

from pyinfra import host
from pyinfra.facts.deb import DebPackage
from pyinfra.operations import server, files

from .base import BaseModule


class TailscaleModule(BaseModule):
    """Module for managing Tailscale VPN installation, authentication, and certificate generation"""

    name = "tailscale"
    description = "Tailscale VPN and certificate management"
    version = "0.1.0"

    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Install and authenticate Tailscale"""

        # Check if tailscale_auth_key is provided
        auth_key = getattr(host_data, 'tailscale_auth_key', None)
        if not auth_key:
            return  # Skip if no auth key configured

        app_user = getattr(host_data, 'app_user', project_config.app_user)

        # Install tailscale if not present
        if host.get_fact(DebPackage, 'tailscale') is None:
            server.shell(
                name="Install Tailscale",
                commands=[
                    'curl -fsSL https://tailscale.com/install.sh | sh'
                ],
                _sudo=True,
            )

        # Authenticate with Tailscale
        server.shell(
            name="Authenticate Tailscale",
            commands=[
                f'tailscale up --authkey {auth_key}'
            ],
            _sudo=True,
        )

    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Generate Tailscale certificates for configured domains"""

        # Import here to avoid circular imports
        from djaploy.certificates import TailscaleDnsCertificate

        domains = getattr(host_data, 'domains', [])
        if not domains:
            return

        app_user = getattr(host_data, 'app_user', project_config.app_user)
        ssl_dir = f'/home/{app_user}/.ssl'

        # Generate certificates for Tailscale domains
        for domain_conf in domains:
            if isinstance(domain_conf, TailscaleDnsCertificate):
                server.shell(
                    name=f"Generate Tailscale certificate for {domain_conf.identifier}",
                    commands=[
                        f'tailscale cert {domain_conf.identifier}',
                    ],
                    _sudo=True,
                    _chdir=ssl_dir,
                )

    def sync_certificates(self, host_data: Dict[str, Any], project_config: Any):
        """
        Sync/renew Tailscale certificates.
        Called by sync_certs management command.
        """
        # Import here to avoid circular imports
        from djaploy.certificates import TailscaleDnsCertificate

        domains = getattr(host_data, 'domains', [])
        if not domains:
            return

        app_user = getattr(host_data, 'app_user', project_config.app_user)
        ssl_dir = f'/home/{app_user}/.ssl'

        # Regenerate certificates for Tailscale domains
        for domain_conf in domains:
            if isinstance(domain_conf, TailscaleDnsCertificate):
                server.shell(
                    name=f"Renew Tailscale certificate for {domain_conf.identifier}",
                    commands=[
                        f'tailscale cert {domain_conf.identifier}',
                    ],
                    _sudo=True,
                    _chdir=ssl_dir,
                )


# Make the module class available for the loader
Module = TailscaleModule