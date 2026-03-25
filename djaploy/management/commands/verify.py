"""
Verify djaploy configuration command
"""

import sys
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings

from djaploy.discovery import find_infra_file, get_app_infra_dirs


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

        # 2. Check discovery
        self.check_configuration()

        # 3. Check inventory
        self.check_inventory()

        # 4. Check apps
        self.check_modules()

        # 5. Check project structure
        self.check_project_structure()
        
        # Print summary
        self.print_summary()
        
        # Exit with appropriate code
        if self.errors:
            sys.exit(1)
        
    def check_django_settings(self):
        """Check Django settings required by djaploy"""
        self.stdout.write(self.style.HTTP_INFO("1. Django Settings"))
        self.stdout.write("-" * 40)

        base_dir = getattr(settings, 'BASE_DIR', None)
        if base_dir:
            self.stdout.write(self.style.SUCCESS(f"  ✓ BASE_DIR: {base_dir}"))
        else:
            self.errors.append("BASE_DIR not set in Django settings")
            self.stdout.write(self.style.ERROR("  ✗ BASE_DIR not set"))

        git_dir = getattr(settings, 'GIT_DIR', None)
        if git_dir:
            self.stdout.write(self.style.SUCCESS(f"  ✓ GIT_DIR: {git_dir}"))
        else:
            self.warnings.append("GIT_DIR not set — artifact creation and versioning will fail")
            self.stdout.write(self.style.WARNING("  ⚠ GIT_DIR not set in settings"))

        artifact_dir = getattr(settings, 'ARTIFACT_DIR', 'deployment')
        self.stdout.write(f"  • ARTIFACT_DIR: {artifact_dir}")

        self.stdout.write("")
        
    def check_configuration(self):
        """Check djaploy discovery and settings"""
        self.stdout.write(self.style.HTTP_INFO("2. Discovery"))
        self.stdout.write("-" * 40)

        try:
            hooks_path = find_infra_file("djaploy_hooks.py")
            if hooks_path:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Hooks found: {hooks_path}"))
            else:
                self.warnings.append("No djaploy_hooks.py found via discovery")
                self.stdout.write(self.style.WARNING("  ⚠ No djaploy_hooks.py found in INSTALLED_APPS infra/ directories"))

            git_dir = getattr(settings, 'GIT_DIR', None)
            if git_dir:
                self.stdout.write(self.style.SUCCESS(f"  ✓ GIT_DIR: {git_dir}"))
            else:
                self.warnings.append("GIT_DIR not set in Django settings")
                self.stdout.write(self.style.WARNING("  ⚠ GIT_DIR not set in Django settings"))
                return None
                
        except Exception as e:
            self.errors.append(f"Failed to load configuration: {e}")
            self.stdout.write(self.style.ERROR(f"  ✗ Failed to load configuration: {e}"))
            return None
            
        self.stdout.write("")

    def check_inventory(self):
        """Check inventory via discovery"""
        self.stdout.write(self.style.HTTP_INFO("3. Inventory"))
        self.stdout.write("-" * 40)

        from djaploy.deploy import _load_inventory_hosts

        # Find all inventory dirs
        inventory_files = []
        for app_label, infra_dir in get_app_infra_dirs():
            inv_dir = infra_dir / "inventory"
            if inv_dir.is_dir():
                inventory_files.extend(inv_dir.glob("*.py"))

        inv_files = [f for f in inventory_files if not f.name.startswith("_")]
        if not inv_files:
            self.warnings.append("No inventory files found")
            self.stdout.write(self.style.WARNING("  ⚠ No inventory files found"))
            self.stdout.write("")
            return

        self.stdout.write(self.style.SUCCESS(f"  ✓ Found {len(inv_files)} inventory file(s)"))

        for inv_file in inv_files:
            env_name = inv_file.stem
            self.stdout.write(f"\n  Environment: {self.style.HTTP_INFO(env_name)}")

            try:
                hosts = _load_inventory_hosts(str(inv_file))
                if hosts:
                    self.stdout.write(self.style.SUCCESS(f"    ✓ Loaded {len(hosts)} host(s)"))
                    for host in hosts:
                        if isinstance(host, tuple) and len(host) == 2:
                            host_name, host_config = host
                            ssh_hostname = host_config.get('ssh_hostname', 'unknown') if isinstance(host_config, dict) else getattr(host_config, 'ssh_hostname', 'unknown')
                            self.stdout.write(f"      {host_name} ({ssh_hostname})")
                else:
                    self.warnings.append(f"No hosts found in {env_name} inventory")
                    self.stdout.write(self.style.WARNING(f"    ⚠ No hosts defined"))
            except Exception as e:
                self.errors.append(f"Failed to load {env_name} inventory: {e}")
                self.stdout.write(self.style.ERROR(f"    ✗ Failed to load: {e}"))

        self.stdout.write("")

    def check_modules(self):
        """Check discovered djaploy apps"""
        self.stdout.write(self.style.HTTP_INFO("4. Apps"))
        self.stdout.write("-" * 40)

        apps_dir = Path(__file__).resolve().parent.parent.parent / "apps"
        if apps_dir.is_dir():
            apps = sorted(
                d.name for d in apps_dir.iterdir()
                if d.is_dir() and not d.name.startswith("_")
                and (d / "infra" / "djaploy_hooks.py").is_file()
            )
            self.stdout.write(f"  Discovered apps ({len(apps)}):")
            for app_name in apps:
                self.stdout.write(self.style.SUCCESS(f"    ✓ {app_name}"))
        else:
            self.warnings.append("No apps directory found")
            self.stdout.write(self.style.WARNING("  ⚠ No apps directory found"))

        self.stdout.write("")

    def check_project_structure(self):
        """Check project structure and paths"""
        self.stdout.write(self.style.HTTP_INFO("5. Project Structure"))
        self.stdout.write("-" * 40)

        base_dir = Path(settings.BASE_DIR)
        self.stdout.write(self.style.SUCCESS(f"  ✓ BASE_DIR: {base_dir}"))

        git_dir = getattr(settings, 'GIT_DIR', None)
        if git_dir:
            git_path = Path(git_dir)
            if git_path.exists():
                self.stdout.write(self.style.SUCCESS(f"  ✓ GIT_DIR: {git_dir}"))
                if not (git_path / '.git').exists():
                    self.warnings.append(f"GIT_DIR exists but .git folder not found: {git_dir}")
                    self.stdout.write(self.style.WARNING("    ⚠ .git folder not found"))
            else:
                self.errors.append(f"GIT_DIR does not exist: {git_dir}")
                self.stdout.write(self.style.ERROR(f"  ✗ GIT_DIR does not exist: {git_dir}"))

        self.stdout.write("")
        
    def print_summary(self):
        """Print verification summary"""
        self.stdout.write("="*60)
        self.stdout.write(self.style.MIGRATE_HEADING("VERIFICATION SUMMARY"))
        self.stdout.write("="*60)
        
        if not self.errors and not self.warnings:
            self.stdout.write(self.style.SUCCESS("\n✅ ALL CHECKS PASSED - Djaploy is properly configured!\n"))
            self.stdout.write("You're ready to deploy with:")
            self.stdout.write("  • python manage.py djaploy deploy --env <environment>")
            self.stdout.write("  • python manage.py djaploy configure --env <environment>")
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