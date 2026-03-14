"""Tests for djaploy app-based infra discovery"""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from djaploy.discovery import (
    get_app_infra_dirs,
    find_command,
    find_inventory,
    find_config,
    get_available_commands,
)


def _make_app_config(label, path):
    """Create a mock AppConfig."""
    app = MagicMock()
    app.label = label
    app.path = str(path)
    return app


class TestGetAppInfraDirs(unittest.TestCase):
    """Test get_app_infra_dirs discovery."""

    @patch("djaploy.discovery.apps")
    def test_returns_apps_with_infra_dir(self, mock_apps, tmp_path=None):
        """Only apps that have an infra/ directory are returned."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            app1_dir = tmpdir / "app1"
            app2_dir = tmpdir / "app2"
            app3_dir = tmpdir / "app3"

            # app1 has infra/
            (app1_dir / "infra").mkdir(parents=True)
            # app2 does not
            app2_dir.mkdir(parents=True)
            # app3 has infra/
            (app3_dir / "infra").mkdir(parents=True)

            mock_apps.get_app_configs.return_value = [
                _make_app_config("app1", app1_dir),
                _make_app_config("app2", app2_dir),
                _make_app_config("app3", app3_dir),
            ]

            result = get_app_infra_dirs()
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0][0], "app1")
            self.assertEqual(result[1][0], "app3")

    @patch("djaploy.discovery.apps")
    def test_empty_when_no_infra_dirs(self, mock_apps):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            app_dir = Path(tmpdir) / "myapp"
            app_dir.mkdir()

            mock_apps.get_app_configs.return_value = [
                _make_app_config("myapp", app_dir),
            ]

            result = get_app_infra_dirs()
            self.assertEqual(result, [])


class TestFindCommand(unittest.TestCase):
    """Test find_command discovery."""

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_finds_command_in_first_app(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            infra1 = tmpdir / "app1_infra"
            infra2 = tmpdir / "app2_infra"

            # Both apps have a deploy command
            (infra1 / "commands").mkdir(parents=True)
            (infra1 / "commands" / "deploy.py").write_text("# app1 deploy")
            (infra2 / "commands").mkdir(parents=True)
            (infra2 / "commands" / "deploy.py").write_text("# app2 deploy")

            mock_dirs.return_value = [
                ("app1", infra1),
                ("app2", infra2),
            ]

            result = find_command("deploy")
            self.assertIsNotNone(result)
            self.assertEqual(result.parent.parent, infra1)

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_finds_command_in_second_app(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            infra1 = tmpdir / "app1_infra"
            infra2 = tmpdir / "app2_infra"

            # Only app2 has the command
            (infra1 / "commands").mkdir(parents=True)
            (infra2 / "commands").mkdir(parents=True)
            (infra2 / "commands" / "setup.py").write_text("# setup")

            mock_dirs.return_value = [
                ("app1", infra1),
                ("app2", infra2),
            ]

            result = find_command("setup")
            self.assertIsNotNone(result)
            self.assertEqual(result.parent.parent, infra2)

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_returns_none_when_not_found(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            infra = Path(tmpdir) / "infra"
            (infra / "commands").mkdir(parents=True)

            mock_dirs.return_value = [("app1", infra)]

            result = find_command("nonexistent")
            self.assertIsNone(result)

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_returns_none_when_no_apps(self, mock_dirs):
        mock_dirs.return_value = []
        result = find_command("deploy")
        self.assertIsNone(result)


class TestFindInventory(unittest.TestCase):
    """Test find_inventory discovery."""

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_finds_inventory_first_match(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            infra1 = tmpdir / "app1_infra"
            infra2 = tmpdir / "app2_infra"

            (infra1 / "inventory").mkdir(parents=True)
            (infra1 / "inventory" / "production.py").write_text("hosts = []")
            (infra2 / "inventory").mkdir(parents=True)
            (infra2 / "inventory" / "production.py").write_text("hosts = []")

            mock_dirs.return_value = [
                ("app1", infra1),
                ("app2", infra2),
            ]

            result = find_inventory("production")
            self.assertIsNotNone(result)
            self.assertEqual(result.parent.parent, infra1)

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_returns_none_when_not_found(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            infra = Path(tmpdir) / "infra"
            (infra / "inventory").mkdir(parents=True)

            mock_dirs.return_value = [("app1", infra)]

            result = find_inventory("production")
            self.assertIsNone(result)


class TestFindConfig(unittest.TestCase):
    """Test find_config discovery."""

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_finds_config_first_match(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            infra1 = tmpdir / "app1_infra"
            infra2 = tmpdir / "app2_infra"

            infra1.mkdir(parents=True)
            (infra1 / "config.py").write_text("config = None")
            infra2.mkdir(parents=True)
            (infra2 / "config.py").write_text("config = None")

            mock_dirs.return_value = [
                ("app1", infra1),
                ("app2", infra2),
            ]

            result = find_config()
            self.assertIsNotNone(result)
            self.assertEqual(result.parent, infra1)

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_returns_none_when_no_config(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            infra = Path(tmpdir) / "infra"
            infra.mkdir(parents=True)

            mock_dirs.return_value = [("app1", infra)]

            result = find_config()
            self.assertIsNone(result)


class TestGetAvailableCommands(unittest.TestCase):
    """Test get_available_commands discovery."""

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_lists_commands_first_wins(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            infra1 = tmpdir / "app1_infra"
            infra2 = tmpdir / "app2_infra"

            (infra1 / "commands").mkdir(parents=True)
            (infra1 / "commands" / "deploy.py").write_text("")
            (infra1 / "commands" / "setup.py").write_text("")

            (infra2 / "commands").mkdir(parents=True)
            (infra2 / "commands" / "deploy.py").write_text("")  # duplicate
            (infra2 / "commands" / "backup.py").write_text("")

            mock_dirs.return_value = [
                ("app1", infra1),
                ("app2", infra2),
            ]

            result = get_available_commands()
            names = {name for name, _ in result}
            self.assertEqual(names, {"deploy", "setup", "backup"})

            # deploy should come from app1 (first wins)
            deploy_app = next(app for name, app in result if name == "deploy")
            self.assertEqual(deploy_app, "app1")

            # backup should come from app2
            backup_app = next(app for name, app in result if name == "backup")
            self.assertEqual(backup_app, "app2")

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_skips_underscore_files(self, mock_dirs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            infra = Path(tmpdir) / "infra"
            (infra / "commands").mkdir(parents=True)
            (infra / "commands" / "__init__.py").write_text("")
            (infra / "commands" / "_helper.py").write_text("")
            (infra / "commands" / "deploy.py").write_text("")

            mock_dirs.return_value = [("app1", infra)]

            result = get_available_commands()
            names = [name for name, _ in result]
            self.assertEqual(names, ["deploy"])

    @patch("djaploy.discovery.get_app_infra_dirs")
    def test_empty_when_no_commands(self, mock_dirs):
        mock_dirs.return_value = []
        result = get_available_commands()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
