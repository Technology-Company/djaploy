"""End-to-end tests for the djaploy deployment pipeline.

These tests wire together config, artifact creation, hooks, and the lifecycle
to verify the full pipeline works — with pyinfra execution mocked at the
boundary (since we don't have real servers).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

from djaploy.config import DjaployConfig, HostConfig
from djaploy.hooks import HookRegistry
from djaploy.deploy import run_command, _get_command_file, _make_value_serializable


def _git(repo_dir, *args):
    """Run git command with signing disabled."""
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo_dir, check=True, capture_output=True,
    )


def _init_test_repo(repo_dir: Path):
    """Init a git repo with signing disabled."""
    _git(repo_dir, "init")
    _git(repo_dir, "config", "user.email", "test@test.com")
    _git(repo_dir, "config", "user.name", "Test")
    _git(repo_dir, "config", "commit.gpgsign", "false")


class TestMakeValueSerializable(unittest.TestCase):
    """Test the inventory serialization helper."""

    def test_primitive_passthrough(self):
        self.assertEqual(_make_value_serializable(42), 42)
        self.assertEqual(_make_value_serializable("hello"), "hello")
        self.assertTrue(_make_value_serializable(True))
        self.assertIsNone(_make_value_serializable(None))

    def test_path_converted_to_str(self):
        self.assertEqual(_make_value_serializable(Path("/tmp/foo")), "/tmp/foo")

    def test_list_recursion(self):
        result = _make_value_serializable([Path("/a"), 1, "b"])
        self.assertEqual(result, ["/a", 1, "b"])

    def test_dict_recursion(self):
        result = _make_value_serializable({"path": Path("/x"), "count": 5})
        self.assertEqual(result, {"path": "/x", "count": 5})

    def test_dataclass_serialized(self):
        from djaploy.config import BackupConfig
        bc = BackupConfig(host="backup.host", user="admin")
        result = _make_value_serializable(bc)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["host"], "backup.host")
        self.assertEqual(result["user"], "admin")
        self.assertEqual(result["__class__"], "BackupConfig")


class TestFullDeployLifecycle(unittest.TestCase):
    """Test the full deploy lifecycle with hooks and mocked pyinfra."""

    def _make_config(self, tmpdir):
        return DjaployConfig(
            project_name="testapp",
            git_dir=Path(tmpdir),
            djaploy_dir=Path(tmpdir) / "infra",
        )

    def test_deploy_lifecycle_calls_hooks_in_order(self):
        """Verify the 4-phase lifecycle fires hooks correctly for a deploy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._make_config(tmpdir)
            registry = HookRegistry()
            order = []

            @registry.hook("deploy:precommand")
            def pre(ctx):
                order.append("deploy:precommand")

            @registry.hook("precommand")
            def generic_pre(ctx):
                order.append("precommand")

            @registry.hook("deploy:postcommand")
            def post(ctx):
                order.append("deploy:postcommand")

            @registry.hook("postcommand")
            def generic_post(ctx):
                order.append("postcommand")

            context = {
                "command": "deploy",
                "config": config,
                "env": "production",
                "command_file": "/dev/null",
                "inventory_file": "/dev/null",
                "pyinfra_data": {},
            }

            with patch("djaploy.hooks.discover_hooks"), \
                 patch("djaploy.hooks.call_hook", side_effect=registry.call), \
                 patch("djaploy.deploy._preprocess_inventory", return_value="/dev/null"), \
                 patch("djaploy.deploy._run_pyinfra"):
                run_command(context)

            self.assertEqual(order, [
                "deploy:precommand",
                "precommand",
                "deploy:postcommand",
                "postcommand",
            ])
            self.assertTrue(context["success"])

    def test_deploy_lifecycle_sets_error_on_failure(self):
        """On pyinfra failure, postcommand hooks still fire and error is set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._make_config(tmpdir)
            registry = HookRegistry()
            post_called = []

            @registry.hook("deploy:postcommand")
            def post(ctx):
                post_called.append(ctx.get("success"))

            @registry.hook("postcommand")
            def generic_post(ctx):
                post_called.append("postcommand")

            context = {
                "command": "deploy",
                "config": config,
                "env": "staging",
                "command_file": "/dev/null",
                "inventory_file": "/dev/null",
                "pyinfra_data": {},
            }

            with patch("djaploy.hooks.discover_hooks"), \
                 patch("djaploy.hooks.call_hook", side_effect=registry.call), \
                 patch("djaploy.deploy._preprocess_inventory", return_value="/dev/null"), \
                 patch("djaploy.deploy._run_pyinfra", side_effect=RuntimeError("SSH failed")):
                with self.assertRaises(RuntimeError):
                    run_command(context)

            self.assertFalse(context["success"])
            self.assertIsInstance(context["error"], RuntimeError)
            # Postcommand hooks DID fire
            self.assertEqual(post_called, [False, "postcommand"])


class TestDeployWithArtifactCreation(unittest.TestCase):
    """Integration: config + artifact + deploy lifecycle."""

    def test_artifact_created_and_available_in_context(self):
        """Simulate a deploy that creates an artifact and passes it through hooks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            # Init git repo
            _init_test_repo(repo)
            (repo / "manage.py").write_text("# manage")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "init")

            config = DjaployConfig(
                project_name="testapp",
                git_dir=repo,
                djaploy_dir=repo / "infra",
            )

            from djaploy.artifact import create_artifact
            artifact_path = create_artifact(config, mode="latest")

            # Simulate what builtin_hooks does: create artifact, add to context
            registry = HookRegistry()
            artifact_seen = []

            @registry.hook("deploy:precommand")
            def create_art(ctx):
                ctx["artifact_path"] = str(artifact_path)

            @registry.hook("precommand")
            def check_art(ctx):
                artifact_seen.append(ctx.get("artifact_path"))

            context = {
                "command": "deploy",
                "config": config,
                "env": "production",
                "command_file": "/dev/null",
                "inventory_file": "/dev/null",
                "pyinfra_data": {},
            }

            with patch("djaploy.hooks.discover_hooks"), \
                 patch("djaploy.hooks.call_hook", side_effect=registry.call), \
                 patch("djaploy.deploy._preprocess_inventory", return_value="/dev/null"), \
                 patch("djaploy.deploy._run_pyinfra"):
                run_command(context)

            self.assertEqual(len(artifact_seen), 1)
            self.assertEqual(artifact_seen[0], str(artifact_path))
            self.assertTrue(Path(artifact_seen[0]).exists())


class TestHostConfigInInventory(unittest.TestCase):
    """Test that HostConfig objects work with inventory preprocessing."""

    def test_host_config_serializable_for_inventory(self):
        """HostConfig tuples can be serialized via _make_value_serializable."""
        host = HostConfig(
            "web-1",
            ssh_hostname="10.0.0.1",
            ssh_user="deploy",
            app_user="app",
            env="production",
            services=["web", "worker"],
        )
        name, data = host
        serialized = _make_value_serializable(data)

        self.assertIsInstance(serialized, dict)
        self.assertEqual(serialized["ssh_hostname"], "10.0.0.1")
        self.assertEqual(serialized["services"], ["web", "worker"])

    def test_host_config_with_backup_serializable(self):
        from djaploy.config import BackupConfig
        backup = BackupConfig(host="backup.host", user="backup")
        host = HostConfig("web-1", ssh_hostname="10.0.0.1", backup=backup)
        _, data = host
        serialized = _make_value_serializable(data)

        self.assertIsInstance(serialized["backup"], dict)
        self.assertEqual(serialized["backup"]["host"], "backup.host")


class TestReleaseInfoCalculation(unittest.TestCase):
    """Test _get_release_info builds correct release metadata."""

    def test_returns_none_without_webhook_url(self):
        from djaploy.deploy import _get_release_info

        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            module_configs={},
        )
        result = _get_release_info(config, "production")
        self.assertIsNone(result)

    def test_returns_info_with_webhook_configured(self):
        from djaploy.deploy import _get_release_info

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_test_repo(repo)
            (repo / "f.txt").write_text("x")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "init")

            config = DjaployConfig(
                project_name="test",
                git_dir=repo,
                djaploy_dir=repo / "infra",
                module_configs={
                    "notifications": {
                        "backend_config": {"webhook_url": "https://hooks.slack.com/test"},
                        "display_name": "TestApp",
                    },
                    "versioning": {"tag_environments": ["production"]},
                },
            )

            result = _get_release_info(config, "production")
            self.assertIsNotNone(result)
            self.assertEqual(result["new_version"], "v1.0.0")
            self.assertEqual(result["display_name"], "TestApp")
            self.assertTrue(result["should_tag"])
            self.assertTrue(result["should_notify"])


if __name__ == "__main__":
    unittest.main()
