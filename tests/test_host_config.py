"""Tests for HostConfig and BackupConfig."""

import unittest

from djaploy.config import HostConfig, BackupConfig


class TestHostConfig(unittest.TestCase):
    """Test HostConfig creates pyinfra-compatible tuples."""

    def test_minimal_creation(self):
        host = HostConfig("web-1", ssh_hostname="192.168.1.1")
        name, data = host
        self.assertEqual(name, "web-1")
        self.assertEqual(data["ssh_hostname"], "192.168.1.1")
        self.assertEqual(data["ssh_user"], "deploy")  # default

    def test_full_creation(self):
        host = HostConfig(
            "web-1",
            ssh_hostname="10.0.0.1",
            ssh_user="admin",
            ssh_port=2222,
            app_user="myapp",
            app_hostname="myapp.example.com",
            env="production",
            services=["web", "worker"],
        )
        name, data = host
        self.assertEqual(name, "web-1")
        self.assertEqual(data["ssh_user"], "admin")
        self.assertEqual(data["ssh_port"], 2222)
        self.assertEqual(data["app_user"], "myapp")
        self.assertEqual(data["services"], ["web", "worker"])
        self.assertEqual(data["env"], "production")

    def test_is_tuple(self):
        host = HostConfig("web-1", ssh_hostname="10.0.0.1")
        self.assertIsInstance(host, tuple)
        self.assertEqual(len(host), 2)

    def test_missing_required_field_raises(self):
        with self.assertRaises(ValueError):
            HostConfig("web-1")  # ssh_hostname is required

    def test_ssh_key_expanded(self):
        import os
        host = HostConfig("web-1", ssh_hostname="10.0.0.1", ssh_key="~/.ssh/id_rsa")
        _, data = host
        self.assertEqual(data["ssh_key"], os.path.expanduser("~/.ssh/id_rsa"))

    def test_extra_kwargs_passed_through(self):
        host = HostConfig("web-1", ssh_hostname="10.0.0.1", custom_field="value")
        _, data = host
        self.assertEqual(data["custom_field"], "value")

    def test_optional_fields_omitted_when_none(self):
        host = HostConfig("web-1", ssh_hostname="10.0.0.1")
        _, data = host
        self.assertNotIn("ssh_key", data)
        self.assertNotIn("services", data)
        self.assertNotIn("domains", data)

    def test_backup_config_attached(self):
        backup = BackupConfig(host="backup.example.com", user="backup")
        host = HostConfig("web-1", ssh_hostname="10.0.0.1", backup=backup)
        _, data = host
        self.assertEqual(data["backup"].host, "backup.example.com")


class TestBackupConfig(unittest.TestCase):
    """Test BackupConfig validation."""

    def test_valid_sftp_config(self):
        config = BackupConfig(host="backup.host", user="backup")
        self.assertTrue(config.validate())

    def test_sftp_missing_host_raises(self):
        config = BackupConfig(user="backup")
        with self.assertRaises(ValueError):
            config.validate()

    def test_sftp_missing_user_raises(self):
        config = BackupConfig(host="backup.host")
        with self.assertRaises(ValueError):
            config.validate()

    def test_valid_s3_config(self):
        config = BackupConfig(
            type="s3",
            s3_endpoint="https://s3.example.com",
            s3_access_key="AKID",
            s3_secret_key="secret",
            s3_bucket="backups",
        )
        self.assertTrue(config.validate())

    def test_s3_missing_fields_raises(self):
        config = BackupConfig(type="s3", s3_bucket="backups")
        with self.assertRaises(ValueError):
            config.validate()

    def test_invalid_type_raises(self):
        config = BackupConfig(type="ftp")
        with self.assertRaises(ValueError):
            config.validate()

    def test_defaults(self):
        config = BackupConfig()
        self.assertTrue(config.enabled)
        self.assertEqual(config.type, "sftp")
        self.assertEqual(config.retention_days, 30)
        self.assertEqual(config.schedule, "0 2 * * *")


if __name__ == "__main__":
    unittest.main()
