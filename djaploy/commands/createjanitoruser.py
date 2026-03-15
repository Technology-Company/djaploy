"""
Pyinfra command: create janitor (deploy) user.

Connects as root to a fresh server and bootstraps the SSH/deploy user
with password and sudo access. This is typically the first command run
on a new server before configure or deploy.

Usage (via djaploy management command):
    manage.py djaploy createjanitoruser --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

for phase in ("createjanitoruser:pre", "createjanitoruser", "createjanitoruser:post"):
    for hook in registry.get_remote_hooks(phase):
        _deploy_decorator(hook.function.__name__)(hook.function)(
            host.data
        )
