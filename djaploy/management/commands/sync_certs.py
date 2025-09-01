"""
Django management command for synchronizing SSL certificates
"""

import os
from django.core.management import BaseCommand, CommandError

from djaploy import deploy_project
from djaploy.management.utils import load_config


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
        
        # Build inventory file path
        inventory_file = f"{inventory_dir}/{env}.py"
        
        # Check if inventory file exists
        if not os.path.exists(inventory_file):
            raise CommandError(f"Inventory file not found: {inventory_file}")
        
        # Set OP_ACCOUNT environment variable if configured
        if hasattr(config, 'op_account') and config.op_account:
            os.environ['OP_ACCOUNT'] = config.op_account
        
        self.stdout.write(f"Synchronizing certificates for {env}")
        
        # Create a temporary config that only uses sync_certs module
        sync_config = config
        sync_config.modules = ["djaploy.modules.sync_certs"]
        
        # Run certificate synchronization
        try:
            deploy_project(
                sync_config,
                inventory_file,
                mode="latest",  # Mode doesn't matter for sync_certs
            )
            
            self.stdout.write(
                self.style.SUCCESS(f"Successfully synchronized certificates for {env}")
            )
        except Exception as e:
            raise CommandError(f"Certificate synchronization failed: {e}")