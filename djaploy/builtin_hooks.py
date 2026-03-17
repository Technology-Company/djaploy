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

from .hooks import hook


# ── deploy:precommand ────────────────────────────────────────────────

@hook("deploy:precommand")
def _deploy_run_prepare(context):
    """Run the prepare.py script before artifact creation."""
    if context.get("skip_prepare"):
        return

    config = context["config"]
    prepare_script = config.djaploy_dir / "prepare.py"
    if prepare_script.exists():
        from .deploy import _run_prepare
        _run_prepare(prepare_script, config)


@hook("deploy:precommand")
def _deploy_create_artifact(context):
    """Create the deployment artifact and inject its path into pyinfra_data."""
    from .artifact import create_artifact

    config = context["config"]
    mode = context.get("mode", "latest")
    release_tag = context.get("release") if mode == "release" else None

    artifact_path = create_artifact(
        config=config,
        mode=mode,
        release_tag=release_tag,
    )
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
    from .deploy import _get_release_info

    config = context["config"]
    env = context["env"]
    version_bump = context.get("version_bump")

    release_info = _get_release_info(config, env, version_bump)
    context["release_info"] = release_info

    if release_info:
        context["pyinfra_data"]["version"] = release_info["new_version"]
        context["pyinfra_data"]["commit"] = release_info["commit"]


# ── deploy:postcommand ───────────────────────────────────────────────

@hook("deploy:postcommand")
def _send_notification_hook(context):
    """Send deployment notification after deploy (success or failure)."""
    from .deploy import _send_notification

    config = context.get("config")
    env = context.get("env")
    release_info = context.get("release_info")
    success = context.get("success", True)
    error = context.get("error")

    error_message = str(error) if error else ""
    _send_notification(config, env, release_info, success=success, error_message=error_message)


@hook("deploy:postcommand")
def _create_version_tag_hook(context):
    """Create a git version tag after successful deploy."""
    if not context.get("success", True):
        return

    from .deploy import _create_version_tag

    config = context.get("config")
    env = context.get("env")
    release_info = context.get("release_info")

    _create_version_tag(config, env, release_info)


# ── rollback:precommand ──────────────────────────────────────────────

@hook("rollback:precommand")
def _rollback_validate_strategy(context):
    """Ensure the project uses zero_downtime strategy before rollback."""
    config = context["config"]
    if config.deployment_strategy != "zero_downtime":
        raise ValueError(
            "Rollback is only supported with deployment_strategy='zero_downtime'"
        )
