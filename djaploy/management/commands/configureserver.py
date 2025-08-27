"""
Django management command for configuring servers
"""

from django.core.management import BaseCommand, CommandError

from djaploy import configure_server as djaploy_configure
from djaploy.management.utils import load_config, load_inventory


class Command(BaseCommand):
    help = "Configure server for the application"
    
    def add_arguments(self, parser):
        parser.add_argument(
            "--env",
            type=str,
            required=True,
            help="Specify the environment to configure the server for",
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
        
        # Load inventory for the environment
        hosts = load_inventory(inventory_dir, env)
        
        if not hosts:
            raise CommandError(f"No hosts found in inventory for environment '{env}'")
        
        # Run configuration
        try:
            djaploy_configure(config, hosts, env=env)
            self.stdout.write(
                self.style.SUCCESS(f"Successfully configured servers for {env}")
            )
        except Exception as e:
            raise CommandError(f"Configuration failed: {e}")