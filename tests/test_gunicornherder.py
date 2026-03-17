"""Tests for the gunicornherder zero-downtime gunicorn manager."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from djaploy.bin.gunicornherder import GunicornHerder


class TestCreateConfig(unittest.TestCase):
    """Test that _create_config generates a correct gunicorn pre_exec hook."""

    def _make_venv_tree(self, base, python_name="python"):
        """Create a minimal venv bin/ directory with a gunicorn and python script."""
        bin_dir = base / "bin"
        bin_dir.mkdir(parents=True)
        gunicorn = bin_dir / "gunicorn"
        gunicorn.write_text("#!/usr/bin/env python\n# gunicorn stub")
        gunicorn.chmod(gunicorn.stat().st_mode | stat.S_IEXEC)
        python = bin_dir / python_name
        python.write_text("#!/bin/sh\n# python stub")
        python.chmod(python.stat().st_mode | stat.S_IEXEC)
        # Also create a 'python' symlink if python_name is versioned
        if python_name != "python":
            (bin_dir / "python").symlink_to(python)
        return gunicorn, python

    def _load_pre_exec(self, config_path):
        """Load the pre_exec function from a generated config file."""
        ns = {}
        with open(config_path) as f:
            exec(f.read(), ns)
        return ns["pre_exec"]

    def _make_mock_server(self, executable, args):
        """Create a mock gunicorn server object with START_CTX and log."""
        server = MagicMock()
        server.START_CTX = {0: executable, "args": list(args), "cwd": "/old"}
        server.log = MagicMock()
        return server

    def test_pre_exec_updates_cwd(self):
        """pre_exec should chdir to app_dir and update START_CTX['cwd']."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            release = tmpdir / "releases" / "20260317_120000"
            release.mkdir(parents=True)
            current = tmpdir / "current"
            current.symlink_to(release)

            herder = GunicornHerder(
                pidfile="/tmp/test.pid",
                cmd=["gunicorn", "app:app"],
                app_dir=str(current),
            )
            config_path = herder._create_config()
            try:
                pre_exec = self._load_pre_exec(config_path)
                server = self._make_mock_server("/usr/bin/python", ["gunicorn"])
                pre_exec(server)

                # cwd should be the resolved release path, not the symlink
                self.assertEqual(server.START_CTX["cwd"], str(release))
            finally:
                os.unlink(config_path)

    def test_pre_exec_updates_python_executable(self):
        """pre_exec should update START_CTX[0] to the new venv's python."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Old release with old venv
            old_release = tmpdir / "releases" / "20260316_120000"
            old_venv = tmpdir / "shared" / "venv-oldhash"
            old_gunicorn, old_python = self._make_venv_tree(old_venv)

            # New release with new venv (different hash = new dependencies)
            new_release = tmpdir / "releases" / "20260317_120000"
            new_release.mkdir(parents=True)
            new_venv = tmpdir / "shared" / "venv-newhash"
            new_gunicorn, new_python = self._make_venv_tree(new_venv)

            # New release's .venv symlink points to the new shared venv
            (new_release / ".venv").symlink_to(new_venv)

            # `current` symlink points to the new release
            current = tmpdir / "current"
            current.symlink_to(new_release)

            # The gunicorn binary path as it appears in the systemd service
            gunicorn_via_symlink = str(current / ".venv" / "bin" / "gunicorn")

            herder = GunicornHerder(
                pidfile="/tmp/test.pid",
                cmd=[gunicorn_via_symlink, "app:app"],
                app_dir=str(current),
            )
            config_path = herder._create_config()
            try:
                pre_exec = self._load_pre_exec(config_path)
                server = self._make_mock_server(
                    str(old_python.resolve()),  # old venv's python (resolved)
                    [gunicorn_via_symlink, "app:app"],
                )

                pre_exec(server)

                # START_CTX[0] should now point to the NEW venv's python
                self.assertEqual(
                    server.START_CTX[0],
                    str(new_python.resolve()),
                )
                # Should NOT still be the old python
                self.assertNotEqual(
                    server.START_CTX[0],
                    str(old_python.resolve()),
                )
            finally:
                os.unlink(config_path)

    def test_pre_exec_noop_when_no_venv_python(self):
        """pre_exec should leave START_CTX[0] unchanged if no python in venv bin/."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            release = tmpdir / "releases" / "20260317_120000"
            release.mkdir(parents=True)

            # Create a venv with gunicorn but NO python binary
            venv = tmpdir / "shared" / "venv-hash"
            bin_dir = venv / "bin"
            bin_dir.mkdir(parents=True)
            gunicorn = bin_dir / "gunicorn"
            gunicorn.write_text("#!/usr/bin/env python\n# stub")
            gunicorn.chmod(gunicorn.stat().st_mode | stat.S_IEXEC)

            (release / ".venv").symlink_to(venv)
            current = tmpdir / "current"
            current.symlink_to(release)

            gunicorn_via_symlink = str(current / ".venv" / "bin" / "gunicorn")
            old_python = "/usr/bin/python3"

            herder = GunicornHerder(
                pidfile="/tmp/test.pid",
                cmd=[gunicorn_via_symlink, "app:app"],
                app_dir=str(current),
            )
            config_path = herder._create_config()
            try:
                pre_exec = self._load_pre_exec(config_path)
                server = self._make_mock_server(
                    old_python, [gunicorn_via_symlink, "app:app"]
                )

                pre_exec(server)

                # Should remain unchanged
                self.assertEqual(server.START_CTX[0], old_python)
            finally:
                os.unlink(config_path)

    def test_pre_exec_same_venv_is_idempotent(self):
        """When the venv hasn't changed, START_CTX[0] should still be updated to the resolved path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            release = tmpdir / "releases" / "20260317_120000"
            release.mkdir(parents=True)

            venv = tmpdir / "shared" / "venv-samehash"
            gunicorn, python = self._make_venv_tree(venv)

            (release / ".venv").symlink_to(venv)
            current = tmpdir / "current"
            current.symlink_to(release)

            gunicorn_via_symlink = str(current / ".venv" / "bin" / "gunicorn")

            herder = GunicornHerder(
                pidfile="/tmp/test.pid",
                cmd=[gunicorn_via_symlink, "app:app"],
                app_dir=str(current),
            )
            config_path = herder._create_config()
            try:
                pre_exec = self._load_pre_exec(config_path)
                server = self._make_mock_server(
                    str(python.resolve()),
                    [gunicorn_via_symlink, "app:app"],
                )

                pre_exec(server)

                # Should be the same resolved path (idempotent)
                self.assertEqual(server.START_CTX[0], str(python.resolve()))
            finally:
                os.unlink(config_path)

    def test_pre_exec_with_manage_subdir(self):
        """pre_exec works when app_dir includes a manage subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            release = tmpdir / "releases" / "20260317_120000"
            subdir = release / "myapp"
            subdir.mkdir(parents=True)

            venv = tmpdir / "shared" / "venv-hash"
            gunicorn, python = self._make_venv_tree(venv)

            # .venv is at the release root, not in the subdirectory
            (release / ".venv").symlink_to(venv)
            current = tmpdir / "current"
            current.symlink_to(release)

            # app_dir includes the subdirectory
            app_dir = str(current / "myapp")
            # But gunicorn binary is at the release root's .venv
            gunicorn_via_symlink = str(current / ".venv" / "bin" / "gunicorn")

            herder = GunicornHerder(
                pidfile="/tmp/test.pid",
                cmd=[gunicorn_via_symlink, "app:app"],
                app_dir=app_dir,
            )
            config_path = herder._create_config()
            try:
                pre_exec = self._load_pre_exec(config_path)
                server = self._make_mock_server(
                    "/old/venv/bin/python",
                    [gunicorn_via_symlink, "app:app"],
                )

                pre_exec(server)

                # cwd should resolve to the subdirectory
                self.assertEqual(server.START_CTX["cwd"], str(subdir))
                # Python should be updated to the new venv
                self.assertEqual(server.START_CTX[0], str(python.resolve()))
            finally:
                os.unlink(config_path)


class TestInjectArg(unittest.TestCase):
    """Test the _inject_arg helper."""

    def test_injects_after_gunicorn_token(self):
        herder = GunicornHerder("/tmp/test.pid", [])
        cmd = ["/path/to/.venv/bin/gunicorn", "--workers", "4", "app:app"]
        result = herder._inject_arg(cmd, "--config", "/tmp/cfg.py")
        self.assertEqual(result, [
            "/path/to/.venv/bin/gunicorn",
            "--config", "/tmp/cfg.py",
            "--workers", "4", "app:app",
        ])

    def test_fallback_when_no_gunicorn_in_cmd(self):
        herder = GunicornHerder("/tmp/test.pid", [])
        cmd = ["myserver", "--port", "8000"]
        result = herder._inject_arg(cmd, "--pid", "/tmp/pid")
        self.assertEqual(result, ["myserver", "--pid", "/tmp/pid", "--port", "8000"])


if __name__ == "__main__":
    unittest.main()
