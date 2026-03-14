"""
Pyinfra command: rollback to a previous release.

Discovers and runs all @deploy_hook functions for the rollback phase.

Usage (via djaploy management command):
    manage.py djaploy rollback --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.commands._utils import load_project_config
from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

project_config = load_project_config(host)

# None means "roll back to previous release"
release = getattr(host.data, "release", None)
if release == "None" or release == "":
    release = None

for hook in registry.get_remote_hooks("rollback"):
    _deploy_decorator(hook.function.__name__)(hook.function)(
        host.data, project_config, release
    )
