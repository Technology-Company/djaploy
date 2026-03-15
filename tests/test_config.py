"""Tests for HostConfig configuration"""

import unittest
from pathlib import Path

from djaploy.config import HostConfig


class TestHostConfig(unittest.TestCase):
    """Test HostConfig with deployment settings"""

    def test_required_fields(self):
        host = HostConfig("server", ssh_hostname="1.2.3.4", app_name="myapp")
        name, data = host
        self.assertEqual(name, "server")
        self.assertEqual(data["ssh_hostname"], "1.2.3.4")
        self.assertEqual(data["app_name"], "myapp")

    def test_defaults(self):
        host = HostConfig("server", ssh_hostname="1.2.3.4", app_name="myapp")
        _, data = host
        self.assertEqual(data["ssh_user"], "deploy")
        self.assertEqual(data["app_user"], "app")
        self.assertEqual(data["python_version"], "3.11")
        self.assertEqual(data["deployment_strategy"], "zero_downtime")
        self.assertEqual(data["keep_releases"], 5)
        self.assertEqual(data["manage_py_path"], "manage.py")
        self.assertFalse(data["python_compile"])

    def test_override_deployment_settings(self):
        host = HostConfig(
            "server",
            ssh_hostname="1.2.3.4",
            app_name="myapp",
            python_version="3.13",
            deployment_strategy="in_place",
            keep_releases=3,
        )
        _, data = host
        self.assertEqual(data["python_version"], "3.13")
        self.assertEqual(data["deployment_strategy"], "in_place")
        self.assertEqual(data["keep_releases"], 3)

    def test_module_confs(self):
        host = HostConfig(
            "server",
            ssh_hostname="1.2.3.4",
            app_name="myapp",
            gunicorn_conf={"workers": 4, "timeout": 60},
            nginx_conf={"server_name": "example.com"},
        )
        _, data = host
        self.assertEqual(data["gunicorn_conf"]["workers"], 4)
        self.assertEqual(data["nginx_conf"]["server_name"], "example.com")

    def test_ssh_key_expanded(self):
        host = HostConfig(
            "server",
            ssh_hostname="1.2.3.4",
            app_name="myapp",
            ssh_key="~/.ssh/id_rsa",
        )
        _, data = host
        self.assertNotIn("~", data["ssh_key"])

    def test_app_name_required(self):
        with self.assertRaises(ValueError):
            HostConfig("server", ssh_hostname="1.2.3.4")

    def test_extra_kwargs_passed_through(self):
        host = HostConfig(
            "server",
            ssh_hostname="1.2.3.4",
            app_name="myapp",
            janitor_password="secret",
        )
        _, data = host
        self.assertEqual(data["janitor_password"], "secret")


if __name__ == "__main__":
    unittest.main()
