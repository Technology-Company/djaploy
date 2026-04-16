"""
Configuration management for djaploy.

All deployment configuration lives on HostConfig.  Project-level settings
(GIT_DIR, BASE_DIR, ARTIFACT_DIR) come from Django settings.
"""

import typing
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


class HostConfigMetaclass(type):
    def __new__(cls, name, bases, attrs):
        dict_typing = attrs.get("__annotations__", {})
        defaults = {}
        # Capture defaults from annotated fields (e.g., field: Type = default)
        for key in dict_typing:
            if key in attrs and not key.startswith("_"):
                defaults[key] = attrs.pop(key)
        # Capture defaults from non-annotated fields
        for key, value in list(attrs.items()):
            if not key.startswith("_") and not callable(value) and key not in ("__module__", "__qualname__"):
                defaults[key] = attrs.pop(key)
        attrs["_dict_annotations"] = dict_typing
        attrs["_dict_defaults"] = defaults
        return super().__new__(cls, name, bases, attrs)


def is_optional(field):
    """Check if a type hint is Optional"""
    return typing.get_origin(field) is typing.Union and \
           type(None) in typing.get_args(field)


@dataclass
class BackupConfig:
    """Backup configuration for a host"""

    enabled: bool = True
    type: str = "sftp"  # sftp or s3

    # Connection settings
    host: Optional[str] = None  # For SFTP
    user: Optional[str] = None  # For SFTP
    password: Optional[str] = None  # For SFTP
    port: int = 22  # For SFTP

    # S3 settings
    s3_endpoint: Optional[str] = None
    s3_region: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_bucket: Optional[str] = None

    # Backup settings
    backup_path: str = "/backups"  # Remote path for backups
    retention_days: int = 30
    databases: List[str] = field(default_factory=lambda: ["default.db"])
    backup_media: bool = True

    # Local paths (defaults will be computed based on app_user if not set)
    db_path: Optional[str] = None  # e.g., /home/{app_user}/dbs
    media_path: Optional[str] = None  # e.g., /home/{app_user}/apps/{project}/media

    # Schedule (cron format)
    schedule: str = "0 2 * * *"  # Daily at 2 AM by default
    
    def validate(self):
        """Validate backup configuration"""
        if self.type == "sftp":
            if not all([self.host, self.user]):
                raise ValueError("SFTP backup requires host and user")
        elif self.type == "s3":
            if not all([self.s3_endpoint, self.s3_access_key, self.s3_secret_key, self.s3_bucket]):
                raise ValueError("S3 backup requires endpoint, access_key, secret_key, and bucket")
        else:
            raise ValueError(f"Invalid backup type: {self.type}")
        return True


@dataclass
class BorgBackupConfig:
    """Borg backup configuration for a host"""

    enabled: bool = True

    # Remote repository settings (ssh-based)
    repo_host: Optional[str] = None        # SSH hostname of backup server
    repo_user: Optional[str] = None        # SSH user on backup server
    repo_port: int = 22                    # SSH port
    repo_path: Optional[str] = None        # Path on remote server; defaults to ./backups
    ssh_key: Optional[str] = None          # Path to SSH key on target host (if not default)
    ssh_known_hosts_file: Optional[str] = None  # Path to known_hosts file on target host for host verification
    deploy_key: Optional[str] = None        # Local path to private key to deploy to target (e.g. OpFilePath)

    # Encryption
    passphrase: Optional[str] = None       # Borg repo passphrase (BORG_PASSPHRASE)

    # Compression
    compression: str = "zstd,3"            # Borg compression algorithm (lz4, zstd, zlib, etc.)

    # Backup settings
    databases: List[str] = field(default_factory=lambda: ["default.db"])
    backup_media: bool = True

    # Local paths (defaults computed from app_user if not set)
    db_path: Optional[str] = None
    media_path: Optional[str] = None

    # Retention policy (borg prune)
    keep_within: Optional[str] = None  # Keep all archives within this period (e.g. "2d", "48H")
    keep_hourly: int = 0               # Number of hourly archives to keep
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 6

    # Remote borg version (for Hetzner Storage Boxes: "borg-1.1", "borg-1.2", "borg-1.4")
    # When set, adds --remote-path to all borg commands to select the server-side version.
    remote_path: Optional[str] = None

    # Schedule (cron format)
    schedule: str = "0 2 * * *"

    def validate(self):
        if not self.passphrase:
            raise ValueError("Borg backup requires a passphrase")
        return True


class HostConfig(tuple, metaclass=HostConfigMetaclass):
    """
    Configuration for a deployment host.
    Creates pyinfra-compatible tuples (hostname, host_data).
    """
    
    # Type annotations for the metaclass
    ssh_hostname: str
    ssh_user: str = "deploy"
    ssh_port: Optional[int] = 22
    ssh_key: Optional[str] = None
    ssh_known_hosts_file: Optional[str] = None  # Path to known_hosts file for SSH host verification
    ssh_connect_timeout: int = 10  # SSH connection timeout in seconds
    _sudo_password: Optional[str] = None
    
    app_user: str = "app"
    app_hostname: Optional[str] = None
    app_name: str  # Deployment name on this host (e.g. "myapp", "myapp-staging")
    
    # Services to manage on this host
    services: Optional[List[str]] = None
    timer_services: Optional[List[str]] = None
    
    # Domain configurations
    domains: Optional[List[Dict[str, Any]]] = None
    
    pregenerate_certificates: Optional[bool] = False
    
    # Backup configuration for this host
    backup: Optional[BackupConfig] = None

    # Borg backup configuration for this host
    borg_backup: Optional[BorgBackupConfig] = None

    # Additional host-specific data
    data: Optional[Dict[str, Any]] = None

    # Environment identifier (e.g., 'production', 'staging', 'dev')
    env: Optional[str] = None

    # Deployment settings
    python_version: str = "3.11"
    python_compile: bool = False  # Compile Python from source
    deployment_strategy: str = "zero_downtime"  # "in_place" or "zero_downtime"
    keep_releases: int = 5  # Releases to keep (zero_downtime only)
    manage_py_path: str = "manage.py"  # Relative path to manage.py in the artifact
    db_dir: Optional[str] = None  # External database directory template
    generate_local_settings: bool = False  # Generate local.py with DATABASES, ALLOWED_HOSTS, etc.
    shared_resources: Optional[List[str]] = None  # Paths to symlink from shared/

    # Per-module configuration (merged with defaults)
    gunicorn_conf: Optional[Dict[str, Any]] = None  # workers, timeout, wsgi_module
    nginx_conf: Optional[Dict[str, Any]] = None  # server_name, listen, client_max_body_size
    core_conf: Optional[Dict[str, Any]] = None  # poetry_no_root, exclude_groups, poetry_lock, databases
    versioning_conf: Optional[Dict[str, Any]] = None  # increment_type, tag_environments, push_tags
    notifications_conf: Optional[Dict[str, Any]] = None  # webhook_url, display_name, changelog_generator, etc.
    artifact_conf: Optional[Dict[str, Any]] = None  # extra_files
    http_hook_conf: Optional[Dict[str, Any]] = None  # webroot_path

    def __new__(cls, name: str, **kwargs):
        dict_typing = cls._dict_annotations
        dict_defaults = cls._dict_defaults.copy()

        config = {}
        for key, type_hint in dict_typing.items():
            default = dict_defaults.pop(key, None)
            value = kwargs.pop(key, None)
            if value is None and default is not None:
                value = default
            if value is None and is_optional(type_hint):
                continue
            if value is None:
                raise ValueError(f"Missing required key: {key}")
            config[key] = value

        # Add any remaining defaults
        for key in dict_defaults:
            config[key] = dict_defaults[key]

        # Add any extra kwargs
        for key in kwargs:
            config[key] = kwargs[key]

        # Validate deployment_strategy
        strategy = config.get("deployment_strategy", "zero_downtime")
        if strategy not in ("in_place", "zero_downtime", "bluegreen"):
            raise ValueError(
                f"Invalid deployment_strategy: {strategy!r}. "
                f"Must be 'in_place', 'zero_downtime', or 'bluegreen'"
            )

        # Expand SSH key path if provided
        if config.get("ssh_key"):
            import os
            config["ssh_key"] = os.path.expanduser(config["ssh_key"])

        # Map ssh_connect_timeout into pyinfra's ssh_paramiko_connect_kwargs
        timeout = config.pop("ssh_connect_timeout", None)
        if timeout is not None:
            paramiko_kwargs = config.get("ssh_paramiko_connect_kwargs", {})
            paramiko_kwargs.setdefault("timeout", timeout)
            config["ssh_paramiko_connect_kwargs"] = paramiko_kwargs

        config["name"] = name

        return super().__new__(cls, (name, config))