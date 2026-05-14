"""Tests for HostConfig configuration"""

import unittest
from pathlib import Path
from unittest import mock

from djaploy.config import HostConfig


class TestHostConfigOpSecret(unittest.TestCase):
    """HostConfig should accept lazy OpSecret values and batch-resolve them."""

    def setUp(self):
        from djaploy.certificates import OpSecret
        OpSecret._secret_mapping.clear()
        OpSecret._secret_values.clear()

    def tearDown(self):
        from djaploy.certificates import OpSecret
        OpSecret._secret_mapping.clear()
        OpSecret._secret_values.clear()

    @staticmethod
    def _mock_resolver(fake_values):
        """Returns (side_effect, call_log) for patching OpSecret._map_secrets.

        Each invocation appends the snapshot of currently-pending references
        to ``call_log``, so callers can assert batching behavior.
        """
        from djaploy.certificates import OpSecret
        call_log = []

        def _resolve():
            pending = [
                k for k in OpSecret._secret_mapping
                if k not in OpSecret._secret_values
            ]
            call_log.append(pending)
            for k in pending:
                OpSecret._secret_values[k] = fake_values.get(k, "")

        return _resolve, call_log

    def test_opsecret_kwarg_resolves_to_string(self):
        from djaploy.certificates import OpSecret

        resolver, _ = self._mock_resolver({
            "/v/i/username": "alice",
            "/v/i/password": "p@ss",
        })

        with mock.patch.object(OpSecret, "_map_secrets", side_effect=resolver):
            host = HostConfig(
                "server",
                ssh_hostname="1.2.3.4",
                app_name="myapp",
                ovipro_username=OpSecret("/v/i/username"),
                ovipro_password=OpSecret("/v/i/password"),
            )

        _, data = host
        self.assertEqual(data["ovipro_username"], "alice")
        self.assertEqual(data["ovipro_password"], "p@ss")
        self.assertIs(type(data["ovipro_username"]), str)
        self.assertIs(type(data["ovipro_password"]), str)

    def test_batched_single_resolve_call(self):
        from djaploy.certificates import OpSecret

        resolver, call_log = self._mock_resolver({
            "/v/i/a": "1", "/v/i/b": "2", "/v/i/c": "3",
        })

        with mock.patch.object(OpSecret, "_map_secrets", side_effect=resolver):
            HostConfig(
                "server",
                ssh_hostname="1.2.3.4",
                app_name="myapp",
                a=OpSecret("/v/i/a"),
                b=OpSecret("/v/i/b"),
                c=OpSecret("/v/i/c"),
            )

        self.assertEqual(len(call_log), 1, "All secrets must batch into one op inject")
        self.assertEqual(sorted(call_log[0]), ["/v/i/a", "/v/i/b", "/v/i/c"])

    def test_opsecret_in_nested_dict_resolves(self):
        from djaploy.certificates import OpSecret

        resolver, _ = self._mock_resolver({
            "/v/i/webhook": "https://hooks.example.com/abc",
        })

        with mock.patch.object(OpSecret, "_map_secrets", side_effect=resolver):
            host = HostConfig(
                "server",
                ssh_hostname="1.2.3.4",
                app_name="myapp",
                notifications_conf={
                    "webhook_url": OpSecret("/v/i/webhook"),
                    "display_name": "MyApp",
                },
            )

        _, data = host
        self.assertEqual(data["notifications_conf"]["webhook_url"],
                         "https://hooks.example.com/abc")
        self.assertEqual(data["notifications_conf"]["display_name"], "MyApp")
        self.assertIs(type(data["notifications_conf"]["webhook_url"]), str)

    def test_opsecret_in_dataclass_resolves(self):
        """OpSecret nested in a dataclass field (BorgBackupConfig) resolves."""
        from djaploy.config import BorgBackupConfig
        from djaploy.certificates import OpSecret

        resolver, _ = self._mock_resolver({
            "/v/i/passphrase": "borg-secret",
        })

        with mock.patch.object(OpSecret, "_map_secrets", side_effect=resolver):
            host = HostConfig(
                "server",
                ssh_hostname="1.2.3.4",
                app_name="myapp",
                borg_backup=BorgBackupConfig(
                    passphrase=OpSecret("/v/i/passphrase"),
                ),
            )

        _, data = host
        self.assertEqual(data["borg_backup"].passphrase, "borg-secret")
        self.assertIs(type(data["borg_backup"].passphrase), str)

    def test_legacy_str_wrapped_opsecret_still_works(self):
        """The existing ``str(OpSecret(...))`` pattern continues to work."""
        from djaploy.certificates import OpSecret

        resolver, _ = self._mock_resolver({"/v/i/u": "alice"})

        with mock.patch.object(OpSecret, "_map_secrets", side_effect=resolver):
            host = HostConfig(
                "server",
                ssh_hostname="1.2.3.4",
                app_name="myapp",
                ovipro_username=str(OpSecret("/v/i/u")),
            )

        _, data = host
        self.assertEqual(data["ovipro_username"], "alice")

    def test_no_opsecrets_means_no_resolve_call(self):
        """A HostConfig without OpSecret values must not invoke ``op inject``."""
        from djaploy.certificates import OpSecret

        resolver, call_log = self._mock_resolver({})
        with mock.patch.object(OpSecret, "_map_secrets", side_effect=resolver):
            HostConfig("server", ssh_hostname="1.2.3.4", app_name="myapp")

        self.assertEqual(call_log, [])


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

    def test_health_check_retries_default(self):
        ctx = self._build(gunicorn_conf={})
        self.assertEqual(ctx["health_check_retries"], 3)

    def test_health_check_interval_default(self):
        ctx = self._build(gunicorn_conf={})
        self.assertEqual(ctx["health_check_interval"], 2)

    def test_health_check_retries_override(self):
        ctx = self._build(gunicorn_conf={"health_check_retries": 5, "health_check_interval": 10})
        self.assertEqual(ctx["health_check_retries"], 5)
        self.assertEqual(ctx["health_check_interval"], 10)


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


try:
    import jinja2 as _jinja2
    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False


@unittest.skipUnless(_JINJA2_AVAILABLE, "jinja2 not installed")
class TestSystemdTemplateRender(unittest.TestCase):
    """Render SYSTEMD_ZERO_DOWNTIME with Jinja2 and assert the actual output."""

    BASE_CTX = dict(
        project_name="myapp",
        app_user="app",
        app_path="/home/app/apps/myapp",
        manage_subdir="",
        workers=4,
        timeout=30,
        umask="002",
        wsgi_module="myapp.wsgi:application",
        health_check_retries=3,
        health_check_interval=2,
    )

    def _render(self, **overrides):
        from jinja2 import Environment
        from djaploy.infra.templates import SYSTEMD_ZERO_DOWNTIME
        ctx = {**self.BASE_CTX, **overrides}
        return Environment().from_string(SYSTEMD_ZERO_DOWNTIME).render(**ctx)

    def _exec_start_lines(self, rendered):
        """Return the ExecStart continuation block as a list of stripped lines."""
        lines = rendered.splitlines()
        in_exec = False
        result = []
        for line in lines:
            if line.startswith("ExecStart="):
                in_exec = True
            if in_exec:
                result.append(line.rstrip())
                if not line.endswith("\\"):
                    break
        return result

    def test_health_check_url_present_in_rendered_output(self):
        rendered = self._render(health_check_url="http://localhost:8000/health/")
        self.assertIn("--health-check-url http://localhost:8000/health/", rendered)

    def test_health_check_url_absent_when_not_set(self):
        rendered = self._render(health_check_url=None)
        self.assertNotIn("--health-check-url", rendered)

    def test_no_blank_lines_in_exec_start_without_health_check(self):
        """A missing health_check_url must not leave a blank continuation line."""
        rendered = self._render(health_check_url=None)
        for line in self._exec_start_lines(rendered):
            self.assertTrue(line.strip(), f"Blank line in ExecStart: {line!r}")

    def test_no_blank_lines_in_exec_start_with_health_check(self):
        rendered = self._render(health_check_url="http://localhost:8000/health/")
        for line in self._exec_start_lines(rendered):
            self.assertTrue(line.strip(), f"Blank line in ExecStart: {line!r}")

    def test_health_check_line_comes_before_double_dash_separator(self):
        rendered = self._render(health_check_url="http://localhost:8000/health/")
        lines = self._exec_start_lines(rendered)
        hc_idx = next(i for i, l in enumerate(lines) if "--health-check-url" in l)
        sep_idx = next(i for i, l in enumerate(lines) if l.strip() == "-- \\")
        self.assertLess(hc_idx, sep_idx)

    def test_health_check_retries_and_interval_rendered(self):
        rendered = self._render(health_check_url="http://localhost:8000/health/",
                                health_check_retries=5, health_check_interval=10)
        self.assertIn("--health-check-retries 5", rendered)
        self.assertIn("--health-check-interval 10", rendered)

    def test_health_check_retries_absent_without_url(self):
        rendered = self._render(health_check_url=None)
        self.assertNotIn("--health-check-retries", rendered)
        self.assertNotIn("--health-check-interval", rendered)

    def test_double_dash_separator_always_present(self):
        for url in [None, "http://localhost/health"]:
            with self.subTest(health_check_url=url):
                rendered = self._render(health_check_url=url)
                lines = self._exec_start_lines(rendered)
                self.assertTrue(
                    any(l.strip() == "-- \\" for l in lines),
                    "Missing '--' separator in ExecStart",
                )


if __name__ == "__main__":
    unittest.main()
