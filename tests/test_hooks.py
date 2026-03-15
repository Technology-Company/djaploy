"""Tests for the djaploy hooks system."""

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from djaploy.hooks import HookRegistry


class TestHookRegistry(unittest.TestCase):
    """Unit tests for the HookRegistry class."""

    def setUp(self):
        self.registry = HookRegistry()

    def test_register_and_call_local_hook(self):
        calls = []

        @self.registry.hook("deploy:prerequisites")
        def my_hook(ctx):
            calls.append(ctx)

        self.registry.call("deploy:prerequisites", {"env": "prod"})
        self.assertEqual(calls, [{"env": "prod"}])

    def test_call_unknown_hook_is_noop(self):
        # Should not raise
        self.registry.call("nonexistent", {})

    def test_multiple_hooks_same_name_run_in_order(self):
        order = []

        @self.registry.hook("deploy:post_actions")
        def first(ctx):
            order.append("first")

        @self.registry.hook("deploy:post_actions")
        def second(ctx):
            order.append("second")

        self.registry.call("deploy:post_actions", {})
        self.assertEqual(order, ["first", "second"])

    def test_deploy_hook_registered_as_remote(self):
        from djaploy.hooks import RemoteFunctionHook

        @self.registry.deploy_hook("deploy")
        def my_remote(host_data, artifact_path):
            pass

        # Should NOT appear in local hooks
        self.registry.call("deploy", {})  # no error, no call

        # Should appear in remote hooks
        remote = self.registry.get_remote_hooks("deploy")
        self.assertEqual(len(remote), 1)
        self.assertIsInstance(remote[0], RemoteFunctionHook)
        self.assertIs(remote[0].function, my_remote)

    def test_get_remote_hooks_empty(self):
        self.assertEqual(self.registry.get_remote_hooks("configure"), [])

    def test_clear(self):
        @self.registry.hook("deploy:prerequisites")
        def my_hook(ctx):
            pass

        @self.registry.deploy_hook("deploy")
        def my_remote(host_data, artifact_path):
            pass

        self.registry.clear()
        self.assertEqual(self.registry.get_remote_hooks("deploy"), [])
        self.assertEqual(self.registry.get_hook_names(), [])

    def test_get_hook_names(self):
        @self.registry.hook("deploy:prerequisites")
        def h1(ctx):
            pass

        @self.registry.deploy_hook("configure")
        def h2(host_data):
            pass

        names = self.registry.get_hook_names()
        self.assertIn("deploy:prerequisites", names)
        self.assertIn("configure", names)

    def test_context_mutation_flows_between_hooks(self):
        @self.registry.hook("deploy:prerequisites")
        def add_key(ctx):
            ctx["extra"] = 42

        @self.registry.hook("deploy:prerequisites")
        def read_key(ctx):
            ctx["result"] = ctx.get("extra")

        ctx = {}
        self.registry.call("deploy:prerequisites", ctx)
        self.assertEqual(ctx["result"], 42)


class TestHookOverride(unittest.TestCase):
    """Test hook override behavior: first registration wins, override controls warnings."""

    def setUp(self):
        self.registry = HookRegistry()

    def test_first_registration_wins_for_local_hooks(self):
        """A registers hook_a, B registers hook_a — A's runs, B's is ignored."""
        calls = []

        @self.registry.hook("configure")
        def my_hook(ctx):
            calls.append("A")

        @self.registry.hook("configure")
        def my_hook(ctx):  # noqa: F811 — same name on purpose
            calls.append("B")

        self.registry.call("configure", {})
        self.assertEqual(calls, ["A"])

    def test_first_registration_wins_for_remote_hooks(self):
        """A registers deploy_nginx, B registers deploy_nginx — A's is returned."""
        @self.registry.deploy_hook("deploy:configure")
        def deploy_nginx(host_data, artifact_path):
            return "A"

        @self.registry.deploy_hook("deploy:configure")
        def deploy_nginx(host_data, artifact_path):  # noqa: F811
            return "B"

        hooks = self.registry.get_remote_hooks("deploy:configure")
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0].function(None, None), "A")

    def test_duplicate_without_override_logs_warning(self):
        """Duplicate without override=True should log a warning."""
        @self.registry.hook("configure")
        def my_hook(ctx):
            pass

        with self.assertLogs("djaploy.hooks", level="WARNING") as cm:
            @self.registry.hook("configure")
            def my_hook(ctx):  # noqa: F811
                pass

        self.assertTrue(any("already registered" in msg for msg in cm.output))

    def test_duplicate_with_override_suppresses_warning(self):
        """Duplicate with override=True should not log a warning."""
        @self.registry.hook("configure")
        def my_hook(ctx):
            pass

        # Should not produce any warning log
        import logging
        logger = logging.getLogger("djaploy.hooks")
        with patch.object(logger, "warning") as mock_warn:
            @self.registry.hook("configure", override=True)
            def my_hook(ctx):  # noqa: F811
                pass

        mock_warn.assert_not_called()

    def test_a_no_override_b_override_c_override(self):
        """A(no override), B(override), C(override) — A always wins, no warnings from B and C."""
        calls = []

        @self.registry.deploy_hook("deploy:configure")
        def deploy_nginx(host_data):
            calls.append("A")

        @self.registry.deploy_hook("deploy:configure", override=True)
        def deploy_nginx(host_data):  # noqa: F811
            calls.append("B")

        @self.registry.deploy_hook("deploy:configure", override=True)
        def deploy_nginx(host_data):  # noqa: F811
            calls.append("C")

        hooks = self.registry.get_remote_hooks("deploy:configure")
        self.assertEqual(len(hooks), 1)
        hooks[0].function(None)
        self.assertEqual(calls, ["A"])

    def test_different_names_are_independent(self):
        """Hooks with different function names coexist regardless of override."""
        calls = []

        @self.registry.hook("configure")
        def hook_a(ctx):
            calls.append("A")

        @self.registry.hook("configure")
        def hook_b(ctx):
            calls.append("B")

        self.registry.call("configure", {})
        self.assertEqual(calls, ["A", "B"])


class TestDiscovery(unittest.TestCase):
    """Test hook file discovery from app infra/ directories."""

    def test_discover_loads_hooks_from_apps(self):
        import djaploy.discovery  # ensure module is loaded for patching
        registry = HookRegistry()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a fake app with djaploy_hooks.py
            infra_dir = tmpdir / "myapp" / "infra"
            infra_dir.mkdir(parents=True)

            hooks_file = infra_dir / "djaploy_hooks.py"
            hooks_file.write_text(textwrap.dedent("""\
                from djaploy.hooks import hook

                @hook("deploy:prerequisites")
                def my_app_hook(context):
                    context["myapp_ran"] = True
            """))

            mock_app = MagicMock()
            mock_app.label = "myapp"
            mock_app.path = str(tmpdir / "myapp")

            with patch("djaploy.hooks.HookRegistry._load_builtin_hooks"):
                with patch("djaploy.discovery.apps") as mock_apps:
                    mock_apps.get_app_configs.return_value = [mock_app]
                    registry.discover()

            # The hook should be registered on the global registry (because
            # the hooks file imports from djaploy.hooks which uses the global)
            from djaploy.hooks import _registry as global_registry
            try:
                hooks = global_registry._hooks.get("deploy:prerequisites", [])
                found = any(fn.__name__ == "my_app_hook" for fn in hooks)
                self.assertTrue(found, "my_app_hook should be registered on the global registry")
            finally:
                # Clean up global registry
                global_registry._hooks["deploy:prerequisites"] = [
                    fn for fn in global_registry._hooks.get("deploy:prerequisites", [])
                    if fn.__name__ != "my_app_hook"
                ]

    def test_discover_is_idempotent(self):
        registry = HookRegistry()

        with patch("djaploy.hooks.HookRegistry._load_builtin_hooks"):
            with patch("djaploy.discovery.apps") as mock_apps:
                mock_apps.get_app_configs.return_value = []
                registry.discover()
                registry.discover()  # second call should be a no-op

        self.assertTrue(registry._discovered)

    def test_discover_skips_missing_hooks_file(self):
        import djaploy.discovery  # ensure module is loaded for patching
        registry = HookRegistry()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            infra_dir = tmpdir / "app_no_hooks" / "infra"
            infra_dir.mkdir(parents=True)
            # No djaploy_hooks.py

            mock_app = MagicMock()
            mock_app.label = "app_no_hooks"
            mock_app.path = str(tmpdir / "app_no_hooks")

            with patch("djaploy.hooks.HookRegistry._load_builtin_hooks"):
                with patch("djaploy.discovery.apps") as mock_apps:
                    mock_apps.get_app_configs.return_value = [mock_app]
                    registry.discover()  # should not raise


class TestBuiltinHooks(unittest.TestCase):
    """Test that built-in hooks are registered correctly."""

    def test_builtin_hooks_register_on_import(self):
        from djaploy.hooks import _registry

        # Clear and re-import
        old_hooks = _registry._hooks.copy()
        _registry._hooks.clear()

        try:
            import importlib
            import djaploy.builtin_hooks
            importlib.reload(djaploy.builtin_hooks)

            # Check deploy:postcommand hooks (notification + tagging)
            post_hooks = _registry._hooks.get("deploy:postcommand", [])
            post_names = [fn.__name__ for fn in post_hooks]
            self.assertIn("_send_notification_hook", post_names)
            self.assertIn("_create_version_tag_hook", post_names)

            # Check deploy:precommand hooks (prepare, artifact, local_settings, release_info)
            pre_hooks = _registry._hooks.get("deploy:precommand", [])
            pre_names = [fn.__name__ for fn in pre_hooks]
            self.assertIn("_deploy_create_artifact", pre_names)

            # Check rollback:precommand hook (strategy validation)
            rollback_hooks = _registry._hooks.get("rollback:precommand", [])
            rollback_names = [fn.__name__ for fn in rollback_hooks]
            self.assertIn("_rollback_validate_strategy", rollback_names)
        finally:
            _registry._hooks = old_hooks

    @patch("djaploy.deploy._send_notification")
    @patch("djaploy.deploy._load_inventory_hosts", return_value=[])
    def test_notification_hook_calls_send_notification(self, _mock_hosts, mock_send):
        from djaploy.builtin_hooks import _send_notification_hook

        ctx = {
            "env": "production",
            "inventory_file": "/tmp/inv.py",
            "release_info": {"new_version": "v1.0.0"},
            "success": True,
            "error": None,
        }
        _send_notification_hook(ctx)
        mock_send.assert_called_once_with(
            "production", [], ctx["release_info"],
            success=True, error_message=""
        )

    @patch("djaploy.deploy._create_version_tag")
    def test_version_tag_hook_calls_create_version_tag(self, mock_tag):
        from djaploy.builtin_hooks import _create_version_tag_hook

        ctx = {
            "env": "production",
            "release_info": {"new_version": "v1.0.0"},
            "success": True,
        }
        _create_version_tag_hook(ctx)
        mock_tag.assert_called_once_with(
            "production", ctx["release_info"]
        )

    @patch("djaploy.deploy._create_version_tag")
    def test_version_tag_hook_skips_on_failure(self, mock_tag):
        from djaploy.builtin_hooks import _create_version_tag_hook

        ctx = {"env": "prod", "release_info": {}, "success": False}
        _create_version_tag_hook(ctx)
        mock_tag.assert_not_called()


class TestDjaployAppDiscovery(unittest.TestCase):
    """Test that djaploy built-in apps are discovered via INSTALLED_APPS."""

    def test_djaploy_apps_discovered_via_installed_apps(self):
        """Built-in apps are discovered when added to INSTALLED_APPS."""
        import djaploy.discovery
        from djaploy.hooks import HookRegistry
        from pathlib import Path

        registry = HookRegistry()

        # Simulate Django seeing the built-in apps in INSTALLED_APPS
        djaploy_dir = Path(__file__).resolve().parent.parent / "djaploy"
        apps_dir = djaploy_dir / "apps"
        mock_apps = []

        # "djaploy" itself provides core hooks (infra/ lives in djaploy/)
        mock_djaploy = MagicMock()
        mock_djaploy.label = "djaploy"
        mock_djaploy.path = str(djaploy_dir)
        mock_apps.append(mock_djaploy)

        # nginx and systemd are separate apps under djaploy/apps/
        for app_name in ("nginx", "systemd"):
            mock_app = MagicMock()
            mock_app.label = f"djaploy_{app_name}"
            mock_app.path = str(apps_dir / app_name)
            mock_apps.append(mock_app)

        with patch("djaploy.hooks.HookRegistry._load_builtin_hooks"):
            with patch("djaploy.discovery.apps") as mock_django_apps:
                mock_django_apps.get_app_configs.return_value = mock_apps
                registry.discover()

        from djaploy.hooks import _registry as global_registry
        saved_remote = dict(global_registry._remote_hooks)
        try:
            hook_names = global_registry.get_hook_names()
            self.assertIn("configure", hook_names)
            self.assertIn("deploy:upload", hook_names)
            self.assertIn("deploy:configure", hook_names)
            self.assertIn("deploy:pre", hook_names)
            self.assertIn("deploy:start", hook_names)
            self.assertIn("rollback", hook_names)

            # configure — server setup (from core)
            configure_hooks = global_registry.get_remote_hooks("configure")
            configure_names = [h.function.__name__ for h in configure_hooks]
            self.assertIn("configure_server", configure_names)

            # deploy:upload — upload and extract artifact (from core)
            upload_hooks = global_registry.get_remote_hooks("deploy:upload")
            upload_names = [h.function.__name__ for h in upload_hooks]
            self.assertIn("upload_artifact", upload_names)

            # deploy:configure — deps, configs, SSL, daemon-reload
            config_hooks = global_registry.get_remote_hooks("deploy:configure")
            config_names = [h.function.__name__ for h in config_hooks]
            self.assertIn("configure_application", config_names)
            self.assertIn("deploy_nginx", config_names)
            self.assertIn("reload_systemd_daemon", config_names)

            # deploy:pre — migrations, collectstatic, symlink swap
            pre_hooks = global_registry.get_remote_hooks("deploy:pre")
            pre_names = [h.function.__name__ for h in pre_hooks]
            self.assertIn("activate_release", pre_names)

            # deploy:start — reload/restart services
            start_hooks = global_registry.get_remote_hooks("deploy:start")
            start_names = [h.function.__name__ for h in start_hooks]
            self.assertIn("reload_nginx", start_names)
            self.assertIn("start_services", start_names)

            # Verify rollback hook
            rollback_hooks = global_registry.get_remote_hooks("rollback")
            rollback_names = [h.function.__name__ for h in rollback_hooks]
            self.assertIn("rollback_release", rollback_names)
        finally:
            global_registry._remote_hooks.clear()
            global_registry._remote_hooks.update(saved_remote)

    def test_apps_not_discovered_when_not_in_installed_apps(self):
        """Built-in apps are NOT loaded when absent from INSTALLED_APPS."""
        from djaploy.hooks import HookRegistry

        registry = HookRegistry()

        with patch("djaploy.hooks.HookRegistry._load_builtin_hooks"):
            with patch("djaploy.discovery.apps") as mock_django_apps:
                mock_django_apps.get_app_configs.return_value = []
                registry.discover()

        from djaploy.hooks import _registry as global_registry
        # No remote hooks should be registered from djaploy apps
        configure_hooks = global_registry.get_remote_hooks("configure")
        djaploy_fns = [h.function.__name__ for h in configure_hooks
                       if h.function.__name__ == "configure_server"]
        self.assertEqual(djaploy_fns, [])


if __name__ == "__main__":
    unittest.main()
