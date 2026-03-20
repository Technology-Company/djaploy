"""
Versioning hooks for djaploy.

Deploys a VERSION file containing version, commit, timestamp, and environment info.
"""

from djaploy.hooks import deploy_hook


@deploy_hook("deploy:configure")
def deploy_version_file(host_data, artifact_path):
    """Deploy VERSION file to server."""
    import tempfile
    from datetime import datetime, timezone

    from pyinfra.operations import files

    version = getattr(host_data, "version", None)
    commit = getattr(host_data, "commit", "unknown")
    env = getattr(host_data, "env", "unknown")

    if not version:
        print("[VERSIONING] No version info provided, skipping VERSION file deployment")
        return

    app_user = getattr(host_data, "app_user", "app")
    app_name = getattr(host_data, 'app_name', None)
    if not app_name:
        return

    module_config = getattr(host_data, 'versioning_conf', None) or {}
    version_file_path = module_config.get("version_file_path", "VERSION")
    app_root = f"/home/{app_user}/apps/{app_name}"
    dest_path = f"{app_root}/{version_file_path}"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version_content = f"""VERSION={version}
COMMIT={commit}
DEPLOYED_AT={timestamp}
ENVIRONMENT={env}
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(version_content)
        temp_path = f.name

    files.put(
        name=f"Deploy VERSION file to {dest_path}",
        src=temp_path,
        dest=dest_path,
        user=app_user,
        group=app_user,
        mode="644",
        _sudo=True,
    )

    print(f"[VERSIONING] Deployed VERSION file: {version} ({commit[:7]})")
