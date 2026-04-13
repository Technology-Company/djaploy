"""
Pyinfra command: show blue-green deployment status.

Reads and displays the blue-green state from each host.

Usage (via djaploy management command):
    manage.py djaploy status --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator
from pyinfra.operations import server

from djaploy.hooks import discover_hooks, get_registry
from djaploy.infra.utils import is_bluegreen, get_app_path, get_slot_socket_path
from djaploy.infra.bluegreen import print_status_cmd

discover_hooks()
registry = get_registry()

if is_bluegreen(host.data):
    app_name = getattr(host.data, 'app_name', 'unknown')
    app_path = get_app_path(host.data)
    state_file = f"{app_path}/state.json"

    @_deploy_decorator("show_bluegreen_status")
    def show_bluegreen_status(host_data):
        server.shell(
            name="Show blue-green status",
            commands=[
                print_status_cmd(state_file, app_name),
                f'echo "Sockets:"',
                f'echo "  Blue:  {get_slot_socket_path(app_name, "blue")}"',
                f'echo "  Green: {get_slot_socket_path(app_name, "green")}"',
                f'echo ""',
                # Show systemd service status
                f'echo "Services:"',
                f'systemctl is-active {app_name}-blue.service 2>/dev/null && '
                f'echo "  {app_name}-blue:  active" || '
                f'echo "  {app_name}-blue:  inactive"',
                f'systemctl is-active {app_name}-green.service 2>/dev/null && '
                f'echo "  {app_name}-green: active" || '
                f'echo "  {app_name}-green: inactive"',
                f'echo ""',
            ],
            _sudo=True,
        )

    show_bluegreen_status(host.data)
else:
    @_deploy_decorator("show_status")
    def show_status(host_data):
        server.shell(
            name="Show deployment status",
            commands=[
                f'echo "Deployment strategy: {getattr(host.data, "deployment_strategy", "zero_downtime")}"',
                f'echo "Not a blue-green deployment. Use deploy/rollback commands."',
            ],
            _sudo=True,
        )

    show_status(host.data)
