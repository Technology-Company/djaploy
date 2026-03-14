"""
Pyinfra command: configure servers.

Discovers and runs all @deploy_hook functions for the configure phases.

Usage (via djaploy management command):
    manage.py djaploy configure --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.commands._utils import load_project_config
from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

project_config = load_project_config(host)

for phase in ("configure:pre", "configure", "configure:post"):
    for hook in registry.get_remote_hooks(phase):
        _deploy_decorator(hook.function.__name__)(hook.function)(
            host.data, project_config
        )
