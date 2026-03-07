"""Tests for the systemd module zero-downtime reload behavior"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Mock pyinfra before importing
sys.modules.setdefault("pyinfra", MagicMock())
sys.modules.setdefault("pyinfra.operations", MagicMock())
sys.modules.setdefault("pyinfra.operations.files", MagicMock())
sys.modules.setdefault("pyinfra.operations.systemd", MagicMock())

from pyinfra.operations import systemd

from djaploy.modules.systemd import SystemdModule
from djaploy.config import DjaployConfig


def _make_host_data(**kwargs):
    defaults = dict(
        services=["myapp_api", "myapp_worker"],
        timer_services=["cleanup"],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_config(**kwargs):
    defaults = dict(
        project_name="myapp",
        djaploy_dir="/tmp/infra",
    )
    defaults.update(kwargs)
    return DjaployConfig(**defaults)


class TestSystemdModuleZeroDowntime(unittest.TestCase):

    def setUp(self):
        self.module = SystemdModule()
        systemd.daemon_reload.reset_mock()
        systemd.service.reset_mock()

    def test_in_place_uses_restarted(self):
        config = _make_config(deployment_strategy="in_place")
        host_data = _make_host_data(services=["myapp_api"])
        self.module.deploy(host_data, config, Path("/tmp/art.tar.gz"))

        # Should have restarted=True
        service_calls = systemd.service.call_args_list
        restart_calls = [c for c in service_calls if c.kwargs.get("restarted") is True]
        self.assertTrue(len(restart_calls) > 0, "in_place should use restarted=True")

    def test_zero_downtime_uses_reloaded(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data(services=["myapp_api"])
        self.module.deploy(host_data, config, Path("/tmp/art.tar.gz"))

        # Should have reloaded=True (USR2) and NOT restarted=True
        service_calls = systemd.service.call_args_list
        reload_calls = [c for c in service_calls if c.kwargs.get("reloaded") is True]
        restart_calls = [c for c in service_calls if c.kwargs.get("restarted") is True]
        self.assertTrue(len(reload_calls) > 0, "zero_downtime should use reloaded=True")
        self.assertEqual(len(restart_calls), 0, "zero_downtime should NOT use restarted=True")

    def test_timer_services_always_started(self):
        """Timer services don't need reload/restart distinction"""
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data(services=[], timer_services=["cleanup"])
        self.module.deploy(host_data, config, Path("/tmp/art.tar.gz"))

        timer_calls = [c for c in systemd.service.call_args_list
                       if "cleanup.timer" in str(c)]
        self.assertTrue(len(timer_calls) > 0, "Timer services should be started")

    def test_daemon_reload_always_called(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data()
        self.module.deploy(host_data, config, Path("/tmp/art.tar.gz"))
        systemd.daemon_reload.assert_called_once()


class TestSystemdModuleRollback(unittest.TestCase):

    def setUp(self):
        self.module = SystemdModule()
        systemd.service.reset_mock()

    def test_rollback_reloads_services(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data(services=["myapp_api", "myapp_worker"])

        self.module.rollback(host_data, config, release=None)

        # Should reload each service
        reload_calls = [c for c in systemd.service.call_args_list
                        if c.kwargs.get("reloaded") is True]
        self.assertEqual(len(reload_calls), 2)

    def test_rollback_does_not_restart(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data(services=["myapp_api"])

        self.module.rollback(host_data, config, release=None)

        restart_calls = [c for c in systemd.service.call_args_list
                         if c.kwargs.get("restarted") is True]
        self.assertEqual(len(restart_calls), 0)

    def test_rollback_with_no_services(self):
        config = _make_config(deployment_strategy="zero_downtime")
        host_data = _make_host_data(services=[])

        self.module.rollback(host_data, config, release=None)

        systemd.service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
