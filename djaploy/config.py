"""
Configuration management for djaploy
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from pathlib import Path


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
        "djaploy.modules.base",
        "djaploy.modules.nginx", 
        "djaploy.modules.systemd"
    ])
    
    # Module configurations
    module_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Deployment settings
    artifact_dir: str = "deployment"
    
    # SSL settings
    ssl_cert_path: Optional[str] = None
    ssl_key_path: Optional[str] = None
    
    # Services
    services: List[str] = field(default_factory=list)
    timer_services: List[str] = field(default_factory=list)
    
    # Hosts (if configured, used to add required modules)
    hosts: Optional[List["HostConfig"]] = None
    
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
        
        # Add required modules from hosts
        if self.hosts:
            for host in self.hosts:
                if hasattr(host, 'get_required_modules'):
                    for module in host.get_required_modules():
                        if module not in self.modules:
                            self.modules.append(module)
    
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


@dataclass
class HostConfig:
    """Configuration for a deployment host"""
    
    name: str
    ssh_host: str
    ssh_user: str = "deploy"
    ssh_port: int = 22
    
    app_user: str = "app"
    app_hostname: Optional[str] = None
    
    env: str = "production"  # Environment name
    
    # Services to manage on this host
    services: List[str] = field(default_factory=list)
    timer_services: List[str] = field(default_factory=list)
    
    # Domain configurations
    domains: List[Dict[str, Any]] = field(default_factory=list)
    
    # Backup configuration for this host
    backup: Optional[BackupConfig] = None
    
    # Additional host-specific data
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_pyinfra_host(self):
        """Convert to pyinfra host format"""
        return {
            "name": self.name,
            "ssh_host": self.ssh_host,
            "ssh_user": self.ssh_user,
            "ssh_port": self.ssh_port,
            "data": {
                "app_user": self.app_user,
                "app_hostname": self.app_hostname or self.ssh_host,
                "env": self.env,
                "services": self.services,
                "timer_services": self.timer_services,
                "domains": self.domains,
                "backup": asdict(self.backup) if self.backup else None,
                **self.data
            }
        }
    
    def get_required_modules(self) -> List[str]:
        """Get list of modules required for this host based on its configuration"""
        modules = []
        
        # Add rclone module if backup is configured
        if self.backup and self.backup.enabled:
            modules.append("djaploy.modules.rclone")
        
        # Add litestream module if configured (future)
        # if self.litestream:
        #     modules.append("djaploy.modules.litestream")
        
        return modules