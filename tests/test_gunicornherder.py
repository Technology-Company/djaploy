"""Tests for the gunicornherder zero-downtime gunicorn manager."""

import os
import signal
import stat
import tempfile
import unittest
import unittest.mock
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
                self.assertEqual(server.START_CTX["cwd"], str(release.resolve()))
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

                # START_CTX[0] should point to python in the new venv's bin/ dir.
                # Must NOT be resolved further (e.g. to /usr/bin/python3.11),
                # because Python needs to start from inside the venv to find pyvenv.cfg.
                # Use resolve() on the directory to handle OS-level dir symlinks
                # (e.g. /var → /private/var on macOS) without following the
                # python symlink itself.
                new_venv_python = str((new_venv / "bin").resolve() / "python")
                self.assertEqual(server.START_CTX[0], new_venv_python)
                # Should NOT still be the old python
                self.assertNotEqual(
                    server.START_CTX[0],
                    str(old_python.resolve()),
                )
            finally:
                os.unlink(config_path)

    def test_pre_exec_does_not_resolve_python_symlink(self):
        """pre_exec must NOT resolve python to the system python.

        In a real venv, bin/python is a symlink chain: python → python3 →
        python3.11 → /usr/bin/python3.11.  If we follow all the way to
        /usr/bin/python3.11, gunicorn re-execs outside the venv — Python
        never finds pyvenv.cfg and the new packages are invisible.
        START_CTX[0] must stay inside the venv's bin/ directory.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Simulate system python living outside the venv
            system_bin = tmpdir / "usr" / "bin"
            system_bin.mkdir(parents=True)
            system_python = system_bin / "python3.11"
            system_python.write_text("#!/bin/sh\n# system python stub")
            system_python.chmod(system_python.stat().st_mode | stat.S_IEXEC)

            # New venv: gunicorn is a real file, python is a symlink chain
            # (mirrors a real venv: python → python3 → python3.11 → system)
            new_release = tmpdir / "releases" / "20260317_120000"
            new_release.mkdir(parents=True)
            new_venv = tmpdir / "shared" / "venv-newhash"
            bin_dir = new_venv / "bin"
            bin_dir.mkdir(parents=True)
            gunicorn = bin_dir / "gunicorn"
            gunicorn.write_text("#!/usr/bin/env python\n# gunicorn stub")
            gunicorn.chmod(gunicorn.stat().st_mode | stat.S_IEXEC)
            # python3.11 → system python (symlink, as in a real venv)
            (bin_dir / "python3.11").symlink_to(system_python)
            (bin_dir / "python3").symlink_to(bin_dir / "python3.11")
            (bin_dir / "python").symlink_to(bin_dir / "python3")

            (new_release / ".venv").symlink_to(new_venv)
            current = tmpdir / "current"
            current.symlink_to(new_release)
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
                    str(system_python),
                    [gunicorn_via_symlink, "app:app"],
                )
                pre_exec(server)

                # Must be the venv's bin/python — NOT the system python
                self.assertEqual(
                    server.START_CTX[0],
                    str((new_venv / "bin").resolve() / "python"),
                )
                self.assertNotEqual(server.START_CTX[0], str(system_python.resolve()))
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
                self.assertEqual(server.START_CTX["cwd"], str(subdir.resolve()))
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


class TestHealthCheck(unittest.TestCase):
    """Test health check during reload."""

    def _make_herder(self, **kwargs):
        defaults = dict(
            pidfile="/tmp/test.pid",
            cmd=["gunicorn", "app:app"],
            health_check_url="http://localhost:8000/health",
            health_check_timeout=2,
            health_check_retries=3,
            health_check_interval=0,  # no delay in tests
        )
        defaults.update(kwargs)
        return GunicornHerder(**defaults)

    @unittest.mock.patch("djaploy.bin.gunicornherder.urllib.request.urlopen")
    def test_health_check_passes_on_200(self, mock_urlopen):
        herder = self._make_herder()
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_urlopen.return_value = mock_resp

        with unittest.mock.patch.object(herder, "_pid_alive", return_value=True):
            self.assertTrue(herder._health_check(new_pid=1234))

        mock_urlopen.assert_called_once()

    @unittest.mock.patch("djaploy.bin.gunicornherder.urllib.request.urlopen")
    def test_health_check_retries_then_fails(self, mock_urlopen):
        herder = self._make_herder()
        mock_urlopen.side_effect = ConnectionRefusedError("refused")

        with unittest.mock.patch.object(herder, "_pid_alive", return_value=True):
            self.assertFalse(herder._health_check(new_pid=1234))

        self.assertEqual(mock_urlopen.call_count, 3)

    @unittest.mock.patch("djaploy.bin.gunicornherder.urllib.request.urlopen")
    def test_health_check_succeeds_on_retry(self, mock_urlopen):
        herder = self._make_herder()
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_urlopen.side_effect = [ConnectionRefusedError("refused"), mock_resp]

        with unittest.mock.patch.object(herder, "_pid_alive", return_value=True):
            self.assertTrue(herder._health_check(new_pid=1234))

        self.assertEqual(mock_urlopen.call_count, 2)

    @unittest.mock.patch("djaploy.bin.gunicornherder.urllib.request.urlopen")
    def test_health_check_aborts_if_process_dies(self, mock_urlopen):
        herder = self._make_herder()

        with unittest.mock.patch.object(herder, "_pid_alive", return_value=False):
            self.assertFalse(herder._health_check(new_pid=1234))

        # Should not even try the HTTP request if process is dead
        mock_urlopen.assert_not_called()

    @unittest.mock.patch("djaploy.bin.gunicornherder.urllib.request.urlopen")
    def test_health_check_fails_on_500(self, mock_urlopen):
        herder = self._make_herder(health_check_retries=1)
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 500
        mock_urlopen.return_value = mock_resp

        with unittest.mock.patch.object(herder, "_pid_alive", return_value=True):
            self.assertFalse(herder._health_check(new_pid=1234))

    def test_no_health_check_when_url_not_set(self):
        """Reload should skip health check when no URL is configured."""
        herder = self._make_herder(health_check_url=None)
        self.assertIsNone(herder.health_check_url)

    @unittest.mock.patch("djaploy.bin.gunicornherder.urllib.request.urlopen")
    def test_reload_rolls_back_on_health_check_failure(self, mock_urlopen):
        """_do_reload should TERM the new master and keep old on health failure."""
        herder = self._make_herder()
        mock_urlopen.side_effect = ConnectionRefusedError("refused")

        old_pid, new_pid = 100, 200

        with unittest.mock.patch.object(herder, "_read_pid", return_value=old_pid), \
             unittest.mock.patch.object(herder, "_signal", return_value=True) as mock_signal, \
             unittest.mock.patch.object(herder, "_wait_for_new_pid", return_value=new_pid), \
             unittest.mock.patch.object(herder, "_pid_alive", return_value=True):
            herder._do_reload()

        # Should have sent USR2 to old, then TERM to new (rollback)
        # Should NOT have sent WINCH or QUIT to old
        calls = [c[0] for c in mock_signal.call_args_list]
        self.assertEqual(calls[0], (old_pid, signal.SIGUSR2))
        self.assertEqual(calls[1], (new_pid, signal.SIGTERM))
        # No WINCH or QUIT to old master
        signals_to_old = [sig for pid, sig in calls if pid == old_pid]
        self.assertNotIn(signal.SIGWINCH, signals_to_old)
        self.assertNotIn(signal.SIGQUIT, signals_to_old)


if __name__ == "__main__":
    unittest.main()
