"""
Pyinfra command: restore from backup.

Discovers and runs all @deploy_hook functions for the restore phases.

Usage (via djaploy management command):
    manage.py djaploy restore --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

restore_opts = {
    "backup_host_name": getattr(host.data, "backup_host_name", ""),
    "date": getattr(host.data, "date", ""),
    "db_only": getattr(host.data, "db_only", "false").lower() == "true"
    if isinstance(getattr(host.data, "db_only", False), str)
    else bool(getattr(host.data, "db_only", False)),
    "archive": getattr(host.data, "archive", "latest"),
    "backend": getattr(host.data, "backend", ""),
    "source_repo_name": getattr(host.data, "source_repo_name", ""),
    "source_media_path": getattr(host.data, "source_media_path", ""),
}

# Source borg config for cross-env restores (pyinfra may auto-parse the JSON)
_source_borg = getattr(host.data, "source_borg_config", "")
if _source_borg:
    if isinstance(_source_borg, str):
        import json
        _source_borg = json.loads(_source_borg)
    restore_opts["source_borg_config"] = _source_borg

for phase in ("restore:pre", "restore", "restore:post"):
    for hook in registry.get_remote_hooks(phase):
        _deploy_decorator(hook.function.__name__)(hook.function)(
            host.data, restore_opts
        )
