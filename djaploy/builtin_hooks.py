"""
Built-in hooks for djaploy.

These implement the core deployment lifecycle (artifact creation, prepare
scripts, notifications, version tagging, rollback validation) using the
standard precommand/postcommand hook points.

The management command calls exactly four hooks:
    {command}:precommand, precommand, {command}:postcommand, postcommand

Command files (djaploy/commands/*.py) can call whatever hooks they want
on the remote side — those are their own business.
"""

import base64
from pathlib import Path

from .hooks import hook


# ── deploy:precommand ────────────────────────────────────────────────

@hook("deploy:precommand")
def _deploy_run_prepare(context):
    """Run the prepare.py script before artifact creation."""
    if context.get("skip_prepare"):
        return

    from .discovery import get_app_infra_dirs

    for app_label, infra_dir in get_app_infra_dirs():
        prepare_script = infra_dir / "prepare.py"
        if prepare_script.exists():
            from .deploy import _run_prepare
            print(f"Running prepare script ({app_label})...", flush=True)
            _run_prepare(prepare_script)


@hook("deploy:precommand")
def _deploy_create_artifact(context):
    """Create the deployment artifact and copy per host app_name."""
    from .artifact import create_artifact, copy_artifact_for_host
    from .deploy import _load_inventory_hosts, _get_host_conf

    mode = context.get("mode", "latest")
    release_tag = context.get("release") if mode == "release" else None

    hosts = context.get("_hosts") or _load_inventory_hosts(context["inventory_file"])
    context["_hosts"] = hosts
    artifact_conf = _get_host_conf(hosts, "artifact_conf")

    print(f"Creating {mode} artifact...", flush=True)
    temp_artifact = create_artifact(
        mode=mode,
        release_tag=release_tag,
        artifact_conf=artifact_conf,
    )

    # Copy with first host's app_name
    if hosts:
        _, data = hosts[0]
        app_name = (data.get("app_name") if isinstance(data, dict)
                    else getattr(data, "app_name", None))
        if app_name:
            artifact_path = copy_artifact_for_host(temp_artifact, app_name)
        else:
            artifact_path = temp_artifact
    else:
        artifact_path = temp_artifact

    context["artifact_path"] = artifact_path
    context["pyinfra_data"]["artifact_path"] = str(artifact_path)


@hook("deploy:precommand")
def _deploy_collect_local_settings(context):
    """Collect local_settings from hooks and encode for the remote command."""
    from .hooks import call_hook

    local_settings = call_hook("deploy:local_settings", context)
    if local_settings:
        content = "\n\n".join(local_settings)
        content = "\n\n" + content + "\n"
        context["pyinfra_data"]["local_settings_b64"] = (
            base64.b64encode(content.encode()).decode()
        )


@hook("deploy:precommand")
def _deploy_calculate_release_info(context):
    """Calculate release info (version, changelog) and inject into pyinfra_data."""
    from .deploy import _get_release_info, _load_inventory_hosts

    env = context["env"]
    version_bump = context.get("version_bump")

    hosts = context.get("_hosts") or _load_inventory_hosts(context["inventory_file"])
    context["_hosts"] = hosts

    release_info = _get_release_info(env, hosts, version_bump)
    context["release_info"] = release_info

    if release_info:
        context["pyinfra_data"]["version"] = release_info["new_version"]
        context["pyinfra_data"]["commit"] = release_info["commit"]
    elif "commit" not in context["pyinfra_data"]:
        # Always pass commit hash even without versioning configured
        try:
            import subprocess
            from django.conf import settings
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(settings.GIT_DIR),
                text=True,
            ).strip()
            context["pyinfra_data"]["commit"] = commit
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Failed to get commit hash: %s", e)


# ── deploy:postcommand ───────────────────────────────────────────────

@hook("deploy:postcommand")
def _send_notification_hook(context):
    """Send deployment notification after deploy (success or failure)."""
    from .deploy import _send_notification, _load_inventory_hosts

    env = context.get("env")
    release_info = context.get("release_info")
    success = context.get("success", True)
    error = context.get("error")
    hosts = context.get("_hosts") or _load_inventory_hosts(context["inventory_file"])

    error_message = str(error) if error else ""
    _send_notification(env, hosts, release_info, success=success, error_message=error_message)


@hook("deploy:postcommand")
def _create_version_tag_hook(context):
    """Create a git version tag after successful deploy."""
    if not context.get("success", True):
        return

    from .deploy import _create_version_tag

    env = context.get("env")
    release_info = context.get("release_info")

    _create_version_tag(env, release_info)


# ── rollback:precommand ──────────────────────────────────────────────

@hook("rollback:precommand")
def _rollback_validate_strategy(context):
    """Ensure the deployment uses zero_downtime strategy before rollback."""
    from .deploy import _load_inventory_hosts

    hosts = _load_inventory_hosts(context["inventory_file"])
    if not hosts:
        raise ValueError("No hosts found in inventory")

    _, data = hosts[0]
    strategy = (data.get("deployment_strategy") if isinstance(data, dict)
                else getattr(data, "deployment_strategy", "zero_downtime"))
    if strategy not in ("zero_downtime", "bluegreen"):
        raise ValueError(
            "Rollback is only supported with deployment_strategy='zero_downtime' or 'bluegreen'"
        )
