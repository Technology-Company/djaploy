"""Tests for the core module zero-downtime deployment logic.

These tests mock pyinfra and Django to test the core module's decision logic
without needing actual remote servers.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call


# Mock pyinfra and django before importing core module
_pyinfra_mock = MagicMock()
_django_mock = MagicMock()
sys.modules.setdefault("pyinfra", _pyinfra_mock)
sys.modules.setdefault("pyinfra.operations", MagicMock())
sys.modules.setdefault("pyinfra.operations.apt", MagicMock())
sys.modules.setdefault("pyinfra.operations.server", MagicMock())
sys.modules.setdefault("pyinfra.operations.pip", MagicMock())
sys.modules.setdefault("pyinfra.operations.files", MagicMock())
sys.modules.setdefault("pyinfra.facts", MagicMock())
sys.modules.setdefault("pyinfra.facts.server", MagicMock())
sys.modules.setdefault("django", _django_mock)
sys.modules.setdefault("django.conf", MagicMock())

from pyinfra.operations import server, files

from djaploy.modules.core import CoreModule
from djaploy.config import DjaployConfig


def _make_host_data(**kwargs):
    defaults = dict(
        ssh_hostname="192.168.1.100",
        ssh_user="deploy",
        app_user="myapp",
        env="production",
        services=["myapp_api"],
        pregenerate_certificates=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_config(**kwargs):
    defaults = dict(
        project_name="myapp",
        djaploy_dir="/home/me/myapp/infra",
        project_dir="/home/me/myapp/djangoroot",
        app_user="myapp",
        python_version="3.11",
        manage_py_path="djangoroot/manage.py",
    )
    defaults.update(kwargs)
    return DjaployConfig(**defaults)


class TestCoreModuleStrategyDispatch(unittest.TestCase):
    """Test that deploy() dispatches to the right strategy"""

    def setUp(self):
        self.module = CoreModule()

    def test_is_zero_downtime_false_by_default(self):
        config = _make_config()
        self.assertFalse(self.module._is_zero_downtime(config))

    def test_is_zero_downtime_true(self):
        config = _make_config(deployment_strategy="zero_downtime")
        self.assertTrue(self.module._is_zero_downtime(config))

    @patch.object(CoreModule, '_deploy_in_place')
    @patch.object(CoreModule, '_deploy_zero_downtime')
    def test_deploy_dispatches_in_place(self, mock_zd, mock_ip):
        config = _make_config(deployment_strategy="in_place")
        host_data = _make_host_data()
        self.module.deploy(host_data, config, Path("/tmp/artifact.tar.gz"))
        mock_ip.assert_called_once()
        mock_zd.assert_not_called()

    @patch.object(CoreModule, '_deploy_in_place')
    @patch.object(CoreModule, '_deploy_zero_downtime')
    def test_deploy_dispatches_zero_downtime(self, mock_zd, mock_ip):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data()
        self.module.deploy(host_data, config, Path("/tmp/artifact.tar.gz"))
        mock_zd.assert_called_once()
        mock_ip.assert_not_called()


class TestReleaseNameParsing(unittest.TestCase):
    """Test that release names are correctly derived from artifact filenames"""

    def _parse_release_name(self, artifact_filename):
        """Replicate the parsing logic from _deploy_zero_downtime"""
        parts = artifact_filename.rsplit('.tar.gz', 1)[0]
        ref = parts.split('.', 1)[1] if '.' in parts else parts
        return f"app-{ref}"

    def test_latest_commit_hash(self):
        self.assertEqual(
            self._parse_release_name("myapp.abc1234.tar.gz"),
            "app-abc1234",
        )

    def test_release_tag(self):
        self.assertEqual(
            self._parse_release_name("myapp.v1.2.0.tar.gz"),
            "app-v1.2.0",
        )

    def test_local_deploy(self):
        self.assertEqual(
            self._parse_release_name("myapp.local.tar.gz"),
            "app-local",
        )

    def test_hyphenated_project_name(self):
        self.assertEqual(
            self._parse_release_name("my-app.abc1234.tar.gz"),
            "app-abc1234",
        )

    def test_tag_with_rc_suffix(self):
        self.assertEqual(
            self._parse_release_name("myapp.v2.0.0-rc1.tar.gz"),
            "app-v2.0.0-rc1",
        )


class TestAppPath(unittest.TestCase):
    """Test _get_app_path with host-level and config-level overrides"""

    def setUp(self):
        self.module = CoreModule()

    def test_default_path(self):
        config = _make_config(app_user="myapp")
        host_data = _make_host_data(app_user=None)
        path = self.module._get_app_path(host_data, config)
        self.assertEqual(path, "/home/myapp/apps/myapp")

    def test_host_level_app_user(self):
        config = _make_config(app_user="default")
        host_data = _make_host_data(app_user="custom")
        path = self.module._get_app_path(host_data, config)
        self.assertEqual(path, "/home/custom/apps/myapp")

    def test_host_level_project_name(self):
        config = _make_config(project_name="myapp")
        host_data = _make_host_data(project_name="override")
        path = self.module._get_app_path(host_data, config)
        self.assertEqual(path, "/home/myapp/apps/override")


class TestZeroDowntimeDeployFlow(unittest.TestCase):
    """Test that _deploy_zero_downtime calls pyinfra operations in the right order"""

    def setUp(self):
        self.module = CoreModule()
        # Reset mocks
        server.shell.reset_mock()
        files.directory.reset_mock()
        files.put.reset_mock()

    @patch.object(CoreModule, '_collect_static')
    @patch.object(CoreModule, '_run_migrations')
    @patch.object(CoreModule, '_install_dependencies')
    @patch.object(CoreModule, '_deploy_config_files')
    def test_zero_downtime_flow_order(self, mock_config_files, mock_deps, mock_migrate, mock_static):
        config = _make_config(
            deployment_strategy="zero_downtime",
            shared_resources=["media"],
        )
        host_data = _make_host_data()
        artifact = Path("/tmp/myapp.abc1234.tar.gz")

        self.module._deploy_zero_downtime(host_data, config, artifact)

        # Verify shared resource symlinks were created
        symlink_calls = [c for c in server.shell.call_args_list
                         if "Symlink shared resources" in str(c)]
        self.assertTrue(len(symlink_calls) > 0, "Should create shared resource symlinks")

        # Verify deps are installed via the stable build/ symlink so Poetry
        # reuses the same virtualenv across releases
        mock_deps.assert_called_once()
        install_path = mock_deps.call_args[0][1]  # second positional arg is app_path
        self.assertIn("/build", install_path,
                      "Dependencies should be installed via the build/ symlink path")

        # Verify build symlink is created pointing to the release
        build_calls = [c for c in server.shell.call_args_list
                       if "stable build symlink" in str(c)]
        self.assertTrue(len(build_calls) > 0, "Should create stable build symlink for Poetry")

        # Verify cleanup runs
        cleanup_calls = [c for c in server.shell.call_args_list
                         if "Clean up old releases" in str(c)]
        self.assertTrue(len(cleanup_calls) > 0, "Should clean up old releases")

        # Verify gunicorn ExecStart is patched with --chdir so USR2 reload
        # re-resolves the current/ symlink to the new release
        chdir_calls = [c for c in server.shell.call_args_list
                       if "Patch gunicorn ExecStart" in str(c)]
        self.assertTrue(len(chdir_calls) > 0,
                        "Should patch gunicorn ExecStart with --chdir")

    @patch.object(CoreModule, '_collect_static')
    @patch.object(CoreModule, '_run_migrations')
    @patch.object(CoreModule, '_install_dependencies')
    @patch.object(CoreModule, '_deploy_config_files')
    def test_no_symlinks_when_no_shared_resources(self, mock_config_files, mock_deps, mock_migrate, mock_static):
        config = _make_config(
            deployment_strategy="zero_downtime",
            shared_resources=[],
        )
        host_data = _make_host_data()
        artifact = Path("/tmp/myapp.abc1234.tar.gz")

        server.shell.reset_mock()
        self.module._deploy_zero_downtime(host_data, config, artifact)

        symlink_calls = [c for c in server.shell.call_args_list
                         if "Symlink shared resources" in str(c)]
        self.assertEqual(len(symlink_calls), 0, "Should not create symlinks when shared_resources is empty")


class TestConfigureServerZeroDowntime(unittest.TestCase):
    """Test configure_server creates the right directory structure"""

    def setUp(self):
        self.module = CoreModule()
        files.directory.reset_mock()

    @patch.object(CoreModule, '_configure_http_challenge_sudo')
    @patch.object(CoreModule, '_install_python')
    def test_creates_releases_and_shared_dirs(self, mock_python, mock_http):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data()

        self.module.configure_server(host_data, config)

        dir_calls = [str(c) for c in files.directory.call_args_list]
        dir_paths = " ".join(dir_calls)
        self.assertIn("releases", dir_paths)
        self.assertIn("shared", dir_paths)

    @patch.object(CoreModule, '_configure_http_challenge_sudo')
    @patch.object(CoreModule, '_install_python')
    def test_in_place_does_not_create_release_dirs(self, mock_python, mock_http):
        config = _make_config(deployment_strategy="in_place")
        host_data = _make_host_data()

        files.directory.reset_mock()
        self.module.configure_server(host_data, config)

        dir_calls = [str(c) for c in files.directory.call_args_list]
        dir_paths = " ".join(dir_calls)
        self.assertNotIn("releases", dir_paths)
        self.assertNotIn("shared", dir_paths)


class TestCoreModuleRollback(unittest.TestCase):
    """Test CoreModule.rollback()"""

    def setUp(self):
        self.module = CoreModule()
        server.shell.reset_mock()

    def test_rollback_to_previous_release(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data()

        self.module.rollback(host_data, config, release=None)

        server.shell.assert_called_once()
        call_str = str(server.shell.call_args)
        self.assertIn("ls -1t", call_str)
        self.assertIn("ln -sfn", call_str)
        self.assertIn("Roll back to previous release", call_str)

    def test_rollback_to_specific_release(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data()

        self.module.rollback(host_data, config, release="app-v1.2.0")

        server.shell.assert_called_once()
        call_str = str(server.shell.call_args)
        self.assertIn("app-v1.2.0", call_str)
        self.assertIn("test -d", call_str)
        self.assertIn("ln -sfn", call_str)

    def test_rollback_uses_correct_app_path(self):
        config = _make_config(deployment_strategy="zero_downtime", app_user="custom")
        host_data = _make_host_data(app_user=None)

        self.module.rollback(host_data, config, release="app-v1.0.0")

        call_str = str(server.shell.call_args)
        self.assertIn("/home/custom/apps/myapp", call_str)

    def test_rollback_runs_as_app_user(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data(app_user="myapp")

        self.module.rollback(host_data, config, release=None)

        kwargs = server.shell.call_args.kwargs
        self.assertEqual(kwargs["_sudo_user"], "myapp")
        self.assertTrue(kwargs["_sudo"])


if __name__ == "__main__":
    unittest.main()
