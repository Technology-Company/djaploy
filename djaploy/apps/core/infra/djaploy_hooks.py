"""
Core hooks for djaploy.

Handles server configuration, application deployment, post-deploy tasks
(migrations, collectstatic, symlink swap), and rollback.
"""

from djaploy.hooks import deploy_hook


@deploy_hook("configure")
def configure_server(host_data):
    """Configure basic server requirements: user, Python, Poetry, directories."""
    from pyinfra.operations import apt, server, pip, files
    from djaploy.apps.core.infra.utils import (
        is_zero_downtime, get_app_path, install_python,
        configure_http_challenge_sudo,
    )
    from pathlib import Path

    app_user = getattr(host_data, 'app_user', 'app')
    ssh_user = getattr(host_data, 'ssh_user', 'deploy')

    # Create application user
    server.user(
        name="Create application user",
        user=app_user,
        shell="/bin/bash",
        create_home=True,
        _sudo=True,
    )

    # For zero-downtime deploys, add www-data to app user group
    if is_zero_downtime(host_data):
        server.shell(
            name="Add www-data to app user group",
            commands=[f"usermod -aG {app_user} www-data"],
            _sudo=True,
        )
        server.shell(
            name="Allow group traversal of app user home",
            commands=[f"chmod 711 /home/{app_user}"],
            _sudo=True,
        )

    # Update apt repositories
    apt.update(
        name="Update apt repositories",
        _sudo=True,
    )

    # Configure ACME challenge directory
    configure_http_challenge_sudo(ssh_user, host_data)

    # Install Python
    install_python(host_data)

    # Install Poetry
    pip.packages(
        name="Install poetry",
        packages=["poetry"],
        extra_install_args="--break-system-packages",
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
    )

    # Install basic packages
    apt.packages(
        name="Install basic packages",
        packages=["git", "curl", "wget", "build-essential"],
        _sudo=True,
    )

    # Deploy gunicornherder for zero-downtime systemd service management
    if is_zero_downtime(host_data):
        import djaploy.bin.gunicornherder as _herder_mod
        herder_src = Path(_herder_mod.__file__)
        files.put(
            name="Deploy gunicornherder",
            src=str(herder_src),
            dest="/usr/local/bin/gunicornherder",
            mode="0755",
            _sudo=True,
        )

    # Create external database directory if configured
    db_dir = getattr(host_data, 'db_dir', None)
    if db_dir:
        app_name = getattr(host_data, 'app_name', '')
        resolved_db_dir = db_dir.format(app_user=app_user, app_name=app_name)
        parent_dir = str(Path(resolved_db_dir).parent)
        for directory in [parent_dir, resolved_db_dir]:
            files.directory(
                name=f"Create {directory}",
                path=directory,
                user=app_user,
                group=app_user,
                _sudo=True,
            )

    # Set up zero-downtime directory structure
    if is_zero_downtime(host_data):
        app_path = get_app_path(host_data)
        apps_dir = f"/home/{app_user}/apps"
        for directory in [apps_dir, app_path]:
            files.directory(
                name=f"Create {directory}",
                path=directory,
                user=app_user,
                group=app_user,
                _sudo=True,
            )

        for subdir in ["releases", "shared"]:
            files.directory(
                name=f"Create {subdir} directory",
                path=f"{app_path}/{subdir}",
                user=app_user,
                group=app_user,
                _sudo=True,
            )

        shared_resources = getattr(host_data, 'shared_resources', None) or []
        if shared_resources:
            mkdir_commands = [
                f"mkdir -p {app_path}/shared/{resource}"
                for resource in shared_resources
            ]
            if mkdir_commands:
                mkdir_commands.append(f"chown -R {app_user}:{app_user} {app_path}/shared")
                server.shell(
                    name="Create shared resource directories",
                    commands=mkdir_commands,
                    _sudo=True,
                )


@deploy_hook("deploy:upload")
def upload_artifact(host_data, artifact_path):
    """Upload artifact, extract, and symlink shared resources."""
    from pyinfra import host
    from pyinfra.operations import server, files
    from djaploy.apps.core.infra.utils import is_zero_downtime, get_app_path
    from pathlib import Path
    from datetime import datetime

    app_user = getattr(host_data, 'app_user', 'app')
    ssh_user = getattr(host_data, 'ssh_user', 'deploy')
    app_path = get_app_path(host_data)
    artifact_filename = artifact_path.name

    # Create tars directory
    files.directory(
        name="Create tars directory",
        path=f"/home/{ssh_user}/tars",
        _sudo=False,
    )

    if is_zero_downtime(host_data):
        releases_path = f"{app_path}/releases"
        shared_path = f"{app_path}/shared"

        # Determine release name from artifact filename
        parts = artifact_filename.rsplit('.tar.gz', 1)[0]
        ref = parts.split('.', 1)[1] if '.' in parts else parts

        if ref == "local":
            ref = f"local-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        release_name = f"app-{ref}"
        release_path = f"{releases_path}/{release_name}"

        # Upload artifact
        files.put(
            name="Upload deployment artifact",
            src=str(artifact_path),
            dest=f"/home/{ssh_user}/tars/{artifact_filename}",
        )

        # Create release directory and extract
        files.directory(
            name=f"Create release directory {release_name}",
            path=release_path,
            user=app_user,
            group=app_user,
            _sudo=True,
        )

        server.shell(
            name=f"Extract artifact into {release_name}",
            commands=[
                f"tar -C {release_path} -xf /home/{ssh_user}/tars/{artifact_filename}",
                f"chown -R {app_user}:{app_user} {release_path}",
            ],
            _sudo=True,
        )

        # Symlink shared resources into the release
        shared_resources = getattr(host_data, 'shared_resources', None) or []
        if shared_resources:
            symlink_commands = []
            for resource in shared_resources:
                parent = str(Path(resource).parent)
                if parent and parent != '.':
                    symlink_commands.append(f"mkdir -p {release_path}/{parent}")
                symlink_commands.append(f"rm -rf {release_path}/{resource}")
                symlink_commands.append(
                    f"ln -sfn {shared_path}/{resource} {release_path}/{resource}"
                )
            server.shell(
                name="Symlink shared resources into release",
                commands=symlink_commands,
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
            )

        # Create stable build symlink (used by configure and pre phases)
        build_link = f"{app_path}/build"
        server.shell(
            name="Create stable build symlink",
            commands=[f"ln -sfn {release_path} {build_link}"],
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
        )

        # Store release path for later phases
        host.data._zero_downtime_release_path = release_path

        # Clean up old releases
        keep_releases = max(getattr(host_data, 'keep_releases', 5), 1)
        server.shell(
            name=f"Clean up old releases (keeping {keep_releases}, preserving active)",
            commands=[
                (
                    f"cd {releases_path} && "
                    f"ACTIVE=$(basename \"$(readlink -f {app_path}/current)\" 2>/dev/null) && "
                    f"ls -1t | grep -v \"^${{ACTIVE}}$\" | tail -n +{keep_releases + 1} | xargs -r rm -rf --"
                ),
            ],
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
        )
    else:
        # In-place deployment
        files.directory(
            name="Create application directory",
            path=app_path,
            user=app_user,
            group=app_user,
            _sudo=True,
        )

        files.put(
            name="Upload deployment artifact",
            src=str(artifact_path),
            dest=f"/home/{ssh_user}/tars/{artifact_filename}",
        )

        server.shell(
            name="Extract artifact and set permissions",
            commands=[
                f"tar -C {app_path} -xf /home/{ssh_user}/tars/{artifact_filename}",
                f"chown -R {app_user}:{app_user} {app_path}",
            ],
            _sudo=True,
        )


@deploy_hook("deploy:configure")
def configure_application(host_data, artifact_path):
    """Deploy config files, SSL certs, and install dependencies."""
    from djaploy.apps.core.infra.utils import (
        is_zero_downtime, get_app_path, deploy_config_files,
        generate_ssl_certificates, install_dependencies,
    )

    app_user = getattr(host_data, 'app_user', 'app')
    app_path = get_app_path(host_data)

    if is_zero_downtime(host_data):
        target_path = f"{app_path}/build"
    else:
        target_path = app_path

    deploy_config_files(host_data, target_path)

    if getattr(host_data, 'pregenerate_certificates', False):
        generate_ssl_certificates(host_data, app_user)

    install_dependencies(app_user, target_path, host_data)


@deploy_hook("deploy:configure")
def inject_local_settings(host_data, artifact_path):
    """Append hook-contributed local_settings to local.py."""
    from pyinfra.operations import server
    from djaploy.apps.core.infra.utils import is_zero_downtime, get_app_path

    local_settings_b64 = getattr(host_data, "local_settings_b64", None)
    if not local_settings_b64:
        return

    app_user = getattr(host_data, "app_user", "app")
    app_path = get_app_path(host_data)
    app_name = getattr(host_data, 'app_name', None)
    if not app_name:
        return

    if is_zero_downtime(host_data):
        base_path = f"{app_path}/build"
    else:
        base_path = app_path

    local_py = f"{base_path}/{app_name}/local.py"
    server.shell(
        name="Append hook-contributed settings to local.py",
        commands=[f"printf '%s' '{local_settings_b64}' | base64 -d >> {local_py}"],
        _sudo=True,
        _sudo_user=app_user,
    )


@deploy_hook("deploy:pre")
def activate_release(host_data, artifact_path):
    """Run migrations, collectstatic, and swap symlink (zero-downtime)."""
    from pyinfra import host
    from pyinfra.operations import server
    from djaploy.apps.core.infra.utils import (
        is_zero_downtime, get_app_path, run_migrations, collect_static,
    )

    app_user = getattr(host_data, 'app_user', 'app')

    if is_zero_downtime(host_data):
        base_path = get_app_path(host_data)
        build_path = f"{base_path}/build"
        release_path = getattr(host.data, '_zero_downtime_release_path', None)
        app_path = build_path if release_path else f"{base_path}/current"

        run_migrations(app_user, app_path, host_data)
        collect_static(app_user, app_path, host_data)

        # Atomic symlink swap
        if release_path:
            server.shell(
                name="Swap current symlink to new release",
                commands=[
                    f"ln -sfn {release_path} {base_path}/current.tmp && mv -Tf {base_path}/current.tmp {base_path}/current",
                ],
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
            )
    else:
        app_path = get_app_path(host_data)
        run_migrations(app_user, app_path, host_data)
        collect_static(app_user, app_path, host_data)


@deploy_hook("rollback")
def rollback_release(host_data, release=None):
    """Roll back to a previous release by swapping the current symlink."""
    import re
    from pyinfra.operations import server
    from djaploy.apps.core.infra.utils import get_app_path

    app_user = getattr(host_data, 'app_user', 'app')
    app_path = get_app_path(host_data)
    releases_path = f"{app_path}/releases"

    if release:
        if not re.match(r'^[a-zA-Z0-9._-]+$', release):
            raise ValueError(f"Invalid release name: {release}")
        server.shell(
            name=f"Roll back to release {release}",
            commands=[
                f'test -d {releases_path}/{release} || (echo "Release {release} not found" && exit 1)',
                f'ln -sfn {releases_path}/{release} {app_path}/current.tmp && mv -Tf {app_path}/current.tmp {app_path}/current',
                f'echo "Rolled back to {release}"',
            ],
            _sudo=True,
            _sudo_user=app_user,
        )
    else:
        rollback_cmd = (
            'CURR=$(basename "$(readlink -f {app}/current)") && '
            'PREV=$(cd {rels} && ls -1t | grep -v "^$CURR$" | head -n 1) && '
            'test -n "$PREV" || (echo "No previous release to roll back to" && exit 1) && '
            'ln -sfn {rels}/$PREV {app}/current.tmp && mv -Tf {app}/current.tmp {app}/current && '
            'echo "Rolled back to $PREV"'
        ).format(app=app_path, rels=releases_path)
        server.shell(
            name="Roll back to previous release",
            commands=[rollback_cmd],
            _sudo=True,
            _sudo_user=app_user,
        )
