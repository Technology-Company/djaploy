"""
Janitor user hooks for djaploy.

Creates the janitor (deploy/ssh) user on target servers with sudo access.
This is typically the first command run on a fresh server, connecting as root
to bootstrap the deploy user that all subsequent commands use.
"""

import importlib.util
import os
import sys
import tempfile

from djaploy.hooks import deploy_hook, hook


@hook("createjanitoruser:precommand")
def override_inventory_to_root(context):
    """Rewrite the inventory so pyinfra connects as root.

    The janitor user doesn't exist yet, so we must SSH in as root
    regardless of what ssh_user the inventory defines.
    """
    inventory_file = context["inventory_file"]

    spec = importlib.util.spec_from_file_location("_janitor_inv", inventory_file)
    module = importlib.util.module_from_spec(spec)

    try:
        sys.modules["_janitor_inv"] = module
        spec.loader.exec_module(module)
        hosts = getattr(module, "hosts", [])
    finally:
        sys.modules.pop("_janitor_inv", None)

    # Write a temp inventory with ssh_user forced to root
    fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="janitor_inv_")
    with os.fdopen(fd, "w") as f:
        f.write("# Auto-generated inventory (ssh_user overridden to root)\n\n")
        f.write("hosts = [\n")
        for host_entry in hosts:
            if isinstance(host_entry, tuple) and len(host_entry) == 2:
                name, data = host_entry
                data = dict(data)
                data["_original_ssh_user"] = data.get("ssh_user", "deploy")
                data["ssh_user"] = "root"
                f.write(f"    ({repr(name)}, {repr(data)}),\n")
            else:
                f.write(f"    {repr(host_entry)},\n")
        f.write("]\n")

    context["_janitor_original_inventory"] = inventory_file
    context["inventory_file"] = tmp_path


@hook("createjanitoruser:postcommand")
def cleanup_temp_inventory(context):
    """Remove the temporary root-override inventory file."""
    original = context.get("_janitor_original_inventory")
    if original:
        temp_inventory = context.get("inventory_file")
        if temp_inventory and temp_inventory != original:
            try:
                os.unlink(temp_inventory)
            except OSError:
                pass
        # Restore the original inventory path
        context["inventory_file"] = original


@deploy_hook("createjanitoruser")
def create_janitor_user(host_data):
    """Create the SSH/deploy user, set plaintext password, and grant sudo access."""
    from pyinfra.operations import server, apt, files

    # Read the original ssh_user (saved by precommand hook before overriding to root)
    ssh_user = getattr(host_data, '_original_ssh_user', 'deploy')
    password = getattr(host_data, 'janitor_password', None)

    if not password:
        raise ValueError(
            "janitor_password must be set on HostConfig to create the janitor user."
        )

    # Ensure sudo is installed
    apt.packages(
        name="Ensure sudo is installed",
        packages=["sudo"],
        _sudo=True,
    )

    # Create the user and add to sudo group
    server.user(
        name=f"Create janitor user '{ssh_user}'",
        user=ssh_user,
        shell="/bin/bash",
        groups=["sudo"],
        create_home=True,
        _sudo=True,
    )

    # Set password using chpasswd (accepts plaintext, via printf to avoid shell escaping)
    import shlex
    server.shell(
        name=f"Set password for '{ssh_user}'",
        commands=[
            f"printf '%s\\n' {shlex.quote(f'{ssh_user}:{password}')} | chpasswd",
        ],
        _sudo=True,
    )

    # Set up SSH directory
    files.directory(
        name=f"Create .ssh directory for '{ssh_user}'",
        path=f"/home/{ssh_user}/.ssh",
        user=ssh_user,
        group=ssh_user,
        mode="0700",
        _sudo=True,
    )

    # Copy authorized_keys from root if available
    server.shell(
        name=f"Copy root authorized_keys to '{ssh_user}'",
        commands=[
            f"test -f /root/.ssh/authorized_keys && "
            f"cp /root/.ssh/authorized_keys /home/{ssh_user}/.ssh/authorized_keys && "
            f"chown {ssh_user}:{ssh_user} /home/{ssh_user}/.ssh/authorized_keys && "
            f"chmod 600 /home/{ssh_user}/.ssh/authorized_keys || true",
        ],
        _sudo=True,
    )
