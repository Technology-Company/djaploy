"""
Django management command for running djaploy commands.

Usage:
    manage.py djaploy deploy --env production [--local|--latest|--release TAG]
    manage.py djaploy deploy --env production --bump-minor
    manage.py djaploy configure --env staging
    manage.py djaploy rollback --env production [--release app-v1.2.0]
    manage.py djaploy <custom_command> --env <environment>
    manage.py djaploy --list

Every command — built-in or custom — goes through the same lifecycle::

    {command}:precommand   ← command-specific setup
    precommand             ← runs for every command
    ── pyinfra execution ──
    {command}:postcommand  ← command-specific teardown
    postcommand            ← runs for every command (always, even on failure)

All CLI flags are collected into a ``context`` dict.  Hooks and command
files can read/mutate it uniformly.  The dispatcher has zero knowledge
of any specific command's internals — command files call whatever hooks
they want on the remote side.
"""

from pathlib import Path

from django.core.management import BaseCommand, CommandError

from djaploy.discovery import (
    find_command,
    find_inventory,
    find_config,
    get_available_commands,
)
from djaploy.management.utils import load_config


# Built-in command files shipped with djaploy
_BUILTIN_COMMANDS_DIR = Path(__file__).resolve().parent.parent.parent / "commands"


def _find_builtin_command(name: str) -> Path | None:
    """Return the path to a built-in djaploy command file, or None."""
    path = _BUILTIN_COMMANDS_DIR / f"{name}.py"
    return path if path.is_file() else None


def _get_all_builtin_commands() -> list[tuple[str, str]]:
    """Return (name, source) pairs for built-in commands."""
    if not _BUILTIN_COMMANDS_DIR.is_dir():
        return []
    return [
        (f.stem, "djaploy")
        for f in sorted(_BUILTIN_COMMANDS_DIR.glob("*.py"))
        if not f.name.startswith("_")
    ]


class Command(BaseCommand):
    help = "Run djaploy deployment commands"

    def add_arguments(self, parser):
        parser.add_argument(
            "command_name",
            nargs="?",
            type=str,
            help="Name of the command to run (deploy, configure, rollback, ...)",
        )

        parser.add_argument(
            "--env",
            type=str,
            help="Target environment (e.g., production, staging)",
        )

        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_commands",
            help="List all available commands",
        )

        parser.add_argument(
            "--config",
            type=str,
            default=None,
            help="Path to djaploy configuration file (overrides discovery)",
        )

        parser.add_argument(
            "--inventory",
            type=str,
            default=None,
            help="Path to inventory file (overrides discovery)",
        )

        parser.add_argument(
            "--inventory-dir",
            type=str,
            default=None,
            help="Directory containing inventory files (overrides config)",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be run without executing",
        )

        # ── deploy mode options ──────────────────────────────────────
        mode_group = parser.add_mutually_exclusive_group()
        mode_group.add_argument(
            "--local",
            action="store_true",
            help="Deploy local uncommitted changes",
        )
        mode_group.add_argument(
            "--latest",
            action="store_true",
            help="Deploy the latest git HEAD commit (default)",
        )
        mode_group.add_argument(
            "--release",
            type=str,
            default=None,
            help="Deploy a specific release tag, or roll back to a specific release",
        )

        # ── version bump options ─────────────────────────────────────
        bump_group = parser.add_mutually_exclusive_group()
        bump_group.add_argument(
            "--bump-major",
            action="store_true",
            help="Bump major version (e.g., v1.0.0 -> v2.0.0)",
        )
        bump_group.add_argument(
            "--bump-minor",
            action="store_true",
            help="Bump minor version (e.g., v1.0.0 -> v1.1.0)",
        )
        bump_group.add_argument(
            "--bump-patch",
            action="store_true",
            help="Bump patch version (e.g., v1.0.0 -> v1.0.1)",
        )

        parser.add_argument(
            "--skip-prepare",
            action="store_true",
            help="Skip running prepare.py script",
        )

    def handle(self, *args, **options):
        if options["list_commands"]:
            return self._list_commands()

        command_name = options["command_name"]
        if not command_name:
            raise CommandError(
                "Please provide a command name, or use --list to see available commands."
            )

        env = options["env"]
        if not env:
            raise CommandError("--env is required")

        # Resolve command file (built-in or app-provided)
        command_file = _find_builtin_command(command_name) or find_command(command_name)
        if not command_file:
            all_cmds = _get_all_builtin_commands() + get_available_commands()
            if all_cmds:
                names = ", ".join(name for name, _ in all_cmds)
                raise CommandError(
                    f"Unknown command: '{command_name}'. "
                    f"Available commands: {names}"
                )
            raise CommandError(
                f"Unknown command: '{command_name}'. No commands found."
            )

        # Load config
        config = self._load_config(options)
        config.validate()

        # Resolve inventory
        inventory_file = self._resolve_inventory(env, config, options)

        # Determine mode
        if options["local"]:
            mode = "local"
        elif options["release"]:
            mode = "release"
        else:
            mode = "latest"

        # Determine version bump
        version_bump = None
        if options["bump_major"]:
            version_bump = "major"
        elif options["bump_minor"]:
            version_bump = "minor"
        elif options["bump_patch"]:
            version_bump = "patch"

        # Build the context — every hook and command sees the same data
        context = {
            "command": command_name,
            "config": config,
            "env": env,
            "mode": mode,
            "release": options.get("release"),
            "version_bump": version_bump,
            "skip_prepare": options["skip_prepare"],
            "inventory_file": str(inventory_file),
            "command_file": str(command_file),
            # pyinfra_data is what gets passed via --data flags.
            # Hooks can add keys to this dict during precommand phase.
            "pyinfra_data": {
                "env": env,
                "djaploy_dir": str(config.djaploy_dir),
            },
        }

        # Pass release through to pyinfra for commands that need it
        if options.get("release"):
            context["pyinfra_data"]["release"] = options["release"]

        if options["dry_run"]:
            self.stdout.write(f"Command: {command_name}")
            self.stdout.write(f"Command file: {command_file}")
            self.stdout.write(f"Inventory: {inventory_file}")
            self.stdout.write(f"Environment: {env}")
            self.stdout.write(f"Mode: {mode}")
            if version_bump:
                self.stdout.write(f"Version bump: {version_bump}")
            return

        # ── Lifecycle ────────────────────────────────────────────────
        from djaploy.deploy import run_command

        self.stdout.write(
            f"Running '{command_name}' for environment '{env}'"
        )

        try:
            run_command(context)
        except Exception as e:
            raise CommandError(
                f"Command '{command_name}' failed: {e}"
            )

        self.stdout.write(
            self.style.SUCCESS(f"Successfully ran '{command_name}' for {env}")
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _load_config(self, options):
        config_path = options["config"]
        if not config_path:
            discovered = find_config()
            if discovered:
                config_path = str(discovered)

        try:
            return load_config(config_path)
        except Exception as e:
            raise CommandError(f"Failed to load djaploy config: {e}")

    def _resolve_inventory(self, env, config, options):
        if options["inventory"]:
            inventory_file = Path(options["inventory"])
            if not inventory_file.is_absolute():
                inventory_file = Path.cwd() / inventory_file
        elif options["inventory_dir"]:
            inventory_file = Path(options["inventory_dir"]) / f"{env}.py"
            if not inventory_file.is_absolute():
                inventory_file = Path.cwd() / inventory_file
        else:
            inventory_file = find_inventory(env)
            if not inventory_file:
                try:
                    fallback = config.get_inventory_dir() / f"{env}.py"
                    if fallback.exists():
                        inventory_file = fallback
                except Exception:
                    pass

        if not inventory_file or not inventory_file.exists():
            raise CommandError(
                f"Inventory file not found for environment '{env}'."
            )

        return inventory_file

    def _list_commands(self):
        builtin = _get_all_builtin_commands()
        app_commands = get_available_commands()

        seen = set()
        all_commands = []
        for name, source in builtin + app_commands:
            if name not in seen:
                seen.add(name)
                all_commands.append((name, source))

        if not all_commands:
            self.stdout.write("No commands found.")
            return

        self.stdout.write("Available commands:\n")
        for name, source in all_commands:
            self.stdout.write(f"  {name} (from {source})")
