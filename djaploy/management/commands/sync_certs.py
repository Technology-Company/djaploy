"""
Django management command for synchronizing SSL certificates
"""

import os
from pathlib import Path

from django.core.management import BaseCommand, CommandError

from djaploy import run_command
from djaploy.discovery import find_inventory
from djaploy.deploy import _build_pyinfra_data


class Command(BaseCommand):
    help = "Synchronize SSL certificates from 1Password to servers"

    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            type=str,
            required=True,
            help="Specify the environment to sync certificates for",
        )

        parser.add_argument(
            "--inventory-dir",
            type=str,
            default=None,
            help="Directory containing inventory files (overrides discovery)",
        )

        parser.add_argument(
            "--run-prepare",
            action="store_true",
            default=False,
            help="Run prepare.py script before syncing (default: skip)",
        )

    def handle(self, *args, **options):
        env = options["env"]

        # Resolve inventory
        if options["inventory_dir"]:
            inventory_file = str(Path(options["inventory_dir"]) / f"{env}.py")
        else:
            inv_path = find_inventory(env)
            if not inv_path:
                raise CommandError(f"Inventory file not found for environment '{env}'")
            inventory_file = str(inv_path)

        if not os.path.exists(inventory_file):
            raise CommandError(f"Inventory file not found: {inventory_file}")

        self.stdout.write(f"Synchronizing certificates for {env}")

        command_file = Path(__file__).resolve().parent.parent.parent / "commands" / "sync_certs.py"

        try:
            run_command({
                "command": "sync_certs",
                "env": env,
                "skip_prepare": not options["run_prepare"],
                "command_file": str(command_file),
                "inventory_file": inventory_file,
                "pyinfra_data": _build_pyinfra_data(env),
            })

            self.stdout.write(
                self.style.SUCCESS(f"Successfully synchronized certificates for {env}")
            )
        except Exception as e:
            raise CommandError(f"Certificate synchronization failed: {e}") from e
