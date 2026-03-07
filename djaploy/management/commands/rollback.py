"""
Django management command for rolling back to a previous release
"""

import os
from pathlib import Path

from django.core.management import BaseCommand, CommandError

from djaploy.deploy import rollback_project
from djaploy.management.utils import load_config


class Command(BaseCommand):
    help = "Roll back to a previous release (zero_downtime strategy only)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            type=str,
            required=True,
            help="Specify the environment to roll back",
        )

        parser.add_argument(
            "--release",
            type=str,
            default=None,
            help="Specific release to roll back to (e.g. app-v1.2.0). Defaults to previous release.",
        )

        parser.add_argument(
            "--config",
            type=str,
            default=None,
            help="Path to djaploy configuration file (overrides settings)",
        )

        parser.add_argument(
            "--inventory-dir",
            type=str,
            default=None,
            help="Directory containing inventory files (overrides settings)",
        )

    def handle(self, *args, **options):
        env = options["env"]

        config = load_config(options["config"])

        inventory_dir = options["inventory_dir"] or str(config.get_inventory_dir())
        inventory_file = str(Path(inventory_dir) / f"{env}.py")

        if not os.path.exists(inventory_file):
            raise CommandError(f"Inventory file not found: {inventory_file}")

        release = options["release"]

        if release:
            self.stdout.write(f"Rolling back {env} to release: {release}")
        else:
            self.stdout.write(f"Rolling back {env} to previous release")

        try:
            rollback_project(
                config,
                inventory_file,
                release=release,
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully rolled back {env}")
            )
        except Exception as e:
            raise CommandError(f"Rollback failed: {e}")
