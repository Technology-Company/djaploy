"""
Pyinfra command: synchronize SSL certificates.

Only runs certificate-related deploy hooks (sync_certs, tailscale).
Does NOT run core deploy, nginx config, systemd, etc.

Usage (via djaploy management command):
    manage.py sync_certs --env production
"""

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

# Only run sync_certs-specific hooks, not the full deploy lifecycle.
for phase in ("sync_certs:pre", "sync_certs", "sync_certs:post"):
    for hook in registry.get_remote_hooks(phase):
        _deploy_decorator(hook.function.__name__)(hook.function)(
            host.data
        )
