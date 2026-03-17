#!/usr/bin/env python3
"""
gunicornherder — Zero-downtime gunicorn manager for systemd.

Wraps gunicorn, maintaining a stable PID for systemd to track while
managing USR2-based graceful restarts internally.

On HUP (from systemd ExecReload):
  1. USR2 → gunicorn forks + re-execs new master (inherits listen socket)
  2. Waits for new master PID in pidfile.2 (gunicorn 21+) or pidfile
  3. WINCH → old master gracefully stops accepting, drains workers
  4. Waits overlap period for in-flight requests to complete
  5. QUIT → old master exits
  6. New master auto-promotes (renames pidfile.2 → pidfile)

On TERM/INT:
  Forwards to gunicorn for graceful shutdown.

Usage:
  gunicornherder --pidfile /run/app/gunicorn.pid \\
                 --app-dir /home/app/apps/myapp/current \\
                 -- gunicorn [gunicorn-args...]

The --app-dir flag is key: it generates a gunicorn pre_exec hook that
re-resolves the symlink before re-exec, so the new master loads code
from the latest release.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time

log = logging.getLogger("gunicornherder")


class GunicornHerder:
    def __init__(self, pidfile, cmd, app_dir=None, overlap=5, timeout=30):
        self.pidfile = pidfile
        self.cmd = cmd
        self.app_dir = app_dir
        self.overlap = overlap
        self.timeout = timeout
        self.running = True
        self._reloading = False
        self._config_file = None

    def run(self):
        cmd = list(self.cmd)

        # Generate gunicorn config with pre_exec hook
        if self.app_dir:
            self._config_file = self._create_config()
            cmd = self._inject_arg(cmd, "--config", self._config_file)

        # Ensure --pid is set
        if not any(a == "--pid" for a in cmd):
            cmd = self._inject_arg(cmd, "--pid", self.pidfile)

        # Auto-reap child processes to prevent zombies.
        # Without this, os.kill(pid, 0) returns True for zombies,
        # making _pid_alive unreliable after the old master exits.
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)

        log.info("Starting: %s", " ".join(cmd))
        subprocess.Popen(cmd)

        # Wait for gunicorn to write its pidfile
        pid = self._wait_for_pid()
        if pid is None:
            log.error("Gunicorn failed to start (no pidfile after %ds)", self.timeout)
            sys.exit(1)

        log.info("Gunicorn master ready (PID %d)", pid)

        # Install signal handlers
        signal.signal(signal.SIGHUP, self._on_hup)
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)

        # Monitor loop — exit when gunicorn dies (and we're not mid-reload)
        while self.running:
            time.sleep(1)
            if not self._is_master_alive() and not self._reloading:
                log.info("Gunicorn master exited, herder shutting down")
                break

        self._cleanup()

    def _on_hup(self, signum, frame):
        if self._reloading:
            log.warning("Reload already in progress, ignoring HUP")
            return

        self._reloading = True
        try:
            self._do_reload()
        except Exception:
            log.exception("Reload failed")
        finally:
            self._reloading = False

    def _on_term(self, signum, frame):
        log.info("Received %s, forwarding TERM to gunicorn",
                 signal.Signals(signum).name)
        # Signal both masters if in mid-transition
        signaled = set()
        for path in [self.pidfile, self.pidfile + ".2"]:
            pid = self._read_pid_from(path)
            if pid and pid not in signaled:
                self._signal(pid, signal.SIGTERM)
                signaled.add(pid)
        self.running = False

    def _do_reload(self):
        old_pid = self._read_pid()
        if old_pid is None:
            log.error("Cannot reload: no pidfile")
            return

        log.info("Zero-downtime reload: old master PID %d", old_pid)

        # Step 1: USR2 — old master forks + execs new master
        if not self._signal(old_pid, signal.SIGUSR2):
            log.error("Failed to send USR2 to PID %d", old_pid)
            return

        # Step 2: Wait for new master to appear in pidfile
        new_pid = self._wait_for_new_pid(old_pid)
        if new_pid is None:
            log.error(
                "New master did not start within %ds, keeping old master",
                self.timeout,
            )
            return

        log.info("New master ready (PID %d)", new_pid)

        # Step 3: WINCH — old master stops accepting, workers drain
        self._signal(old_pid, signal.SIGWINCH)
        log.info(
            "Sent WINCH to old master, waiting %ds for in-flight requests",
            self.overlap,
        )
        time.sleep(self.overlap)

        # Step 4: QUIT — old master exits
        self._signal(old_pid, signal.SIGQUIT)
        log.info("Sent QUIT to old master (PID %d)", old_pid)

        # Step 5: Wait for old master to exit so new master promotes
        # itself (renames pidfile.2 → pidfile via maybe_promote_master)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if not self._pid_alive(old_pid):
                log.info("Old master (PID %d) exited", old_pid)
                break
            time.sleep(0.2)
        else:
            log.warning("Old master (PID %d) did not exit within %ds",
                        old_pid, self.timeout)

    # -- helpers --

    def _read_pid(self):
        try:
            with open(self.pidfile) as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError, IOError):
            return None

    def _wait_for_pid(self):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            pid = self._read_pid()
            if pid and self._pid_alive(pid):
                return pid
            time.sleep(0.2)
        return None

    def _read_pid_from(self, path):
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError, IOError):
            return None

    def _wait_for_new_pid(self, old_pid):
        # Gunicorn 21+ writes the new master PID to pidfile + ".2"
        # (not .oldbin). The new master renames .2 → pidfile only
        # after the old master exits (maybe_promote_master).
        pid2_file = self.pidfile + ".2"
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            # Check the .2 pidfile first (gunicorn 21+)
            pid = self._read_pid_from(pid2_file)
            if pid and pid != old_pid and self._pid_alive(pid):
                return pid

            # Also check the main pidfile (older gunicorn versions
            # that use .oldbin rename pattern)
            pid = self._read_pid()
            if pid and pid != old_pid and self._pid_alive(pid):
                return pid

            time.sleep(0.2)
        return None

    def _is_master_alive(self):
        # Check both main pidfile and .2 (during USR2 transitions)
        for path in [self.pidfile, self.pidfile + ".2"]:
            pid = self._read_pid_from(path)
            if pid is not None and self._pid_alive(pid):
                return True
        return False

    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _signal(pid, sig):
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False

    def _create_config(self):
        """Create a temporary gunicorn config with a pre_exec hook."""
        # Escape backslashes/quotes in path for safety
        safe_path = self.app_dir.replace("\\", "\\\\").replace("'", "\\'")
        content = (
            "import os\n"
            "import sys\n"
            "\n"
            "def pre_exec(server):\n"
            f"    os.chdir('{safe_path}')\n"
            "    resolved = os.getcwd()\n"
            "    # Update START_CTX so gunicorn's os.chdir(START_CTX['cwd'])\n"
            "    # after pre_exec uses the new path, not the old release\n"
            "    server.START_CTX['cwd'] = resolved\n"
            "    # Update sys.executable to the new venv's Python so the re-exec\n"
            "    # uses updated dependencies rather than the old venv. We derive\n"
            "    # the new Python from the gunicorn script's shebang — argv[0]\n"
            "    # already resolves through the updated 'current' symlink.\n"
            "    gunicorn_script = server.START_CTX.get('argv', [''])[0]\n"
            "    try:\n"
            "        with open(gunicorn_script) as _f:\n"
            "            shebang = _f.readline().strip()\n"
            "        if shebang.startswith('#!'):\n"
            "            new_python = shebang[2:].strip().split()[0]\n"
            "            if os.path.isfile(new_python):\n"
            "                sys.executable = new_python\n"
            "                server.log.info('pre_exec: updated Python to %s', new_python)\n"
            "            else:\n"
            "                server.log.warning(\n"
            "                    'pre_exec: shebang Python not found: %s', new_python\n"
            "                )\n"
            "    except Exception as _e:\n"
            "        server.log.warning(\n"
            "            'pre_exec: could not update Python executable: %s', _e\n"
            "        )\n"
            f"    server.log.info('pre_exec: chdir to {safe_path} -> %s', resolved)\n"
        )
        fd, path = tempfile.mkstemp(suffix=".py", prefix="gunicornherder_")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return path

    def _inject_arg(self, cmd, flag, value):
        """Insert a flag+value right after the 'gunicorn' token in cmd."""
        for i, arg in enumerate(cmd):
            if "gunicorn" in os.path.basename(arg):
                return cmd[: i + 1] + [flag, value] + cmd[i + 1:]
        # Fallback: insert after first arg
        return [cmd[0], flag, value] + cmd[1:]

    def _cleanup(self):
        if self._config_file:
            try:
                os.unlink(self._config_file)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Zero-downtime gunicorn manager for systemd",
        usage="gunicornherder [options] -- gunicorn [gunicorn-args...]",
    )
    parser.add_argument(
        "--pidfile", required=True, help="Path to gunicorn PID file"
    )
    parser.add_argument(
        "--app-dir",
        help="App directory symlink path (re-resolved on each reload)",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=5,
        help="Seconds to keep old workers alive after WINCH (default: 5)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Seconds to wait for new master to start (default: 30)",
    )

    # Split on '--'
    try:
        sep = sys.argv.index("--")
        our_args = sys.argv[1:sep]
        cmd = sys.argv[sep + 1:]
    except ValueError:
        parser.print_help()
        sys.exit(1)

    if not cmd:
        parser.error("No gunicorn command specified after '--'")

    args = parser.parse_args(our_args)

    logging.basicConfig(
        level=logging.INFO,
        format="[gunicornherder] %(message)s",
        stream=sys.stderr,
    )

    herder = GunicornHerder(
        pidfile=args.pidfile,
        cmd=cmd,
        app_dir=args.app_dir,
        overlap=args.overlap,
        timeout=args.timeout,
    )
    herder.run()


if __name__ == "__main__":
    main()
