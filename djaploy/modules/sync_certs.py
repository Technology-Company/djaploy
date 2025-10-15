"""
Certificate synchronization module for djaploy
"""

from pathlib import Path
from typing import Dict, Any

from pyinfra import host
from pyinfra.operations import files, systemd

from .base import BaseModule
from ..certificates import discover_certificates


class SyncCertsModule(BaseModule):
    """Module specifically for syncing certificates from 1Password to servers"""
    
    name = "sync_certs"
    description = "Synchronize SSL certificates from 1Password to servers"
    version = "0.1.0"
    
    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Not used for sync operations"""
        pass
    
    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Main certificate synchronization operation"""

        # Get certificates configured for this specific host
        host_domains = host_data.get('domains', []) if isinstance(host_data, dict) else getattr(host_data, 'domains', [])

        if not host_domains:
            return

        # Ensure SSL directory exists
        app_user = getattr(host_data, 'app_user', 'deploy')
        files.directory(
            name="Create SSL certificates directory",
            path=f"/home/{app_user}/.ssl",
            user=app_user,
            group=app_user,
            _sudo=True,
        )

        # Sync only the certificates configured for this host
        for cert in host_domains:
            self._sync_certificate(cert, host_data)

        # Reload services that use certificates
        self._reload_ssl_services(host_data)
    
    def _discover_certificates(self, project_config: Any):
        """Discover certificates from project configuration"""
        
        # project_config should be a DjaployConfig object
        if not hasattr(project_config, 'djaploy_dir'):
            return []
        
        djaploy_path = Path(project_config.djaploy_dir)
        certificates_path = djaploy_path / "certificates.py"
        
        if certificates_path.exists():
            return discover_certificates(str(certificates_path))
        
        return []
    
    def _sync_certificate(self, cert, host_data: Dict[str, Any]):
        """Sync a single certificate to the server"""
        
        cert_identifier = cert.identifier
        crt_file, key_file = cert.download_cert(download_key=True)
        app_user = getattr(host_data, 'app_user', 'deploy')
        
        # Upload certificate files with secure permissions
        for file_type, file_to_copy in [('crt', crt_file), ('key', key_file)]:
            if file_to_copy is not None:
                files.put(
                    name=f"Upload {file_type} for {cert_identifier} to remote server",
                    src=file_to_copy,
                    dest=f"/home/{app_user}/.ssl/{cert_identifier}.{file_type}",
                    mode="400",  # Secure permissions
                    user="www-data",  # NGINX user typically
                    group="www-data",
                    _sudo=True
                )
    
    def _reload_ssl_services(self, host_data: Dict[str, Any]):
        """Reload services that use SSL certificates"""
        
        # Default services that use SSL
        ssl_services = ["nginx"]
        
        # Only reload services that are actually configured for this host
        host_services = getattr(host_data, 'services', [])
        for svc in ssl_services:
            if svc in host_services:
                systemd.service(
                    name=f"Reload {svc} service after certificate sync",
                    service=svc,
                    running=True,
                    reloaded=True,
                    enabled=True,
                    _sudo=True,
                )


# Make the module class available for the loader
Module = SyncCertsModule