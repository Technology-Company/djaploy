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
import textwrap
import time
import urllib.request

log = logging.getLogger("gunicornherder")


class GunicornHerder:
    def __init__(self, pidfile, cmd, app_dir=None, overlap=5, timeout=30,
                 health_check_url=None, health_check_timeout=10,
                 health_check_retries=3, health_check_interval=2):
        self.pidfile = pidfile
        self.cmd = cmd
        self.app_dir = app_dir
        self.overlap = overlap
        self.timeout = timeout
        self.health_check_url = health_check_url
        self.health_check_timeout = health_check_timeout
        self.health_check_retries = health_check_retries
        self.health_check_interval = health_check_interval
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

        # Step 2b: Health check — verify new master is actually serving
        if self.health_check_url:
            if not self._health_check(new_pid):
                log.error(
                    "Health check failed for new master (PID %d), rolling back",
                    new_pid,
                )
                self._signal(new_pid, signal.SIGTERM)
                log.info("Sent TERM to new master (PID %d), keeping old master (PID %d)",
                         new_pid, old_pid)
                # Wait for new master to actually exit so it doesn't linger
                # and confuse future reloads (e.g. stale pidfile.2).
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    if not self._pid_alive(new_pid):
                        log.info("New master (PID %d) exited after rollback", new_pid)
                        break
                    time.sleep(0.2)
                else:
                    log.warning("New master (PID %d) did not exit within %ds after rollback",
                                new_pid, self.timeout)
                return

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

    def _health_check(self, new_pid):
        """Hit health_check_url to verify the new master is serving.

        Retries up to health_check_retries times with health_check_interval
        seconds between attempts. Returns True if a 2xx response is received.
        """
        for attempt in range(1, self.health_check_retries + 1):
            # If the new master died, no point retrying
            if not self._pid_alive(new_pid):
                log.error("New master (PID %d) died during health check", new_pid)
                return False

            try:
                req = urllib.request.Request(self.health_check_url, method="GET")
                resp = urllib.request.urlopen(req, timeout=self.health_check_timeout)
                code = resp.getcode()
                if 200 <= code < 300:
                    log.info("Health check passed (HTTP %d) on attempt %d",
                             code, attempt)
                    return True
                log.warning("Health check returned HTTP %d on attempt %d/%d",
                            code, attempt, self.health_check_retries)
            except Exception as exc:
                log.warning("Health check failed on attempt %d/%d: %s",
                            attempt, self.health_check_retries, exc)

            if attempt < self.health_check_retries:
                time.sleep(self.health_check_interval)

        return False

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
        # Escape backslashes/quotes in path for embedding in a Python string literal.
        safe_path = self.app_dir.replace("\\", "\\\\").replace("'", "\\'")
        content = textwrap.dedent(f"""\
            import os

            def pre_exec(server):
                # Update START_CTX so gunicorn's os.chdir(START_CTX['cwd'])
                # after pre_exec uses the new release path, not the old one.
                os.chdir('{safe_path}')
                resolved = os.getcwd()
                server.START_CTX['cwd'] = resolved
                server.log.info('pre_exec: chdir to {safe_path} -> %s', resolved)

                # Update START_CTX[0] to the new venv's python.
                # The deploy step rewrites each script shebang to the venv's absolute
                # python path (e.g. $VENV_DIR/bin/python3.11).  Reading it here and
                # using it as the re-exec executable guarantees the correct Python
                # version and venv are used — matching how the initial startup works
                # via the kernel shebang mechanism.
                if server.START_CTX.get('args'):
                    gunicorn_bin = os.path.realpath(server.START_CTX['args'][0])
                    new_python = None
                    try:
                        with open(gunicorn_bin, 'r', errors='replace') as _f:
                            _shebang = _f.readline().strip()
                        if _shebang.startswith('#!'):
                            _candidate = _shebang[2:].strip().split()[0]
                            if os.path.isfile(_candidate) and os.access(_candidate, os.X_OK):
                                new_python = _candidate
                    except (IOError, OSError):
                        pass
                    # Fallback: 'python' alongside the gunicorn script
                    if not new_python:
                        _candidate = os.path.join(os.path.dirname(gunicorn_bin), 'python')
                        if os.path.exists(_candidate):
                            new_python = _candidate
                    if new_python:
                        server.START_CTX[0] = new_python
                        server.log.info('pre_exec: updated python executable to %s', new_python)
        """)
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
    parser.add_argument(
        "--health-check-url",
        help="URL to GET after new master starts; rollback on failure",
    )
    parser.add_argument(
        "--health-check-timeout",
        type=int,
        default=10,
        help="Seconds to wait for each health check request (default: 10)",
    )
    parser.add_argument(
        "--health-check-retries",
        type=int,
        default=3,
        help="Number of health check attempts before rollback (default: 3)",
    )
    parser.add_argument(
        "--health-check-interval",
        type=int,
        default=2,
        help="Seconds between health check retries (default: 2)",
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
        health_check_url=args.health_check_url,
        health_check_timeout=args.health_check_timeout,
        health_check_retries=args.health_check_retries,
        health_check_interval=args.health_check_interval,
    )
    herder.run()


if __name__ == "__main__":
    main()
