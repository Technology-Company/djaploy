"""
Rclone module for djaploy - manages rclone-based backups
"""

import os
import tempfile
from pathlib import Path
from typing import Dict, Any, List

from pyinfra import host
from pyinfra.operations import apt, server, files

from .base import BaseModule


class RcloneModule(BaseModule):
    """Module for managing rclone-based backups"""
    
    name = "rclone"
    description = "Rclone-based backup system for databases and media files"
    version = "0.1.0"
    
    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Install rclone and setup backup directories"""
        
        # Install rclone
        apt.packages(
            name="Install rclone for backups",
            packages=["rclone", "sqlite3"],  # sqlite3 for VACUUM INTO
            _sudo=True,
        )
        
        # Create backup directories
        app_user = getattr(host_data, 'app_user', 'app')
        
        files.directory(
            name="Create rclone config directory",
            path=f"/home/{app_user}/.config/rclone",
            user=app_user,
            group=app_user,
            _sudo=True,
        )
        
        files.directory(
            name="Create backup logs directory",
            path=f"/home/{app_user}/logs",
            user=app_user,
            group=app_user,
            _sudo=True,
        )
        
        # Create initial log file
        files.file(
            name="Create backup log file",
            path=f"/home/{app_user}/logs/backup.log",
            user=app_user,
            group=app_user,
            mode="644",
            _sudo=True,
        )
    
    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Deploy backup configuration and scripts"""
        
        backup_config = getattr(host_data, 'backup', None)
        if not backup_config:
            return  # No backup configured for this host
        
        app_user = getattr(host_data, 'app_user', 'app')
        host_name = getattr(host_data, 'name', 'unknown-host').replace(" ", "_").lower()
        
        # Deploy rclone configuration
        self._deploy_rclone_config(backup_config, app_user, host_data)
        
        # Deploy backup script
        self._deploy_backup_script(backup_config, app_user, host_name, project_config)
        
        # Setup cron job
        self._setup_backup_cron(backup_config, app_user)
    
    def _deploy_rclone_config(self, backup_config, app_user: str, host_data: Dict[str, Any]):
        """Deploy rclone configuration file"""

        backup_type = getattr(backup_config, "type", "sftp")
        backup_host = getattr(backup_config, "host", "")
        backup_user = getattr(backup_config, "user", "")
        backup_password = getattr(backup_config, "password", "")
        backup_port = getattr(backup_config, "port", 22)
        
        # Create rclone config content
        rclone_config = f"""[backup]
type = {backup_type}
host = {backup_host}
user = {backup_user}
pass = {backup_password}
port = {backup_port}"""
        
        if backup_type == "sftp":
            rclone_config += """
shell_type = unix
md5sum_command = none
sha1sum_command = none"""
        
        # Deploy configuration file using temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(rclone_config)
            temp_config_path = f.name

        files.put(
            name="Create rclone configuration",
            src=temp_config_path,
            dest=f"/home/{app_user}/.config/rclone/rclone.conf",
            user=app_user,
            group=app_user,
            mode="600",
            _sudo=True,
        )
        
        # Obscure password in rclone config
        server.shell(
            name="Obscure password in rclone config",
            commands=[
                f'cd /home/{app_user} && rclone config update backup pass --obscure "{backup_password}"'
            ],
            _sudo=True,
            _sudo_user=app_user,
            _use_sudo_login=True,
        )
    
    def _deploy_backup_script(self, backup_config, app_user: str,
                             host_name: str, project_config: Any):
        """Deploy backup script"""

        # Get backup paths
        db_path = getattr(backup_config, "db_path", None) or f"/home/{app_user}/dbs"
        media_path = getattr(backup_config, "media_path", None) or f"/home/{app_user}/apps/{project_config.project_name}/media"
        retention_days = getattr(backup_config, "retention_days", 30)

        # Get database names from config
        databases = getattr(backup_config, "databases", ["default.db"])
        if isinstance(databases, str):
            databases = [databases]
        
        # Generate backup script
        backup_script = self._generate_backup_script(
            host_name=host_name,
            app_user=app_user,
            db_path=db_path,
            media_path=media_path,
            retention_days=retention_days,
            databases=databases
        )
        
        # Deploy backup script using temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(backup_script)
            temp_script_path = f.name

        files.put(
            name="Create backup script",
            src=temp_script_path,
            dest=f"/home/{app_user}/backup.sh",
            user=app_user,
            group=app_user,
            mode="755",
            _sudo=True,
        )
    
    def _generate_backup_script(self, host_name: str, app_user: str, db_path: str, 
                               media_path: str, retention_days: int, databases: List[str]) -> str:
        """Generate backup script content"""
        
        # Convert databases list to bash array
        db_array = " ".join([f'"{db}"' for db in databases])
        
        return f'''#!/bin/bash
# Backup script for {host_name}
# Generated by djaploy backup module

set -euo pipefail

# Configuration
RCLONE_CONFIG="/home/{app_user}/.config/rclone/rclone.conf"
REMOTE_NAME="backup"
LOG_DIR="/home/{app_user}/logs"
LOG_FILE="$LOG_DIR/backup.log"

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
DATE_FOLDER=$(date +"%Y-%m-%d")
TEMP_BACKUP_DIR="/tmp/backup_{host_name}_${{TIMESTAMP}}"
DB_DIR="{db_path}"
MEDIA_DIR="{media_path}"
REMOTE_BACKUP_PATH="${{REMOTE_NAME}}:{host_name}/${{DATE_FOLDER}}"

# Function to log messages
log_message() {{
    local message="[$(date +"%Y-%m-%d %H:%M:%S")] $1"
    
    # Always write to log file
    echo "$message" >> "$LOG_FILE"
    
    # Also output to console if running interactively (has a terminal)
    if [ -t 1 ]; then
        echo "$message"
    fi
}}

# Cleanup function
cleanup() {{
    log_message "Cleaning up temporary files"
    rm -rf "$TEMP_BACKUP_DIR"
}}

# Set trap to cleanup on exit
trap cleanup EXIT

# Start backup
log_message "Starting backup for {host_name}"

# Create temporary backup directory
mkdir -p "$TEMP_BACKUP_DIR"

# Backup SQLite databases using VACUUM INTO for consistency and compaction
log_message "Creating consistent database backups"

# List of databases to backup
DATABASES=({db_array})

for DB in "${{DATABASES[@]}}"; do
    if [ -f "$DB_DIR/$DB" ]; then
        log_message "Backing up database: $DB"
        # Remove any existing backup file first
        rm -f "$TEMP_BACKUP_DIR/$DB"
        
        # Use VACUUM INTO for consistent, compacted backup
        if sqlite3 "$DB_DIR/$DB" "VACUUM INTO '$TEMP_BACKUP_DIR/$DB';" 2>> "$LOG_FILE"; then
            log_message "Successfully backed up $DB"
        else
            log_message "ERROR: Failed to backup $DB"
            exit 1
        fi
    else
        log_message "WARNING: Database $DB not found, skipping"
    fi
done

# Compress database backups
log_message "Compressing database backups"
ARCHIVE_NAME="dbs_backup_${{TIMESTAMP}}.tar.gz"

# Count .db files to verify they exist
DB_COUNT=$(ls -1 "$TEMP_BACKUP_DIR"/*.db 2>/dev/null | wc -l)
if [ "$DB_COUNT" -eq 0 ]; then
    log_message "ERROR: No database files found to compress"
    exit 1
fi

log_message "Found $DB_COUNT database files to compress"

# Create compressed archive - explicitly list the database files
DB_FILES_TO_COMPRESS=""
for DB in "${{DATABASES[@]}}"; do
    if [ -f "$TEMP_BACKUP_DIR/$DB" ]; then
        DB_FILES_TO_COMPRESS="$DB_FILES_TO_COMPRESS $DB"
    fi
done

if [ -n "$DB_FILES_TO_COMPRESS" ]; then
    if tar -czf "$TEMP_BACKUP_DIR/$ARCHIVE_NAME" -C "$TEMP_BACKUP_DIR" $DB_FILES_TO_COMPRESS 2>> "$LOG_FILE"; then
        log_message "Successfully compressed databases to $ARCHIVE_NAME"
        # Remove individual .db files after compression
        for DB in "${{DATABASES[@]}}"; do
            rm -f "$TEMP_BACKUP_DIR/$DB"
        done
    else
        log_message "ERROR: Failed to compress databases"
        exit 1
    fi
else
    log_message "ERROR: No database files found to compress"
    exit 1
fi

# Upload compressed database backup to date folder
log_message "Uploading database backup to $REMOTE_BACKUP_PATH"
if rclone copy "$TEMP_BACKUP_DIR/$ARCHIVE_NAME" "$REMOTE_BACKUP_PATH/" \\
    --config "$RCLONE_CONFIG" \\
    --transfers 4 \\
    --checkers 8 \\
    --contimeout 60s \\
    --timeout 300s \\
    --retries 3 \\
    --low-level-retries 10 \\
    --stats 10s \\
    --stats-log-level NOTICE \\
    --log-file "$LOG_FILE" \\
    --log-level INFO; then
    
    log_message "Successfully uploaded database backup to $DATE_FOLDER folder"
else
    log_message "ERROR: Failed to upload database backup"
    exit 1
fi

# Backup media files (if they exist)
if [ -d "$MEDIA_DIR" ] && [ "$(ls -A "$MEDIA_DIR" 2>/dev/null)" ]; then
    log_message "Compressing media files"
    MEDIA_ARCHIVE="media_backup_${{TIMESTAMP}}.tar.gz"
    
    # Compress media files
    if tar -czf "$TEMP_BACKUP_DIR/$MEDIA_ARCHIVE" -C "$MEDIA_DIR" . 2>> "$LOG_FILE"; then
        log_message "Successfully compressed media files to $MEDIA_ARCHIVE"
        
        # Upload compressed media backup to same date folder
        log_message "Uploading media backup to $REMOTE_BACKUP_PATH"
        if rclone copy "$TEMP_BACKUP_DIR/$MEDIA_ARCHIVE" "$REMOTE_BACKUP_PATH/" \\
            --config "$RCLONE_CONFIG" \\
            --transfers 4 \\
            --checkers 8 \\
            --contimeout 60s \\
            --timeout 300s \\
            --retries 3 \\
            --low-level-retries 10 \\
            --stats 10s \\
            --stats-log-level NOTICE \\
            --log-file "$LOG_FILE" \\
            --log-level INFO; then
            
            log_message "Successfully uploaded media backup to $DATE_FOLDER folder"
        else
            log_message "ERROR: Failed to upload media backup"
            exit 1
        fi
    else
        log_message "ERROR: Failed to compress media files"
        exit 1
    fi
else
    log_message "Media directory not found or empty at $MEDIA_DIR, skipping media backup"
fi

# Clean up old backups based on retention policy
{"" if retention_days <= 0 else f'''log_message "Cleaning up backup folders older than {retention_days} days"

# Calculate cutoff date
CUTOFF_DATE=$(date -d "{retention_days} days ago" +"%Y-%m-%d")

# List all date folders and delete those older than cutoff
rclone lsf "${{REMOTE_NAME}}:{host_name}/" --dirs-only --config "$RCLONE_CONFIG" | while read -r folder; do
    # Remove trailing slash
    folder_name="${{folder%/}}"
    
    # Check if folder name matches date format and is older than cutoff
    if [[ "$folder_name" =~ ^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$ ]] && [[ "$folder_name" < "$CUTOFF_DATE" ]]; then
        log_message "Deleting old backup folder: $folder_name"
        rclone purge "${{REMOTE_NAME}}:{host_name}/$folder_name" --config "$RCLONE_CONFIG" --quiet
    fi
done

log_message "Cleanup completed"'''}

log_message "Backup completed successfully for {host_name}"
'''
    
    def _setup_backup_cron(self, backup_config, app_user: str):
        """Setup backup cron job"""

        schedule = getattr(backup_config, "schedule", "0 2 * * *")  # Default: 2 AM daily
        
        # Remove existing backup cron jobs
        server.shell(
            name="Remove existing backup cron jobs",
            commands=[
                f'crontab -u {app_user} -l 2>/dev/null | grep -v "/home/{app_user}/backup.sh" | crontab -u {app_user} - || true'
            ],
            _sudo=True,
        )
        
        # Add new backup cron job
        server.shell(
            name="Add backup cron job",
            commands=[
                f'(crontab -u {app_user} -l 2>/dev/null || true; echo "{schedule} /home/{app_user}/backup.sh >> /home/{app_user}/logs/backup.log 2>&1") | crontab -u {app_user} -'
            ],
            _sudo=True,
        )
    
    def get_required_packages(self) -> List[str]:
        """Get required system packages"""
        return ["rclone", "sqlite3"]


# Make the module class available for the loader
Module = RcloneModule