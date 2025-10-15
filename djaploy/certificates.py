"""
SSL Certificate management for djaploy
"""

import os
import json
import ssl
import datetime
import re
import subprocess
import tempfile
from typing import List, Optional

from .utils import StringLike


class OpSecret(StringLike):
    """
    Lazy loading secret class for 1Password secrets
    """
    _secret_mapping = {}
    _secret_values = {}

    def __new__(cls, value):
        if not re.match(r"^/.+", value) and "op://" not in value:
            raise ValueError(
                f"Invalid secret format: {value}. Must start with / and contain op://"
            )
        OpSecret._secret_mapping[value] = cls._create_secret_reference(value)
        return super().__new__(cls)

    @staticmethod
    def _map_secrets():
        """Fetch all secrets from 1Password"""
        import shutil
        
        if not shutil.which("op"):
            import warnings
            warnings.warn(
                "1Password CLI (op) is not installed. Using empty values for secrets.",
                UserWarning
            )
            OpSecret._secret_values = {k: "" for k in OpSecret._secret_mapping.keys()}
            return
            
        secrets_as_json = json.dumps(OpSecret._secret_mapping)
        output = subprocess.run(
            ["op", "inject"], input=secrets_as_json, capture_output=True, text=True
        )
        if output.returncode != 0:
            raise ValueError(
                f"{output.stderr}\nFailed to fetch secrets: {secrets_as_json}"
            )
        OpSecret._secret_values = json.loads(output.stdout)

    @staticmethod
    def _create_secret_reference(value):
        return "{{ " + "op:/" + value + " }}"

    @property
    def data(self):
        try:
            value = OpSecret._secret_values[self._data]
        except KeyError:
            self._map_secrets()
            value = OpSecret._secret_values[self._data]
        return value

    @data.setter
    def data(self, value):
        self._data = value


class OpFilePath(StringLike):
    """
    Lazy loading file class for 1Password files
    """
    _files = {}

    def __new__(cls, value):
        if not re.match(r"^/.+", value) and "op://" not in value:
            raise ValueError(
                f"Invalid secret format: {value}. Must start with / and contain op://"
            )
        if value not in OpFilePath._files:
            import shutil
            
            if not shutil.which("op"):
                import warnings
                warnings.warn(
                    f"1Password CLI (op) is not installed. Creating empty temp file for: {value}",
                    UserWarning
                )
                keyfile = tempfile.NamedTemporaryFile(delete=False)
                keyfile.write(b'')
                keyfile.flush()
                OpFilePath._files[value] = keyfile
                return str(keyfile.name)
                
            output = subprocess.run(
                ["op", "inject"],
                input=OpSecret._create_secret_reference(value),
                capture_output=True,
                text=True,
            )

            if output.returncode != 0:
                raise ValueError(f"{output.stderr}\nFailed to fetch secrets: {value}")
            keyfile = tempfile.NamedTemporaryFile(delete=False)
            keyfile.write(output.stdout.encode())
            keyfile.write("\n".encode())
            keyfile.flush()
            OpFilePath._files[value] = keyfile
        return str(OpFilePath._files[value].name)

    @property
    def data(self):
        keyfile = OpFilePath._files[self._data]
        return keyfile.name

    @data.setter
    def data(self, value):
        self._data = value


class DnsCertificate:
    """Base class for DNS certificates"""
    
    def __init__(
        self,
        *domains: str,
        op_crt: str,
        op_key: str,
        skip_validity_check: bool = False,
        **kwargs,
    ):
        self.identifier = domains[0]
        self.domains = list(domains)
        self.op_crt = op_crt
        self.op_key = op_key
        self.skip_validity_check = skip_validity_check

    def download_cert(self, download_key: bool = False):
        """Download certificate and optionally key from 1Password"""
        try:
            return (
                OpFilePath(str(self.op_crt)),
                None if not download_key else OpFilePath(self.op_key),
            )
        except ValueError:
            return (None, None)

    @property
    def cert_file(self):
        """Get certificate file path"""
        try:
            return OpFilePath(str(self.op_crt))
        except ValueError:
            print(f"Certificate file {self.op_crt} doesn't exist in 1Password, skipping for now")
            return ""

    @property
    def key_file(self):
        """Get key file path"""
        try:
            return OpFilePath(str(self.op_key))
        except ValueError:
            print(f"Key file {self.op_key} doesn't exist in 1Password, skipping for now")
            return ""

    def upload_cert(self, crt_path: str, key_path: str, op_account: str):
        """Upload certificate and key to 1Password"""
        item_name = self.op_crt.split('/')[2]
        item_vault = self.op_crt.split('/')[1]
        crt_field = self.op_crt.split('/')[3]
        key_field = self.op_key.split('/')[3]

        for field_name, path_to_file in [(crt_field, crt_path), (key_field, key_path)]:
            escaped_field_name = re.sub(r'\.', r'\\.', field_name)
            
            if not os.path.exists(path_to_file):
                raise FileNotFoundError(f"Certificate file not found: {path_to_file}")

            command = [
                "op",
                "item",
                "edit",
                "--vault",
                item_vault,
                "--account",
                op_account,
                item_name,
                f"{escaped_field_name}[file]={path_to_file}",
            ]

            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                raise ValueError(f"Failed to upload certificate: {result.stderr}")

    def check_if_cert_valid(self, days_before_expiry: int = 10) -> bool:
        """Check if certificate is valid and not expiring soon"""
        if self.skip_validity_check:
            return True
            
        try:
            crt_file, _ = self.download_cert()
            if crt_file is None:
                raise FileNotFoundError(
                    "Cannot fetch certificate file, it doesn't exist"
                )

            cert_object = ssl._ssl._test_decode_cert(crt_file)
            expiry_date_str = cert_object["notAfter"]
            expiry_date = datetime.datetime.strptime(
                expiry_date_str, "%b %d %H:%M:%S %Y %Z"
            )
            current_date = datetime.datetime.utcnow()

            # Check if the certificate is expiring within the given number of days
            if expiry_date <= current_date + datetime.timedelta(
                days=days_before_expiry
            ):
                return False
            return True

        except FileNotFoundError as fnf_error:
            print(f"Error: {fnf_error}")
            return False
        except ssl.SSLError as ssl_error:
            print(f"Error: Failed to parse the certificate - {ssl_error}")
            return False
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return False

    def issue_cert(self, email: str, **kwargs):
        """Issue a new certificate - to be implemented by subclasses"""
        raise NotImplementedError("Method issue_cert not implemented")


class BunnyDnsCertificate(DnsCertificate):
    """Certificate using Bunny DNS for validation"""
    
    def issue_cert(
        self, 
        email: str, 
        is_staging: bool = True, 
        dns_propagate_wait_seconds: int = 10,
        bunny_api_key_secret: str = None,
        git_dir: str = None,
        project_config = None
    ):
        """Issue certificate using Bunny DNS"""
        if git_dir is None:
            git_dir = os.getcwd()
        
        # Get bunny API key from project config or parameter
        if bunny_api_key_secret is None:
            if project_config and hasattr(project_config, 'module_configs'):
                # Try to get from module config
                bunny_config = project_config.module_configs.get('bunny', {})
                bunny_api_key_secret = bunny_config.get('api_key')


            # If still None, raise an error
            if bunny_api_key_secret is None:
                raise ValueError(
                    "bunny api_key is required. Either pass it as a parameter or "
                    "configure it in project_config.module_configs['bunny']['api_key']"
                )
            
        # Setup certbot directory
        certbot_dir = os.path.join(git_dir, "certbot")
        os.makedirs(certbot_dir, exist_ok=True)

        # Create Bunny credentials file
        bunny_creds_file = os.path.join(certbot_dir, "bunny.ini")
        with open(bunny_creds_file, "w") as file:
            secret_data = f'dns_bunny_api_key = {OpSecret(bunny_api_key_secret)}'
            file.write(secret_data)

        # Set correct permissions
        os.chmod(bunny_creds_file, 400)

        # Build certbot command
        command = [
            "certbot",
            "certonly",
            "--non-interactive",
            "--agree-tos",
            "--authenticator",
            "dns-bunny",
            "--config-dir",
            os.path.join(certbot_dir, "config"),
            "--logs-dir",
            os.path.join(certbot_dir, "logs"),
            "--work-dir",
            os.path.join(certbot_dir, "work"),
            "--dns-bunny-credentials",
            bunny_creds_file,
            "--dns-bunny-propagation-seconds",
            str(dns_propagate_wait_seconds),
            "--email",
            email,
        ]

        # Add domains
        for domain in self.domains:
            command.extend(["-d", domain])

        # Staging flag for testing
        if is_staging:
            command.append("--staging")

        # Run certbot
        result = subprocess.run(command, capture_output=True, text=True)

        # Clean up credentials file
        os.remove(bunny_creds_file)

        if result.returncode != 0:
            raise ValueError(f"Failed to issue certificate: {result.stderr}")


class TailscaleDnsCertificate(DnsCertificate):
    """Certificate using Tailscale for validation"""
    
    def download_cert(self, download_key: bool = False):
        """Tailscale certificates are generated on-demand"""
        return (None, None)

    @property
    def cert_file(self):
        return ""

    @property
    def key_file(self):
        return ""

    def upload_cert(self, crt_path: str, key_path: str, op_account: str):
        """Upload certificates after Tailscale generation"""
        # Actually upload to 1Password unlike the original implementation
        return super().upload_cert(crt_path, key_path, op_account)

    def check_if_cert_valid(self, days_before_expiry: int = 10):
        """Tailscale certificates auto-renew"""
        return True

    def issue_cert(self, **kwargs):
        """Tailscale certificates are generated automatically by the system"""
        pass


class LetsEncryptCertificate(DnsCertificate):
    """Certificate using Let's Encrypt with HTTP validation"""
    
    def issue_cert(
        self,
        email: str,
        webroot_path: str = None,
        is_staging: bool = True,
        git_dir: str = None,
        project_config = None
    ):
        """Issue certificate using manual HTTP validation"""
        if git_dir is None:
            git_dir = os.getcwd()

        # Get webroot from project config if not provided
        if webroot_path is None and project_config:
            webroot_path = getattr(project_config, 'letsencrypt_webroot', '/var/www/challenges')
        elif webroot_path is None:
            webroot_path = '/var/www/challenges'

        # Display instructions to user
        print("\n" + "="*70)
        print("Let's Encrypt Manual HTTP Validation")
        print("="*70)
        print(f"\nDomains: {', '.join(self.domains)}")
        print(f"Server webroot path: {webroot_path}")
        print("\nCertbot will pause and show you challenge tokens.")
        print("You need to upload each challenge file to your server at:")
        print(f"  {webroot_path}/.well-known/acme-challenge/[TOKEN]")
        print("\nMake sure your nginx is configured to serve files from this path.")
        print("="*70 + "\n")

        # Setup certbot directory
        certbot_dir = os.path.join(git_dir, "certbot")
        os.makedirs(certbot_dir, exist_ok=True)

        # Build certbot command
        command = [
            "certbot",
            "certonly",
            "--agree-tos",
            "--manual",
            "--preferred-challenges", "http",
            "--config-dir",
            os.path.join(certbot_dir, "config"),
            "--logs-dir",
            os.path.join(certbot_dir, "logs"),
            "--work-dir",
            os.path.join(certbot_dir, "work"),
            "--email",
            email,
        ]

        # Add domains
        for domain in self.domains:
            command.extend(["-d", domain])

        # Staging flag for testing
        if is_staging:
            command.append("--staging")

        # Run certbot in interactive mode
        result = subprocess.run(command, capture_output=False, text=True)

        if result.returncode != 0:
            raise ValueError(f"Failed to issue certificate (exit code: {result.returncode})")


def discover_certificates(certificates_module_path: str) -> List[DnsCertificate]:
    """Discover certificates from a project's certificates module"""
    import importlib.util
    
    if not os.path.exists(certificates_module_path):
        return []
    
    spec = importlib.util.spec_from_file_location("certificates", certificates_module_path)
    certificates_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(certificates_module)
    
    certificates = []
    if hasattr(certificates_module, 'all_certificates'):
        certificates.extend(certificates_module.all_certificates)
    elif hasattr(certificates_module, 'certificates'):
        certificates.extend(certificates_module.certificates)
    
    return certificates