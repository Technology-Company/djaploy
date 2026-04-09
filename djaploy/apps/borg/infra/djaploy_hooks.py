"""
Borg backup hooks for djaploy.

Manages BorgBackup-based backups over SSH: installs borgbackup, initializes
remote repositories, deploys backup scripts, sets up cron jobs, and handles
restore from backup.

Compatible with standard SSH borg servers and Hetzner Storage Boxes
(set repo_port=23 and use relative repo_path like "./backups").

SSH key auth must be set up on the target server beforehand. If the key
is not the default, set ``ssh_key`` to the path on the target server
(e.g. ``/home/app/.ssh/id_ed25519``).
"""

import shlex

from djaploy.hooks import deploy_hook


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------

def _build_repo_url(borg_config, repo_name: str) -> str:
    """Build the borg repository URL.

    When repo_host is set, builds an SSH URL.
    When repo_host is empty/None, uses a local path (for testing).
    """
    repo_host = borg_config.get("repo_host", "")
    repo_path = borg_config.get("repo_path", None) or "./backups"

    if not repo_host:
        return f"{repo_path}/{repo_name}"

    repo_user = borg_config.get("repo_user", "borg")
    repo_port = borg_config.get("repo_port", 22)
    return f"ssh://{repo_user}@{repo_host}:{repo_port}/{repo_path}/{repo_name}"


def _build_borg_rsh(borg_config) -> str:
    """Build BORG_RSH value from config.

    Returns empty string for local repos (no repo_host).

    When ``ssh_known_hosts_file`` is set, uses strict host key checking
    with that file.  This is recommended for Hetzner Storage Boxes —
    add the box's fingerprint to a known_hosts file on the target server
    and point this setting at it.
    """
    repo_host = borg_config.get("repo_host", "")
    if not repo_host:
        return ""

    repo_port = borg_config.get("repo_port", 22)
    ssh_key = borg_config.get("ssh_key", "")
    known_hosts = borg_config.get("ssh_known_hosts_file", "")

    if known_hosts:
        # The file is deployed to ~/.ssh/borg_known_hosts by configure_borg
        rsh = f"ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile=~/.ssh/borg_known_hosts -p {repo_port}"
    else:
        rsh = f"ssh -o StrictHostKeyChecking=accept-new -p {repo_port}"
    if ssh_key:
        rsh += f" -i {ssh_key}"
    return rsh


def _remote_path_arg(borg_config) -> str:
    """Build --remote-path argument if configured.

    For Hetzner Storage Boxes, set remote_path to e.g. "borg-1.4" to
    select the server-side borg version.
    """
    remote_path = borg_config.get("remote_path", None)
    if remote_path:
        return f" --remote-path {shlex.quote(remote_path)}"
    return ""


def _init_borg_repo(borg_config, app_user: str, repo_name: str):
    """Initialize borg repository on remote server if not already initialized."""
    from pyinfra.operations import server

    passphrase = borg_config.get("passphrase", "")
    repo_url = _build_repo_url(borg_config, repo_name)
    borg_rsh = _build_borg_rsh(borg_config)

    rsh_export = f'export BORG_RSH={shlex.quote(borg_rsh)} && ' if borg_rsh else ''

    # Ensure parent directory exists (for local repos)
    if not borg_config.get("repo_host"):
        from pyinfra.operations import files
        import posixpath
        parent = posixpath.dirname(repo_url)
        files.directory(
            name="Create borg repo parent directory",
            path=parent,
            user=app_user,
            group=app_user,
            _sudo=True,
        )

    remote_path = _remote_path_arg(borg_config)

    # Unset PYTHONPATH to prevent borg (a Python script) from picking up
    # msgpack or other packages from the app's venv.
    # Break any stale locks first — a previous backup may have crashed
    # (e.g. storage full) and left a lock that blocks borg info.
    server.shell(
        name="Initialize borg repository if needed",
        commands=[
            f'unset PYTHONPATH && '
            f'export BORG_PASSPHRASE={shlex.quote(passphrase)} && '
            f'{rsh_export}'
            f'borg break-lock{remote_path} {shlex.quote(repo_url)} 2>/dev/null || true; '
            f'borg info{remote_path} {shlex.quote(repo_url)} > /dev/null 2>&1 || '
            f'borg init{remote_path} --encryption=repokey-blake2 {shlex.quote(repo_url)} || true'
        ],
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
    )


def _generate_backup_script(borg_config, app_user: str, repo_name: str,
                            host_data) -> str:
    """Generate borg backup script content."""
    passphrase = borg_config.get("passphrase", "")
    compression = borg_config.get("compression", "zstd,3")
    databases = borg_config.get("databases", ["default.db"])
    backup_media = borg_config.get("backup_media", True)
    keep_within = borg_config.get("keep_within", None)
    keep_hourly = borg_config.get("keep_hourly", 0)
    keep_daily = borg_config.get("keep_daily", 7)
    keep_weekly = borg_config.get("keep_weekly", 4)
    keep_monthly = borg_config.get("keep_monthly", 6)

    app_name = getattr(host_data, 'app_name', '') if not isinstance(host_data, dict) else host_data.get('app_name', '')
    # Fall back to HostConfig.db_dir, then generic default
    host_db_dir = getattr(host_data, 'db_dir', None) if not isinstance(host_data, dict) else host_data.get('db_dir')
    db_path = borg_config.get("db_path") or host_db_dir or f"/home/{app_user}/dbs"
    deployment_strategy = (
        getattr(host_data, 'deployment_strategy', 'zero_downtime')
        if not isinstance(host_data, dict)
        else host_data.get('deployment_strategy', 'zero_downtime')
    )
    if deployment_strategy == 'zero_downtime':
        default_media = f"/home/{app_user}/apps/{app_name}/shared/media"
    else:
        default_media = f"/home/{app_user}/apps/{app_name}/media"
    media_path = borg_config.get("media_path") or default_media

    if isinstance(databases, str):
        databases = [databases]

    db_array = " ".join([f'"{db}"' for db in databases])
    repo_url = _build_repo_url(borg_config, repo_name)
    borg_rsh = _build_borg_rsh(borg_config)
    rsh_export = f'\nexport BORG_RSH={shlex.quote(borg_rsh)}' if borg_rsh else ''
    remote_path = borg_config.get("remote_path", None)
    remote_path_export = f'\nexport BORG_REMOTE_PATH={shlex.quote(remote_path)}' if remote_path else ''

    # Build prune retention args
    prune_parts = []
    if keep_within:
        prune_parts.append(f"--keep-within={keep_within}")
    if keep_hourly:
        prune_parts.append(f"--keep-hourly={keep_hourly}")
    prune_parts.append(f"--keep-daily={keep_daily}")
    prune_parts.append(f"--keep-weekly={keep_weekly}")
    prune_parts.append(f"--keep-monthly={keep_monthly}")
    prune_args = " \\\n    ".join(prune_parts) + " "

    # Media section
    if backup_media:
        media_section = f'''
# Include media directory if it exists
MEDIA_ARGS=""
if [ -d "{media_path}" ] && [ "$(ls -A "{media_path}" 2>/dev/null)" ]; then
    MEDIA_ARGS="{media_path}"
    log_message "Including media directory in backup"
else
    log_message "Media directory not found or empty, skipping"
fi
'''
        create_cmd_media = f'''
if [ -n "$MEDIA_ARGS" ]; then
    borg create \\
        --compression {compression} \\
        --stats \\
        "$BORG_REPO::$ARCHIVE_NAME" \\
        "$TEMP_DIR/dbs" \\
        "$MEDIA_ARGS" \\
        2>&1 | tee -a "$LOG_FILE"
else
    borg create \\
        --compression {compression} \\
        --stats \\
        "$BORG_REPO::$ARCHIVE_NAME" \\
        "$TEMP_DIR/dbs" \\
        2>&1 | tee -a "$LOG_FILE"
fi'''
    else:
        media_section = ""
        create_cmd_media = f'''
borg create \\
    --compression {compression} \\
    --stats \\
    "$BORG_REPO::$ARCHIVE_NAME" \\
    "$TEMP_DIR/dbs" \\
    2>&1 | tee -a "$LOG_FILE"'''

    return f'''#!/bin/bash
# Borg backup script for {repo_name}
# Generated by djaploy borg backup module

set -euo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
unset PYTHONPATH

# Load passphrase from separate file (deployed with 0600 permissions)
source /home/{app_user}/.borg_env
export BORG_REPO={shlex.quote(repo_url)}{rsh_export}{remote_path_export}

LOG_FILE="/home/{app_user}/logs/borg_backup.log"
DB_DIR="{db_path}"
MEDIA_DIR="{media_path}"
TEMP_DIR="/home/{app_user}/tmp/borg_backup_$$"

log_message() {{
    local message="[$(date +"%Y-%m-%d %H:%M:%S")] $1"
    echo "$message" >> "$LOG_FILE"
    if [ -t 1 ]; then
        echo "$message"
    fi
}}

cleanup() {{
    rm -rf "$TEMP_DIR"
}}
trap cleanup EXIT

log_message "Starting borg backup"

# 1. Create consistent SQLite backups via VACUUM INTO
mkdir -p "$TEMP_DIR/dbs"

DATABASES=({db_array})

for DB in "${{DATABASES[@]}}"; do
    if [ -f "$DB_DIR/$DB" ]; then
        log_message "Backing up database: $DB"
        mkdir -p "$(dirname "$TEMP_DIR/dbs/$DB")"
        rm -f "$TEMP_DIR/dbs/$DB"
        if sqlite3 "$DB_DIR/$DB" "VACUUM INTO '$TEMP_DIR/dbs/$DB';" 2>> "$LOG_FILE"; then
            log_message "Successfully backed up $DB"
        else
            log_message "ERROR: Failed to backup $DB"
            exit 1
        fi
    else
        log_message "WARNING: Database $DB not found, skipping"
    fi
done
{media_section}
# 2. Create borg archive
ARCHIVE_NAME="{{hostname}}-{{now:%Y-%m-%dT%H:%M:%S}}"
{create_cmd_media}

# 3. Prune old archives
log_message "Pruning old archives"
borg prune --list \\
    {prune_args}\\
    "$BORG_REPO" \\
    2>&1 | tee -a "$LOG_FILE"

# 4. Compact repository (free disk space)
log_message "Compacting repository"
borg compact "$BORG_REPO" 2>&1 | tee -a "$LOG_FILE"

log_message "Borg backup completed successfully"
'''


def _deploy_backup_script(borg_config, app_user: str, repo_name: str,
                          host_data):
    """Deploy borg backup script and passphrase env file."""
    from pyinfra.operations import files
    from djaploy.utils import temp_files

    # Deploy passphrase in a separate file with restricted permissions
    passphrase = borg_config.get("passphrase", "")
    env_path = temp_files.create(suffix='.env')
    with open(env_path, 'w') as f:
        f.write(f"export BORG_PASSPHRASE={shlex.quote(passphrase)}\n")

    files.put(
        name="Deploy borg passphrase env file",
        src=env_path,
        dest=f"/home/{app_user}/.borg_env",
        user=app_user,
        group=app_user,
        mode="600",
        _sudo=True,
    )

    script_content = _generate_backup_script(
        borg_config, app_user, repo_name, host_data
    )

    temp_path = temp_files.create(suffix='.sh')
    with open(temp_path, 'w') as f:
        f.write(script_content)

    files.put(
        name="Deploy borg backup script",
        src=temp_path,
        dest=f"/home/{app_user}/borg_backup.sh",
        user=app_user,
        group=app_user,
        mode="755",
        _sudo=True,
    )


def _setup_backup_cron(borg_config, app_user: str):
    """Setup borg backup cron job."""
    from pyinfra.operations import server

    schedule = borg_config.get("schedule", "0 2 * * *")

    # Remove existing borg backup cron jobs
    server.shell(
        name="Remove existing borg backup cron jobs",
        commands=[
            f'crontab -u {app_user} -l 2>/dev/null | grep -v "/home/{app_user}/borg_backup.sh" | crontab -u {app_user} - || true'
        ],
        _sudo=True,
    )

    # Add new borg backup cron job
    server.shell(
        name="Add borg backup cron job",
        commands=[
            f'(crontab -u {app_user} -l 2>/dev/null || true; echo "{schedule} /home/{app_user}/borg_backup.sh >> /home/{app_user}/logs/borg_backup.log 2>&1") | crontab -u {app_user} -'
        ],
        _sudo=True,
    )


# ------------------------------------------------------------------
# Deploy hooks
# ------------------------------------------------------------------

@deploy_hook("configure")
def configure_borg(host_data):
    """Install borgbackup and setup backup directories."""
    from pyinfra.operations import apt, pip, files

    apt.packages(
        name="Install borg dependencies",
        packages=[
            "sqlite3", "cron",
            # Build dependencies for pip-installing borgbackup from source
            "pkg-config", "python3-dev", "libacl1-dev", "libssl-dev",
            "liblz4-dev", "libzstd-dev", "libxxhash-dev",
        ],
        _sudo=True,
    )

    # Install borg via pip to get a current version with correct dependencies.
    # The apt borgbackup package on Debian 12/13 can have msgpack conflicts
    # when PYTHONPATH from the app venv leaks into borg's Python runtime.
    pip.packages(
        name="Install borgbackup",
        packages=["borgbackup"],
        extra_install_args="--break-system-packages",
        _sudo=True,
    )

    app_user = getattr(host_data, 'app_user', 'app')

    files.directory(
        name="Create borg backup logs directory",
        path=f"/home/{app_user}/logs",
        user=app_user,
        group=app_user,
        _sudo=True,
    )

    files.file(
        name="Create borg backup log file",
        path=f"/home/{app_user}/logs/borg_backup.log",
        user=app_user,
        group=app_user,
        mode="644",
        _sudo=True,
    )

    # Deploy SSH config for borg backup
    borg_config = getattr(host_data, 'borg_backup', None)
    if borg_config:
        repo_host = borg_config.get("repo_host", "")
        deploy_key = borg_config.get("deploy_key", None)

        # Ensure .ssh directory exists when we need SSH access
        if deploy_key or repo_host:
            files.directory(
                name="Ensure .ssh directory for app user",
                path=f"/home/{app_user}/.ssh",
                user=app_user,
                group=app_user,
                mode="700",
                _sudo=True,
            )

        if deploy_key:
            files.put(
                name="Deploy SSH private key for borg backup",
                src=str(deploy_key),
                dest=f"/home/{app_user}/.ssh/id_ed25519",
                user=app_user,
                group=app_user,
                mode="600",
                _sudo=True,
            )

            # Generate public key from private key
            from pyinfra.operations import server
            server.shell(
                name="Generate SSH public key from private key",
                commands=[
                    f'ssh-keygen -y -f /home/{app_user}/.ssh/id_ed25519 '
                    f'> /home/{app_user}/.ssh/id_ed25519.pub && '
                    f'chown {app_user}:{app_user} /home/{app_user}/.ssh/id_ed25519.pub'
                ],
                _sudo=True,
            )

        # When ssh_known_hosts_file is set, deploy it to the target server
        # so borg uses strict host key checking against known fingerprints
        # (e.g. Hetzner's published Storage Box fingerprints).
        # Without it, _build_borg_rsh falls back to accept-new.
        known_hosts = borg_config.get("ssh_known_hosts_file", "")
        if repo_host and known_hosts:
            files.put(
                name="Deploy borg SSH known_hosts file",
                src=str(known_hosts),
                dest=f"/home/{app_user}/.ssh/borg_known_hosts",
                user=app_user,
                group=app_user,
                mode="644",
                _sudo=True,
            )


@deploy_hook("deploy:configure")
def deploy_borg(host_data, artifact_path):
    """Deploy borg backup configuration and scripts."""

    borg_config = getattr(host_data, 'borg_backup', None)
    if not borg_config:
        return  # No borg backup configured for this host

    app_user = getattr(host_data, 'app_user', 'app')
    repo_name = getattr(host_data, 'name', 'unknown-host').replace(" ", "_").lower()

    # Initialize borg repository
    _init_borg_repo(borg_config, app_user, repo_name)

    # Deploy backup script
    _deploy_backup_script(borg_config, app_user, repo_name, host_data)

    # Setup cron job
    _setup_backup_cron(borg_config, app_user)

@deploy_hook("restore")
def restore_borg(host_data, restore_opts):
    """Restore databases and media from borg backup.

    restore_opts:
        archive: specific archive name (default: "latest")
        db_only: if True, skip media restore
        backend: if set, only run when "borg"
    """
    # Skip if a different backend was explicitly requested
    backend = restore_opts.get("backend", "")
    if backend and backend != "borg":
        return

    from pyinfra.operations import server, systemd

    app_user = getattr(host_data, "app_user", None) or "app"

    # Use source borg config for cross-env restores (e.g. --env prod --target staging),
    # fall back to the target host's own borg config for same-env restores.
    source_borg_config = restore_opts.get("source_borg_config")
    target_borg_config = getattr(host_data, "borg_backup", None)
    borg_config = source_borg_config or target_borg_config
    if not borg_config:
        return

    source_repo_name = restore_opts.get("source_repo_name", "")
    repo_name = (
        source_repo_name
        or getattr(host_data, 'name', 'unknown-host').replace(" ", "_").lower()
    )

    # Restore paths come from the *target* host (where files will be written)
    app_name = getattr(host_data, 'app_name', '') if not isinstance(host_data, dict) else host_data.get('app_name', '')
    # Fall back to HostConfig.db_dir, then generic default
    host_db_dir = getattr(host_data, 'db_dir', None) if not isinstance(host_data, dict) else host_data.get('db_dir')
    target_bc = target_borg_config or {}
    db_path = (
        target_bc.get("db_path") if isinstance(target_bc, dict)
        else getattr(target_bc, "db_path", None)
    ) or host_db_dir or f"/home/{app_user}/dbs"

    deployment_strategy = (
        getattr(host_data, 'deployment_strategy', 'zero_downtime')
        if not isinstance(host_data, dict)
        else host_data.get('deployment_strategy', 'zero_downtime')
    )
    if deployment_strategy == 'zero_downtime':
        default_media = f"/home/{app_user}/apps/{app_name}/shared/media"
    else:
        default_media = f"/home/{app_user}/apps/{app_name}/media"
    media_path = (
        target_bc.get("media_path") if isinstance(target_bc, dict)
        else getattr(target_bc, "media_path", None)
    ) or default_media

    archive = restore_opts.get("archive", "latest")
    db_only = restore_opts.get("db_only", False)
    services = getattr(host_data, "services", None) or []

    # Repo connection details come from the *source* borg config, but
    # ssh_known_hosts_file must come from the *target* (the file lives on
    # the machine running borg, not the machine the backup was taken from).
    repo_url = _build_repo_url(borg_config, repo_name)
    if source_borg_config:
        rsh_config = dict(borg_config)
        target_known_hosts = (
            target_bc.get("ssh_known_hosts_file", "")
            if isinstance(target_bc, dict)
            else getattr(target_bc, "ssh_known_hosts_file", "")
        )
        rsh_config["ssh_known_hosts_file"] = target_known_hosts
    else:
        rsh_config = borg_config
    borg_rsh = _build_borg_rsh(rsh_config)
    rsh_export = f'\nexport BORG_RSH={shlex.quote(borg_rsh)}' if borg_rsh else ''
    remote_path = borg_config.get("remote_path", None)
    remote_path_export = f'\nexport BORG_REMOTE_PATH={shlex.quote(remote_path)}' if remote_path else ''

    # Resolve "latest" to actual archive name
    if archive == "latest":
        archive_ref = f"$(borg list --short --last 1 {shlex.quote(repo_url)} | head -1)"
    else:
        archive_ref = shlex.quote(archive)

    # Stop application services (keep nginx running)
    app_services = [s for s in services if s != "nginx"]
    for svc in app_services:
        systemd.service(
            name=f"Stop {svc} for borg restore",
            service=svc,
            running=False,
            _sudo=True,
        )

    # Build restore script
    #
    # For cross-env restores the media sits under the *source* host's path
    # inside the archive, but must be copied to the *target* host's path.
    source_media_path = restore_opts.get("source_media_path", "")
    archive_media_path = source_media_path or media_path

    media_restore = ""
    if not db_only:
        media_restore = f'''
# Restore media
if [ -d "$TEMP_DIR{archive_media_path}" ]; then
    log_message "Restoring media from $TEMP_DIR{archive_media_path} to {media_path}"
    mkdir -p "{media_path}"
    cp -a "$TEMP_DIR{archive_media_path}/." "{media_path}/"
    log_message "Media restore complete"
else
    log_message "No media found in archive at {archive_media_path}, skipping"
fi
'''

    # For cross-env restores, inline the source passphrase instead of
    # sourcing .borg_env (which has the target host's passphrase).
    if source_borg_config:
        passphrase = borg_config.get("passphrase", "")
        passphrase_line = f'export BORG_PASSPHRASE={shlex.quote(passphrase)}'
    else:
        passphrase_line = f'source /home/{app_user}/.borg_env'

    restore_script = f'''set -euo pipefail
unset PYTHONPATH
{passphrase_line}{rsh_export}{remote_path_export}

REPO={shlex.quote(repo_url)}
TEMP_DIR="/home/{app_user}/tmp/borg_restore_$$"

log_message() {{ echo "[$(date +"%Y-%m-%d %H:%M:%S")] $1"; }}
cleanup() {{ rm -rf "$TEMP_DIR"; }}
trap cleanup EXIT

ARCHIVE_NAME={archive_ref}
if [ -z "$ARCHIVE_NAME" ]; then
    echo "ERROR: No archives found in repository"
    exit 1
fi

log_message "Restoring from archive: $ARCHIVE_NAME"

mkdir -p "$TEMP_DIR"
cd "$TEMP_DIR"

# Extract archive
borg extract "$REPO::$ARCHIVE_NAME"

# Restore databases
DB_SRC=$(find "$TEMP_DIR" -path "*/dbs" -type d | head -1)
if [ -n "$DB_SRC" ]; then
    log_message "Restoring databases to {db_path}"
    find "$DB_SRC" -type f \\( -name "*.db" -o -name "*.sqlite3" \\) | while read -r dbfile; do
        REL_PATH="${{dbfile#$DB_SRC/}}"
        DEST="{db_path}/$REL_PATH"
        mkdir -p "$(dirname "$DEST")"
        cp "$dbfile" "$DEST"
        log_message "Restored: $REL_PATH -> $DEST"
    done
else
    log_message "WARNING: No database directory found in archive"
fi
{media_restore}
log_message "Borg restore complete"
'''

    # Deploy restore script as temp file and execute it
    restore_script_path = f"/home/{app_user}/tmp/borg_restore.sh"

    from djaploy.utils import temp_files

    temp_path = temp_files.create(suffix='.sh')
    with open(temp_path, 'w') as f:
        f.write(f"#!/bin/bash\n{restore_script}")

    from pyinfra.operations import files
    files.directory(
        name="Ensure tmp directory for borg restore",
        path=f"/home/{app_user}/tmp",
        user=app_user,
        group=app_user,
        _sudo=True,
    )

    files.put(
        name="Upload borg restore script",
        src=temp_path,
        dest=restore_script_path,
        user=app_user,
        group=app_user,
        mode="755",
        _sudo=True,
    )

    server.shell(
        name="Restore from borg backup",
        commands=[restore_script_path],
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
    )

    server.shell(
        name="Clean up borg restore script",
        commands=[f"rm -f {restore_script_path}"],
        _sudo=True,
        _sudo_user=app_user,
    )

    # Restart application services
    for svc in app_services:
        systemd.service(
            name=f"Start {svc} after borg restore",
            service=svc,
            running=True,
            restarted=True,
            _sudo=True,
        )
