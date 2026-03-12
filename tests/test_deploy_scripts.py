"""Tests for deployment script generation (deploy.py)"""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from djaploy.config import DjaployConfig
from djaploy.deploy import _generate_rollback_script


class TestRollbackScriptGeneration(unittest.TestCase):
    """Test the generated pyinfra rollback scripts"""

    def _make_config(self, **kwargs):
        defaults = dict(
            project_name="myapp",
            djaploy_dir="/home/me/myapp/infra",
            deployment_strategy="zero_downtime",
            app_user="myapp-api",
        )
        defaults.update(kwargs)
        return DjaployConfig(**defaults)

    def _make_mock_modules(self):
        """Create mock modules that look like real djaploy modules"""
        core = MagicMock()
        core.name = "core"
        core.__class__.__name__ = "CoreModule"
        core.__class__.__module__ = "djaploy.modules.core"
        core.config = {}
        core.get_required_imports.return_value = [
            "from pyinfra import host",
            "from pyinfra.operations import server",
        ]

        systemd = MagicMock()
        systemd.name = "systemd"
        systemd.__class__.__name__ = "SystemdModule"
        systemd.__class__.__module__ = "djaploy.modules.systemd"
        systemd.config = {}
        systemd.get_required_imports.return_value = [
            "from pyinfra.operations import systemd",
        ]

        return [core, systemd]

    def test_rollback_to_previous_release(self):
        config = self._make_config()
        modules = self._make_mock_modules()
        script = _generate_rollback_script(config, modules, release=None)

        self.assertIn("release = None", script)
        self.assertIn("module.rollback(host.data, project_config, release)", script)

    def test_rollback_to_specific_release(self):
        config = self._make_config()
        modules = self._make_mock_modules()
        script = _generate_rollback_script(config, modules, release="app-v1.2.0")

        self.assertIn("'app-v1.2.0'", script)
        self.assertIn("module.rollback(host.data, project_config, release)", script)

    def test_rollback_script_calls_all_modules(self):
        config = self._make_config()
        modules = self._make_mock_modules()
        script = _generate_rollback_script(config, modules, release=None)

        self.assertIn("CoreModule", script)
        self.assertIn("SystemdModule", script)
        # Each module should have a rollback call
        self.assertEqual(script.count("module.rollback("), 2)

    def test_rollback_script_imports_modules(self):
        config = self._make_config()
        modules = self._make_mock_modules()
        script = _generate_rollback_script(config, modules, release=None)

        self.assertIn("from djaploy.modules.core import CoreModule", script)
        self.assertIn("from djaploy.modules.systemd import SystemdModule", script)

    def test_rollback_script_collects_imports(self):
        config = self._make_config()
        modules = self._make_mock_modules()
        script = _generate_rollback_script(config, modules, release=None)

        self.assertIn("from pyinfra import host", script)
        self.assertIn("from pyinfra.operations import server", script)

    def test_rollback_script_uses_config_paths(self):
        config = self._make_config(djaploy_dir="/opt/project/infra")
        modules = self._make_mock_modules()
        script = _generate_rollback_script(config, modules, release=None)

        self.assertIn("/opt/project/infra", script)


class TestRollbackValidation(unittest.TestCase):
    """Test rollback_project validation"""

    def test_rollback_rejects_in_place_strategy(self):
        from djaploy.deploy import rollback_project

        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            deployment_strategy="in_place",
        )

        with self.assertRaises(ValueError) as ctx:
            rollback_project(config, "/tmp/inventory.py")
        self.assertIn("zero_downtime", str(ctx.exception))


class TestDeployScriptGeneration(unittest.TestCase):
    """Test the generated pyinfra deploy scripts"""

    def _make_config(self, **kwargs):
        defaults = dict(
            project_name="myapp",
            djaploy_dir="/home/me/myapp/infra",
            app_user="myapp-api",
        )
        defaults.update(kwargs)
        return DjaployConfig(**defaults)

    def test_deploy_script_contains_artifact_path(self):
        from djaploy.deploy import _generate_deploy_script

        config = self._make_config()
        mock_module = MagicMock()
        mock_module.name = "core"
        mock_module.__class__.__name__ = "CoreModule"
        mock_module.__class__.__module__ = "djaploy.modules.core"
        mock_module.config = {}
        mock_module.get_required_imports.return_value = [
            "from pyinfra import host",
        ]

        script = _generate_deploy_script(config, [mock_module], Path("/tmp/myapp.abc1234.tar.gz"))

        self.assertIn("/tmp/myapp.abc1234.tar.gz", script)
        self.assertIn("artifact_path", script)

    def test_configure_script_has_module_calls(self):
        from djaploy.deploy import _generate_configure_script

        config = self._make_config()
        mock_module = MagicMock()
        mock_module.name = "core"
        mock_module.__class__.__name__ = "CoreModule"
        mock_module.__class__.__module__ = "djaploy.modules.core"
        mock_module.config = {}
        mock_module.get_required_imports.return_value = []

        script = _generate_configure_script(config, [mock_module])

        self.assertIn("pre_configure", script)
        self.assertIn("configure_server", script)
        self.assertIn("post_configure", script)


if __name__ == "__main__":
    unittest.main()
