"""
Nginx hooks for djaploy.

Installs, configures, and reloads NGINX during deployment lifecycle.
"""

from djaploy.hooks import deploy_hook


@deploy_hook("configure")
def configure_nginx(host_data, project_config):
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
def deploy_nginx(host_data, project_config, artifact_path):
    """Deploy NGINX configuration files and SSL certificates."""
    from pyinfra.operations import server, files

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

    # Symlink each site individually to avoid creating a literal '*' symlink
    # when sites-available is empty, and to avoid enabling unrelated sites
    # on multi-tenant servers.
    server.shell(
        name="Enable NGINX sites",
        commands=[
            "for f in /etc/nginx/sites-available/*; do "
            "[ -f \"$f\" ] && ln -fs \"$f\" /etc/nginx/sites-enabled/; "
            "done",
        ],
        _sudo=True,
    )


@deploy_hook("deploy:start")
def reload_nginx(host_data, project_config, artifact_path):
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
