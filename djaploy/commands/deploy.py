"""
Pyinfra command: deploy project.

Discovers and runs all @deploy_hook functions for the deploy phases.
Handles local_settings injection between deploy and deploy:post.

Usage (via djaploy management command):
    manage.py djaploy deploy --env production
"""

import base64
from pathlib import Path

from pyinfra import host
from pyinfra.api import deploy as _deploy_decorator
from pyinfra.operations import server

from djaploy.commands._utils import load_project_config
from djaploy.hooks import discover_hooks, get_registry

discover_hooks()
registry = get_registry()

project_config = load_project_config(host)
artifact_path = Path(host.data.artifact_path)

# --- deploy:pre ---
for hook in registry.get_remote_hooks("deploy:pre"):
    _deploy_decorator(hook.function.__name__)(hook.function)(
        host.data, project_config, artifact_path
    )

# --- deploy ---
for hook in registry.get_remote_hooks("deploy"):
    _deploy_decorator(hook.function.__name__)(hook.function)(
        host.data, project_config, artifact_path
    )

# --- local_settings injection (between deploy and deploy:post) ---
local_settings_b64 = getattr(host.data, "local_settings_b64", None)
if local_settings_b64:
    app_user = getattr(host.data, "app_user", None) or project_config.app_user
    project_name = project_config.project_name
    app_path = f"/home/{app_user}/apps/{project_name}"
    if getattr(project_config, "deployment_strategy", "in_place") == "zero_downtime":
        base_path = f"{app_path}/build"
    else:
        base_path = app_path
    local_py = f"{base_path}/{project_name}/local.py"
    server.shell(
        name="Append hook-contributed settings to local.py",
        commands=[f"printf '%s' '{local_settings_b64}' | base64 -d >> {local_py}"],
        _sudo=True,
        _sudo_user=app_user,
    )

# --- deploy:post ---
for hook in registry.get_remote_hooks("deploy:post"):
    _deploy_decorator(hook.function.__name__)(hook.function)(
        host.data, project_config, artifact_path
    )
