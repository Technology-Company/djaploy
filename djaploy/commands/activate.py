"""
Pyinfra command: activate a blue-green deployment slot.

Discovers and runs all @deploy_hook functions for the activate phase:

    activate:pre  — verify target slot is healthy
    activate      — switch nginx upstream, update state
    activate:post — cleanup tasks

Usage (via djaploy management command):
    manage.py djaploy activate --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

for phase in ("activate:pre", "activate", "activate:post"):
    for hook in registry.get_remote_hooks(phase):
        _deploy_decorator(hook.function.__name__)(hook.function)(
            host.data
        )
