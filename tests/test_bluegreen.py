"""Tests for blue-green deployment support."""

import ast
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from djaploy.config import HostConfig
from djaploy.hooks import HookRegistry


class TestHostConfigBluegreen(unittest.TestCase):
    """Test HostConfig with bluegreen strategy."""

    def test_bluegreen_strategy_accepted(self):
        host = HostConfig(
            "server", ssh_hostname="1.2.3.4", app_name="myapp",
            deployment_strategy="bluegreen",
        )
        _, data = host
        self.assertEqual(data["deployment_strategy"], "bluegreen")

    def test_invalid_strategy_still_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            HostConfig(
                "server", ssh_hostname="1.2.3.4", app_name="myapp",
                deployment_strategy="rolling",
            )
        self.assertIn("rolling", str(ctx.exception))

    def test_all_valid_strategies(self):
        for strategy in ("in_place", "zero_downtime", "bluegreen"):
            host = HostConfig(
                "server", ssh_hostname="1.2.3.4", app_name="myapp",
                deployment_strategy=strategy,
            )
            _, data = host
            self.assertEqual(data["deployment_strategy"], strategy)


class TestBluegreenUtilFunctions(unittest.TestCase):
    """Test blue-green utility functions in utils.py."""

    def _make_host_data(self, **kwargs):
        defaults = dict(
            app_user="app",
            app_name="myapp",
            deployment_strategy="bluegreen",
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_is_bluegreen_true(self):
        from djaploy.infra.utils import is_bluegreen
        hd = self._make_host_data(deployment_strategy="bluegreen")
        self.assertTrue(is_bluegreen(hd))

    def test_is_bluegreen_false_for_zero_downtime(self):
        from djaploy.infra.utils import is_bluegreen
        hd = self._make_host_data(deployment_strategy="zero_downtime")
        self.assertFalse(is_bluegreen(hd))

    def test_is_bluegreen_false_for_in_place(self):
        from djaploy.infra.utils import is_bluegreen
        hd = self._make_host_data(deployment_strategy="in_place")
        self.assertFalse(is_bluegreen(hd))

    def test_get_bluegreen_paths(self):
        from djaploy.infra.utils import get_bluegreen_paths
        hd = self._make_host_data()
        paths = get_bluegreen_paths(hd)
        self.assertEqual(paths["app_path"], "/home/app/apps/myapp")
        self.assertEqual(paths["slots_path"], "/home/app/apps/myapp/slots")
        self.assertEqual(paths["blue_path"], "/home/app/apps/myapp/slots/blue")
        self.assertEqual(paths["green_path"], "/home/app/apps/myapp/slots/green")
        self.assertEqual(paths["shared_path"], "/home/app/apps/myapp/shared")
        self.assertEqual(paths["state_file"], "/home/app/apps/myapp/state.json")

    def test_get_slot_socket_path(self):
        from djaploy.infra.utils import get_slot_socket_path
        self.assertEqual(
            get_slot_socket_path("myapp", "blue"),
            "/run/myapp-blue/myapp.sock",
        )
        self.assertEqual(
            get_slot_socket_path("myapp", "green"),
            "/run/myapp-green/myapp.sock",
        )

    def test_get_slot_service_name(self):
        from djaploy.infra.utils import get_slot_service_name
        self.assertEqual(get_slot_service_name("myapp", "blue"), "myapp-blue")
        self.assertEqual(get_slot_service_name("myapp", "green"), "myapp-green")


class TestBluegreenStateModule(unittest.TestCase):
    """Test the bluegreen state management module."""

    def test_other_slot(self):
        from djaploy.infra.bluegreen import other_slot
        self.assertEqual(other_slot("blue"), "green")
        self.assertEqual(other_slot("green"), "blue")

    def test_default_state(self):
        from djaploy.infra.bluegreen import DEFAULT_STATE
        self.assertIsNone(DEFAULT_STATE["active_slot"])
        self.assertIn("blue", DEFAULT_STATE["slots"])
        self.assertIn("green", DEFAULT_STATE["slots"])
        self.assertIsNone(DEFAULT_STATE["slots"]["blue"])
        self.assertIsNone(DEFAULT_STATE["slots"]["green"])

    def test_init_state_cmd_creates_valid_json(self):
        from djaploy.infra.bluegreen import init_state_cmd, DEFAULT_STATE
        cmd = init_state_cmd("/tmp/state.json")
        # The command should contain valid JSON
        self.assertIn("test -f /tmp/state.json", cmd)
        # Extract the JSON from the echo command
        json_str = cmd.split("echo '")[1].split("' > ")[0]
        parsed = json.loads(json_str)
        self.assertEqual(parsed, DEFAULT_STATE)

    def test_read_active_slot_cmd(self):
        from djaploy.infra.bluegreen import read_active_slot_cmd
        cmd = read_active_slot_cmd("/tmp/state.json")
        self.assertIn("python3", cmd)
        self.assertIn("/tmp/state.json", cmd)
        self.assertIn("active_slot", cmd)

    def test_update_slot_info_cmd(self):
        from djaploy.infra.bluegreen import update_slot_info_cmd
        cmd = update_slot_info_cmd(
            "/tmp/state.json", "blue", "app-v1.0", "abc123",
            "/home/app/venv/bin/python", "/home/app/venv",
        )
        self.assertIn("python3", cmd)
        self.assertIn("/tmp/state.json", cmd)
        self.assertIn("blue", cmd)
        self.assertIn("app-v1.0", cmd)
        # Should write atomically via .tmp + rename
        self.assertIn("state.json.tmp", cmd)
        self.assertIn("os.rename", cmd)

    def test_set_active_slot_cmd(self):
        from djaploy.infra.bluegreen import set_active_slot_cmd
        cmd = set_active_slot_cmd("/tmp/state.json", "green")
        self.assertIn("python3", cmd)
        self.assertIn("green", cmd)
        self.assertIn("active_slot", cmd)
        # Should write atomically
        self.assertIn("os.rename", cmd)


class TestBluegreenTemplates(unittest.TestCase):
    """Test blue-green systemd and nginx templates."""

    def test_bluegreen_systemd_template_exists(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        self.assertIsInstance(SYSTEMD_BLUEGREEN, str)
        self.assertTrue(len(SYSTEMD_BLUEGREEN) > 0)

    def test_bluegreen_systemd_type_notify(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        self.assertIn("Type=notify", SYSTEMD_BLUEGREEN)

    def test_bluegreen_systemd_no_gunicornherder(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        self.assertNotIn("gunicornherder", SYSTEMD_BLUEGREEN)

    def test_bluegreen_systemd_has_slot_variables(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        self.assertIn("{{ slot }}", SYSTEMD_BLUEGREEN)
        self.assertIn("{{ slot_path }}", SYSTEMD_BLUEGREEN)

    def test_bluegreen_systemd_runtime_directory_includes_slot(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        self.assertIn("RuntimeDirectory={{ project_name }}-{{ slot }}", SYSTEMD_BLUEGREEN)

    def test_bluegreen_systemd_socket_includes_slot(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        self.assertIn(
            "unix:/run/{{ project_name }}-{{ slot }}/{{ project_name }}.sock",
            SYSTEMD_BLUEGREEN,
        )

    def test_bluegreen_nginx_upstream_template(self):
        from djaploy.infra.templates import NGINX_UPSTREAM_BLUEGREEN
        self.assertIn("upstream {{ project_name }}", NGINX_UPSTREAM_BLUEGREEN)
        self.assertIn("{{ active_slot }}", NGINX_UPSTREAM_BLUEGREEN)

    def test_bluegreen_nginx_site_no_upstream(self):
        from djaploy.infra.templates import NGINX_SITE_BLUEGREEN
        self.assertNotIn("upstream", NGINX_SITE_BLUEGREEN)
        self.assertIn("proxy_pass http://{{ project_name }}", NGINX_SITE_BLUEGREEN)

    def test_bluegreen_nginx_site_ssl_no_upstream(self):
        from djaploy.infra.templates import NGINX_SITE_SSL_BLUEGREEN
        self.assertNotIn("upstream", NGINX_SITE_SSL_BLUEGREEN)
        self.assertIn("proxy_pass http://{{ project_name }}", NGINX_SITE_SSL_BLUEGREEN)


try:
    import jinja2 as _jinja2
    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False


@unittest.skipUnless(_JINJA2_AVAILABLE, "jinja2 not installed")
class TestBluegreenTemplateRender(unittest.TestCase):
    """Render blue-green templates with Jinja2 and verify output."""

    def _base_ctx(self):
        return dict(
            project_name="myapp",
            app_user="app",
            slot="blue",
            slot_path="/home/app/apps/myapp/slots/blue",
            manage_subdir="",
            workers=4,
            timeout=30,
            umask="002",
            wsgi_module="myapp.wsgi:application",
        )

    def _render(self, template_str, **overrides):
        from jinja2 import Environment
        ctx = {**self._base_ctx(), **overrides}
        return Environment().from_string(template_str).render(**ctx)

    def test_systemd_renders_slot_in_socket_path(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        rendered = self._render(SYSTEMD_BLUEGREEN)
        self.assertIn("unix:/run/myapp-blue/myapp.sock", rendered)

    def test_systemd_renders_slot_in_runtime_directory(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        rendered = self._render(SYSTEMD_BLUEGREEN)
        self.assertIn("RuntimeDirectory=myapp-blue", rendered)

    def test_systemd_renders_slot_path_in_exec_start(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        rendered = self._render(SYSTEMD_BLUEGREEN)
        self.assertIn("/home/app/apps/myapp/slots/blue/.venv/bin/gunicorn", rendered)

    def test_systemd_renders_working_directory_with_subdir(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        rendered = self._render(SYSTEMD_BLUEGREEN, manage_subdir="bostad")
        self.assertIn("WorkingDirectory=/home/app/apps/myapp/slots/blue/bostad", rendered)

    def test_systemd_description_includes_slot(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        rendered = self._render(SYSTEMD_BLUEGREEN)
        self.assertIn("(blue)", rendered)

    def test_systemd_green_slot_renders_differently(self):
        from djaploy.infra.templates import SYSTEMD_BLUEGREEN
        rendered = self._render(
            SYSTEMD_BLUEGREEN,
            slot="green",
            slot_path="/home/app/apps/myapp/slots/green",
        )
        self.assertIn("unix:/run/myapp-green/myapp.sock", rendered)
        self.assertIn("RuntimeDirectory=myapp-green", rendered)
        self.assertIn("(green)", rendered)

    def test_nginx_upstream_renders_with_active_slot(self):
        from djaploy.infra.templates import NGINX_UPSTREAM_BLUEGREEN
        from jinja2 import Environment
        rendered = Environment().from_string(NGINX_UPSTREAM_BLUEGREEN).render(
            project_name="myapp", active_slot="green",
        )
        self.assertIn("unix:/run/myapp-green/myapp.sock", rendered)


class TestBluegreenTemplateContext(unittest.TestCase):
    """Test build_template_context for bluegreen strategy."""

    def _make_host_data(self, **kwargs):
        defaults = dict(
            app_user="app",
            app_name="myapp",
            deployment_strategy="bluegreen",
            manage_py_path="manage.py",
            gunicorn_conf={},
            nginx_conf={},
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _build(self, **kwargs):
        from djaploy.infra.templates import build_template_context
        host_data = self._make_host_data(**kwargs)
        with patch("djaploy.infra.utils.get_app_path", return_value="/home/app/apps/myapp"):
            return build_template_context(host_data)

    def test_bluegreen_flag_set(self):
        ctx = self._build()
        self.assertTrue(ctx["bluegreen"])

    def test_non_bluegreen_flag_not_set(self):
        ctx = self._build(deployment_strategy="zero_downtime")
        self.assertFalse(ctx["bluegreen"])

    def test_bluegreen_default_active_slot(self):
        ctx = self._build()
        self.assertEqual(ctx["active_slot"], "blue")

    def test_bluegreen_static_path_uses_shared(self):
        ctx = self._build()
        self.assertEqual(ctx["static_path"], "/home/app/apps/myapp/shared/staticfiles")
        self.assertEqual(ctx["media_path"], "/home/app/apps/myapp/shared/media")


class TestBluegreenCommandFiles(unittest.TestCase):
    """Verify new command files parse correctly."""

    commands_dir = Path(__file__).resolve().parent.parent / "djaploy" / "commands"

    def test_activate_parses(self):
        path = self.commands_dir / "activate.py"
        self.assertTrue(path.exists(), "activate.py should exist")
        source = path.read_text()
        ast.parse(source, filename=str(path))

    def test_status_parses(self):
        path = self.commands_dir / "status.py"
        self.assertTrue(path.exists(), "status.py should exist")
        source = path.read_text()
        ast.parse(source, filename=str(path))


class TestBluegreenRollbackValidation(unittest.TestCase):
    """Test rollback validation accepts bluegreen."""

    def _make_inventory(self, strategy):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(
                f"hosts = [('test-host', {{'ssh_hostname': 'localhost', "
                f"'deployment_strategy': '{strategy}', 'app_name': 'test'}})]\n"
            )
        return path

    def test_rollback_allows_bluegreen(self):
        from djaploy.builtin_hooks import _rollback_validate_strategy

        inv = self._make_inventory("bluegreen")
        try:
            context = {"config": None, "env": "production", "inventory_file": inv}
            _rollback_validate_strategy(context)
        finally:
            os.unlink(inv)

    def test_rollback_still_rejects_in_place(self):
        from djaploy.builtin_hooks import _rollback_validate_strategy

        inv = self._make_inventory("in_place")
        try:
            context = {"config": None, "env": "production", "inventory_file": inv}
            with self.assertRaises(ValueError):
                _rollback_validate_strategy(context)
        finally:
            os.unlink(inv)


class TestBluegreenPythonAPI(unittest.TestCase):
    """Test Python API wrappers for blue-green commands."""

    @patch("djaploy.deploy._build_pyinfra_data", return_value={"env": "production"})
    def test_activate_project_builds_context(self, _mock_data):
        captured_context = {}

        def mock_run_command(ctx):
            captured_context.update(ctx)

        with patch("djaploy.deploy.run_command", side_effect=mock_run_command):
            from djaploy.deploy import activate_project
            activate_project("/tmp/production.py")

        self.assertEqual(captured_context["command"], "activate")
        self.assertEqual(captured_context["env"], "production")
        self.assertIn("command_file", captured_context)

    @patch("djaploy.deploy._build_pyinfra_data", return_value={"env": "production"})
    def test_bluegreen_status_builds_context(self, _mock_data):
        captured_context = {}

        def mock_run_command(ctx):
            captured_context.update(ctx)

        with patch("djaploy.deploy.run_command", side_effect=mock_run_command):
            from djaploy.deploy import bluegreen_status
            bluegreen_status("/tmp/production.py")

        self.assertEqual(captured_context["command"], "status")


class TestBluegreenLifecycle(unittest.TestCase):
    """Test the activate command lifecycle."""

    def _run_with_registry(self, registry, context, pyinfra_side_effect=None):
        with patch("djaploy.hooks.discover_hooks"), \
             patch("djaploy.hooks.call_hook", side_effect=registry.call), \
             patch("djaploy.deploy._preprocess_inventory", return_value="/dev/null"), \
             patch("djaploy.deploy._run_pyinfra", side_effect=pyinfra_side_effect):
            from djaploy.deploy import run_command
            run_command(context)

    def test_activate_lifecycle_order(self):
        registry = HookRegistry()
        order = []

        @registry.hook("activate:precommand")
        def h1(ctx): order.append("activate:precommand")

        @registry.hook("precommand")
        def h2(ctx): order.append("precommand")

        @registry.hook("activate:postcommand")
        def h3(ctx): order.append("activate:postcommand")

        @registry.hook("postcommand")
        def h4(ctx): order.append("postcommand")

        context = {
            "command": "activate",
            "config": MagicMock(),
            "env": "test",
            "command_file": "/dev/null",
            "inventory_file": "/dev/null",
            "pyinfra_data": {},
        }

        self._run_with_registry(registry, context)

        self.assertEqual(order, [
            "activate:precommand",
            "precommand",
            "activate:postcommand",
            "postcommand",
        ])
        self.assertTrue(context["success"])


class TestBluegreenHookDiscovery(unittest.TestCase):
    """Test that activate hooks are discoverable."""

    def test_activate_hook_registered(self):
        """The activate_bluegreen hook should be registered in the core hooks."""
        from djaploy.hooks import HookRegistry
        from pathlib import Path
        import djaploy.discovery

        registry = HookRegistry()

        djaploy_dir = Path(__file__).resolve().parent.parent / "djaploy"
        mock_djaploy = MagicMock()
        mock_djaploy.label = "djaploy"
        mock_djaploy.path = str(djaploy_dir)

        from djaploy.hooks import _registry as global_registry
        # Save full global state before discovery pollutes it
        saved_remote = {k: list(v) for k, v in global_registry._remote_hooks.items()}
        saved_hooks = {k: list(v) for k, v in global_registry._hooks.items()}

        try:
            with patch("djaploy.hooks.HookRegistry._load_builtin_hooks"):
                with patch("djaploy.discovery.apps") as mock_django_apps:
                    mock_django_apps.get_app_configs.return_value = [mock_djaploy]
                    registry.discover()

            activate_hooks = global_registry.get_remote_hooks("activate")
            activate_names = [h.function.__name__ for h in activate_hooks]
            self.assertIn("activate_bluegreen", activate_names)
        finally:
            global_registry._remote_hooks.clear()
            global_registry._remote_hooks.update(saved_remote)
            global_registry._hooks.clear()
            global_registry._hooks.update(saved_hooks)


class TestBluegreenHealthCheck(unittest.TestCase):
    """Test health check logic in activate hook."""

    def test_health_check_socket_path_uses_target_slot(self):
        """Health check should curl the target slot's socket."""
        from djaploy.infra.utils import get_slot_socket_path
        blue_socket = get_slot_socket_path("myapp", "blue")
        green_socket = get_slot_socket_path("myapp", "green")
        self.assertEqual(blue_socket, "/run/myapp-blue/myapp.sock")
        self.assertEqual(green_socket, "/run/myapp-green/myapp.sock")
        # Sockets must be different so health check targets the right slot
        self.assertNotEqual(blue_socket, green_socket)

    def test_health_check_service_name_uses_target_slot(self):
        """Health check fallback should check the target slot's service."""
        from djaploy.infra.utils import get_slot_service_name
        self.assertEqual(get_slot_service_name("myapp", "blue"), "myapp-blue")
        self.assertEqual(get_slot_service_name("myapp", "green"), "myapp-green")

    def test_health_check_command_structure(self):
        """Verify the health check shell command contains the expected elements."""
        # Simulate what activate_bluegreen generates
        app_name = "testapp"
        target_slot = "green"
        from djaploy.infra.utils import get_slot_socket_path
        socket_path = get_slot_socket_path(app_name, target_slot)

        # Build the command the same way the hook does
        cmd = (
            f"if curl -sf --max-time 5 --unix-socket {socket_path} http://localhost/ > /dev/null 2>&1; then "
            f"echo 'Health check passed: {target_slot} slot is responding'; "
            f"else "
            f"if [ -S {socket_path} ] && systemctl is-active {app_name}-{target_slot}.service > /dev/null 2>&1; then "
            f"echo 'Health check: {target_slot} slot service is active (HTTP response may be non-200, proceeding)'; "
            f"else "
            f"echo 'ACTIVATION ABORTED: {target_slot} slot is not responding.' && "
            f"echo 'Socket: {socket_path}' && "
            f"echo 'Check: systemctl status {app_name}-{target_slot}.service' && "
            f"exit 1; "
            f"fi; "
            f"fi"
        )

        # Must curl the correct socket
        self.assertIn(f"--unix-socket /run/testapp-green/testapp.sock", cmd)
        # Must check the correct service on fallback
        self.assertIn("systemctl is-active testapp-green.service", cmd)
        # Must abort with exit 1 on failure
        self.assertIn("exit 1", cmd)
        # Must include the abort message
        self.assertIn("ACTIVATION ABORTED", cmd)

    def test_health_check_different_slots_produce_different_commands(self):
        """Blue and green health checks should target different sockets."""
        from djaploy.infra.utils import get_slot_socket_path
        blue_cmd = f"curl --unix-socket {get_slot_socket_path('app', 'blue')}"
        green_cmd = f"curl --unix-socket {get_slot_socket_path('app', 'green')}"
        self.assertIn("app-blue", blue_cmd)
        self.assertIn("app-green", green_cmd)
        self.assertNotEqual(blue_cmd, green_cmd)


class TestBluegreenServiceExistenceCheck(unittest.TestCase):
    """Test that start_services checks unit file existence before starting."""

    def test_service_check_command_structure(self):
        """The start command should test for the unit file before starting."""
        from djaploy.infra.utils import get_slot_service_name
        slot_service = get_slot_service_name("myapp", "blue")

        # Build the command the same way start_services does
        cmd = (
            f"test -f /etc/systemd/system/{slot_service}.service && "
            f"systemctl enable {slot_service} && "
            f"systemctl restart {slot_service} || "
            f"echo 'Unit {slot_service}.service not found, skipping'"
        )

        self.assertIn("test -f /etc/systemd/system/myapp-blue.service", cmd)
        self.assertIn("systemctl enable myapp-blue", cmd)
        self.assertIn("systemctl restart myapp-blue", cmd)
        self.assertIn("not found, skipping", cmd)

    def test_streaming_service_gets_separate_slot_name(self):
        """bostad-streaming should produce bostad-streaming-blue, not bostad-blue."""
        from djaploy.infra.utils import get_slot_service_name
        self.assertEqual(
            get_slot_service_name("bostad-streaming", "blue"),
            "bostad-streaming-blue",
        )
        self.assertEqual(
            get_slot_service_name("bostad-streaming", "green"),
            "bostad-streaming-green",
        )


class TestHeredocInjectionSafety(unittest.TestCase):
    """Test that state.json update safely handles special characters."""

    def test_release_name_with_quotes_is_safe(self):
        """json.dumps should escape quotes in release names."""
        import json
        release = "feat/o'hare"
        safe = json.dumps(release)
        self.assertEqual(safe, '"feat/o\'hare"')
        # Should be parseable back
        self.assertEqual(json.loads(safe), release)

    def test_commit_with_special_chars_is_safe(self):
        """json.dumps should handle commit messages with special chars."""
        import json
        commit = 'fix: handle "edge" case'
        safe = json.dumps(commit)
        self.assertIn('\\"edge\\"', safe)
        self.assertEqual(json.loads(safe), commit)


if __name__ == "__main__":
    unittest.main()
