"""
Pyinfra command: restore from backup.

Discovers and runs all @deploy_hook functions for the restore phases.

Usage (via djaploy management command):
    manage.py djaploy restore --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.commands._utils import load_project_config
from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

project_config = load_project_config(host)

restore_opts = {
    "backup_host_name": getattr(host.data, "backup_host_name", ""),
    "date": getattr(host.data, "date", ""),
    "db_only": getattr(host.data, "db_only", "false").lower() == "true"
    if isinstance(getattr(host.data, "db_only", False), str)
    else bool(getattr(host.data, "db_only", False)),
    "archive": getattr(host.data, "archive", "latest"),
    "backend": getattr(host.data, "backend", ""),
}

for phase in ("restore:pre", "restore", "restore:post"):
    for hook in registry.get_remote_hooks(phase):
        _deploy_decorator(hook.function.__name__)(hook.function)(
            host.data, project_config, restore_opts
        )
