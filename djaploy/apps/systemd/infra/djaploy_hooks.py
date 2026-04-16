"""
Systemd hooks for djaploy.

Reloads the systemd daemon and manages services during deployment lifecycle.
"""

from djaploy.hooks import deploy_hook


@deploy_hook("deploy:configure")
def reload_systemd_daemon(host_data, artifact_path):
    """Reload systemd daemon to pick up new service files."""
    from pyinfra.operations import systemd

    systemd.daemon_reload(
        name="Reload systemd daemon",
        _sudo=True,
    )


@deploy_hook("deploy:start")
def start_services(host_data, artifact_path):
    """Start or restart application services after deploy."""
    from pyinfra import host
    from pyinfra.operations import systemd

    from djaploy.infra.utils import is_zero_downtime, is_bluegreen, get_slot_service_name

    if is_bluegreen(host_data):
        # Start/restart only the target slot's service.
        # The other slot may be empty (first deploy) so we must NOT
        # try to start it — only enable the target slot's service.
        target_slot = getattr(host.data, '_bluegreen_target_slot', None)
        if not target_slot:
            return

        from pyinfra.operations import server
        for service in getattr(host_data, "services", []):
            slot_service = get_slot_service_name(service, target_slot)

            # Only start if the service unit file exists — custom services
            # (e.g. streaming) may be rendered by project hooks, not djaploy core.
            # Uses if/else so enable/restart failures still surface as errors.
            server.shell(
                name=f"Start {slot_service} if unit exists",
                commands=[
                    f"if [ -f /etc/systemd/system/{slot_service}.service ]; then "
                    f"systemctl enable {slot_service} && systemctl restart {slot_service}; "
                    f"else echo 'Unit {slot_service}.service not found, skipping'; fi",
                ],
                _sudo=True,
            )

        # Disable legacy single service from zero_downtime/in_place if present.
        # Runs after the new slot service is started so there's no downtime gap.
        app_name = getattr(host_data, 'app_name', None)
        from pyinfra.operations import server
        server.shell(
            name="Disable legacy service (migration to bluegreen)",
            commands=[
                f"systemctl stop {app_name}.service 2>/dev/null || true",
                f"systemctl disable {app_name}.service 2>/dev/null || true",
                f"rm -f /etc/systemd/system/{app_name}.service",
                "systemctl daemon-reload 2>/dev/null || true",
            ],
            _sudo=True,
        )
    elif is_zero_downtime(host_data):
        for service in getattr(host_data, "services", []):
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
        for service in getattr(host_data, "services", []):
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
def reload_services_on_rollback(host_data, release):
    """Reload or restart services after a rollback."""
    from pyinfra.operations import systemd
    from djaploy.infra.utils import is_zero_downtime, is_bluegreen

    if is_bluegreen(host_data):
        # For bluegreen rollback, nginx switching is handled by the core
        # rollback hook. We just need to ensure the target slot's service
        # is running (it should already be from the previous deploy).
        pass
    elif is_zero_downtime(host_data):
        for service in getattr(host_data, "services", []):
            systemd.service(
                name=f"Reload {service} after rollback",
                service=service,
                reloaded=True,
                _sudo=True,
            )
    else:
        for service in getattr(host_data, "services", []):
            systemd.service(
                name=f"Restart {service} after rollback",
                service=service,
                restarted=True,
                _sudo=True,
            )
