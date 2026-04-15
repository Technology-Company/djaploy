"""
Pyinfra command: show blue-green deployment status.

Reads and displays the blue-green state from each host.

Usage (via djaploy management command):
    manage.py djaploy status --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator
from pyinfra.operations import python as python_op

from djaploy.hooks import discover_hooks, get_registry
from djaploy.infra.utils import is_bluegreen, get_app_path, get_slot_socket_path
from djaploy.infra.djaploy_hooks import _read_slot_info_from_remote

discover_hooks()
registry = get_registry()

if is_bluegreen(host.data):
    app_name = getattr(host.data, 'app_name', 'unknown')
    app_path = get_app_path(host.data)
    state_file = f"{app_path}/state.json"

    def _show_status(app_n, state_f):
        blue_info = _read_slot_info_from_remote(host, "blue", state_f)
        green_info = _read_slot_info_from_remote(host, "green", state_f)

        # Read active slot
        import json
        result = host.run_shell_command(command=f"cat {state_f}")
        active = "none"
        if result[0] and len(result) > 1:
            try:
                lines = [l.line if hasattr(l, 'line') else str(l) for l in result[1]]
                state_data = json.loads("\n".join(lines))
                active = state_data.get("active_slot") or "none"
            except (json.JSONDecodeError, KeyError):
                pass

        print(f"\nBlue-Green Status for {app_n}")
        print("=" * 40)
        print(f"Active slot: {active}\n")

        for slot, info in [("blue", blue_info), ("green", green_info)]:
            tag = "ACTIVE" if active == slot else "inactive"
            print(f"{slot.upper()} ({tag}):")
            if info:
                print(f"  Release:     {info.get('release', 'unknown')}")
                print(f"  Commit:      {info.get('commit', 'unknown')}")
                print(f"  Deployed at: {info.get('deployed_at', 'unknown')}")
                print(f"  Python:      {info.get('python_interpreter', 'unknown')}")
                print(f"  Venv:        {info.get('venv_path', 'unknown')}")
                print(f"  Socket:      {get_slot_socket_path(app_n, slot)}")
            else:
                print("  (empty)")
            print()

        # Show service status
        for slot in ("blue", "green"):
            svc = f"{app_n}-{slot}.service"
            res = host.run_shell_command(command=f"systemctl is-active {svc} 2>/dev/null || echo inactive")
            status_str = "unknown"
            if res[0] and len(res) > 1:
                try:
                    first_item = next(iter(res[1]), None)
                    if first_item is not None:
                        status_str = (first_item.line if hasattr(first_item, 'line') else str(first_item)).strip()
                except (StopIteration, AttributeError):
                    pass
            print(f"Service {svc}: {status_str}")
        print()

    python_op.call(
        name="Show blue-green status",
        function=_show_status,
        app_n=app_name,
        state_f=state_file,
    )
else:
    def _show_non_bluegreen():
        strategy = getattr(host.data, "deployment_strategy", "zero_downtime")
        print(f"\nDeployment strategy: {strategy}")
        print("Not a blue-green deployment. Use deploy/rollback commands.\n")

    python_op.call(
        name="Show deployment status",
        function=_show_non_bluegreen,
    )