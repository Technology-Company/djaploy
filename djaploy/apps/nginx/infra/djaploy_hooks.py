"""
Nginx hooks for djaploy.

Installs, configures, and reloads NGINX during deployment lifecycle.
"""

from djaploy.hooks import deploy_hook


@deploy_hook("configure")
def configure_nginx(host_data):
    """Install NGINX and create SSL directory."""
    from pyinfra.operations import apt, files

    apt.packages(
        name="Install NGINX",
        packages=["nginx"],
        _sudo=True,
    )

    domains = getattr(host_data, "domains", [])
    if domains:
        app_user = getattr(host_data, "app_user", "app")
        files.directory(
            name="Create SSL certificates directory",
            path=f"/home/{app_user}/.ssl",
            user=app_user,
            group=app_user,
            _sudo=True,
        )


@deploy_hook("deploy:configure")
def deploy_nginx(host_data, artifact_path):
    """Deploy NGINX configuration files and SSL certificates."""
    from pyinfra.operations import server, files
    from djaploy.infra.utils import is_bluegreen

    server.shell(
        name="Clear default NGINX sites",
        commands=[
            "rm -f /etc/nginx/sites-available/default",
            "rm -f /etc/nginx/sites-enabled/default",
        ],
        _sudo=True,
    )

    domains = getattr(host_data, "domains", [])
    app_user = getattr(host_data, "app_user", "app")

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
                mode="600",
                force=True,
                _sudo=True,
            )

    # Only enable the site for this app — avoid enabling unrelated sites
    # on multi-tenant servers. Skip if the config file doesn't exist
    # (e.g. when nginx_conf={"custom": True} and a custom hook handles it).
    app_name = getattr(host_data, 'app_name', None)
    if app_name:
        server.shell(
            name=f"Enable {app_name} NGINX site",
            commands=[
                f"test -f /etc/nginx/sites-available/{app_name} && "
                f"ln -fs /etc/nginx/sites-available/{app_name} /etc/nginx/sites-enabled/{app_name} || true",
            ],
            _sudo=True,
        )

        # For bluegreen, also enable the upstream config
        if is_bluegreen(host_data):
            server.shell(
                name=f"Enable {app_name} NGINX upstream config",
                commands=[
                    f"test -f /etc/nginx/sites-available/{app_name}-upstream.conf && "
                    f"ln -fs /etc/nginx/sites-available/{app_name}-upstream.conf "
                    f"/etc/nginx/sites-enabled/{app_name}-upstream.conf || true",
                ],
                _sudo=True,
            )


@deploy_hook("deploy:start")
def reload_nginx(host_data, artifact_path):
    """Reload NGINX after all deploy hooks have run."""
    from pyinfra.operations import systemd

    systemd.service(
        name="Reload NGINX",
        service="nginx",
        running=True,
        reloaded=True,
        enabled=True,
        _sudo=True,
    )
