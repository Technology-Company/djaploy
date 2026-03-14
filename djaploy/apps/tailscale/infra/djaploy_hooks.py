"""
Tailscale hooks for djaploy.

Handles Tailscale VPN installation, authentication, and certificate generation.
"""

from djaploy.hooks import deploy_hook


@deploy_hook("configure")
def configure_tailscale(host_data, project_config):
    """Install and authenticate Tailscale."""
    from pyinfra import host
    from pyinfra.facts.deb import DebPackage
    from pyinfra.operations import server

    auth_key = getattr(host_data, 'tailscale_auth_key', None)
    if not auth_key:
        return  # Skip if no auth key configured

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


def _generate_tailscale_certs(host_data, project_config):
    """Generate Tailscale certificates for configured domains."""
    from pyinfra.operations import server, files

    domains = getattr(host_data, 'domains', [])
    if not domains:
        return

    has_tailscale_certs = any(
        d.get('__class__') == 'TailscaleDnsCertificate'
        if isinstance(d, dict) else
        getattr(d, '__class__', type(d)).__name__ == 'TailscaleDnsCertificate'
        for d in domains
    )
    if not has_tailscale_certs:
        return

    app_user = getattr(host_data, 'app_user', None) or project_config.app_user
    ssl_dir = f'/home/{app_user}/.ssl'

    files.directory(
        name="Create SSL certificates directory",
        path=ssl_dir,
        user=app_user,
        group=app_user,
        _sudo=True,
    )

    for domain_conf in domains:
        if isinstance(domain_conf, dict):
            is_tailscale = domain_conf.get('__class__') == 'TailscaleDnsCertificate'
            identifier = domain_conf.get('identifier')
        else:
            is_tailscale = type(domain_conf).__name__ == 'TailscaleDnsCertificate'
            identifier = getattr(domain_conf, 'identifier', None)

        if is_tailscale and identifier:
            server.shell(
                name=f"Generate Tailscale certificate for {identifier}",
                commands=[
                    f'tailscale cert {identifier}',
                ],
                _sudo=True,
                _chdir=ssl_dir,
            )


@deploy_hook("deploy:configure")
def deploy_tailscale_certificates(host_data, project_config, artifact_path):
    """Generate Tailscale certificates during deploy."""
    _generate_tailscale_certs(host_data, project_config)


@deploy_hook("sync_certs")
def sync_tailscale_certificates(host_data, project_config):
    """Generate Tailscale certificates during sync_certs."""
    _generate_tailscale_certs(host_data, project_config)
