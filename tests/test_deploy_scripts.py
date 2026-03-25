"""Tests for deployment orchestration (deploy.py) and command files."""

import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from djaploy.config import HostConfig
from djaploy.hooks import HookRegistry


class TestCommandFilesAreSyntacticallyValid(unittest.TestCase):
    """Verify that all command files in djaploy/commands/ parse correctly."""

    commands_dir = Path(__file__).resolve().parent.parent / "djaploy" / "commands"

    def _check_file(self, name):
        path = self.commands_dir / f"{name}.py"
        self.assertTrue(path.exists(), f"{name}.py should exist")
        source = path.read_text()
        # Should parse without SyntaxError
        ast.parse(source, filename=str(path))

    def test_configure_parses(self):
        self._check_file("configure")

    def test_deploy_parses(self):
        self._check_file("deploy")

    def test_restore_parses(self):
        self._check_file("restore")

    def test_rollback_parses(self):
        self._check_file("rollback")


class TestRollbackValidation(unittest.TestCase):
    """Test rollback:precommand hook rejects in_place strategy."""

    def _make_inventory(self, strategy):
        """Create a temp inventory file with the given deployment_strategy."""
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write(
                f"hosts = [('test-host', {{'ssh_hostname': 'localhost', "
                f"'deployment_strategy': '{strategy}', 'app_name': 'test'}})]\n"
            )
        return path

    def test_rollback_rejects_in_place_strategy(self):
        from djaploy.builtin_hooks import _rollback_validate_strategy

        inv = self._make_inventory("in_place")
        try:
            context = {"config": None, "env": "production", "inventory_file": inv}
            with self.assertRaises(ValueError) as ctx:
                _rollback_validate_strategy(context)
            self.assertIn("zero_downtime", str(ctx.exception))
        finally:
            os.unlink(inv)

    def test_rollback_allows_zero_downtime(self):
        from djaploy.builtin_hooks import _rollback_validate_strategy

        inv = self._make_inventory("zero_downtime")
        try:
            context = {"config": None, "env": "production", "inventory_file": inv}
            # Should not raise
            _rollback_validate_strategy(context)
        finally:
            os.unlink(inv)

    def test_rollback_allows_zero_downtime(self):
        from djaploy.builtin_hooks import _rollback_validate_strategy

class TestLocalSettingsHook(unittest.TestCase):
    """Test the deploy:local_settings hook collection."""

    def test_collect_local_settings_via_hook(self):
        registry = HookRegistry()

        @registry.hook("deploy:local_settings")
        def add_redis(context):
            return 'REDIS_URL = "redis://localhost:6379/0"'

        @registry.hook("deploy:local_settings")
        def add_celery(context):
            return "CELERY_BROKER_URL = REDIS_URL"

        ctx = {"env": "production", "config": None}
        results = registry.call("deploy:local_settings", ctx)

        self.assertEqual(len(results), 2)
        self.assertIn('REDIS_URL = "redis://localhost:6379/0"', results)
        self.assertIn("CELERY_BROKER_URL = REDIS_URL", results)


class TestGetCommandFile(unittest.TestCase):
    """Test the _get_command_file helper."""

    def test_returns_path_for_known_commands(self):
        from djaploy.deploy import _get_command_file

        for name in ("deploy", "configure", "restore", "rollback"):
            path = _get_command_file(name)
            self.assertTrue(path.exists(), f"Command file for '{name}' should exist")
            self.assertEqual(path.suffix, ".py")

    def test_returns_path_for_unknown_command(self):
        from djaploy.deploy import _get_command_file

        path = _get_command_file("nonexistent")
        self.assertFalse(path.exists())


class TestRunCommandBuildsContext(unittest.TestCase):
    """Test that Python API wrappers build context correctly."""

    @patch("djaploy.deploy._build_pyinfra_data", return_value={"env": "inventory"})
    def test_deploy_project_builds_context_with_all_fields(self, _mock_data):
        captured_context = {}

        def mock_run_command(ctx):
            captured_context.update(ctx)

        with patch("djaploy.deploy.run_command", side_effect=mock_run_command):
            from djaploy.deploy import deploy_project
            deploy_project(
                "/tmp/inventory.py",
                mode="release",
                release_tag="v1.0.0",
                skip_prepare=True,
                version_bump="minor",
            )

        self.assertEqual(captured_context["command"], "deploy")
        self.assertEqual(captured_context["mode"], "release")
        self.assertEqual(captured_context["release"], "v1.0.0")
        self.assertEqual(captured_context["version_bump"], "minor")
        self.assertTrue(captured_context["skip_prepare"])

    @patch("djaploy.deploy._build_pyinfra_data", return_value={"env": "inventory"})
    def test_rollback_project_builds_context(self, _mock_data):
        captured_context = {}

        def mock_run_command(ctx):
            captured_context.update(ctx)

        with patch("djaploy.deploy.run_command", side_effect=mock_run_command):
            from djaploy.deploy import rollback_project
            rollback_project("/tmp/inventory.py", release="app-v1.2.0")

        self.assertEqual(captured_context["command"], "rollback")
        self.assertEqual(captured_context["release"], "app-v1.2.0")
        self.assertEqual(captured_context["pyinfra_data"]["release"], "app-v1.2.0")


class TestLifecycleHookOrder(unittest.TestCase):
    """Test the 4-hook lifecycle: {cmd}:precommand, precommand, {cmd}:postcommand, postcommand."""

    def _run_with_registry(self, registry, context, pyinfra_side_effect=None):
        """Helper: run_command with a custom registry and mocked pyinfra."""
        with patch("djaploy.hooks.discover_hooks"), \
             patch("djaploy.hooks.call_hook", side_effect=registry.call), \
             patch("djaploy.deploy._preprocess_inventory", return_value="/dev/null"), \
             patch("djaploy.deploy._run_pyinfra", side_effect=pyinfra_side_effect):
            from djaploy.deploy import run_command
            run_command(context)

    def test_success_lifecycle(self):
        registry = HookRegistry()
        order = []

        @registry.hook("mytest:precommand")
        def h1(ctx): order.append("mytest:precommand")

        @registry.hook("precommand")
        def h2(ctx): order.append("precommand")

        @registry.hook("mytest:postcommand")
        def h3(ctx): order.append("mytest:postcommand")

        @registry.hook("postcommand")
        def h4(ctx): order.append("postcommand")

        context = {
            "command": "mytest",
            "config": MagicMock(),
            "env": "test",
            "command_file": "/dev/null",
            "inventory_file": "/dev/null",
            "pyinfra_data": {},
        }

        self._run_with_registry(registry, context)

        self.assertEqual(order, [
            "mytest:precommand",
            "precommand",
            "mytest:postcommand",
            "postcommand",
        ])
        self.assertTrue(context["success"])

    def test_failure_lifecycle(self):
        registry = HookRegistry()
        order = []

        @registry.hook("boom:precommand")
        def h1(ctx): order.append("boom:precommand")

        @registry.hook("precommand")
        def h2(ctx): order.append("precommand")

        @registry.hook("boom:postcommand")
        def h3(ctx): order.append("boom:postcommand")

        @registry.hook("postcommand")
        def h4(ctx): order.append("postcommand")

        context = {
            "command": "boom",
            "config": MagicMock(),
            "env": "test",
            "command_file": "/dev/null",
            "inventory_file": "/dev/null",
            "pyinfra_data": {},
        }

        with self.assertRaises(RuntimeError):
            self._run_with_registry(
                registry, context,
                pyinfra_side_effect=RuntimeError("kaboom"),
            )

        # postcommand hooks still fire on failure
        self.assertEqual(order, [
            "boom:precommand",
            "precommand",
            "boom:postcommand",
            "postcommand",
        ])
        self.assertFalse(context["success"])
        self.assertIsInstance(context["error"], RuntimeError)


if __name__ == "__main__":
    unittest.main()
