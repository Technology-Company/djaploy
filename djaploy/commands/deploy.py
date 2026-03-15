"""
Pyinfra command: deploy project.

Discovers and runs all @deploy_hook functions across four phases:

    deploy:upload     — upload artifact, extract, symlink shared resources
    deploy:configure  — install deps, deploy configs, SSL, local_settings
    deploy:pre        — migrations, collectstatic, symlink swap
    deploy:start      — reload/restart services

Usage (via djaploy management command):
    manage.py djaploy deploy --env production
"""

from pathlib import Path

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator

from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

artifact_path = Path(host.data.artifact_path)

for phase in ("deploy:upload", "deploy:configure", "deploy:pre", "deploy:start"):
    for hook in registry.get_remote_hooks(phase):
        _deploy_decorator(hook.function.__name__)(hook.function)(
            host.data, artifact_path
        )
