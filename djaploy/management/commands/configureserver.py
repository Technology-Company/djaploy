"""
Django management command for configuring servers
"""

import os
from django.core.management import BaseCommand, CommandError

from djaploy import configure_server as djaploy_configure
from djaploy.management.utils import load_config


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
        
        config = load_config(options["config"])
        
        inventory_dir = options["inventory_dir"] or str(config.get_inventory_dir())
        
        inventory_file = f"{inventory_dir}/{env}.py"
        
        if not os.path.exists(inventory_file):
            raise CommandError(f"Inventory file not found: {inventory_file}")
        
        # Run configuration
        try:
            djaploy_configure(config, inventory_file)
            self.stdout.write(
                self.style.SUCCESS(f"Successfully configured servers for {env}")
            )
        except Exception as e:
            raise CommandError(f"Configuration failed: {e}")