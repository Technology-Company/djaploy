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
        def my_remote(host_data, project_config, artifact_path):
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
        def my_remote(host_data, project_config, artifact_path):
            pass

        self.registry.clear()
        self.assertEqual(self.registry.get_remote_hooks("deploy"), [])
        self.assertEqual(self.registry.get_hook_names(), [])

    def test_get_hook_names(self):
        @self.registry.hook("deploy:prerequisites")
        def h1(ctx):
            pass

        @self.registry.deploy_hook("configure")
        def h2(host_data, project_config):
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
            hooks = global_registry._hooks.get("deploy:prerequisites", [])
            found = any(fn.__name__ == "my_app_hook" for fn in hooks)
            self.assertTrue(found, "my_app_hook should be registered on the global registry")

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

        # Restore
        _registry._hooks = old_hooks

    @patch("djaploy.deploy._send_notification")
    def test_notification_hook_calls_send_notification(self, mock_send):
        from djaploy.builtin_hooks import _send_notification_hook

        ctx = {
            "config": MagicMock(),
            "env": "production",
            "release_info": {"new_version": "v1.0.0"},
            "success": True,
            "error": None,
        }
        _send_notification_hook(ctx)
        mock_send.assert_called_once_with(
            ctx["config"], "production", ctx["release_info"],
            success=True, error_message=""
        )

    @patch("djaploy.deploy._create_version_tag")
    def test_version_tag_hook_calls_create_version_tag(self, mock_tag):
        from djaploy.builtin_hooks import _create_version_tag_hook

        ctx = {
            "config": MagicMock(),
            "env": "production",
            "release_info": {"new_version": "v1.0.0"},
            "success": True,
        }
        _create_version_tag_hook(ctx)
        mock_tag.assert_called_once_with(
            ctx["config"], "production", ctx["release_info"]
        )

    @patch("djaploy.deploy._create_version_tag")
    def test_version_tag_hook_skips_on_failure(self, mock_tag):
        from djaploy.builtin_hooks import _create_version_tag_hook

        ctx = {"config": MagicMock(), "env": "prod", "release_info": {}, "success": False}
        _create_version_tag_hook(ctx)
        mock_tag.assert_not_called()


class TestDjaployAppDiscovery(unittest.TestCase):
    """Test that djaploy built-in apps are discovered."""

    def test_load_djaploy_apps_finds_core_nginx_and_systemd(self):
        from djaploy.hooks import _registry

        # Save and clear remote hooks, then discover apps
        saved = dict(_registry._remote_hooks)
        _registry._remote_hooks.clear()
        try:
            _registry._load_djaploy_apps()

            hook_names = _registry.get_hook_names()
            self.assertIn("configure", hook_names)
            self.assertIn("deploy", hook_names)
            self.assertIn("deploy:post", hook_names)
            self.assertIn("rollback", hook_names)

            # Verify core app hooks are discovered
            configure_hooks = _registry.get_remote_hooks("configure")
            configure_names = [h.function.__name__ for h in configure_hooks]
            self.assertIn("configure_server", configure_names)

            deploy_hooks = _registry.get_remote_hooks("deploy")
            deploy_names = [h.function.__name__ for h in deploy_hooks]
            # Core hooks (alphabetically first)
            self.assertIn("deploy_application", deploy_names)
            # Nginx and systemd hooks
            self.assertIn("deploy_nginx", deploy_names)
            self.assertIn("reload_systemd_daemon", deploy_names)

            # Verify ordering: core < nginx < systemd
            core_idx = deploy_names.index("deploy_application")
            nginx_idx = deploy_names.index("deploy_nginx")
            systemd_idx = deploy_names.index("reload_systemd_daemon")
            self.assertLess(core_idx, nginx_idx)
            self.assertLess(nginx_idx, systemd_idx)

            # Verify rollback hook
            rollback_hooks = _registry.get_remote_hooks("rollback")
            rollback_names = [h.function.__name__ for h in rollback_hooks]
            self.assertIn("rollback_release", rollback_names)
        finally:
            _registry._remote_hooks.clear()
            _registry._remote_hooks.update(saved)


if __name__ == "__main__":
    unittest.main()
