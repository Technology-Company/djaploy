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
import tempfile

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
    """
    repo_host = borg_config.get("repo_host", "")
    if not repo_host:
        return ""

    repo_port = borg_config.get("repo_port", 22)
    ssh_key = borg_config.get("ssh_key", "")

    rsh = f"ssh -o StrictHostKeyChecking=accept-new -p {repo_port}"
    if ssh_key:
        rsh += f" -i {ssh_key}"
    return rsh


def _init_borg_repo(borg_config, app_user: str, repo_name: str):
    """Initialize borg repository on remote server if not already initialized."""
    from pyinfra.operations import server

    passphrase = borg_config.get("passphrase", "")
    repo_url = _build_repo_url(borg_config, repo_name)
    borg_rsh = _build_borg_rsh(borg_config)

    rsh_export = f'export BORG_RSH={shlex.quote(borg_rsh)} && ' if borg_rsh else ''

    # Ensure parent directory exists (for local repos)
    repo_url = _build_repo_url(borg_config, repo_name)
    if not borg_config.get("repo_host"):
        from pyinfra.operations import files
        # repo_url is a local path; ensure its parent exists
        import posixpath
        parent = posixpath.dirname(repo_url)
        files.directory(
            name="Create borg repo parent directory",
            path=parent,
            user=app_user,
            group=app_user,
            _sudo=True,
        )

    server.shell(
        name="Initialize borg repository if needed",
        commands=[
            f'export BORG_PASSPHRASE={shlex.quote(passphrase)} && '
            f'{rsh_export}'
            f'borg info {shlex.quote(repo_url)} > /dev/null 2>&1 || '
            f'borg init --encryption=repokey-blake2 {shlex.quote(repo_url)}'
        ],
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
    )


def _generate_backup_script(borg_config, app_user: str, repo_name: str,
                            project_config) -> str:
    """Generate borg backup script content."""
    passphrase = borg_config.get("passphrase", "")
    compression = borg_config.get("compression", "zstd,3")
    databases = borg_config.get("databases", ["default.db"])
    backup_media = borg_config.get("backup_media", True)
    keep_daily = borg_config.get("keep_daily", 7)
    keep_weekly = borg_config.get("keep_weekly", 4)
    keep_monthly = borg_config.get("keep_monthly", 6)

    db_path = borg_config.get("db_path") or f"/home/{app_user}/dbs"
    media_path = borg_config.get("media_path") or f"/home/{app_user}/apps/{project_config.project_name}/media"

    if isinstance(databases, str):
        databases = [databases]

    db_array = " ".join([f'"{db}"' for db in databases])
    repo_url = _build_repo_url(borg_config, repo_name)
    borg_rsh = _build_borg_rsh(borg_config)
    rsh_export = f'\nexport BORG_RSH={shlex.quote(borg_rsh)}' if borg_rsh else ''

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

export BORG_PASSPHRASE={shlex.quote(passphrase)}
export BORG_REPO={shlex.quote(repo_url)}{rsh_export}

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
    --keep-daily={keep_daily} \\
    --keep-weekly={keep_weekly} \\
    --keep-monthly={keep_monthly} \\
    "$BORG_REPO" \\
    2>&1 | tee -a "$LOG_FILE"

# 4. Compact repository (free disk space)
log_message "Compacting repository"
borg compact "$BORG_REPO" 2>&1 | tee -a "$LOG_FILE"

log_message "Borg backup completed successfully"
'''


def _deploy_backup_script(borg_config, app_user: str, repo_name: str,
                          project_config):
    """Deploy borg backup script."""
    from pyinfra.operations import files

    script_content = _generate_backup_script(
        borg_config, app_user, repo_name, project_config
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(script_content)
        temp_path = f.name

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
def configure_borg(host_data, project_config):
    """Install borgbackup and setup backup directories."""
    from pyinfra.operations import apt, files

    apt.packages(
        name="Install borgbackup for backups",
        packages=["borgbackup", "sqlite3", "cron"],
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

    # Deploy SSH keypair for app user if deploy_key is configured
    borg_config = getattr(host_data, 'borg_backup', None)
    if borg_config:
        deploy_key = borg_config.get("deploy_key", None)
        if deploy_key:
            files.directory(
                name="Ensure .ssh directory for app user",
                path=f"/home/{app_user}/.ssh",
                user=app_user,
                group=app_user,
                mode="700",
                _sudo=True,
            )

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


@deploy_hook("deploy:configure")
def deploy_borg(host_data, project_config, artifact_path):
    """Deploy borg backup configuration and scripts."""

    borg_config = getattr(host_data, 'borg_backup', None)
    if not borg_config:
        return  # No borg backup configured for this host

    app_user = getattr(host_data, 'app_user', 'app')
    repo_name = getattr(host_data, 'name', 'unknown-host').replace(" ", "_").lower()

    # Initialize borg repository
    _init_borg_repo(borg_config, app_user, repo_name)

    # Deploy backup script
    _deploy_backup_script(borg_config, app_user, repo_name, project_config)

    # Setup cron job
    _setup_backup_cron(borg_config, app_user)


@deploy_hook("restore")
def restore_borg(host_data, project_config, restore_opts):
    """Restore databases and media from borg backup.

    restore_opts:
        archive: specific archive name (default: "latest")
        db_only: if True, skip media restore
    """
    from pyinfra.operations import server, systemd

    app_user = getattr(host_data, "app_user", None) or "app"
    borg_config = getattr(host_data, "borg_backup", None)
    if not borg_config:
        return

    passphrase = borg_config.get("passphrase", "")
    repo_name = getattr(host_data, 'name', 'unknown-host').replace(" ", "_").lower()

    db_path = (
        borg_config.get("db_path") if isinstance(borg_config, dict)
        else getattr(borg_config, "db_path", None)
    ) or f"/home/{app_user}/dbs"

    media_path = (
        borg_config.get("media_path") if isinstance(borg_config, dict)
        else getattr(borg_config, "media_path", None)
    ) or f"/home/{app_user}/apps/{project_config.project_name}/media"

    archive = restore_opts.get("archive", "latest")
    db_only = restore_opts.get("db_only", False)
    services = getattr(host_data, "services", None) or []

    repo_url = _build_repo_url(borg_config, repo_name)
    borg_rsh = _build_borg_rsh(borg_config)
    rsh_export = f'\nexport BORG_RSH={shlex.quote(borg_rsh)}' if borg_rsh else ''

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
    media_restore = ""
    if not db_only:
        media_restore = f'''
# Restore media
if [ -d "$TEMP_DIR{media_path}" ]; then
    log_message "Restoring media to {media_path}"
    mkdir -p "{media_path}"
    cp -a "$TEMP_DIR{media_path}/." "{media_path}/"
    log_message "Media restore complete"
else
    log_message "No media found in archive, skipping"
fi
'''

    restore_script = f'''set -euo pipefail
export BORG_PASSPHRASE={shlex.quote(passphrase)}{rsh_export}

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

    server.shell(
        name="Restore from borg backup",
        commands=[restore_script],
        _sudo=True,
        _sudo_user=app_user,
        _use_sudo_login=True,
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
