"""
Configuration management for djaploy
"""

import typing
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, get_origin
from pathlib import Path


class HostConfigMetaclass(type):
    def __new__(cls, name, bases, attrs):
        dict_typing = attrs.get("__annotations__", {})
        defaults = {
            key: attrs.pop(key) for key, value in list(attrs.items()) 
            if not key.startswith("_") and not callable(value) and key not in dict_typing
        }
        attrs["_dict_annotations"] = dict_typing
        attrs["_dict_defaults"] = defaults
        return super().__new__(cls, name, bases, attrs)


def is_optional(field):
    """Check if a type hint is Optional"""
    return typing.get_origin(field) is typing.Union and \
           type(None) in typing.get_args(field)


@dataclass
class DjaployConfig:
    """Main configuration class for djaploy deployments"""
    
    # Project settings
    project_name: str
    project_dir: Optional[Path] = None
    git_dir: Optional[Path] = None
    
    # Djaploy directory settings
    djaploy_dir: Optional[Path] = None  # Contains config.py, deploy_files/, inventory/
    manage_py_path: Optional[Path] = None  # Relative project path to manage.py file
    
    # Server settings
    app_user: str = "app"
    ssh_user: str = "deploy"
    
    # Python settings
    python_version: str = "3.11"
    python_compile: bool = False  # Whether to compile Python from source
    
    # Modules to enable
    modules: List[str] = field(default_factory=lambda: [
        "djaploy.modules.core",
        "djaploy.modules.nginx", 
        "djaploy.modules.systemd"
    ])
    
    # Module configurations
    module_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Deployment settings
    artifact_dir: str = "deployment"
    
    # SSL settings
    ssl_enabled: bool = False
    letsencrypt_webroot: str = "/var/www/challenges"
    
    def __post_init__(self):
        """Post-initialization processing"""
        # Convert to Path objects if needed
        if self.project_dir is not None:
            self.project_dir = Path(self.project_dir)
        
        if self.git_dir is not None:
            self.git_dir = Path(self.git_dir)
            
        # Convert djaploy_dir to Path if specified
        if self.djaploy_dir is not None:
            self.djaploy_dir = Path(self.djaploy_dir)
            
        # Convert manage_py_path to Path if specified
        if self.manage_py_path is not None:
            self.manage_py_path = Path(self.manage_py_path)
        
        # Add SSL module if SSL is enabled
        if self.ssl_enabled and "djaploy.modules.ssl" not in self.modules:
            self.modules.append("djaploy.modules.ssl")
    
    def get_deploy_files_dir(self) -> Path:
        """Get the deploy_files directory path"""
        return self.djaploy_dir / "deploy_files"
    
    def get_inventory_dir(self) -> Path:
        """Get the inventory directory path"""
        return self.djaploy_dir / "inventory"
    
    def get_config_file(self) -> Path:
        """Get the config.py file path"""
        return self.djaploy_dir / "config.py"
    
    def get_module_config(self, module_name: str) -> Dict[str, Any]:
        """Get configuration for a specific module"""
        return self.module_configs.get(module_name, {})
    
    def validate(self):
        """Validate the configuration"""
        errors = []
        
        if not self.project_name:
            errors.append("project_name is required")
            
        if not self.app_user:
            errors.append("app_user is required")
            
        if not self.djaploy_dir:
            errors.append("djaploy_dir is required")
            
        if errors:
            raise ValueError(f"Configuration validation failed: {', '.join(errors)}")
        
        return True


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
    _sudo_password: Optional[str] = None
    
    app_user: Optional[str] = None
    app_hostname: Optional[str] = None
    
    # Services to manage on this host
    services: Optional[List[str]] = None
    timer_services: Optional[List[str]] = None
    
    # Domain configurations
    domains: Optional[List[Dict[str, Any]]] = None
    
    pregenerate_certificates: bool = False
    
    # Backup configuration for this host
    backup: Optional[BackupConfig] = None
    
    # Additional host-specific data
    data: Optional[Dict[str, Any]] = None

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

        # Expand SSH key path if provided
        if config.get("ssh_key"):
            import os
            config["ssh_key"] = os.path.expanduser(config["ssh_key"])

        return super().__new__(cls, (name, config))