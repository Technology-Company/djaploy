"""
Django management command for restoring database and media from backups.

Supports two backends:
  - rclone: restores from rclone-managed backups (SFTP/S3)
  - borg: restores from BorgBackup repositories over SSH

And two modes:
  - Local restore (default): pulls the main database to the local dev machine
  - Server restore (--target): runs pyinfra against a target server to restore
    all databases + media on the server itself
"""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from djaploy.deploy import _load_inventory_hosts
from djaploy.discovery import find_inventory


def _safe_extract(archive_path: str, dest: str):
    """Extract a tar archive, rejecting path traversal, symlinks, and special files."""
    with tarfile.open(archive_path, "r:gz") as tar:
        if sys.version_info >= (3, 12):
            tar.extractall(path=dest, filter='data')
        else:
            for member in tar.getmembers():
                if member.issym() or member.islnk():
                    raise ValueError(f"Refusing to extract link: {member.name}")
                if not (member.isreg() or member.isdir()):
                    raise ValueError(f"Refusing to extract special file: {member.name}")
                if os.path.isabs(member.name) or '..' in os.path.normpath(member.name).split(os.sep):
                    raise ValueError(f"Refusing to extract path traversal: {member.name}")
                drive, _ = os.path.splitdrive(member.name)
                if drive:
                    raise ValueError(f"Refusing to extract drive-prefixed path: {member.name}")
            tar.extractall(path=dest)


class Command(BaseCommand):
    help = (
        "Restore database and media from backup. "
        "Supports rclone and borg backends. "
        "Use --target to restore onto a remote server instead of locally."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            type=str,
            required=True,
            help="Environment whose backup config to use as source (e.g., production-backup)",
        )
        parser.add_argument(
            "--backend",
            type=str,
            choices=["auto", "rclone", "borg"],
            default="auto",
            help=(
                "Backup backend to use. 'auto' (default) detects from host config. "
                "Specify explicitly if both rclone and borg are configured."
            ),
        )
        parser.add_argument(
            "--target",
            type=str,
            default=None,
            help=(
                "Where to restore: 'local' restores the main DB locally, "
                "or an environment name to restore on that server via pyinfra. "
                "Defaults to the --env value (server restore)."
            ),
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Backup date folder to restore from (YYYY-MM-DD). For rclone backend. Defaults to latest.",
        )
        parser.add_argument(
            "--archive",
            type=str,
            default=None,
            help="Borg archive name to restore from. For borg backend. Defaults to latest.",
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
            help="List available backups and exit.",
        )
        parser.add_argument(
            "--inventory-dir",
            type=str,
            default=None,
            help="Directory containing inventory files (overrides settings)",
        )

    def handle(self, *args, **options):
        env = options["env"]
        target = options["target"] or env
        backend = options["backend"]

        # Resolve inventory file
        if options["inventory_dir"]:
            inv_file = str(Path(options["inventory_dir"]) / f"{env}.py")
        else:
            inv_path = find_inventory(env)
            if not inv_path:
                raise CommandError(f"Inventory not found for environment '{env}'")
            inv_file = str(inv_path)

        # Load source environment
        source_hosts = _load_inventory_hosts(inv_file)
        if not source_hosts:
            raise CommandError(f"No hosts found in {env} inventory.")

        source_config = source_hosts[0][1]
        backup_config = source_config.get("backup")
        borg_config = source_config.get("borg_backup")
        host_name = source_config.get("name", "unknown").replace(" ", "_").lower()

        # Auto-detect backend
        if backend == "auto":
            if borg_config and backup_config:
                raise CommandError(
                    "Both rclone and borg backup configured. "
                    "Specify --backend rclone or --backend borg."
                )
            backend = "borg" if borg_config else "rclone"

        inventory_dir = str(Path(inv_file).parent) if options["inventory_dir"] is None else options["inventory_dir"]

        if backend == "borg":
            if not borg_config:
                raise CommandError(
                    f"No borg_backup configuration found for host '{source_config.get('name')}'. "
                    f"Add a BorgBackupConfig to the host in {env}.py."
                )
            if target == "local":
                self._handle_local_borg(options, borg_config, host_name)
            else:
                # Compute source media path from the source inventory so the
                # restore knows where media lives inside the borg archive.
                src_app_user = source_config.get("app_user", "app")
                src_app_name = source_config.get("app_name", "")
                src_strategy = source_config.get("deployment_strategy", "zero_downtime")
                source_media_path = getattr(borg_config, "media_path", None)
                if not source_media_path:
                    if src_strategy == "zero_downtime":
                        source_media_path = f"/home/{src_app_user}/apps/{src_app_name}/shared/media"
                    else:
                        source_media_path = f"/home/{src_app_user}/apps/{src_app_name}/media"
                self._handle_server_borg(
                    options, inventory_dir, target, host_name,
                    borg_config, source_media_path,
                )
        else:
            if not backup_config:
                raise CommandError(
                    f"No backup configuration found for host '{source_config.get('name')}'. "
                    f"Add a BackupConfig to the host in {env}.py."
                )
            if target == "local":
                self._handle_local_rclone(options, backup_config, host_name)
            else:
                self._handle_server_rclone(options, inventory_dir, target, host_name)

    # ── Borg: Local restore ──────────────────────────────────────────

    def _handle_local_borg(self, options, borg_config, host_name):
        """Restore from a borg repository to the local machine."""
        repo_url, borg_env = self._build_borg_env(borg_config, host_name)

        if options["list_backups"]:
            self._borg_list_archives(repo_url, borg_env)
            return

        archive = options["archive"] or "latest"

        # Resolve "latest" to actual archive name
        if archive == "latest":
            result = subprocess.run(
                ["borg", "list", "--short", "--last", "1", repo_url],
                capture_output=True, text=True, env=borg_env,
            )
            if result.returncode != 0:
                raise CommandError(f"Failed to list borg archives: {result.stderr}")
            archive = result.stdout.strip()
            if not archive:
                raise CommandError("No archives found in borg repository.")

        self.stdout.write(self.style.SUCCESS(f"Using borg archive: {archive}"))

        with tempfile.TemporaryDirectory() as temp_dir:
            # Extract archive
            self.stdout.write("  Extracting archive...")
            result = subprocess.run(
                ["borg", "extract", f"{repo_url}::{archive}"],
                cwd=temp_dir, env=borg_env,
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise CommandError(f"Failed to extract borg archive: {result.stderr}")

            # Restore database
            self._borg_local_restore_database(temp_dir)

            # Restore media
            if not options["db_only"]:
                self._borg_local_restore_media(temp_dir)

        self.stdout.write(self.style.SUCCESS("Local borg restore completed."))

    def _borg_local_restore_database(self, extract_dir):
        """Find and restore the main database from extracted borg archive."""
        self.stdout.write(self.style.WARNING("  Restoring database..."))

        # Database files are under .../dbs/ in the extracted archive
        restored_db = self._find_file_in_dir(extract_dir, "db.sqlite3")
        if not restored_db:
            raise CommandError(
                f"db.sqlite3 not found in archive. Contents: {self._list_dir_recursive(extract_dir)}"
            )

        local_db_path = settings.DATABASES["default"]["NAME"]

        if os.path.exists(local_db_path):
            backup_path = local_db_path + ".pre-restore"
            self.stdout.write(f"  Backing up current DB to {backup_path}")
            shutil.copy2(local_db_path, backup_path)

        os.makedirs(os.path.dirname(local_db_path), exist_ok=True)
        shutil.copy2(restored_db, local_db_path)
        self.stdout.write(self.style.SUCCESS("  Database restored locally."))

    def _borg_local_restore_media(self, extract_dir):
        """Find and restore media files from extracted borg archive."""
        self.stdout.write(self.style.WARNING("  Restoring media..."))

        media_root = getattr(settings, "MEDIA_ROOT", None)
        if not media_root:
            self.stdout.write("  MEDIA_ROOT not configured, skipping media restore.")
            return

        # Media is archived with its full absolute path, look for a "media" dir
        media_src = None
        for root, dirs, files in os.walk(extract_dir):
            if os.path.basename(root) == "media" and (files or dirs):
                # Check it's not the dbs directory
                if "dbs" not in root:
                    media_src = root
                    break

        if not media_src:
            self.stdout.write("  No media found in archive, skipping.")
            return

        if os.path.exists(media_root):
            self.stdout.write(f"  Removing existing media at {media_root}")
            shutil.rmtree(media_root)

        shutil.copytree(media_src, media_root)
        self.stdout.write(self.style.SUCCESS("  Media restored locally."))

    # ── Borg: Server restore ─────────────────────────────────────────

    def _handle_server_borg(self, options, inventory_dir, target, host_name, source_borg_config=None, source_media_path=None):
        """Restore on a remote server using pyinfra and the borg hook."""
        from dataclasses import asdict, is_dataclass

        from djaploy import restore_from_backup

        target_inventory_file = str(Path(inventory_dir) / f"{target}.py")
        if not os.path.exists(target_inventory_file):
            raise CommandError(f"Target inventory file not found: {target_inventory_file}")

        if options["list_backups"]:
            # For --list with server target, list archives from the source borg config
            borg_cfg = source_borg_config
            repo_name = host_name
            if not borg_cfg:
                target_hosts = _load_inventory_hosts(target_inventory_file)
                if not target_hosts:
                    raise CommandError(f"No hosts found in {target} inventory.")
                borg_cfg = target_hosts[0][1].get("borg_backup")
                repo_name = target_hosts[0][1].get("name", "unknown").replace(" ", "_").lower()
            if not borg_cfg:
                raise CommandError(f"No borg_backup configuration found.")
            repo_url, borg_env = self._build_borg_env(borg_cfg, repo_name)
            self._borg_list_archives(repo_url, borg_env)
            return

        archive = options["archive"] or "latest"
        self.stdout.write(self.style.SUCCESS(f"Restoring borg archive '{archive}' on {target}..."))

        restore_opts = {
            "archive": archive,
            "db_only": options["db_only"],
            "backend": "borg",
            "source_repo_name": host_name,
        }
        if source_media_path:
            restore_opts["source_media_path"] = source_media_path

        # Pass source borg config so the hook connects to the correct repo
        if source_borg_config:
            if is_dataclass(source_borg_config) and not isinstance(source_borg_config, type):
                restore_opts["source_borg_config"] = asdict(source_borg_config)
            elif isinstance(source_borg_config, dict):
                restore_opts["source_borg_config"] = source_borg_config
            else:
                restore_opts["source_borg_config"] = dict(source_borg_config)

        try:
            restore_from_backup(target_inventory_file, restore_opts)
            self.stdout.write(self.style.SUCCESS(f"Server borg restore completed on {target}."))
        except Exception as e:
            raise CommandError(f"Restore failed: {e}")

    # ── Borg: Helpers ────────────────────────────────────────────────

    @staticmethod
    def _borg_get(config, key, default=""):
        """Get a value from a borg config (dict or dataclass)."""
        if isinstance(config, dict):
            val = config.get(key, default)
        else:
            val = getattr(config, key, default)
        return val if val is not None else default

    def _build_borg_env(self, borg_config, repo_name):
        """Build borg repo URL and environment variables from config."""
        repo_host = self._borg_get(borg_config, "repo_host", "")
        repo_user = self._borg_get(borg_config, "repo_user", "borg")
        repo_port = self._borg_get(borg_config, "repo_port", 22)
        repo_path = self._borg_get(borg_config, "repo_path") or "./backups"
        passphrase = self._borg_get(borg_config, "passphrase", "")
        ssh_key = self._borg_get(borg_config, "ssh_key", "")

        if repo_host:
            repo_url = f"ssh://{repo_user}@{repo_host}:{repo_port}/{repo_path}/{repo_name}"
        else:
            repo_url = f"{repo_path}/{repo_name}"

        env = {**os.environ, "BORG_PASSPHRASE": passphrase}

        if repo_host:
            rsh = f"ssh -o StrictHostKeyChecking=accept-new -p {repo_port}"
            if ssh_key:
                rsh += f" -i {ssh_key}"
            env["BORG_RSH"] = rsh

        return repo_url, env

    def _borg_list_archives(self, repo_url, borg_env):
        """List available borg archives."""
        result = subprocess.run(
            ["borg", "list", "--short", repo_url],
            capture_output=True, text=True, env=borg_env,
        )
        if result.returncode != 0:
            raise CommandError(f"Failed to list borg archives: {result.stderr}")

        archives = [a.strip() for a in result.stdout.strip().split("\n") if a.strip()]
        if not archives:
            self.stdout.write("No borg archives found.")
            return

        self.stdout.write(self.style.SUCCESS(f"Available borg archives ({len(archives)}):"))
        for archive in archives:
            self.stdout.write(f"  {archive}")

    # ── Rclone: Local restore ────────────────────────────────────────

    def _handle_local_rclone(self, options, backup_config, host_name):
        """Restore the main database to the local machine via rclone."""
        rclone_config_path = self._create_temp_rclone_config(backup_config)
        try:
            if options["list_backups"]:
                self._rclone_list_backups(rclone_config_path, host_name)
                return

            date = options["date"] or self._rclone_get_latest_date(rclone_config_path, host_name)
            if not date:
                raise CommandError("No backups found.")
            self.stdout.write(self.style.SUCCESS(f"Using backup: {date}"))

            remote_path = f"backup:{host_name}/{date}"

            self._rclone_local_restore_database(rclone_config_path, remote_path)

            if not options["db_only"]:
                self._rclone_local_restore_media(rclone_config_path, remote_path)

            self.stdout.write(self.style.SUCCESS("Local rclone restore completed."))
        finally:
            os.unlink(rclone_config_path)

    def _rclone_local_restore_database(self, rclone_config, remote_path):
        """Download backup and restore the main database locally."""
        self.stdout.write(self.style.WARNING("Restoring database locally..."))

        with tempfile.TemporaryDirectory() as temp_dir:
            db_archive = self._rclone_find_archive(rclone_config, remote_path, "dbs_backup_")
            if not db_archive:
                raise CommandError("No database archive found in backup.")

            self.stdout.write(f"  Downloading {db_archive}...")
            self._rclone_download(rclone_config, remote_path, db_archive, temp_dir)

            archive_path = os.path.join(temp_dir, db_archive)
            _safe_extract(archive_path, temp_dir)

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

    def _rclone_local_restore_media(self, rclone_config, remote_path):
        """Download and restore media files locally."""
        self.stdout.write(self.style.WARNING("Restoring media locally..."))

        media_archive = self._rclone_find_archive(rclone_config, remote_path, "media_backup_")
        if not media_archive:
            self.stdout.write(self.style.NOTICE("  No media archive found, skipping."))
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            self.stdout.write(f"  Downloading {media_archive}...")
            self._rclone_download(rclone_config, remote_path, media_archive, temp_dir)

            archive_path = os.path.join(temp_dir, media_archive)
            media_root = settings.MEDIA_ROOT

            if os.path.exists(media_root):
                self.stdout.write(f"  Removing existing media at {media_root}")
                shutil.rmtree(media_root)

            os.makedirs(media_root, exist_ok=True)
            _safe_extract(archive_path, media_root)
            self.stdout.write(self.style.SUCCESS("  Media restored locally."))

    # ── Rclone: Server restore ───────────────────────────────────────

    def _handle_server_rclone(self, options, inventory_dir, target, backup_host_name):
        """Restore on a remote server using pyinfra and the rclone module."""
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
                self._rclone_list_backups(rclone_config_path, backup_host_name)
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
                date = self._rclone_get_latest_date(rclone_config_path, backup_host_name)
            finally:
                os.unlink(rclone_config_path)
            if not date:
                raise CommandError("No backups found.")

        self.stdout.write(self.style.SUCCESS(f"Restoring backup {date} on {target}..."))

        restore_opts = {
            "backup_host_name": backup_host_name,
            "date": date,
            "db_only": options["db_only"],
            "backend": "rclone",
        }

        try:
            restore_from_backup(target_inventory_file, restore_opts)
            self.stdout.write(self.style.SUCCESS(f"Server restore completed on {target}."))
        except Exception as e:
            raise CommandError(f"Restore failed: {e}")

    # ── Rclone: Helpers ──────────────────────────────────────────────

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

        from djaploy.utils import temp_files

        path = temp_files.create(suffix=".conf", prefix="rclone_restore_")
        with open(path, "w") as f:
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

    def _rclone_list_backups(self, rclone_config, host_name):
        """List available rclone backup dates."""
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

    def _rclone_get_latest_date(self, rclone_config, host_name):
        """Get the most recent rclone backup date."""
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

    def _rclone_find_archive(self, rclone_config, remote_path, prefix):
        """Find an archive file in the remote backup by prefix."""
        result = self._run_rclone(["lsf", remote_path + "/"], rclone_config)
        if result.returncode != 0:
            return None
        for filename in result.stdout.strip().split("\n"):
            filename = filename.strip()
            if filename.startswith(prefix) and filename.endswith(".tar.gz"):
                return filename
        return None

    def _rclone_download(self, rclone_config, remote_path, filename, dest_dir):
        """Download a file from the remote backup."""
        result = self._run_rclone(
            ["copy", f"{remote_path}/{filename}", dest_dir, "--progress"],
            rclone_config,
        )
        if result.returncode != 0:
            raise CommandError(f"Download failed: {result.stderr}")

    # ── Shared helpers ───────────────────────────────────────────────

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
