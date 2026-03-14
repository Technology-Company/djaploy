"""
Systemd hooks for djaploy.

Reloads the systemd daemon and manages services during deployment lifecycle.
"""

from djaploy.hooks import deploy_hook


@deploy_hook("deploy")
def reload_systemd_daemon(host_data, project_config, artifact_path):
    """Reload systemd daemon to pick up new service files."""
    from pyinfra.operations import systemd

    systemd.daemon_reload(
        name="Reload systemd daemon",
        _sudo=True,
    )


@deploy_hook("deploy:post")
def start_services(host_data, project_config, artifact_path):
    """Start or restart application services after deploy."""
    from pyinfra.operations import systemd

    zero_downtime = (
        getattr(project_config, "deployment_strategy", "in_place") == "zero_downtime"
    )

    for service in getattr(host_data, "services", []):
        if zero_downtime:
            systemd.service(
                name=f"Start and enable {service}",
                service=service,
                running=True,
                enabled=True,
                _sudo=True,
            )
            systemd.service(
                name=f"Reload {service} (zero-downtime)",
                service=service,
                reloaded=True,
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

    for timer in getattr(host_data, "timer_services", []):
        systemd.service(
            name=f"Start and enable {timer}.timer",
            service=f"{timer}.timer",
            running=True,
            enabled=True,
            _sudo=True,
        )


@deploy_hook("rollback")
def reload_services_on_rollback(host_data, project_config, release):
    """Reload or restart services after a rollback."""
    from pyinfra.operations import systemd

    zero_downtime = (
        getattr(project_config, "deployment_strategy", "in_place") == "zero_downtime"
    )

    for service in getattr(host_data, "services", []):
        if zero_downtime:
            systemd.service(
                name=f"Reload {service} after rollback",
                service=service,
                reloaded=True,
                _sudo=True,
            )
        else:
            systemd.service(
                name=f"Restart {service} after rollback",
                service=service,
                restarted=True,
                _sudo=True,
            )
