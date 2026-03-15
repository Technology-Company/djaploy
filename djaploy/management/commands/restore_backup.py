"""
Django management command for restoring database and media from rclone backups.

Supports two modes:
  - Local restore (default): pulls the main database to the local dev machine
  - Server restore (--target): runs pyinfra against a target server to restore
    all databases + media using the rclone already configured on that server
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from djaploy.deploy import _load_inventory_hosts
from djaploy.discovery import find_inventory


class Command(BaseCommand):
    help = (
        "Restore database and media from rclone backup. "
        "Use --target to restore onto a remote server instead of locally."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            type=str,
            required=True,
            help="Environment whose BackupConfig to use as backup source (e.g., production-backup)",
        )
        parser.add_argument(
            "--target",
            type=str,
            default="local",
            help=(
                "Where to restore: 'local' (default) restores the main DB locally, "
                "or an environment name (e.g., dev, production-backup) to restore "
                "all databases on that server via pyinfra."
            ),
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Backup date folder to restore from (YYYY-MM-DD). Defaults to latest.",
        )
        parser.add_argument(
            "--db-only",
            action="store_true",
            help="Only restore databases, skip media files.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_backups",
            help="List available backup dates and exit.",
        )
        parser.add_argument(
            "--inventory-dir",
            type=str,
            default=None,
            help="Directory containing inventory files (overrides settings)",
        )

    def handle(self, *args, **options):
        env = options["env"]
        target = options["target"]

        # Resolve inventory file
        if options["inventory_dir"]:
            inv_file = str(Path(options["inventory_dir"]) / f"{env}.py")
        else:
            inv_path = find_inventory(env)
            if not inv_path:
                raise CommandError(f"Inventory not found for environment '{env}'")
            inv_file = str(inv_path)

        source_hosts = _load_inventory_hosts(inv_file)
        if not source_hosts:
            raise CommandError(f"No hosts found in {env} inventory.")

        source_config = source_hosts[0][1]
        backup_config = source_config.get("backup")
        if not backup_config:
            raise CommandError(
                f"No backup configuration found for host '{source_config.get('name')}'. "
                f"Add a BackupConfig to the host in {env}.py."
            )

        host_name = source_config.get("name", "unknown").replace(" ", "_").lower()

        if target == "local":
            self._handle_local(options, backup_config, host_name)
        else:
            inventory_dir = str(Path(inv_file).parent) if options["inventory_dir"] is None else options["inventory_dir"]
            self._handle_server(options, inventory_dir, target, host_name)

    # ── Local restore ────────────────────────────────────────────────

    def _handle_local(self, options, backup_config, host_name):
        """Restore the main database to the local machine."""
        rclone_config_path = self._create_temp_rclone_config(backup_config)
        try:
            if options["list_backups"]:
                self._list_backups(rclone_config_path, host_name)
                return

            date = options["date"] or self._get_latest_backup_date(rclone_config_path, host_name)
            if not date:
                raise CommandError("No backups found.")
            self.stdout.write(self.style.SUCCESS(f"Using backup: {date}"))

            remote_path = f"backup:{host_name}/{date}"

            self._local_restore_database(rclone_config_path, remote_path)

            if not options["db_only"]:
                self._local_restore_media(rclone_config_path, remote_path)

            self.stdout.write(self.style.SUCCESS("Local restore completed."))
        finally:
            os.unlink(rclone_config_path)

    def _local_restore_database(self, rclone_config, remote_path):
        """Download backup and restore the main database locally."""
        self.stdout.write(self.style.WARNING("Restoring database locally..."))

        with tempfile.TemporaryDirectory() as temp_dir:
            db_archive = self._find_archive(rclone_config, remote_path, "dbs_backup_")
            if not db_archive:
                raise CommandError("No database archive found in backup.")

            self.stdout.write(f"  Downloading {db_archive}...")
            self._download(rclone_config, remote_path, db_archive, temp_dir)

            archive_path = os.path.join(temp_dir, db_archive)
            subprocess.run(["tar", "-xzf", archive_path, "-C", temp_dir], check=True)

            restored_db = self._find_file_in_dir(temp_dir, "db.sqlite3")
            if not restored_db:
                raise CommandError(
                    f"db.sqlite3 not found in archive. Contents: {self._list_dir_recursive(temp_dir)}"
                )

            local_db_path = settings.DATABASES["default"]["NAME"]

            if os.path.exists(local_db_path):
                backup_path = local_db_path + ".pre-restore"
                self.stdout.write(f"  Backing up current DB to {backup_path}")
                shutil.copy2(local_db_path, backup_path)

            os.makedirs(os.path.dirname(local_db_path), exist_ok=True)
            shutil.copy2(restored_db, local_db_path)
            self.stdout.write(self.style.SUCCESS("  Database restored locally."))

    def _local_restore_media(self, rclone_config, remote_path):
        """Download and restore media files locally."""
        self.stdout.write(self.style.WARNING("Restoring media locally..."))

        media_archive = self._find_archive(rclone_config, remote_path, "media_backup_")
        if not media_archive:
            self.stdout.write(self.style.NOTICE("  No media archive found, skipping."))
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            self.stdout.write(f"  Downloading {media_archive}...")
            self._download(rclone_config, remote_path, media_archive, temp_dir)

            archive_path = os.path.join(temp_dir, media_archive)
            media_root = settings.MEDIA_ROOT

            if os.path.exists(media_root):
                self.stdout.write(f"  Removing existing media at {media_root}")
                shutil.rmtree(media_root)

            os.makedirs(media_root, exist_ok=True)
            subprocess.run(["tar", "-xzf", archive_path, "-C", media_root], check=True)
            self.stdout.write(self.style.SUCCESS("  Media restored locally."))

    # ── Server restore (via pyinfra) ─────────────────────────────────

    def _handle_server(self, options, inventory_dir, target, backup_host_name):
        """Restore on a remote server using pyinfra and the rclone module."""
        from pathlib import Path

        from djaploy import restore_from_backup

        target_inventory_file = str(Path(inventory_dir) / f"{target}.py")
        if not os.path.exists(target_inventory_file):
            raise CommandError(f"Target inventory file not found: {target_inventory_file}")

        # For --list, fall back to local rclone since we just need to query the storage box
        if options["list_backups"]:
            target_hosts = _load_inventory_hosts(target_inventory_file)
            if not target_hosts:
                raise CommandError(f"No hosts found in {target} inventory.")
            backup_config = target_hosts[0][1].get("backup")
            if not backup_config:
                raise CommandError(f"No backup configuration on {target}.")
            rclone_config_path = self._create_temp_rclone_config(backup_config)
            try:
                self._list_backups(rclone_config_path, backup_host_name)
            finally:
                os.unlink(rclone_config_path)
            return

        # Determine backup date
        date = options["date"]
        if not date:
            target_hosts = _load_inventory_hosts(target_inventory_file)
            if not target_hosts:
                raise CommandError(f"No hosts found in {target} inventory.")
            backup_config = target_hosts[0][1].get("backup")
            if not backup_config:
                raise CommandError(f"No backup configuration on {target}.")
            rclone_config_path = self._create_temp_rclone_config(backup_config)
            try:
                date = self._get_latest_backup_date(rclone_config_path, backup_host_name)
            finally:
                os.unlink(rclone_config_path)
            if not date:
                raise CommandError("No backups found.")

        self.stdout.write(self.style.SUCCESS(f"Restoring backup {date} on {target}..."))

        restore_opts = {
            "backup_host_name": backup_host_name,
            "date": date,
            "db_only": options["db_only"],
        }

        try:
            restore_from_backup(target_inventory_file, restore_opts)
            self.stdout.write(self.style.SUCCESS(f"Server restore completed on {target}."))
        except Exception as e:
            raise CommandError(f"Restore failed: {e}")

    # ── Shared helpers ───────────────────────────────────────────────

    def _create_temp_rclone_config(self, backup_config):
        """Create a temporary rclone config file from BackupConfig."""
        host = getattr(backup_config, "host", None) or backup_config.get("host", "")
        user = getattr(backup_config, "user", None) or backup_config.get("user", "")
        password = getattr(backup_config, "password", None) or backup_config.get("password", "")
        port = getattr(backup_config, "port", None) or backup_config.get("port", 22)

        config_content = (
            f"[backup]\n"
            f"type = sftp\n"
            f"host = {host}\n"
            f"user = {user}\n"
            f"pass = {password}\n"
            f"port = {port}\n"
            f"shell_type = unix\n"
            f"md5sum_command = none\n"
            f"sha1sum_command = none\n"
        )

        fd, path = tempfile.mkstemp(suffix=".conf", prefix="rclone_restore_")
        with os.fdopen(fd, "w") as f:
            f.write(config_content)

        subprocess.run(
            ["rclone", "config", "update", "backup", "pass", "--obscure", password],
            env={**os.environ, "RCLONE_CONFIG": path},
            capture_output=True,
        )
        return path

    def _run_rclone(self, args, rclone_config):
        """Run an rclone command and return the result."""
        cmd = ["rclone"] + args + ["--config", rclone_config]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _list_backups(self, rclone_config, host_name):
        """List available backup dates."""
        result = self._run_rclone(
            ["lsf", f"backup:{host_name}/", "--dirs-only"],
            rclone_config,
        )
        if result.returncode != 0:
            raise CommandError(f"Failed to list backups: {result.stderr}")

        dates = sorted(
            line.strip("/") for line in result.stdout.strip().split("\n") if line.strip()
        )
        if not dates:
            self.stdout.write("No backups found.")
            return

        self.stdout.write(self.style.SUCCESS(f"Available backups ({len(dates)}):"))
        for d in dates:
            files_result = self._run_rclone(
                ["lsf", f"backup:{host_name}/{d}/"],
                rclone_config,
            )
            files_info = ""
            if files_result.returncode == 0:
                files = [f.strip() for f in files_result.stdout.strip().split("\n") if f.strip()]
                files_info = f"  ({', '.join(files)})"
            self.stdout.write(f"  {d}{files_info}")

    def _get_latest_backup_date(self, rclone_config, host_name):
        """Get the most recent backup date."""
        result = self._run_rclone(
            ["lsf", f"backup:{host_name}/", "--dirs-only"],
            rclone_config,
        )
        if result.returncode != 0:
            return None

        dates = sorted(
            (line.strip("/") for line in result.stdout.strip().split("\n") if line.strip()),
            reverse=True,
        )
        return dates[0] if dates else None

    def _find_archive(self, rclone_config, remote_path, prefix):
        """Find an archive file in the remote backup by prefix."""
        result = self._run_rclone(["lsf", remote_path + "/"], rclone_config)
        if result.returncode != 0:
            return None
        for filename in result.stdout.strip().split("\n"):
            filename = filename.strip()
            if filename.startswith(prefix) and filename.endswith(".tar.gz"):
                return filename
        return None

    def _download(self, rclone_config, remote_path, filename, dest_dir):
        """Download a file from the remote backup."""
        result = self._run_rclone(
            ["copy", f"{remote_path}/{filename}", dest_dir, "--progress"],
            rclone_config,
        )
        if result.returncode != 0:
            raise CommandError(f"Download failed: {result.stderr}")

    @staticmethod
    def _find_file_in_dir(directory, filename):
        """Recursively find a file in a directory."""
        for root, dirs, files in os.walk(directory):
            if filename in files:
                return os.path.join(root, filename)
        return None

    @staticmethod
    def _list_dir_recursive(directory):
        """List all files in a directory recursively (for debug output)."""
        result = []
        for root, dirs, files in os.walk(directory):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), directory)
                result.append(rel)
        return result