"""
Verify djaploy configuration command
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any
from django.core.management.base import BaseCommand
from django.conf import settings

from djaploy.management.utils import load_config, load_inventory


class Command(BaseCommand):
    help = 'Verify djaploy configuration, inventory, and deploy files'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.errors = []
        self.warnings = []
        self.info = []
        
    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information'
        )
        
    def handle(self, *args, **options):
        self.verbose = options.get('verbose', False)
        
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.MIGRATE_HEADING("DJAPLOY CONFIGURATION VERIFICATION"))
        self.stdout.write("="*60 + "\n")
        
        # 1. Check Django settings
        self.check_django_settings()
        
        # 2. Check and load configuration
        config = self.check_configuration()
        
        if config:
            # 3. Check deploy files
            self.check_deploy_files(config)
            
            # 4. Check inventory
            self.check_inventory(config)
            
            # 5. Check modules
            self.check_modules(config)
            
            # 6. Check project structure
            self.check_project_structure(config)
        
        # Print summary
        self.print_summary()
        
        # Exit with appropriate code
        if self.errors:
            sys.exit(1)
        
    def check_django_settings(self):
        """Check Django settings for djaploy configuration"""
        self.stdout.write(self.style.HTTP_INFO("1. Django Settings"))
        self.stdout.write("-" * 40)
        
        # Check DJAPLOY_CONFIG_DIR
        djaploy_dir = getattr(settings, 'DJAPLOY_CONFIG_DIR', None)
        if djaploy_dir:
            djaploy_path = Path(djaploy_dir)
            if djaploy_path.exists():
                self.info.append(f"DJAPLOY_CONFIG_DIR: {djaploy_dir}")
                self.stdout.write(self.style.SUCCESS(f"  ✓ DJAPLOY_CONFIG_DIR: {djaploy_dir}"))
            else:
                self.errors.append(f"DJAPLOY_CONFIG_DIR path does not exist: {djaploy_dir}")
                self.stdout.write(self.style.ERROR(f"  ✗ DJAPLOY_CONFIG_DIR path does not exist: {djaploy_dir}"))
        else:
            self.warnings.append("DJAPLOY_CONFIG_DIR not set in settings")
            self.stdout.write(self.style.WARNING("  ⚠ DJAPLOY_CONFIG_DIR not set in settings"))
        
        # Check other optional settings
        if hasattr(settings, 'DJAPLOY_GIT_DIR'):
            self.stdout.write(f"  • DJAPLOY_GIT_DIR: {settings.DJAPLOY_GIT_DIR}")
            
        self.stdout.write("")
        
    def check_configuration(self):
        """Check and load djaploy configuration"""
        self.stdout.write(self.style.HTTP_INFO("2. Configuration File"))
        self.stdout.write("-" * 40)
        
        try:
            config = load_config()
            
            if config:
                # Validate configuration
                try:
                    config.validate()
                    self.stdout.write(self.style.SUCCESS("  ✓ Configuration loaded and validated"))
                    
                    # Show configuration details
                    self.stdout.write(f"  • Project: {config.project_name}")
                    self.stdout.write(f"  • Python Version: {config.python_version}")
                    self.stdout.write(f"  • App User: {config.app_user}")
                    
                    if config.manage_py_path:
                        self.stdout.write(f"  • Manage.py Path: {config.manage_py_path}")
                    
                    if config.ssl_enabled:
                        self.stdout.write(self.style.SUCCESS("  • SSL: Enabled"))
                        if config.ssl_cert_path:
                            self.stdout.write(f"    - Cert: {config.ssl_cert_path}")
                        if config.ssl_key_path:
                            self.stdout.write(f"    - Key: {config.ssl_key_path}")
                    
                    if self.verbose:
                        self.stdout.write(f"  • Modules: {', '.join(config.modules)}")
                    
                except Exception as e:
                    self.errors.append(f"Configuration validation failed: {e}")
                    self.stdout.write(self.style.ERROR(f"  ✗ Configuration validation failed: {e}"))
                    return None
                    
            else:
                self.errors.append("No configuration found")
                self.stdout.write(self.style.ERROR("  ✗ No configuration found"))
                self.stdout.write(self.style.WARNING("    Create a config.py file in your DJAPLOY_CONFIG_DIR"))
                return None
                
        except Exception as e:
            self.errors.append(f"Failed to load configuration: {e}")
            self.stdout.write(self.style.ERROR(f"  ✗ Failed to load configuration: {e}"))
            return None
            
        self.stdout.write("")
        return config
        
    def check_deploy_files(self, config):
        """Check deploy files directory"""
        self.stdout.write(self.style.HTTP_INFO("3. Deploy Files"))
        self.stdout.write("-" * 40)
        
        deploy_files_dir = config.get_deploy_files_dir()
        
        if deploy_files_dir.exists():
            self.stdout.write(self.style.SUCCESS(f"  ✓ Deploy files directory exists: {deploy_files_dir}"))
            
            # List files in deploy directory
            deploy_files = list(deploy_files_dir.iterdir())
            if deploy_files:
                self.stdout.write(f"  • Found {len(deploy_files)} deploy file(s):")
                if self.verbose:
                    for f in deploy_files[:10]:  # Show first 10 files
                        self.stdout.write(f"    - {f.name}")
                    if len(deploy_files) > 10:
                        self.stdout.write(f"    ... and {len(deploy_files) - 10} more")
            else:
                self.warnings.append("Deploy files directory is empty")
                self.stdout.write(self.style.WARNING("  ⚠ Deploy files directory is empty"))
                
            # Check for common required files
            common_files = ['requirements.txt', '.env.production']
            for filename in common_files:
                filepath = deploy_files_dir / filename
                if filepath.exists():
                    self.stdout.write(f"  • {filename}: Found")
                elif self.verbose:
                    self.stdout.write(self.style.WARNING(f"  • {filename}: Not found (may not be required)"))
                    
        else:
            self.warnings.append(f"Deploy files directory does not exist: {deploy_files_dir}")
            self.stdout.write(self.style.WARNING(f"  ⚠ Deploy files directory does not exist: {deploy_files_dir}"))
            self.stdout.write(f"    Create it with: mkdir -p {deploy_files_dir}")
            
        self.stdout.write("")
        
    def check_inventory(self, config):
        """Check inventory configuration"""
        self.stdout.write(self.style.HTTP_INFO("4. Inventory"))
        self.stdout.write("-" * 40)
        
        inventory_dir = config.get_inventory_dir()
        
        if not inventory_dir.exists():
            self.warnings.append(f"Inventory directory does not exist: {inventory_dir}")
            self.stdout.write(self.style.WARNING(f"  ⚠ Inventory directory does not exist: {inventory_dir}"))
            self.stdout.write(f"    Create it with: mkdir -p {inventory_dir}")
            self.stdout.write("")
            return
            
        # Check for inventory files
        inventory_files = list(inventory_dir.glob("*.py"))
        if not inventory_files:
            self.warnings.append("No inventory files found")
            self.stdout.write(self.style.WARNING("  ⚠ No inventory files found"))
            self.stdout.write("    Create inventory files (e.g., production.py, staging.py)")
            self.stdout.write("")
            return
            
        self.stdout.write(self.style.SUCCESS(f"  ✓ Found {len(inventory_files)} inventory file(s)"))
        
        # Try to load each inventory
        for inv_file in inventory_files:
            env_name = inv_file.stem
            self.stdout.write(f"\n  Environment: {self.style.HTTP_INFO(env_name)}")
            
            try:
                hosts = load_inventory(str(inventory_dir), env_name)
                if hosts:
                    self.stdout.write(self.style.SUCCESS(f"    ✓ Loaded {len(hosts)} host(s)"))
                    for host in hosts:
                        # HostConfig is a tuple (name, config_dict)
                        if isinstance(host, tuple) and len(host) == 2:
                            host_name, host_config = host
                            ssh_hostname = host_config.get('ssh_hostname', 'unknown')
                            self.stdout.write(f"      • {host_name} ({ssh_hostname})")
                            
                            services = host_config.get('services')
                            if services:
                                self.stdout.write(f"        Services: {', '.join(services)}")
                            
                            domains = host_config.get('domains')
                            if domains:
                                self.stdout.write(f"        Domains: {len(domains)} configured")
                                if self.verbose:
                                    self.stdout.write(f"        DEBUG: domains type = {type(domains)}")
                                    for i, domain in enumerate(domains):
                                        self.stdout.write(f"        DEBUG: domain[{i}] type = {type(domain)}")
                                        if hasattr(domain, '__dict__'):
                                            self.stdout.write(f"        DEBUG: domain[{i}] = {domain.__dict__}")
                                        else:
                                            self.stdout.write(f"        DEBUG: domain[{i}] = {domain}")
                            
                            app_hostname = host_config.get('app_hostname')
                            if app_hostname:
                                self.stdout.write(f"        App Hostname: {app_hostname}")
                                if self.verbose:
                                    self.stdout.write(f"        DEBUG: app_hostname type = {type(app_hostname)}")
                                
                            backup = host_config.get('backup')
                            if backup:
                                backup_type = backup.type if hasattr(backup, 'type') else 'configured'
                                self.stdout.write(f"        Backup: {backup_type} enabled")
                        else:
                            self.stdout.write(f"      • {host} (unknown format)")
                else:
                    self.warnings.append(f"No hosts found in {env_name} inventory")
                    self.stdout.write(self.style.WARNING(f"    ⚠ No hosts defined"))
                    
            except Exception as e:
                self.errors.append(f"Failed to load {env_name} inventory: {e}")
                self.stdout.write(self.style.ERROR(f"    ✗ Failed to load: {e}"))
                
        self.stdout.write("")
        
    def check_modules(self, config):
        """Check configured modules"""
        self.stdout.write(self.style.HTTP_INFO("5. Modules"))
        self.stdout.write("-" * 40)
        
        if not config.modules:
            self.warnings.append("No modules configured")
            self.stdout.write(self.style.WARNING("  ⚠ No modules configured"))
            self.stdout.write("")
            return
            
        self.stdout.write(f"  Configured modules ({len(config.modules)}):")
        
        for module_name in config.modules:
            try:
                # Try to import the module
                if '.' in module_name:
                    module_path = module_name
                else:
                    module_path = f"djaploy.modules.{module_name}"
                    
                __import__(module_path)
                self.stdout.write(self.style.SUCCESS(f"    ✓ {module_name}"))
                
                # Check module configuration if exists
                module_config = config.get_module_config(module_name)
                if module_config and self.verbose:
                    self.stdout.write(f"      Config: {module_config}")
                    
            except ImportError as e:
                self.errors.append(f"Module {module_name} could not be imported: {e}")
                self.stdout.write(self.style.ERROR(f"    ✗ {module_name} - Import failed: {e}"))
                
        self.stdout.write("")
        
    def check_project_structure(self, config):
        """Check project structure and paths"""
        self.stdout.write(self.style.HTTP_INFO("6. Project Structure"))
        self.stdout.write("-" * 40)
        
        # Check project directory
        if config.project_dir:
            if config.project_dir.exists():
                self.stdout.write(self.style.SUCCESS(f"  ✓ Project directory: {config.project_dir}"))
            else:
                self.errors.append(f"Project directory does not exist: {config.project_dir}")
                self.stdout.write(self.style.ERROR(f"  ✗ Project directory does not exist: {config.project_dir}"))
        else:
            self.stdout.write("  • Project directory: Using current directory")
            
        # Check git directory
        if config.git_dir:
            if config.git_dir.exists():
                self.stdout.write(self.style.SUCCESS(f"  ✓ Git directory: {config.git_dir}"))
                # Check if it's actually a git repo
                git_folder = config.git_dir / '.git'
                if not git_folder.exists():
                    self.warnings.append(f"Git directory exists but .git folder not found: {config.git_dir}")
                    self.stdout.write(self.style.WARNING("    ⚠ .git folder not found"))
            else:
                self.errors.append(f"Git directory does not exist: {config.git_dir}")
                self.stdout.write(self.style.ERROR(f"  ✗ Git directory does not exist: {config.git_dir}"))
                
        # Check manage.py path
        if config.manage_py_path:
            # Check relative to git root (where poetry run python would be executed)
            git_root = config.git_dir or Path.cwd()
            manage_py_full = git_root / config.manage_py_path
            if manage_py_full.exists():
                self.stdout.write(self.style.SUCCESS(f"  ✓ manage.py found: {config.manage_py_path}"))
            else:
                self.warnings.append(f"manage.py not found at: {manage_py_full}")
                self.stdout.write(self.style.WARNING(f"  ⚠ manage.py not found at: {manage_py_full}"))
                
        self.stdout.write("")
        
    def print_summary(self):
        """Print verification summary"""
        self.stdout.write("="*60)
        self.stdout.write(self.style.MIGRATE_HEADING("VERIFICATION SUMMARY"))
        self.stdout.write("="*60)
        
        if not self.errors and not self.warnings:
            self.stdout.write(self.style.SUCCESS("\n✅ ALL CHECKS PASSED - Djaploy is properly configured!\n"))
            self.stdout.write("You're ready to deploy with:")
            self.stdout.write("  • python manage.py deploy <environment>")
            self.stdout.write("  • python manage.py configureserver <environment> <host>")
        else:
            if self.errors:
                self.stdout.write(self.style.ERROR(f"\n❌ ERRORS ({len(self.errors)}):"))
                for error in self.errors:
                    self.stdout.write(self.style.ERROR(f"  • {error}"))
                    
            if self.warnings:
                self.stdout.write(self.style.WARNING(f"\n⚠️  WARNINGS ({len(self.warnings)}):"))
                for warning in self.warnings:
                    self.stdout.write(self.style.WARNING(f"  • {warning}"))
                    
            self.stdout.write("\n" + "-"*60)
            
            if self.errors:
                self.stdout.write(self.style.ERROR("Fix the errors above before deploying."))
            else:
                self.stdout.write(self.style.WARNING("Review the warnings above. Deployment may still work."))
                
        self.stdout.write("")