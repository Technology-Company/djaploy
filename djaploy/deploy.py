"""
Deployment utilities for djaploy.

Provides:
- Python-API wrappers (``deploy_project``, ``configure_server``, etc.) that
  build a context dict and run the same 4-hook lifecycle as the management
  command.
- Internal helpers for pyinfra execution, inventory pre-processing,
  notifications, versioning, and prepare scripts.

Lifecycle (same as ``manage.py djaploy``)::

    {command}:precommand
    precommand
    ── pyinfra execution ──
    {command}:postcommand
    postcommand            ← always runs, even on failure
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any


# ------------------------------------------------------------------
# Shared lifecycle (used by both management command and Python API)
# ------------------------------------------------------------------

def _get_command_file(name: str) -> Path:
    """Return the path to a built-in djaploy command file."""
    return Path(__file__).parent / "commands" / f"{name}.py"


def run_command(context: Dict[str, Any]) -> None:
    """Execute a command through the 4-hook lifecycle.

    Required context keys::

        command, env, command_file, inventory_file, pyinfra_data

    Lifecycle::

        {command}:precommand
        precommand
        ── pyinfra execution ──
        {command}:postcommand
        postcommand            ← always runs
    """
    from .hooks import discover_hooks, call_hook

    discover_hooks()

    command_name = context["command"]

    # 1. Precommand hooks
    print(f"Preparing {command_name}...", flush=True)
    call_hook(f"{command_name}:precommand", context)
    call_hook("precommand", context)

    # 2. Run pyinfra
    print("Starting remote execution...", flush=True)
    processed_inventory = _preprocess_inventory(str(context["inventory_file"]))

    try:
        _run_pyinfra(
            str(context["command_file"]),
            processed_inventory,
            data=context.get("pyinfra_data", {}),
        )
        context["success"] = True
    except Exception as e:
        context["success"] = False
        context["error"] = e
    finally:
        if processed_inventory != str(context["inventory_file"]):
            try:
                os.unlink(processed_inventory)
            except OSError:
                pass

    # 3. Postcommand hooks (always run)
    call_hook(f"{command_name}:postcommand", context)
    call_hook("postcommand", context)

    # 4. Re-raise if failed
    if not context["success"]:
        raise context["error"]


# ------------------------------------------------------------------
# Python API wrappers (build context, delegate to run_command)
# ------------------------------------------------------------------

def _build_pyinfra_data(env_name: str) -> Dict[str, str]:
    """Build the base pyinfra --data dict."""
    return {"env": env_name}


def configure_server(inventory_file: str, **kwargs):
    """Configure servers for deployment."""
    env_name = Path(inventory_file).stem
    run_command({
        "command": "configure",
        "env": env_name,
        "command_file": str(_get_command_file("configure")),
        "inventory_file": inventory_file,
        "pyinfra_data": _build_pyinfra_data(env_name),
    })


def deploy_project(inventory_file: str,
                   mode: str = "latest",
                   release_tag: Optional[str] = None,
                   skip_prepare: bool = False,
                   version_bump: Optional[str] = None,
                   **kwargs):
    """Deploy project to servers."""
    env_name = Path(inventory_file).stem
    run_command({
        "command": "deploy",
        "env": env_name,
        "mode": mode,
        "release": release_tag,
        "version_bump": version_bump,
        "skip_prepare": skip_prepare,
        "command_file": str(_get_command_file("deploy")),
        "inventory_file": inventory_file,
        "pyinfra_data": _build_pyinfra_data(env_name),
    })


def restore_from_backup(inventory_file: str,
                        restore_opts: Dict[str, Any],
                        **kwargs):
    """Restore from backup on target servers via pyinfra."""
    import json

    env_name = Path(inventory_file).stem
    data = _build_pyinfra_data(env_name)
    data.update({
        "backup_host_name": restore_opts.get("backup_host_name", ""),
        "date": restore_opts.get("date", ""),
        "db_only": str(restore_opts.get("db_only", False)).lower(),
        "archive": restore_opts.get("archive", ""),
        "backend": restore_opts.get("backend", ""),
    })
    # Pass source borg config for cross-env restores (e.g. --env prod --target staging)
    if restore_opts.get("source_borg_config"):
        data["source_borg_config"] = json.dumps(restore_opts["source_borg_config"])
    if restore_opts.get("source_repo_name"):
        data["source_repo_name"] = restore_opts["source_repo_name"]
    if restore_opts.get("source_media_path"):
        data["source_media_path"] = restore_opts["source_media_path"]
    run_command({
        "command": "restore",
        "env": env_name,
        "restore_opts": restore_opts,
        "command_file": str(_get_command_file("restore")),
        "inventory_file": inventory_file,
        "pyinfra_data": data,
    })


def create_janitor_user(inventory_file: str, **kwargs):
    """Create the janitor (deploy) user on target servers.

    Connects as root and bootstraps the SSH user with password and
    sudo access.  Typically the first command run on a fresh server.

    Requires ``djaploy.apps.janitor`` in INSTALLED_APPS.
    """
    from .discovery import find_command

    command_file = find_command("createjanitoruser")
    if not command_file:
        raise RuntimeError(
            "Command 'createjanitoruser' not found. "
            "Add 'djaploy.apps.janitor' to INSTALLED_APPS."
        )
    env_name = Path(inventory_file).stem
    run_command({
        "command": "createjanitoruser",
        "env": env_name,
        "command_file": str(command_file),
        "inventory_file": inventory_file,
        "pyinfra_data": _build_pyinfra_data(env_name),
    })


def rollback_project(inventory_file: str,
                     release: Optional[str] = None,
                     **kwargs):
    """Roll back to a previous release."""
    env_name = Path(inventory_file).stem
    data = _build_pyinfra_data(env_name)
    if release:
        data["release"] = release

    run_command({
        "command": "rollback",
        "env": env_name,
        "release": release,
        "command_file": str(_get_command_file("rollback")),
        "inventory_file": inventory_file,
        "pyinfra_data": data,
    })


# ------------------------------------------------------------------
# Internal helpers (called by hooks in builtin_hooks.py)
# ------------------------------------------------------------------

def _load_inventory_hosts(inventory_file: str) -> list:
    """Load host tuples from an inventory file.

    Returns a list of (hostname, data_dict) tuples.  Used by local hooks
    that need to read HostConfig fields before pyinfra connects.

    If OpSecret objects are used, resolves all secrets in a single
    1Password call before returning.
    """
    import importlib.util

    module_name = f"_inv_loader_{id(inventory_file)}"
    spec = importlib.util.spec_from_file_location(module_name, inventory_file)
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        hosts = list(getattr(module, "hosts", []))
    finally:
        sys.modules.pop(module_name, None)

    # Batch-resolve all OpSecret values in one op inject call
    try:
        from .certificates import OpSecret
        OpSecret.resolve_all()
    except ImportError:
        pass

    return hosts


def _get_host_conf(hosts: list, key: str) -> Dict[str, Any]:
    """Read a *_conf dict from the first host in the inventory.

    Used for project-wide settings (notifications, versioning) that
    apply to the whole deployment, not per-host.
    """
    if not hosts:
        return {}
    _, data = hosts[0]
    return (data.get(key) if isinstance(data, dict)
            else getattr(data, key, None)) or {}


def _get_host_field(hosts: list, key: str, default=None):
    """Read a single field from the first host."""
    if not hosts:
        return default
    _, data = hosts[0]
    val = (data.get(key) if isinstance(data, dict)
           else getattr(data, key, None))
    return val if val is not None else default


def _get_release_info(env_name: str, hosts: list, version_bump: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Calculate release info for notifications and tagging.

    Returns None if versioning/notifications are not configured.
    """
    versioning_config = _get_host_conf(hosts, "versioning_conf")
    if not versioning_config:
        return None

    notifications_config = _get_host_conf(hosts, "notifications_conf")

    webhook_url = notifications_config.get("webhook_url")
    if not webhook_url:
        return None

    try:
        from .versioning import (
            get_latest_version_tag,
            get_commits_since_tag,
            get_current_commit_hash,
            increment_version,
            get_tag_message,
            extract_changelog_from_tag,
        )
        from .changelog import get_changelog_generator
        from django.conf import settings

        git_dir = Path(settings.GIT_DIR)
        current_version = get_latest_version_tag(git_dir)
        commit = get_current_commit_hash(git_dir, short=False)
        commits = get_commits_since_tag(git_dir, current_version)

        if commits:
            increment_type = version_bump or versioning_config.get("increment_type", "patch")
            new_version = increment_version(current_version, increment_type)
        else:
            new_version = current_version or "v1.0.0"

        changelog = ""
        if commits:
            generator_type = notifications_config.get("changelog_generator", "simple")
            generator_config = notifications_config.get("changelog_config", {})
            generator = get_changelog_generator(generator_type, generator_config)
            try:
                changelog = generator.generate(commits)
            except Exception as e:
                print(f"[RELEASE] Warning: Failed to generate changelog: {e}")
                changelog = commits
        elif current_version:
            tag_message = get_tag_message(git_dir, current_version)
            if tag_message:
                changelog = extract_changelog_from_tag(tag_message)
                print(f"[RELEASE] Using changelog from existing tag {current_version}")

        tag_environments = versioning_config.get("tag_environments", ["production"])

        # Support both boolean `notify` (preferred) and legacy `notify_environments` list
        if "notify" in notifications_config:
            should_notify = bool(notifications_config["notify"])
        else:
            notify_environments = notifications_config.get("notify_environments", tag_environments)
            should_notify = env_name in notify_environments

        display_name = (notifications_config.get("display_name")
                        or _get_host_field(hosts, "app_name", "unknown"))

        return {
            "current_version": current_version,
            "new_version": new_version,
            "commit": commit or "unknown",
            "commits": commits,
            "changelog": changelog,
            "display_name": display_name,
            "should_notify": should_notify,
            "should_tag": env_name in tag_environments,
            "notify_on_failure": notifications_config.get("notify_on_failure", True),
            "webhook_url": webhook_url,
            "push_tags": versioning_config.get("push_tags", True),
        }

    except Exception as e:
        print(f"[RELEASE] Warning: Failed to get release info: {e}")
        return None


def _send_notification(env_name: str, hosts: list, release_info: Dict[str, Any], success: bool, error_message: str = ""):
    """Send deployment notification (success or failure)."""
    if not release_info or not release_info.get("should_notify"):
        return

    if not success and not release_info.get("notify_on_failure", True):
        return

    try:
        from .notifications import get_notification_backend

        backend = get_notification_backend("slack", {"webhook_url": release_info["webhook_url"]})
        if not backend:
            return

        display_name = release_info["display_name"]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        context = {
            "env": env_name,
            "version": release_info["new_version"],
            "commit": release_info["commit"],
            "changelog": release_info["changelog"] if success else "",
            "success": success,
            "timestamp": timestamp,
            "project_name": display_name,
            "display_name": display_name,
            "error_message": error_message,
        }

        if success:
            message = f"Deployment succeeded: {display_name} {release_info['new_version']} to {env_name}"
        else:
            message = f"Deployment failed for {display_name} to {env_name}"
            if error_message:
                message += f": {error_message}"

        if backend.send(message, context):
            status = "success" if success else "failure"
            print(f"[RELEASE] Sent {status} notification for {env_name}")
        else:
            print(f"[RELEASE] Warning: Failed to send notification")

    except Exception as e:
        print(f"[RELEASE] Warning: Failed to send notification: {e}")


def _create_version_tag(env_name: str, release_info: Dict[str, Any]):
    """Create version tag after successful deployment."""
    if not release_info or not release_info.get("should_tag"):
        return

    if not release_info.get("commits"):
        print(f"[RELEASE] No new commits since {release_info.get('current_version') or 'initial'}, skipping tag")
        return

    try:
        from .versioning import create_git_tag
        from django.conf import settings

        git_dir = Path(settings.GIT_DIR)
        new_version = release_info["new_version"]
        changelog = release_info.get("changelog", "")
        commits = release_info.get("commits", "")

        if changelog and changelog != commits:
            tag_message = f"Release {new_version}\n\n{changelog}\n\n---\nCommits:\n{commits}"
        else:
            tag_message = f"Release {new_version}\n\n{commits}"

        push_tags = release_info.get("push_tags", True)

        if create_git_tag(git_dir, new_version, message=tag_message, push=push_tags):
            print(f"[RELEASE] Created tag {new_version}")
            if push_tags:
                print(f"[RELEASE] Pushed tag to origin")
        else:
            print(f"[RELEASE] Warning: Failed to create tag {new_version}")

    except Exception as e:
        print(f"[RELEASE] Warning: Failed to create version tag: {e}")


# ------------------------------------------------------------------
# pyinfra execution and inventory helpers
# ------------------------------------------------------------------

def _run_pyinfra(script_path: str, inventory_path: str, data: dict = None):
    """Run pyinfra with the given command script and inventory."""
    import djaploy
    djaploy_path = Path(djaploy.__file__).parent
    django_pyinfra_path = djaploy_path / "bin" / "django_pyinfra.py"

    env = os.environ.copy()

    from django.conf import settings
    project_dir = str(settings.BASE_DIR)

    current_python_path = env.get('PYTHONPATH', '')
    if current_python_path:
        env['PYTHONPATH'] = f"{project_dir}{os.pathsep}{current_python_path}"
    else:
        env['PYTHONPATH'] = project_dir

    cmd = [
        sys.executable,
        str(django_pyinfra_path),
        "-y",
    ]

    if data:
        for key, value in data.items():
            cmd.extend(["--data", f"{key}={value}"])

    cmd.extend([inventory_path, script_path])

    subprocess.run(cmd, check=True, env=env)


def _preprocess_inventory(inventory_file: str) -> str:
    """Pre-process inventory file to convert HostConfig objects to pyinfra tuples."""
    import importlib.util

    module_name = f"_inventory_{id(inventory_file)}"
    spec = importlib.util.spec_from_file_location(module_name, inventory_file)
    inventory_module = importlib.util.module_from_spec(spec)

    try:
        sys.modules[module_name] = inventory_module
        spec.loader.exec_module(inventory_module)

        hosts = getattr(inventory_module, 'hosts', [])

        # Batch-resolve any OpSecret values before serialization
        try:
            from .certificates import OpSecret
            OpSecret.resolve_all()
        except ImportError:
            pass

        from .utils import temp_files

        path = temp_files.create(suffix='.py')
        with open(path, 'w') as f:
            f.write("# Auto-processed inventory file\n\n")
            f.write("hosts = [\n")
            for host in hosts:
                if isinstance(host, tuple) and len(host) == 2:
                    host_name, host_data = host
                    safe_host_data = {}
                    for key, value in host_data.items():
                        safe_host_data[key] = _make_value_serializable(value)
                    f.write(f"    ({repr(host_name)}, {repr(safe_host_data)}),\n")
                else:
                    f.write(f"    {repr(host)},\n")
            f.write("]\n")

            return path

    finally:
        sys.modules.pop(module_name, None)


def _make_value_serializable(value):
    """Convert a value to a serializable form for inventory processing."""
    from dataclasses import is_dataclass, asdict
    from .utils import StringLike

    if isinstance(value, StringLike):
        return str(value)
    elif is_dataclass(value) and not isinstance(value, type):
        result = {k: _make_value_serializable(v) for k, v in asdict(value).items()}
        result['__class__'] = value.__class__.__name__
        return result
    elif hasattr(value, '__dict__') and not isinstance(value, type):
        result = {}
        for attr, attr_value in value.__dict__.items():
            if not attr.startswith('_'):
                result[attr] = _make_value_serializable(attr_value)
        result['__class__'] = value.__class__.__name__
        return result
    elif isinstance(value, list):
        return [_make_value_serializable(item) for item in value]
    elif isinstance(value, dict):
        return {k: _make_value_serializable(v) for k, v in value.items()}
    elif isinstance(value, Path):
        return str(value)
    else:
        return value


def _run_prepare(prepare_script: Path):
    """Run the prepare script if it exists."""
    from django.conf import settings
    subprocess.run([sys.executable, str(prepare_script)], check=True,
                   cwd=settings.BASE_DIR)
