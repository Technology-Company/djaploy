"""
Django management command for deploying applications
"""

from django.core.management import BaseCommand, CommandError

from djaploy import deploy_project as djaploy_deploy
from djaploy.management.utils import load_config, load_inventory


class Command(BaseCommand):
    help = "Deploy application to target servers"
    
    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            type=str,
            required=True,
            help="Specify the environment to deploy to",
        )
        
        # Add mutually exclusive deployment mode options
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--local",
            action="store_true",
            help="Deploy local uncommitted changes",
        )
        group.add_argument(
            "--latest",
            action="store_true",
            help="Deploy the latest git HEAD commit",
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
    
    def handle(self, *args, **options):
        env = options["env"]
        
        # Load djaploy configuration
        config = load_config(options["config"])
        
        # Use inventory directory from config or override
        inventory_dir = options["inventory_dir"] or str(config.get_inventory_dir())
        
        # Determine deployment mode
        if options["local"]:
            mode = "local"
            release_tag = None
        elif options["latest"]:
            mode = "latest"
            release_tag = None
        elif options["release"]:
            mode = "release"
            release_tag = options["release"]
        else:
            raise CommandError("Must specify one of --local, --latest, or --release")
        
        # Load inventory for the environment
        hosts = load_inventory(inventory_dir, env)
        
        if not hosts:
            raise CommandError(f"No hosts found in inventory for environment '{env}'")
        
        self.stdout.write(f"Deploying to {env} using mode: {mode}")
        if release_tag:
            self.stdout.write(f"Release tag: {release_tag}")
        
        # Run deployment
        try:
            djaploy_deploy(
                config, 
                hosts, 
                mode=mode,
                release_tag=release_tag,
                env=env
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully deployed to {env}")
            )
        except Exception as e:
            raise CommandError(f"Deployment failed: {e}")