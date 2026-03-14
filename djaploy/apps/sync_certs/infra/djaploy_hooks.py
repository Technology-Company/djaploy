"""
Certificate synchronization hooks for djaploy.

Syncs SSL certificates from 1Password to servers during deployment.
"""

from djaploy.hooks import deploy_hook


def _sync_certificate(cert, host_data):
    """Sync a single certificate to the server."""
    from pyinfra.operations import files
    from djaploy.certificates import OpFilePath

    if isinstance(cert, dict):
        if '__dict__' in cert:
            cert_data = cert['__dict__']
        else:
            cert_data = cert

        cert_class = cert_data.get('__class__', '')
        if cert_class == 'TailscaleDnsCertificate':
            print("Skipping Tailscale certificate (managed by tailscale cert)")
            return

        cert_identifier = cert_data.get('identifier')

        crt_file = cert_data.get('cert_file') or cert_data.get('op_crt')
        key_file = cert_data.get('key_file') or cert_data.get('op_key')

        if not cert_identifier or not crt_file or not key_file:
            print("Skipping invalid certificate: missing identifier or file paths")
            return

        # If we have op_crt/op_key instead of downloaded files, download them
        if not cert_data.get('cert_file'):
            try:
                crt_file = OpFilePath(str(crt_file))
                key_file = OpFilePath(str(key_file))
            except Exception as e:
                print(f"Failed to download certificate {cert_identifier}: {e}")
                return
    else:
        # Certificate is an object with methods
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
                _sudo=True,
            )


def _reload_ssl_services(host_data):
    """Reload services that use SSL certificates."""
    from pyinfra.operations import systemd

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


@deploy_hook("deploy")
def sync_certificates(host_data, project_config, artifact_path):
    """Main certificate synchronization operation."""
    from pyinfra.operations import files
    from djaploy.certificates import discover_certificates, OpFilePath

    # Get certificates configured for this specific host
    host_domains = (
        host_data.get('domains', [])
        if isinstance(host_data, dict)
        else getattr(host_data, 'domains', [])
    )

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
        _sync_certificate(cert, host_data)

    # Reload services that use certificates
    _reload_ssl_services(host_data)
