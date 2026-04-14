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

# If --activate flag is set, run activation phases after deploy
_activate_val = getattr(host.data, "activate", None)
if _activate_val in (True, "true", "True"):
    for phase in ("activate:pre", "activate", "activate:post"):
        _hooks = registry.get_remote_hooks(phase)
        print(f"[activate] phase={phase} hooks={[h.function.__name__ for h in _hooks]}")
        for hook in _hooks:
            _deploy_decorator(hook.function.__name__)(hook.function)(
                host.data
            )
