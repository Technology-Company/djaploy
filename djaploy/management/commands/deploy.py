"""
Django management command for deploying applications
"""

import os
from pathlib import Path

from django.core.management import BaseCommand, CommandError

from djaploy import deploy_project as djaploy_deploy
from djaploy.management.utils import load_config


class Command(BaseCommand):
    help = "Deploy application to target servers"
    
    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            type=str,
            required=True,
            help="Specify the environment to deploy to",
        )
        
        # Add mutually exclusive deployment mode options (--latest is the default)
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--local",
            action="store_true",
            help="Deploy local uncommitted changes",
        )
        group.add_argument(
            "--latest",
            action="store_true",
            default=True,
            help="Deploy the latest git HEAD commit (default)",
        )
        group.add_argument(
            "--release",
            type=str,
            help="Deploy a specific release tag",
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

        # Version bump options (mutually exclusive)
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
            help="Bump patch version (e.g., v1.0.0 -> v1.0.1) - this is the default",
        )
    
    def handle(self, *args, **options):
        env = options["env"]
        
        # Load djaploy configuration
        config = load_config(options["config"])
        
        # Use inventory directory from config or override
        inventory_dir = options["inventory_dir"] or str(config.get_inventory_dir())
        
        # Build inventory file path
        inventory_file = str(Path(inventory_dir) / f"{env}.py")
        
        # Check if inventory file exists
        if not os.path.exists(inventory_file):
            raise CommandError(f"Inventory file not found: {inventory_file}")
        
        # Determine deployment mode (--latest is the default)
        if options["local"]:
            mode = "local"
            release_tag = None
        elif options["release"]:
            mode = "release"
            release_tag = options["release"]
        else:
            mode = "latest"
            release_tag = None
        
        # Determine version bump override
        version_bump = None
        if options["bump_major"]:
            version_bump = "major"
        elif options["bump_minor"]:
            version_bump = "minor"
        elif options["bump_patch"]:
            version_bump = "patch"

        self.stdout.write(f"Deploying to {env} using mode: {mode}")
        if release_tag:
            self.stdout.write(f"Release tag: {release_tag}")
        if version_bump:
            self.stdout.write(f"Version bump: {version_bump}")

        # Run deployment
        try:
            djaploy_deploy(
                config,
                inventory_file,
                mode=mode,
                release_tag=release_tag,
                version_bump=version_bump,
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully deployed to {env}")
            )
        except Exception as e:
            raise CommandError(f"Deployment failed: {e}")