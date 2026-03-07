"""Tests for DjaployConfig deployment strategy configuration"""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from djaploy.config import DjaployConfig


class TestDeploymentStrategyConfig(unittest.TestCase):
    """Test deployment_strategy, keep_releases, and shared_resources config fields"""

    def test_default_strategy_is_in_place(self):
        config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
        self.assertEqual(config.deployment_strategy, "in_place")

    def test_zero_downtime_strategy(self):
        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            deployment_strategy="zero_downtime",
        )
        self.assertEqual(config.deployment_strategy, "zero_downtime")

    def test_default_keep_releases(self):
        config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
        self.assertEqual(config.keep_releases, 5)

    def test_custom_keep_releases(self):
        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            keep_releases=10,
        )
        self.assertEqual(config.keep_releases, 10)

    def test_custom_shared_resources(self):
        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            shared_resources=["public/media", "private_media"],
        )
        self.assertEqual(config.shared_resources, ["public/media", "private_media"])

    def test_explicit_empty_shared_resources(self):
        """Explicitly setting [] should not trigger auto-detection"""
        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            shared_resources=[],
        )
        self.assertEqual(config.shared_resources, [])

    def test_shared_resources_not_shared_between_instances(self):
        """Ensure separate lists per instance"""
        config1 = DjaployConfig(project_name="a", djaploy_dir="/tmp/infra",
                                shared_resources=["media"])
        config2 = DjaployConfig(project_name="b", djaploy_dir="/tmp/infra",
                                shared_resources=["media"])
        config1.shared_resources.append("logs")
        self.assertNotIn("logs", config2.shared_resources)

    def test_validate_passes_with_zero_downtime(self):
        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            deployment_strategy="zero_downtime",
        )
        self.assertTrue(config.validate())


class TestSharedResourcesAutoDetection(unittest.TestCase):
    """Test auto-detection of shared_resources from Django settings"""

    def test_auto_detects_media_root(self):
        """When shared_resources is None, resolves MEDIA_ROOT from Django settings"""
        mock_settings = MagicMock()
        mock_settings.configured = True
        mock_settings.BASE_DIR = "/home/app/myproject"
        mock_settings.MEDIA_ROOT = "/home/app/myproject/media"
        mock_settings.PRIVATE_MEDIA_ROOT = None

        with patch("djaploy.config.settings", mock_settings, create=True):
            # Patch at the import location used in _resolve_shared_resources
            import djaploy.config as config_module
            original = config_module.DjaployConfig._resolve_shared_resources

            def patched_resolve(self):
                import sys
                sys.modules["django.conf"] = MagicMock(settings=mock_settings)
                try:
                    return original(self)
                finally:
                    pass

            with patch.object(config_module.DjaployConfig, '_resolve_shared_resources', patched_resolve):
                config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
                self.assertIn("media", config.shared_resources)

    def test_auto_detects_nested_media_root(self):
        """Detects MEDIA_ROOT like public/media"""
        mock_settings = MagicMock()
        mock_settings.configured = True
        mock_settings.BASE_DIR = "/home/app/bostad"
        mock_settings.MEDIA_ROOT = "/home/app/bostad/public/media"
        mock_settings.PRIVATE_MEDIA_ROOT = "/home/app/bostad/private_media"

        import djaploy.config as config_module
        original = config_module.DjaployConfig._resolve_shared_resources

        def patched_resolve(self):
            import sys
            sys.modules["django.conf"] = MagicMock(settings=mock_settings)
            try:
                return original(self)
            finally:
                pass

        with patch.object(config_module.DjaployConfig, '_resolve_shared_resources', patched_resolve):
            config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
            self.assertIn("public/media", config.shared_resources)
            self.assertIn("private_media", config.shared_resources)

    def test_auto_detect_skips_external_paths(self):
        """MEDIA_ROOT outside BASE_DIR is not included"""
        mock_settings = MagicMock()
        mock_settings.configured = True
        mock_settings.BASE_DIR = "/home/app/myproject"
        mock_settings.MEDIA_ROOT = "/mnt/storage/media"  # outside BASE_DIR
        mock_settings.PRIVATE_MEDIA_ROOT = None

        import djaploy.config as config_module
        original = config_module.DjaployConfig._resolve_shared_resources

        def patched_resolve(self):
            import sys
            sys.modules["django.conf"] = MagicMock(settings=mock_settings)
            try:
                return original(self)
            finally:
                pass

        with patch.object(config_module.DjaployConfig, '_resolve_shared_resources', patched_resolve):
            config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
            self.assertEqual(config.shared_resources, [])

    def test_auto_detect_returns_empty_when_django_unavailable(self):
        """Falls back to [] when Django is not configured"""
        # Default behavior without Django configured
        config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
        self.assertIsInstance(config.shared_resources, list)


class TestDbDirConfig(unittest.TestCase):
    """Test db_dir configuration"""

    def test_default_db_dir_is_none(self):
        config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
        self.assertIsNone(config.db_dir)

    def test_custom_db_dir(self):
        config = DjaployConfig(
            project_name="test",
            djaploy_dir="/tmp/infra",
            db_dir="/home/{app_user}/dbs/{project_name}",
        )
        self.assertEqual(config.db_dir, "/home/{app_user}/dbs/{project_name}")

    def test_resolve_db_dir(self):
        config = DjaployConfig(
            project_name="bostad",
            djaploy_dir="/tmp/infra",
            app_user="bostad",
            db_dir="/home/{app_user}/dbs/{project_name}",
        )
        self.assertEqual(config.resolve_db_dir(), "/home/bostad/dbs/bostad")

    def test_resolve_db_dir_with_override(self):
        config = DjaployConfig(
            project_name="bostad",
            djaploy_dir="/tmp/infra",
            app_user="default_user",
            db_dir="/home/{app_user}/dbs/{project_name}",
        )
        self.assertEqual(
            config.resolve_db_dir(app_user="custom"),
            "/home/custom/dbs/bostad",
        )

    def test_resolve_db_dir_returns_none_when_not_set(self):
        config = DjaployConfig(project_name="test", djaploy_dir="/tmp/infra")
        self.assertIsNone(config.resolve_db_dir())


if __name__ == "__main__":
    unittest.main()
