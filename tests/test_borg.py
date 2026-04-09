"""Tests for Borg backup configuration, helpers, and script generation."""

import unittest
from unittest.mock import patch, MagicMock

from djaploy.config import BorgBackupConfig, HostConfig


class TestBorgBackupConfig(unittest.TestCase):
    """Test BorgBackupConfig dataclass defaults and validation."""

    def test_defaults(self):
        cfg = BorgBackupConfig(passphrase="secret")
        self.assertTrue(cfg.enabled)
        self.assertIsNone(cfg.repo_host)
        self.assertEqual(cfg.repo_port, 22)
        self.assertEqual(cfg.compression, "zstd,3")
        self.assertEqual(cfg.databases, ["default.db"])
        self.assertTrue(cfg.backup_media)
        self.assertEqual(cfg.keep_daily, 7)
        self.assertEqual(cfg.keep_weekly, 4)
        self.assertEqual(cfg.keep_monthly, 6)
        self.assertEqual(cfg.schedule, "0 2 * * *")

    def test_validate_requires_passphrase(self):
        cfg = BorgBackupConfig()
        with self.assertRaises(ValueError) as ctx:
            cfg.validate()
        self.assertIn("passphrase", str(ctx.exception))

    def test_validate_passes_with_passphrase(self):
        cfg = BorgBackupConfig(passphrase="secret")
        self.assertTrue(cfg.validate())

    def test_custom_retention(self):
        cfg = BorgBackupConfig(
            passphrase="secret",
            keep_daily=14,
            keep_weekly=8,
            keep_monthly=12,
        )
        self.assertEqual(cfg.keep_daily, 14)
        self.assertEqual(cfg.keep_weekly, 8)
        self.assertEqual(cfg.keep_monthly, 12)


class TestHostConfigWithBorg(unittest.TestCase):
    """Test HostConfig accepts borg_backup field."""

    def test_borg_backup_in_host_config(self):
        from dataclasses import asdict
        borg = BorgBackupConfig(
            repo_host="backup.example.com",
            repo_user="borg",
            passphrase="secret",
        )
        host = HostConfig(
            "server",
            ssh_hostname="1.2.3.4",
            app_name="myapp",
            borg_backup=borg,
        )
        _, data = host
        self.assertIn("borg_backup", data)
        self.assertEqual(data["borg_backup"].repo_host, "backup.example.com")

    def test_borg_backup_defaults_to_none(self):
        host = HostConfig("server", ssh_hostname="1.2.3.4", app_name="myapp")
        _, data = host
        # borg_backup is Optional, so it should not be present when not set
        self.assertNotIn("borg_backup", data)


class TestBorgHelpers(unittest.TestCase):
    """Test pure helper functions from borg hooks."""

    def setUp(self):
        from djaploy.apps.borg.infra.djaploy_hooks import (
            _build_repo_url,
            _build_borg_rsh,
        )
        self._build_repo_url = _build_repo_url
        self._build_borg_rsh = _build_borg_rsh

    def test_build_repo_url_ssh(self):
        config = {
            "repo_host": "backup.example.com",
            "repo_user": "borg",
            "repo_port": 22,
            "repo_path": "./repos",
        }
        url = self._build_repo_url(config, "myhost")
        self.assertEqual(url, "ssh://borg@backup.example.com:22/./repos/myhost")

    def test_build_repo_url_hetzner_storage_box(self):
        config = {
            "repo_host": "u12345.your-storagebox.de",
            "repo_user": "u12345",
            "repo_port": 23,
            "repo_path": "./backups",
        }
        url = self._build_repo_url(config, "prod_server")
        self.assertEqual(
            url, "ssh://u12345@u12345.your-storagebox.de:23/./backups/prod_server"
        )

    def test_build_repo_url_local(self):
        config = {"repo_host": "", "repo_path": "/tmp/borg"}
        url = self._build_repo_url(config, "test")
        self.assertEqual(url, "/tmp/borg/test")

    def test_build_repo_url_local_default_path(self):
        config = {"repo_host": ""}
        url = self._build_repo_url(config, "test")
        self.assertEqual(url, "./backups/test")

    def test_build_borg_rsh_ssh(self):
        config = {"repo_host": "backup.example.com", "repo_port": 22, "ssh_key": ""}
        rsh = self._build_borg_rsh(config)
        self.assertEqual(rsh, "ssh -o StrictHostKeyChecking=accept-new -p 22")

    def test_build_borg_rsh_with_key(self):
        config = {
            "repo_host": "backup.example.com",
            "repo_port": 23,
            "ssh_key": "/home/app/.ssh/id_ed25519",
        }
        rsh = self._build_borg_rsh(config)
        self.assertIn("-p 23", rsh)
        self.assertIn("-i /home/app/.ssh/id_ed25519", rsh)

    def test_build_borg_rsh_local(self):
        config = {"repo_host": ""}
        rsh = self._build_borg_rsh(config)
        self.assertEqual(rsh, "")


class TestBorgBackupScriptGeneration(unittest.TestCase):
    """Test generated backup script content."""

    def _generate(self, borg_config, app_user="app", repo_name="test_host",
                  host_data=None):
        from djaploy.apps.borg.infra.djaploy_hooks import _generate_backup_script
        if host_data is None:
            host_data = MagicMock()
            host_data.app_name = "myapp"
            host_data.db_dir = None
        return _generate_backup_script(borg_config, app_user, repo_name, host_data)

    def test_script_has_shebang(self):
        config = {"passphrase": "secret", "repo_host": ""}
        script = self._generate(config)
        self.assertTrue(script.startswith("#!/bin/bash"))

    def test_script_loads_passphrase_from_env_file(self):
        config = {"passphrase": "my-secret-phrase", "repo_host": ""}
        script = self._generate(config)
        # Passphrase should NOT be inline in the script
        self.assertNotIn("my-secret-phrase", script)
        # Script should source the separate env file
        self.assertIn("source /home/app/.borg_env", script)

    def test_script_uses_correct_db_path(self):
        config = {"passphrase": "s", "repo_host": "", "db_path": "/data/dbs"}
        script = self._generate(config)
        self.assertIn('DB_DIR="/data/dbs"', script)

    def test_script_default_db_path(self):
        config = {"passphrase": "s", "repo_host": ""}
        script = self._generate(config, app_user="myuser")
        self.assertIn('DB_DIR="/home/myuser/dbs"', script)

    def test_script_includes_databases(self):
        config = {
            "passphrase": "s",
            "repo_host": "",
            "databases": ["main.db", "analytics.db"],
        }
        script = self._generate(config)
        self.assertIn('"main.db"', script)
        self.assertIn('"analytics.db"', script)

    def test_script_compression(self):
        config = {"passphrase": "s", "repo_host": "", "compression": "lz4"}
        script = self._generate(config)
        self.assertIn("--compression lz4", script)

    def test_script_retention_policy(self):
        config = {
            "passphrase": "s",
            "repo_host": "",
            "keep_daily": 14,
            "keep_weekly": 8,
            "keep_monthly": 12,
        }
        script = self._generate(config)
        self.assertIn("--keep-daily=14", script)
        self.assertIn("--keep-weekly=8", script)
        self.assertIn("--keep-monthly=12", script)

    def test_script_includes_media_by_default(self):
        config = {"passphrase": "s", "repo_host": ""}
        script = self._generate(config)
        self.assertIn("MEDIA_ARGS", script)
        self.assertIn("Including media directory", script)

    def test_script_skips_media_when_disabled(self):
        config = {"passphrase": "s", "repo_host": "", "backup_media": False}
        script = self._generate(config)
        self.assertNotIn("MEDIA_ARGS", script)

    def test_script_sets_borg_rsh_for_ssh(self):
        config = {
            "passphrase": "s",
            "repo_host": "backup.example.com",
            "repo_port": 23,
        }
        script = self._generate(config)
        self.assertIn("BORG_RSH", script)
        self.assertIn("-p 23", script)

    def test_script_no_borg_rsh_for_local(self):
        config = {"passphrase": "s", "repo_host": ""}
        script = self._generate(config)
        self.assertNotIn("BORG_RSH", script)

    def test_script_default_media_path_zero_downtime(self):
        """Zero-downtime deploys use shared/media by default."""
        config = {"passphrase": "s", "repo_host": ""}
        host_data = MagicMock()
        host_data.app_name = "myapp"
        host_data.db_dir = None
        host_data.deployment_strategy = "zero_downtime"
        script = self._generate(config, app_user="app", host_data=host_data)
        self.assertIn('MEDIA_DIR="/home/app/apps/myapp/shared/media"', script)

    def test_script_default_media_path_in_place(self):
        """In-place deploys use media directly under app path by default."""
        config = {"passphrase": "s", "repo_host": ""}
        host_data = MagicMock()
        host_data.app_name = "myapp"
        host_data.db_dir = None
        host_data.deployment_strategy = "in_place"
        script = self._generate(config, app_user="app", host_data=host_data)
        self.assertIn('MEDIA_DIR="/home/app/apps/myapp/media"', script)
        self.assertNotIn("/shared/media", script)

    def test_script_explicit_media_path_overrides_strategy(self):
        """Explicit media_path overrides the strategy-derived default."""
        config = {"passphrase": "s", "repo_host": "", "media_path": "/custom/media"}
        host_data = MagicMock()
        host_data.app_name = "myapp"
        host_data.db_dir = None
        host_data.deployment_strategy = "zero_downtime"
        script = self._generate(config, app_user="app", host_data=host_data)
        self.assertIn('MEDIA_DIR="/custom/media"', script)
        self.assertNotIn("/shared/media", script)

    def test_script_default_db_path_in_place(self):
        """In-place db_path defaults to host db_dir or /home/{user}/dbs."""
        config = {"passphrase": "s", "repo_host": ""}
        host_data = MagicMock()
        host_data.app_name = "myapp"
        host_data.db_dir = "/home/app/dbs/myapp"
        host_data.deployment_strategy = "in_place"
        script = self._generate(config, app_user="app", host_data=host_data)
        self.assertIn('DB_DIR="/home/app/dbs/myapp"', script)

    def test_script_default_db_path_zero_downtime(self):
        """Zero-downtime db_path defaults to host db_dir or /home/{user}/dbs."""
        config = {"passphrase": "s", "repo_host": ""}
        host_data = MagicMock()
        host_data.app_name = "myapp"
        host_data.db_dir = "/home/app/dbs/myapp"
        host_data.deployment_strategy = "zero_downtime"
        script = self._generate(config, app_user="app", host_data=host_data)
        self.assertIn('DB_DIR="/home/app/dbs/myapp"', script)


class TestRestoreBackupBackendDetection(unittest.TestCase):
    """Test backend auto-detection in restore_backup management command."""

    def _make_source_config(self, backup=None, borg_backup=None):
        """Build a host data dict as _load_inventory_hosts would return."""
        data = {"name": "test-host", "ssh_hostname": "localhost", "app_name": "test"}
        if backup is not None:
            data["backup"] = backup
        if borg_backup is not None:
            data["borg_backup"] = borg_backup
        return [("test-host", data)]

    @patch("djaploy.management.commands.restore_backup._load_inventory_hosts")
    @patch("djaploy.management.commands.restore_backup.find_inventory")
    def test_auto_detects_borg(self, mock_find_inv, mock_load):
        from djaploy.management.commands.restore_backup import Command
        from django.core.management import CommandError

        mock_find_inv.return_value = "/fake/inv/dev.py"
        mock_load.return_value = self._make_source_config(
            borg_backup={"passphrase": "s", "repo_host": "backup.example.com"}
        )

        cmd = Command()
        # Patch the borg handler to verify it gets called
        with patch.object(cmd, "_handle_local_borg") as mock_borg:
            cmd.handle(
                env="dev", target="local", backend="auto",
                date=None, archive=None, db_only=False,
                list_backups=False, inventory_dir=None,
            )
            mock_borg.assert_called_once()

    @patch("djaploy.management.commands.restore_backup._load_inventory_hosts")
    @patch("djaploy.management.commands.restore_backup.find_inventory")
    def test_auto_detects_rclone(self, mock_find_inv, mock_load):
        from djaploy.management.commands.restore_backup import Command

        mock_find_inv.return_value = "/fake/inv/dev.py"
        mock_load.return_value = self._make_source_config(
            backup={"type": "sftp", "host": "backup.example.com", "user": "u"}
        )

        cmd = Command()
        with patch.object(cmd, "_handle_local_rclone") as mock_rclone:
            cmd.handle(
                env="dev", target="local", backend="auto",
                date=None, archive=None, db_only=False,
                list_backups=False, inventory_dir=None,
            )
            mock_rclone.assert_called_once()

    @patch("djaploy.management.commands.restore_backup._load_inventory_hosts")
    @patch("djaploy.management.commands.restore_backup.find_inventory")
    def test_auto_raises_when_both_configured(self, mock_find_inv, mock_load):
        from djaploy.management.commands.restore_backup import Command
        from django.core.management import CommandError

        mock_find_inv.return_value = "/fake/inv/dev.py"
        mock_load.return_value = self._make_source_config(
            backup={"type": "sftp", "host": "h", "user": "u"},
            borg_backup={"passphrase": "s"},
        )

        cmd = Command()
        with self.assertRaises(CommandError) as ctx:
            cmd.handle(
                env="dev", target="local", backend="auto",
                date=None, archive=None, db_only=False,
                list_backups=False, inventory_dir=None,
            )
        self.assertIn("Specify --backend", str(ctx.exception))

    @patch("djaploy.management.commands.restore_backup._load_inventory_hosts")
    @patch("djaploy.management.commands.restore_backup.find_inventory")
    def test_explicit_borg_without_config_raises(self, mock_find_inv, mock_load):
        from djaploy.management.commands.restore_backup import Command
        from django.core.management import CommandError

        mock_find_inv.return_value = "/fake/inv/dev.py"
        mock_load.return_value = self._make_source_config()

        cmd = Command()
        with self.assertRaises(CommandError) as ctx:
            cmd.handle(
                env="dev", target="local", backend="borg",
                date=None, archive=None, db_only=False,
                list_backups=False, inventory_dir=None,
            )
        self.assertIn("No borg_backup configuration", str(ctx.exception))

    @patch("djaploy.management.commands.restore_backup._load_inventory_hosts")
    @patch("djaploy.management.commands.restore_backup.find_inventory")
    def test_explicit_rclone_without_config_raises(self, mock_find_inv, mock_load):
        from djaploy.management.commands.restore_backup import Command
        from django.core.management import CommandError

        mock_find_inv.return_value = "/fake/inv/dev.py"
        mock_load.return_value = self._make_source_config()

        cmd = Command()
        with self.assertRaises(CommandError) as ctx:
            cmd.handle(
                env="dev", target="local", backend="rclone",
                date=None, archive=None, db_only=False,
                list_backups=False, inventory_dir=None,
            )
        self.assertIn("No backup configuration", str(ctx.exception))


class TestRcloneRestoreBackendSkip(unittest.TestCase):
    """Test that rclone restore hook respects backend field."""

    @patch("djaploy.apps.rclone.infra.djaploy_hooks.server", create=True)
    @patch("djaploy.apps.rclone.infra.djaploy_hooks.systemd", create=True)
    def test_rclone_skips_when_backend_is_borg(self, mock_systemd, mock_server):
        from djaploy.apps.rclone.infra.djaploy_hooks import restore_rclone

        host_data = MagicMock()
        host_data.backup = {"type": "sftp", "host": "h", "user": "u"}
        host_data.services = ["myapp"]

        restore_opts = {"backend": "borg", "db_only": False}
        # Should return early without touching pyinfra
        restore_rclone(host_data, restore_opts)
        mock_server.shell.assert_not_called()


if __name__ == "__main__":
    unittest.main()
