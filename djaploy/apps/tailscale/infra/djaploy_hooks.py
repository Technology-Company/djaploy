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

    auth_key = host_data.get('tailscale_auth_key')
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


@deploy_hook("deploy")
def deploy_tailscale_certificates(host_data, project_config, artifact_path):
    """Generate Tailscale certificates for configured domains."""
    from pyinfra.operations import server, files

    domains = host_data.get('domains', [])
    if not domains:
        return

    # Check if any Tailscale certificates are configured
    has_tailscale_certs = any(
        d.get('__class__') == 'TailscaleDnsCertificate' for d in domains
    )
    if not has_tailscale_certs:
        return

    app_user = host_data.get('app_user') or project_config.app_user
    ssl_dir = f'/home/{app_user}/.ssl'

    # Ensure SSL directory exists
    files.directory(
        name="Create SSL certificates directory",
        path=ssl_dir,
        user=app_user,
        group=app_user,
        _sudo=True,
    )

    # Generate certificates for Tailscale domains
    for domain_conf in domains:
        if domain_conf.get('__class__') == 'TailscaleDnsCertificate':
            identifier = domain_conf.get('identifier')
            server.shell(
                name=f"Generate Tailscale certificate for {identifier}",
                commands=[
                    f'tailscale cert {identifier}',
                ],
                _sudo=True,
                _chdir=ssl_dir,
            )
