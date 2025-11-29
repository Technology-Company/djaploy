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
import importlib.util
from pathlib import Path
from typing import List, Dict, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import DjaployConfig

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
            escaped_field_name = field_name.replace('.', '\\.')

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


class SshHttpHook:
    """
    SSH-based HTTP challenge hook for Let's Encrypt certificates.

    Automatically discovers which host serves a domain by scanning inventory files,
    then uses SSH to place/remove challenge files for HTTP-01 validation.

    Configuration precedence (highest to lowest):
    1. Instance-level (passed to SshHttpHook.__init__)
    2. Host-level (in HostConfig's http_hook dict)
    3. Project-level (DjaployConfig.module_configs['http_hook'])
    4. Defaults

    Example usage in project config:
        module_configs={
            'http_hook': {
                'webroot_path': '/var/www/challenges',
                'use_sudo': True,
                'file_group': 'www-data',
            },
        }

    Example usage in inventory:
        HostConfig(
            'my-server',
            ssh_hostname='...',
            http_hook={
                'webroot_path': '/custom/path',
            },
        )
    """

    DEFAULT_WEBROOT = '/var/www/challenges'

    def __init__(
        self,
        djaploy_dir: Path = None,
        project_config: "DjaployConfig" = None,
        # Overridable settings
        webroot_path: str = None,
        use_sudo: bool = None,
        file_owner: str = None,
        file_group: str = None,
        file_mode: str = '0644',
    ):
        """
        Initialize SSH HTTP hook.

        Args:
            djaploy_dir: Path to the djaploy configuration directory (contains inventory/)
            project_config: DjaployConfig instance for project-level settings
            webroot_path: Web server path where challenges are served from
            use_sudo: Whether to use sudo for file operations
            file_owner: Owner for challenge files (defaults to host's ssh_user)
            file_group: Group for challenge files
            file_mode: Permission mode for challenge files
        """
        self.djaploy_dir = Path(djaploy_dir) if djaploy_dir else None
        self.project_config = project_config

        # Instance-level overrides
        self._webroot_path = webroot_path
        self._use_sudo = use_sudo
        self._file_owner = file_owner
        self._file_group = file_group
        self._file_mode = file_mode

        # Cache for host lookups
        self._host_cache: Dict[str, Tuple[str, Dict, str]] = {}

    def _load_inventory(self, inventory_file: Path) -> List[Tuple[str, Dict]]:
        """Load hosts from an inventory file"""
        import sys

        spec = importlib.util.spec_from_file_location("inventory", str(inventory_file))
        inventory_module = importlib.util.module_from_spec(spec)

        original_path = sys.path[:]
        try:
            # Add djaploy_dir and its parent to path so imports work
            # Parent is needed for imports like 'from infra.certificates import x'
            # when djaploy_dir is /project/app/infra
            if self.djaploy_dir:
                sys.path.insert(0, str(self.djaploy_dir.parent))
                sys.path.insert(0, str(self.djaploy_dir))

            sys.modules['inventory'] = inventory_module
            spec.loader.exec_module(inventory_module)

            hosts = getattr(inventory_module, 'hosts', [])

            # Convert to list of (name, data) tuples
            result = []
            for host in hosts:
                if isinstance(host, tuple) and len(host) == 2:
                    result.append(host)

            return result
        finally:
            sys.path[:] = original_path
            if 'inventory' in sys.modules:
                del sys.modules['inventory']

    def find_host_for_domain(self, domain: str) -> Tuple[str, Dict[str, Any], str]:
        """
        Find the host that serves a given domain.

        Returns:
            Tuple of (host_name, host_data, environment_name)

        Raises:
            ValueError: If no host found for the domain
        """
        # Check cache first
        if domain in self._host_cache:
            return self._host_cache[domain]

        # Determine inventory directory
        inventory_dir = None
        if self.djaploy_dir:
            inventory_dir = self.djaploy_dir / 'inventory'
        elif self.project_config and self.project_config.djaploy_dir:
            inventory_dir = self.project_config.djaploy_dir / 'inventory'

        if not inventory_dir or not inventory_dir.exists():
            raise ValueError(
                f"Cannot find inventory directory. "
                f"Provide djaploy_dir or project_config with djaploy_dir set."
            )

        # Scan all inventory files
        for inv_file in inventory_dir.glob('*.py'):
            if inv_file.name.startswith('_'):
                continue

            env_name = inv_file.stem

            try:
                hosts = self._load_inventory(inv_file)
            except Exception:
                # Silently skip inventories that fail to load
                # (they may reference certificates not relevant to this domain)
                continue

            for host_name, host_data in hosts:
                # Check domains in host_data
                host_domains = host_data.get('domains', [])

                for domain_cert in host_domains:
                    # Handle both certificate objects and dicts
                    cert_domains = []
                    if hasattr(domain_cert, 'domains'):
                        cert_domains = domain_cert.domains
                    elif hasattr(domain_cert, 'identifier'):
                        cert_domains = [domain_cert.identifier]
                    elif isinstance(domain_cert, dict):
                        cert_domains = domain_cert.get('domains', [])
                        if not cert_domains and domain_cert.get('identifier'):
                            cert_domains = [domain_cert['identifier']]

                    if domain in cert_domains:
                        result = (host_name, host_data, env_name)
                        self._host_cache[domain] = result
                        return result

                # Also check app_hostname
                if host_data.get('app_hostname') == domain:
                    result = (host_name, host_data, env_name)
                    self._host_cache[domain] = result
                    return result

        raise ValueError(f"No host found for domain: {domain}")

    def _get_config_for_host(self, host_data: Dict[str, Any]) -> Dict[str, Any]:
        """Merge configuration from all levels for a specific host"""
        # Start with defaults
        config = {
            'webroot_path': self.DEFAULT_WEBROOT,
            'use_sudo': False,
            'file_owner': None,
            'file_group': 'www-data',
            'file_mode': '0644',
        }

        # Layer 1: Project-level config
        if self.project_config:
            hook_config = self.project_config.module_configs.get('http_hook', {})
            for k, v in hook_config.items():
                if v is not None:
                    config[k] = v

        # Layer 2: Host-level config
        host_hook_config = host_data.get('http_hook', {})
        if isinstance(host_hook_config, dict):
            for k, v in host_hook_config.items():
                if v is not None:
                    config[k] = v

        # Layer 3: Instance-level overrides
        if self._webroot_path is not None:
            config['webroot_path'] = self._webroot_path
        if self._use_sudo is not None:
            config['use_sudo'] = self._use_sudo
        if self._file_owner is not None:
            config['file_owner'] = self._file_owner
        if self._file_group is not None:
            config['file_group'] = self._file_group
        if self._file_mode is not None:
            config['file_mode'] = self._file_mode

        # Default file_owner to ssh_user if not specified
        if not config['file_owner']:
            config['file_owner'] = host_data.get('ssh_user', 'www-data')

        return config

    def generate_hook_scripts(self, certbot_dir: str, domain: str) -> Tuple[str, str]:
        """
        Generate auth and cleanup hook scripts for certbot.

        Generates simple bash scripts with SSH commands baked in.

        Args:
            certbot_dir: Directory to write the hook scripts
            domain: The domain to generate hooks for (used to resolve host)

        Returns:
            Tuple of (auth_hook_path, cleanup_hook_path)
        """
        os.makedirs(certbot_dir, exist_ok=True)

        # Resolve host details at generation time
        host_name, host_data, env_name = self.find_host_for_domain(domain)
        config = self._get_config_for_host(host_data)

        ssh_host = host_data['ssh_hostname']
        ssh_user = host_data.get('ssh_user', 'deploy')
        ssh_port = host_data.get('ssh_port', 22)
        ssh_key = host_data.get('ssh_key', '')
        sudo_password = host_data.get('_sudo_password', '')
        webroot = config['webroot_path']
        use_sudo = config['use_sudo']
        file_owner = config['file_owner']
        file_group = config['file_group']
        file_mode = config['file_mode']

        # Build SSH command prefix
        ssh_opts = f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {ssh_port}"
        if ssh_key:
            ssh_key_expanded = os.path.expanduser(ssh_key)
            ssh_opts += f" -i {ssh_key_expanded}"

        ssh_cmd = f"ssh {ssh_opts} {ssh_user}@{ssh_host}"

        # Build sudo prefix (with -S for password via stdin if needed)
        if use_sudo and sudo_password:
            sudo_prefix = f"echo '{sudo_password}' | sudo -S"
        elif use_sudo:
            sudo_prefix = "sudo"
        else:
            sudo_prefix = ""

        # Build the commands
        if use_sudo:
            write_cmd = f'echo \\"$CERTBOT_VALIDATION\\" | {sudo_prefix} tee {{webroot}}/$CERTBOT_TOKEN > /dev/null'
            mkdir_cmd = f"{sudo_prefix} mkdir -p {webroot}"
            chown_cmd = f"{sudo_prefix} chown {file_owner}:{file_group} {webroot}/$CERTBOT_TOKEN"
            chmod_cmd = f"{sudo_prefix} chmod {file_mode} {webroot}/$CERTBOT_TOKEN"
            rm_cmd = f"{sudo_prefix} rm -f {webroot}/$CERTBOT_TOKEN"
        else:
            write_cmd = 'echo \\"$CERTBOT_VALIDATION\\" > {webroot}/$CERTBOT_TOKEN'
            mkdir_cmd = f"mkdir -p {webroot}"
            chown_cmd = ""
            chmod_cmd = f"chmod {file_mode} {webroot}/$CERTBOT_TOKEN"
            rm_cmd = f"rm -f {webroot}/$CERTBOT_TOKEN"

        write_cmd = write_cmd.format(webroot=webroot)

        # Build combined remote command (single SSH call for speed)
        remote_cmds = [mkdir_cmd, write_cmd]
        if chown_cmd:
            remote_cmds.append(chown_cmd)
        remote_cmds.append(chmod_cmd)
        combined_cmd = " && ".join(remote_cmds)

        # Auth hook script
        auth_script = f'''#!/bin/bash
# Auto-generated certbot auth hook for {domain}
# Host: {host_name} ({env_name})
set -e

echo "Placing challenge for $CERTBOT_DOMAIN on {host_name} ({env_name})"

{ssh_cmd} "{combined_cmd}"

echo "  Challenge file created: {webroot}/$CERTBOT_TOKEN"
'''

        auth_hook_path = os.path.join(certbot_dir, 'auth_hook.sh')
        with open(auth_hook_path, 'w') as f:
            f.write(auth_script)
        os.chmod(auth_hook_path, 0o755)

        # Cleanup hook script
        cleanup_script = f'''#!/bin/bash
# Auto-generated certbot cleanup hook for {domain}
# Host: {host_name} ({env_name})

echo "Cleaning up challenge for $CERTBOT_DOMAIN on {host_name}"

{ssh_cmd} "{rm_cmd}" || echo "  Warning: Failed to remove challenge file"

echo "  Challenge file removed: {webroot}/$CERTBOT_TOKEN"
'''

        cleanup_hook_path = os.path.join(certbot_dir, 'cleanup_hook.sh')
        with open(cleanup_hook_path, 'w') as f:
            f.write(cleanup_script)
        os.chmod(cleanup_hook_path, 0o755)

        return auth_hook_path, cleanup_hook_path


class LetsEncryptCertificate(DnsCertificate):
    """Certificate using Let's Encrypt with HTTP validation"""

    def __init__(
        self,
        *domains: str,
        op_crt: str,
        op_key: str,
        skip_validity_check: bool = False,
        use_webroot: bool = False,
        http_hook: SshHttpHook = None,
        **kwargs,
    ):
        """
        Initialize Let's Encrypt certificate

        Args:
            use_webroot: If True, use webroot plugin for automated validation.
                        Requires certbot to run on the same server as the webroot.
            http_hook: SshHttpHook instance for automated SSH-based HTTP validation.
        """
        super().__init__(*domains, op_crt=op_crt, op_key=op_key, skip_validity_check=skip_validity_check, **kwargs)
        self.use_webroot = use_webroot
        self.http_hook = http_hook

    def issue_cert(
        self,
        email: str,
        webroot_path: str = None,
        is_staging: bool = True,
        git_dir: str = None,
        project_config: "DjaployConfig" = None,
        use_ssh_hook: bool = None,
    ):
        """
        Issue certificate using HTTP validation

        Modes (in order of precedence):
        1. http_hook provided or use_ssh_hook=True: SSH-based automated validation
        2. use_webroot=True: Webroot plugin (certbot writes directly)
        3. Neither: Interactive manual mode

        Args:
            email: Email for Let's Encrypt registration
            webroot_path: Path to webroot for challenge files
            is_staging: Use Let's Encrypt staging environment
            git_dir: Directory for certbot files (defaults to cwd)
            project_config: DjaployConfig for project-level settings
            use_ssh_hook: Force SSH hook mode even without http_hook set.
                         If True and no http_hook is set, creates one from project_config.
        """
        if git_dir is None:
            git_dir = os.getcwd()

        # Get webroot from project config if not provided
        if webroot_path is None and project_config:
            webroot_path = getattr(project_config, 'letsencrypt_webroot', '/var/www/challenges')
        elif webroot_path is None:
            webroot_path = '/var/www/challenges'

        # Setup certbot directory
        certbot_dir = os.path.join(git_dir, "certbot")
        os.makedirs(certbot_dir, exist_ok=True)

        # Determine if we should use SSH hook mode
        http_hook = self.http_hook
        if use_ssh_hook and not http_hook and project_config:
            # Auto-create SshHttpHook from project config
            http_hook = SshHttpHook(
                djaploy_dir=project_config.djaploy_dir,
                project_config=project_config,
            )

        if http_hook:
            # SSH hook mode - generate hook scripts
            print(f"Issuing certificate using SSH HTTP hook")
            print(f"  Domains: {', '.join(self.domains)}")

            # Pass project_config to hook if not already set
            if not http_hook.project_config and project_config:
                http_hook.project_config = project_config

            # Generate hook scripts (use first domain for host resolution)
            primary_domain = self.domains[0]
            auth_hook_path, cleanup_hook_path = http_hook.generate_hook_scripts(certbot_dir, primary_domain)
            print(f"  Auth hook: {auth_hook_path}")
            print(f"  Cleanup hook: {cleanup_hook_path}")

            command = [
                "certbot",
                "certonly",
                "--non-interactive",
                "--agree-tos",
                "--manual",
                "--preferred-challenges", "http",
                "--manual-auth-hook", auth_hook_path,
                "--manual-cleanup-hook", cleanup_hook_path,
                "--config-dir",
                os.path.join(certbot_dir, "config"),
                "--logs-dir",
                os.path.join(certbot_dir, "logs"),
                "--work-dir",
                os.path.join(certbot_dir, "work"),
                "--email",
                email,
            ]

        elif self.use_webroot:
            # Automated webroot mode - requires access to web server directory
            print(f"Issuing certificate using webroot: {webroot_path}")
            command = [
                "certbot",
                "certonly",
                "--non-interactive",
                "--agree-tos",
                "--webroot",
                "--webroot-path", webroot_path,
                "--config-dir",
                os.path.join(certbot_dir, "config"),
                "--logs-dir",
                os.path.join(certbot_dir, "logs"),
                "--work-dir",
                os.path.join(certbot_dir, "work"),
                "--email",
                email,
            ]
        else:
            # Manual interactive mode - requires user to upload challenge files
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

        # Run certbot - capture output for automated modes
        is_automated = bool(http_hook) or self.use_webroot
        result = subprocess.run(command, capture_output=is_automated, text=True)

        if result.returncode != 0:
            error_msg = result.stderr if is_automated else f"exit code: {result.returncode}"
            raise ValueError(f"Failed to issue certificate: {error_msg}")


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