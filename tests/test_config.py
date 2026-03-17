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


class TestBuildTemplateContext(unittest.TestCase):
    """Test that build_template_context maps HostConfig fields to template vars."""

    def _make_host_data(self, **kwargs):
        """Return a simple object with attributes, simulating pyinfra host_data."""
        from types import SimpleNamespace
        defaults = dict(
            app_user="app",
            app_name="myapp",
            deployment_strategy="zero_downtime",
            manage_py_path="manage.py",
            gunicorn_conf={},
            nginx_conf={},
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _build(self, **kwargs):
        from djaploy.infra.templates import build_template_context
        from unittest.mock import patch
        host_data = self._make_host_data(**kwargs)
        with patch("djaploy.infra.utils.get_app_path", return_value="/home/app/apps/myapp"), \
             patch("djaploy.infra.utils.is_zero_downtime", return_value=True):
            return build_template_context(host_data)

    def test_health_check_url_passed_through(self):
        ctx = self._build(gunicorn_conf={"health_check_url": "http://localhost:8000/health/"})
        self.assertEqual(ctx["health_check_url"], "http://localhost:8000/health/")

    def test_health_check_url_defaults_to_none(self):
        ctx = self._build(gunicorn_conf={})
        self.assertIsNone(ctx["health_check_url"])

    def test_health_check_url_none_when_gunicorn_conf_absent(self):
        ctx = self._build(gunicorn_conf=None)
        self.assertIsNone(ctx["health_check_url"])

    def test_standard_gunicorn_fields_still_present(self):
        ctx = self._build(gunicorn_conf={"workers": 4, "health_check_url": "http://localhost/health"})
        self.assertEqual(ctx["workers"], 4)
        self.assertEqual(ctx["health_check_url"], "http://localhost/health")


class TestSystemdTemplate(unittest.TestCase):
    """Test the SYSTEMD_ZERO_DOWNTIME template string structure."""

    def test_template_contains_health_check_conditional(self):
        from djaploy.infra.templates import SYSTEMD_ZERO_DOWNTIME
        self.assertIn("{% if health_check_url %}", SYSTEMD_ZERO_DOWNTIME)
        self.assertIn("--health-check-url {{ health_check_url }}", SYSTEMD_ZERO_DOWNTIME)

    def test_health_check_flag_appears_before_double_dash(self):
        """--health-check-url must come before the '--' separator in ExecStart."""
        from djaploy.infra.templates import SYSTEMD_ZERO_DOWNTIME
        hc_pos = SYSTEMD_ZERO_DOWNTIME.index("--health-check-url")
        sep_pos = SYSTEMD_ZERO_DOWNTIME.index("\n    -- \\")
        self.assertLess(hc_pos, sep_pos)


if __name__ == "__main__":
    unittest.main()
