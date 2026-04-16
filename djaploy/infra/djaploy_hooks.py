"""
Core hooks for djaploy.

Handles server configuration, application deployment, post-deploy tasks
(migrations, collectstatic, symlink swap), and rollback.
"""

from djaploy.hooks import deploy_hook


def _update_nginx_upstream(host_data, app_name, target_slot):
    """Render and deploy the bluegreen nginx upstream config.

    Skipped when nginx_conf.custom is set — custom setups handle
    their own upstream via activate:post hooks.
    """
    from io import StringIO
    from pyinfra.operations import files
    from djaploy.infra.templates import NGINX_UPSTREAM_BLUEGREEN, build_template_context
    from jinja2 import Environment

    ctx = build_template_context(host_data)
    ctx["active_slot"] = target_slot
    upstream_content = Environment().from_string(NGINX_UPSTREAM_BLUEGREEN).render(**ctx)

    nginx_cfg = getattr(host_data, 'nginx_conf', None) or {}
    if not nginx_cfg.get("custom"):
        files.put(
            name=f"Update nginx upstream to {target_slot} slot",
            src=StringIO(upstream_content),
            dest=f"/etc/nginx/sites-available/{app_name}-upstream.conf",
            _sudo=True,
        )


def _read_slot_info_from_remote(host, slot, state_file):
    """Read a slot's deployment info from state.json on the remote server.

    Uses host.run_shell_command (inside a python.call callback) to cat
    the file and parse it locally.  Returns the slot dict or {}.
    """
    import json

    sudo_password = getattr(host.data, '_sudo_password', None)
    kwargs = {"command": f"cat {state_file}", "_sudo": True}
    if sudo_password:
        kwargs["_sudo_password"] = sudo_password

    result = host.run_shell_command(**kwargs)
    status = result[0]
    output = result[1] if len(result) > 1 else None

    if status and output:
        try:
            # pyinfra returns OutputLine objects with a .line attribute
            lines = [line.line if hasattr(line, 'line') else str(line) for line in output]
            raw = "\n".join(lines)
            state_data = json.loads(raw)
            return state_data.get("slots", {}).get(slot) or {}
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
    return {}


@deploy_hook("configure")
def configure_server(host_data):
    """Configure basic server requirements: user, Python, Poetry, directories."""
    from pyinfra.operations import apt, server, pip, files
    from djaploy.infra.utils import (
        is_zero_downtime, is_bluegreen, get_app_path, install_python,
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

    # Add www-data to app user group so nginx can serve static/media files
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

    # Set up blue-green directory structure
    if is_bluegreen(host_data):
        from djaploy.infra.bluegreen import init_state_cmd

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

        for subdir in ["slots", "slots/blue", "slots/green", "shared"]:
            files.directory(
                name=f"Create {subdir} directory",
                path=f"{app_path}/{subdir}",
                user=app_user,
                group=app_user,
                _sudo=True,
            )

        # Initialize state.json
        state_file = f"{app_path}/state.json"
        server.shell(
            name="Initialize blue-green state file",
            commands=[init_state_cmd(state_file)],
            _sudo=True,
            _sudo_user=app_user,
        )

        shared_resources = getattr(host_data, 'shared_resources', None) or []
        if shared_resources:
            mkdir_commands = [
                f"mkdir -p {app_path}/shared/{resource}"
                for resource in shared_resources
            ]
            mkdir_commands.append(f"chown -R {app_user}:{app_user} {app_path}/shared")
            server.shell(
                name="Create shared resource directories",
                commands=mkdir_commands,
                _sudo=True,
            )

    # Set up zero-downtime directory structure
    elif is_zero_downtime(host_data):
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
    from pyinfra.facts.server import Command
    from djaploy.infra.utils import is_zero_downtime, is_bluegreen, get_app_path
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

    if is_bluegreen(host_data):
        from djaploy.infra.bluegreen import read_active_slot_cmd

        shared_path = f"{app_path}/shared"
        state_file = f"{app_path}/state.json"

        # Determine release name from artifact filename
        parts = artifact_filename.rsplit('.tar.gz', 1)[0]
        ref = parts.split('.', 1)[1] if '.' in parts else parts
        if ref == "local":
            ref = f"local-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        release_name = f"app-{ref}"

        # Ensure slot directories and state.json exist (in case configure wasn't run)
        from djaploy.infra.bluegreen import init_state_cmd
        server.shell(
            name="Ensure blue-green directories exist",
            commands=[
                f"mkdir -p {app_path}/slots/blue {app_path}/slots/green {app_path}/shared",
                init_state_cmd(state_file),
            ],
            _sudo=True,
            _sudo_user=app_user,
        )

        # Determine inactive slot by reading state.json
        active_slot = host.get_fact(
            Command,
            read_active_slot_cmd(state_file),
        )
        active_slot = (active_slot or "").strip()
        target_slot = "green" if active_slot == "blue" else "blue"
        slot_path = f"{app_path}/slots/{target_slot}"

        # Upload artifact
        files.put(
            name="Upload deployment artifact",
            src=str(artifact_path),
            dest=f"/home/{ssh_user}/tars/{artifact_filename}",
        )

        # Clear target slot and extract
        server.shell(
            name=f"Clear and extract artifact into {target_slot} slot",
            commands=[
                f"find {slot_path} -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {{}} +",
                f"tar -C {slot_path} -xf /home/{ssh_user}/tars/{artifact_filename}",
                f"chown -R {app_user}:{app_user} {slot_path}",
            ],
            _sudo=True,
        )

        # Symlink shared resources into the slot
        shared_resources = getattr(host_data, 'shared_resources', None) or []
        if shared_resources:
            symlink_commands = []
            for resource in shared_resources:
                parent = str(Path(resource).parent)
                if parent and parent != '.':
                    symlink_commands.append(f"mkdir -p {slot_path}/{parent}")
                symlink_commands.append(f"rm -rf {slot_path}/{resource}")
                symlink_commands.append(
                    f"ln -sfn {shared_path}/{resource} {slot_path}/{resource}"
                )
            server.shell(
                name="Symlink shared resources into slot",
                commands=symlink_commands,
                _sudo=True,
                _sudo_user=app_user,
                _use_sudo_login=True,
            )

        # Create stable build symlink (used by configure and pre phases)
        build_link = f"{app_path}/build"
        server.shell(
            name="Create stable build symlink to target slot",
            commands=[f"ln -sfn {slot_path} {build_link}"],
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
        )

        # Store slot info for later phases
        host.data._bluegreen_target_slot = target_slot
        host.data._bluegreen_slot_path = slot_path
        host.data._bluegreen_release_name = release_name

    elif is_zero_downtime(host_data):
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
    from djaploy.infra.utils import (
        is_zero_downtime, is_bluegreen, get_app_path, deploy_config_files,
        generate_ssl_certificates, install_dependencies,
    )

    app_user = getattr(host_data, 'app_user', 'app')
    app_path = get_app_path(host_data)

    if is_zero_downtime(host_data) or is_bluegreen(host_data):
        target_path = f"{app_path}/build"
    else:
        target_path = app_path

    deploy_config_files(host_data, target_path)

    if getattr(host_data, 'pregenerate_certificates', False):
        generate_ssl_certificates(host_data, app_user)

    install_dependencies(app_user, target_path, host_data)


@deploy_hook("deploy:configure")
def generate_local_settings(host_data, artifact_path):
    """Generate local.py with deployment settings derived from HostConfig.

    Requires ``generate_local_settings=True`` on HostConfig.

    Writes DATABASES, ALLOWED_HOSTS, DEBUG, STATIC_ROOT, and MEDIA_ROOT
    based on HostConfig fields (db_dir, app_hostname, app_user, app_name).

    The project's settings must import local.py::

        try:
            from .local import *  # noqa
        except ImportError:
            pass
    """
    import posixpath
    from pyinfra.operations import files
    from djaploy.infra.utils import is_zero_downtime, is_bluegreen, get_app_path

    if not getattr(host_data, "generate_local_settings", False):
        return

    app_user = getattr(host_data, "app_user", "app")
    app_name = getattr(host_data, "app_name", None)
    if not app_name:
        return

    app_path = get_app_path(host_data)
    db_dir = getattr(host_data, "db_dir", None)
    app_hostname = getattr(host_data, "app_hostname", None)

    manage_py_path = getattr(host_data, "manage_py_path", "manage.py")
    manage_subdir = posixpath.dirname(manage_py_path)

    if is_zero_downtime(host_data) or is_bluegreen(host_data):
        base_path = f"{app_path}/build"
    else:
        base_path = app_path

    if manage_subdir:
        local_py = f"{base_path}/{manage_subdir}/{app_name}/settings/local.py"
    else:
        local_py = f"{base_path}/{app_name}/settings/local.py"

    # Build local.py content
    lines = ["# Generated by djaploy — do not edit manually\n"]

    if db_dir:
        core_conf = getattr(host_data, "core_conf", None) or {}
        databases = core_conf.get("databases", ["default:db.sqlite3"])
        if isinstance(databases, str):
            databases = [databases]

        # Parse database entries: each can be "alias:filename", "alias",
        # or just a filename like "db.sqlite3" (alias defaults to "default").
        lines.append("DATABASES = {")
        for i, entry in enumerate(databases):
            if ":" in entry:
                alias, db_file = entry.split(":", 1)
            elif entry.endswith((".db", ".sqlite3")):
                # Bare filename — first entry gets alias "default"
                alias = "default" if i == 0 else entry.rsplit(".", 1)[0]
                db_file = entry
            else:
                # Bare alias name — derive filename
                alias = entry
                db_file = f"{entry}.db"
            lines.append(f'    "{alias}": {{')
            lines.append(f'        "ENGINE": "django.db.backends.sqlite3",')
            lines.append(f'        "NAME": "{db_dir}/{db_file}",')
            lines.append(f'    }},')
        lines.append("}\n")

    if app_hostname:
        lines.append(f'ALLOWED_HOSTS = ["{app_hostname}"]\n')

    lines.append("DEBUG = False\n")

    if is_zero_downtime(host_data) or is_bluegreen(host_data):
        shared_path = f"{app_path}/shared"
        lines.append(f'STATIC_ROOT = "{shared_path}/staticfiles"\n')
        lines.append(f'MEDIA_ROOT = "{shared_path}/media"\n')

    content = "\n".join(lines)

    from djaploy.utils import temp_files

    tmp_path = temp_files.create(suffix=".py")
    with open(tmp_path, "w") as f:
        f.write(content)

    files.put(
        name="Deploy local.py settings",
        src=tmp_path,
        dest=local_py,
        user=app_user,
        group=app_user,
        mode="644",
        _sudo=True,
    )


@deploy_hook("deploy:configure")
def inject_local_settings(host_data, artifact_path):
    """Append hook-contributed local_settings to local.py."""
    import posixpath
    from pyinfra.operations import server
    from djaploy.infra.utils import is_zero_downtime, is_bluegreen, get_app_path

    local_settings_b64 = getattr(host_data, "local_settings_b64", None)
    if not local_settings_b64:
        return

    app_user = getattr(host_data, "app_user", "app")
    app_path = get_app_path(host_data)
    app_name = getattr(host_data, 'app_name', None)
    if not app_name:
        return

    manage_py_path = getattr(host_data, "manage_py_path", "manage.py")
    manage_subdir = posixpath.dirname(manage_py_path)

    if is_zero_downtime(host_data) or is_bluegreen(host_data):
        base_path = f"{app_path}/build"
    else:
        base_path = app_path

    if manage_subdir:
        local_py = f"{base_path}/{manage_subdir}/{app_name}/settings/local.py"
    else:
        local_py = f"{base_path}/{app_name}/settings/local.py"

    server.shell(
        name="Append hook-contributed settings to local.py",
        commands=[f"printf '%s' '{local_settings_b64}' | base64 -d >> {local_py}"],
        _sudo=True,
        _sudo_user=app_user,
    )


@deploy_hook("deploy:pre")
def activate_release(host_data, artifact_path):
    """Run migrations, collectstatic, and swap symlink (zero-downtime) or
    update state (bluegreen)."""
    from pyinfra import host
    from pyinfra.operations import server
    from djaploy.infra.utils import (
        is_zero_downtime, is_bluegreen, get_app_path,
        run_migrations, collect_static, get_slot_socket_path,
    )

    app_user = getattr(host_data, 'app_user', 'app')

    if is_bluegreen(host_data):
        base_path = get_app_path(host_data)
        build_path = f"{base_path}/build"
        app_name = getattr(host_data, 'app_name', None)
        state_file = f"{base_path}/state.json"

        target_slot = getattr(host.data, '_bluegreen_target_slot', 'blue')
        slot_path = getattr(host.data, '_bluegreen_slot_path', f"{base_path}/slots/{target_slot}")
        release_name = getattr(host.data, '_bluegreen_release_name', 'unknown')

        run_migrations(app_user, build_path, host_data)
        collect_static(app_user, build_path, host_data)

        # Update state.json on the remote server.
        # Serialize release_name and commit with json.dumps to safely
        # handle quotes and special characters in branch names / messages.
        import json as _json
        commit = getattr(host_data, 'commit', 'unknown')
        safe_release = _json.dumps(release_name)
        safe_commit = _json.dumps(commit)
        socket_path = get_slot_socket_path(app_name, target_slot)
        server.shell(
            name="Update state.json with deployment info",
            commands=[
                f"python3 << 'PYEOF'\n"
                f"import json, os, datetime\n"
                f"venv_path = os.readlink('{slot_path}/.venv') if os.path.islink('{slot_path}/.venv') else 'unknown'\n"
                f"python_path = venv_path + '/bin/python' if venv_path != 'unknown' else 'unknown'\n"
                f"s = json.load(open('{state_file}'))\n"
                f"s['slots']['{target_slot}'] = {{\n"
                f"    'release': {safe_release},\n"
                f"    'commit': {safe_commit},\n"
                f"    'deployed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),\n"
                f"    'python_interpreter': python_path,\n"
                f"    'venv_path': venv_path,\n"
                f"}}\n"
                f"f = open('{state_file}.tmp', 'w')\n"
                f"json.dump(s, f, indent=2)\n"
                f"f.close()\n"
                f"os.rename('{state_file}.tmp', '{state_file}')\n"
                f"PYEOF"
            ],
            _sudo=True,
            _sudo_user=app_user,
        )

        # Print deployment summary locally by reading state from remote
        from pyinfra.operations import python as python_op

        def _print_deploy_summary(state_f, t_slot, rel, sock):
            info = _read_slot_info_from_remote(host, t_slot, state_f)

            print("\n=== Blue-Green Deploy Summary ===")
            print(f"  Slot:     {t_slot}")
            print(f"  Release:  {rel}")
            print(f"  Socket:   {sock}")
            print(f"  Venv:     {info.get('venv_path', 'unknown')}")
            print(f"  Python:   {info.get('python_interpreter', 'unknown')}")
            print("\n  Status: staged (run 'djaploy activate' to switch traffic)\n")

        python_op.call(
            name="Print deployment summary",
            function=_print_deploy_summary,
            state_f=state_file,
            t_slot=target_slot,
            rel=release_name,
            sock=socket_path,
        )

    elif is_zero_downtime(host_data):
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
    """Roll back to a previous release by swapping the current symlink,
    or by switching the active blue-green slot."""
    import re
    from pyinfra.operations import server
    from djaploy.infra.utils import is_bluegreen, get_app_path

    app_user = getattr(host_data, 'app_user', 'app')
    app_path = get_app_path(host_data)

    if is_bluegreen(host_data):
        from pyinfra import host
        from pyinfra.facts.server import Command
        from djaploy.infra.bluegreen import (
            read_active_slot_cmd, set_active_slot_cmd, other_slot,
        )
        from djaploy.infra.utils import get_slot_socket_path
        from io import StringIO
        from djaploy.infra.templates import NGINX_UPSTREAM_BLUEGREEN, build_template_context
        from jinja2 import Environment

        app_name = getattr(host_data, 'app_name', None)
        state_file = f"{app_path}/state.json"

        # Read active slot and compute target
        active_slot = host.get_fact(Command, read_active_slot_cmd(state_file))
        active_slot = (active_slot or "").strip()
        target_slot = other_slot(active_slot) if active_slot else "blue"

        # Verify the target slot has a deployment before switching
        server.shell(
            name=f"Verify {target_slot} slot has a deployment for rollback",
            commands=[
                f"python3 -c \""
                f"import json, sys; s=json.load(open('{state_file}')); "
                f"sys.exit(0 if s['slots'].get('{target_slot}') else 1)"
                f"\" || (echo 'ROLLBACK ABORTED: slot {target_slot} has no deployment.' && exit 1)",
            ],
            _sudo=True,
        )

        # Health check: verify the target slot is running before rolling back.
        # Retries up to 3 times with 2s delay.
        socket_path = get_slot_socket_path(app_name, target_slot)
        server.shell(
            name=f"Health check: verify {target_slot} slot is healthy for rollback",
            commands=[
                f"for attempt in 1 2 3; do "
                f"HTTP_CODE=$(curl -o /dev/null -w '%{{http_code}}' --max-time 5 --unix-socket {socket_path} http://localhost/ 2>/dev/null); "
                f"if [ \"$HTTP_CODE\" != \"000\" ]; then "
                f"echo \"Health check passed: {target_slot} slot responding (HTTP $HTTP_CODE)\"; exit 0; fi; "
                f"echo \"Health check attempt $attempt/3: no response, retrying in 2s...\"; "
                f"sleep 2; "
                f"done; "
                f"echo 'ROLLBACK ABORTED: {target_slot} slot is not responding on {socket_path}' && "
                f"echo 'Check: systemctl status {app_name}-{target_slot}.service' && "
                f"exit 1",
            ],
            _sudo=True,
        )

        # Store for activate:post hooks (timers, streaming, custom nginx)
        host.data._bluegreen_activated_slot = target_slot

        # Update nginx upstream for non-custom setups
        _update_nginx_upstream(host_data, app_name, target_slot)

        # Reload nginx
        server.shell(
            name="Test and reload nginx for rollback",
            commands=["nginx -t && nginx -s reload"],
            _sudo=True,
        )

        # Update state.json
        server.shell(
            name=f"Set active slot to {target_slot} (rollback)",
            commands=[set_active_slot_cmd(state_file, target_slot)],
            _sudo=True,
            _sudo_user=app_user,
        )

        # Print summary
        from pyinfra.operations import python as python_op

        def _print_rollback_summary(state_f, t_slot, p_slot):
            info = _read_slot_info_from_remote(host, t_slot, state_f)
            print("\n=== Blue-Green Rollback ===")
            print(f"  Switched:  {p_slot} -> {t_slot}")
            print(f"  Release:   {info.get('release', 'unknown')}")
            print(f"  Python:    {info.get('python_interpreter', 'unknown')}")
            print()

        python_op.call(
            name="Print rollback summary",
            function=_print_rollback_summary,
            state_f=state_file,
            t_slot=target_slot,
            p_slot=active_slot or "none",
        )
        return

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


@deploy_hook("activate")
def activate_bluegreen(host_data):
    """Activate the staged blue-green slot by switching nginx upstream."""
    from pyinfra import host
    from pyinfra.operations import server
    from pyinfra.facts.server import Command
    from djaploy.infra.utils import (
        is_bluegreen, get_app_path, get_slot_socket_path,
        get_slot_service_name,
    )
    from djaploy.infra.bluegreen import (
        read_active_slot_cmd, set_active_slot_cmd, other_slot,
    )

    if not is_bluegreen(host_data):
        return

    app_user = getattr(host_data, 'app_user', 'app')
    app_name = getattr(host_data, 'app_name', None)
    app_path = get_app_path(host_data)
    state_file = f"{app_path}/state.json"

    # If called from deploy --activate, the target slot is already known
    # and we can skip the stale-fact verification.
    deploy_target = getattr(host.data, '_bluegreen_target_slot', None)
    if deploy_target:
        # deploy --activate: we just deployed to this slot, activate it
        active_slot_raw = host.get_fact(
            Command,
            read_active_slot_cmd(state_file),
        )
        active_slot = (active_slot_raw or "").strip()
        target_slot = deploy_target
    else:
        # Standalone activate: read state and switch to the other slot
        active_slot_raw = host.get_fact(
            Command,
            read_active_slot_cmd(state_file),
        )
        active_slot = (active_slot_raw or "").strip()
        target_slot = other_slot(active_slot) if active_slot else "blue"

        # Verify target slot has a deployment (use server.shell to avoid
        # fact caching issues — the check runs at execution time, not plan time)
        current_display = active_slot or "none"
        server.shell(
            name=f"Verify {target_slot} slot has a deployment",
            commands=[
                f"python3 -c \""
                f"import json, sys; s=json.load(open('{state_file}')); "
                f"sys.exit(0 if s['slots'].get('{target_slot}') else 1)"
                f"\" || (echo 'Nothing to activate: slot {target_slot} has no deployment.' && "
                f"echo 'Current active slot: {current_display}' && "
                f"echo 'Run djaploy deploy first to stage a new version.' && exit 1)",
            ],
            _sudo=True,
        )

    # Store on host.data so activate:post hooks can read it
    host.data._bluegreen_activated_slot = target_slot

    # Health check: verify all target slot services are running before
    # switching nginx. Abort activation if any service is down.
    services = getattr(host_data, "services", []) or []
    failed_checks = []
    for svc in services:
        slot_svc = get_slot_service_name(svc, target_slot)
        server.shell(
            name=f"Health check: verify {slot_svc} is active",
            commands=[
                f"if systemctl is-active {slot_svc}.service > /dev/null 2>&1; then "
                f"echo 'Health check passed: {slot_svc} is active'; "
                f"else "
                f"echo 'ACTIVATION ABORTED: {slot_svc} is not running.' && "
                f"echo 'Check: systemctl status {slot_svc}.service' && "
                f"exit 1; "
                f"fi",
            ],
            _sudo=True,
        )

    # Extra gunicorn-specific check: verify the socket accepts HTTP requests.
    # Retries up to 3 times with 2s delay to allow freshly started gunicorn to boot.
    socket_path = get_slot_socket_path(app_name, target_slot)
    server.shell(
        name=f"Health check: verify {target_slot} gunicorn socket responds",
        commands=[
            f"for attempt in 1 2 3 4 5; do "
            f"HTTP_CODE=$(curl -o /dev/null -w '%{{http_code}}' --max-time 5 --unix-socket {socket_path} http://localhost/ 2>/dev/null); "
            f"if [ \"$HTTP_CODE\" != \"000\" ]; then "
            f"echo \"Health check passed: gunicorn responding (HTTP $HTTP_CODE)\"; exit 0; fi; "
            f"echo \"Health check attempt $attempt/5: no response, retrying in 3s...\"; "
            f"sleep 3; "
            f"done; "
            f"echo 'ACTIVATION ABORTED: gunicorn not responding on {socket_path}' && "
            f"echo 'Check: systemctl status {app_name}-{target_slot}.service' && "
            f"exit 1",
        ],
        _sudo=True,
    )

    # Update nginx upstream config to point to target slot
    _update_nginx_upstream(host_data, app_name, target_slot)

    # Reload nginx
    server.shell(
        name="Test and reload nginx",
        commands=["nginx -t && nginx -s reload"],
        _sudo=True,
    )

    # Update state.json on remote
    prev_slot = active_slot or "none"
    server.shell(
        name=f"Set active slot to {target_slot}",
        commands=[set_active_slot_cmd(state_file, target_slot)],
        _sudo=True,
        _sudo_user=app_user,
    )

    # Print activation summary locally by reading state from remote
    from pyinfra.operations import python as python_op

    def _print_activate_summary(state_f, t_slot, p_slot, sock):
        new_info = _read_slot_info_from_remote(host, t_slot, state_f)
        old_info = _read_slot_info_from_remote(host, p_slot, state_f) if p_slot != "none" else {}

        print("\n=== Blue-Green Activation ===")
        print(f"  Switched:    {p_slot} -> {t_slot}")
        print(f"  Socket:      {sock} (now serving production)")
        print(f"  Deployed at: {new_info.get('deployed_at', 'unknown')}")

        new_release = new_info.get('release', 'unknown')
        old_release = old_info.get('release', 'unknown') if old_info else 'n/a'
        print(f"  Release:     {old_release} -> {new_release}")

        new_python = new_info.get('python_interpreter', 'unknown')
        old_python = old_info.get('python_interpreter', 'unknown') if old_info else 'n/a'
        if old_python == new_python:
            print(f"  Python:      {new_python}")
        else:
            print(f"  Python:      {old_python} -> {new_python}")

        new_venv = new_info.get('venv_path', 'unknown')
        old_venv = old_info.get('venv_path', 'unknown') if old_info else 'n/a'
        if old_venv == new_venv:
            print(f"  Venv:        {new_venv}")
        else:
            print(f"  Venv:        {old_venv} -> {new_venv}")

        print(f"\n  Previous slot ({p_slot}) is still running.")
        print("  To rollback: manage.py djaploy rollback --env <env>\n")

    python_op.call(
        name="Print activation summary",
        function=_print_activate_summary,
        state_f=state_file,
        t_slot=target_slot,
        p_slot=prev_slot,
        sock=socket_path,
    )
