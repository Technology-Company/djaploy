"""
Django management command for updating SSL certificates
"""

import os
from pathlib import Path

from django.core.management import BaseCommand, CommandError
from django.conf import settings

from djaploy.certificates import discover_certificates, TailscaleDnsCertificate
from djaploy.discovery import find_infra_file
from djaploy.management.utils import find_git_root


class Command(BaseCommand):
    help = "Issue and upload SSL certificates to 1Password if expired or missing"
    
    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            required=True,
            help="Email address for certificate registration",
        )
        
        parser.add_argument(
            "--days-before-expiry",
            type=int,
            default=30,
            help="Days before expiry to renew certificate (default: 30)",
        )
        
        parser.add_argument(
            "--staging",
            action="store_true",
            help="Use staging certificates (for testing)",
        )
        
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force renewal of all certificates regardless of expiry",
        )
    
    def handle(self, *args, **options):
        email = options["email"]
        days_before_expiry = options["days_before_expiry"]
        is_staging = options["staging"]
        force_renewal = options["force"]
        
        os.environ['OP_ACCOUNT'] = settings.OP_ACCOUNT

        # Discover certificates from project
        certificates_path = find_infra_file("certificates.py")
        if not certificates_path:
            raise CommandError(
                "No certificates.py found in any INSTALLED_APPS infra/ directory"
            )
        
        certificates = discover_certificates(str(certificates_path))
        
        if not certificates:
            self.stdout.write("No certificates found to manage")
            return
        
        self.stdout.write(f"Checking {len(certificates)} certificates...")
        
        renewed_count = 0
        skipped_count = 0
        
        # Process each certificate
        for cert in certificates:
            domain_list = " ".join(cert.domains)
            
            # Check if certificate needs renewal
            if not force_renewal and cert.check_if_cert_valid(days_before_expiry=days_before_expiry):
                self.stdout.write(f"Certificate valid for domains: {domain_list}")
                skipped_count += 1
                continue
            
            self.stdout.write(f"Renewing certificate for domains: {domain_list}")
            
            try:
                # Issue new certificate
                if isinstance(cert, TailscaleDnsCertificate):
                    # Tailscale certificates need special handling
                    self._handle_tailscale_cert(cert, email, is_staging)
                else:
                    # Standard certificate issuance
                    git_dir = str(getattr(settings, 'GIT_DIR', find_git_root(Path(settings.BASE_DIR))))
                    cert.issue_cert(
                        email=email,
                        is_staging=is_staging,
                        git_dir=git_dir,
                        djaploy_dir=certificates_path.parent,
                        force_renewal=force_renewal,
                    )
                    
                    # Upload to 1Password
                    primary_domain = cert.domains[0]
                    cert_path = os.path.join(git_dir, f"certbot/config/live/{primary_domain}/fullchain.pem")
                    key_path = os.path.join(git_dir, f"certbot/config/live/{primary_domain}/privkey.pem")
                    
                    cert.upload_cert(
                        crt_path=cert_path,
                        key_path=key_path,
                        op_account=os.environ['OP_ACCOUNT']
                    )
                
                self.stdout.write(
                    self.style.SUCCESS(f"Successfully renewed certificate for: {domain_list}")
                )
                renewed_count += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Failed to renew certificate for {domain_list}: {e}")
                )
        
        # Summary
        self.stdout.write(
            f"\nCertificate update complete: {renewed_count} renewed, {skipped_count} skipped"
        )
        
        if renewed_count > 0:
            self.stdout.write(
                self.style.SUCCESS("Run 'python manage.py sync_certs --env <env>' to deploy certificates to servers")
            )
    
    def _handle_tailscale_cert(self, cert, email, is_staging):
        """Handle Tailscale certificate generation and upload"""
        
        # For Tailscale, certificates are generated on the target machine
        # Here we would typically trigger the generation process
        self.stdout.write(
            f"Tailscale certificate for {cert.identifier} - certificates are auto-generated on target machines"
        )
        
        # If you need to upload existing Tailscale certs to 1Password, you would do it here
        # This would require the certificates to already exist on the local machine