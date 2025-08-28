"""
Shared utilities for Django management commands
"""

import importlib.util
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError

from djaploy.config import DjaployConfig, HostConfig


def load_config(config_file: str = None) -> DjaployConfig:
    """Load djaploy configuration from file or settings"""
    
    base_dir = Path(settings.BASE_DIR) if hasattr(settings, 'BASE_DIR') else Path.cwd()
    
    # Determine config file path
    if config_file:
        config_path = Path(config_file)
        if not config_path.is_absolute():
            config_path = base_dir / config_file
    else:
        # Check settings for config file location
        if hasattr(settings, 'DJAPLOY_CONFIG_DIR'):
            djaploy_dir = Path(settings.DJAPLOY_CONFIG_DIR)
            if not djaploy_dir.is_absolute():
                djaploy_dir = base_dir / djaploy_dir
            config_path = djaploy_dir / 'config.py'
        else:
            config_path = None
            
        if not config_path:
            raise CommandError("No djaploy configuration file found. Create one or specify --config")
    
    if not config_path.exists():
        raise CommandError(f"Configuration file not found: {config_path}")
    
    # Import the config file as a module
    spec = importlib.util.spec_from_file_location("djaploy_config", config_path)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    
    # Get the config object
    if hasattr(config_module, 'config'):
        config = config_module.config
    elif hasattr(config_module, 'DJAPLOY_CONFIG'):
        config = config_module.DJAPLOY_CONFIG
    else:
        raise CommandError(f"No 'config' or 'DJAPLOY_CONFIG' found in {config_path}")
    
    if not isinstance(config, DjaployConfig):
        raise CommandError("Configuration must be a DjaployConfig instance")
    
    # Set project paths from Django settings if not already set
    if config.project_dir is None:
        # Check if explicitly set in settings
        if hasattr(settings, 'PROJECT_DIR'):
            config.project_dir = Path(settings.PROJECT_DIR)
            if not config.project_dir.is_absolute():
                config.project_dir = base_dir / config.project_dir
        else:
            config.project_dir = base_dir
    
    if config.git_dir is None:
        # Check if explicitly set in settings
        if hasattr(settings, 'GIT_DIR'):
            config.git_dir = Path(settings.GIT_DIR)
            if not config.git_dir.is_absolute():
                config.git_dir = base_dir / config.git_dir
        else:
            # Try to find git root from project_dir
            config.git_dir = find_git_root(config.project_dir)
    
    # Re-run post_init to ensure all paths are properly set
    config.__post_init__()
    
    return config


def load_inventory(inventory_dir: str, env: str) -> list:
    """Load inventory for the specified environment"""
    
    inventory_path = Path(inventory_dir)
    if not inventory_path.is_absolute():
        if hasattr(settings, 'BASE_DIR'):
            inventory_path = settings.BASE_DIR / inventory_dir
        else:
            inventory_path = Path.cwd() / inventory_dir
    
    env_file = inventory_path / f"{env}.py"
    if not env_file.exists():
        raise CommandError(f"Inventory file not found: {env_file}")
    
    # Import the inventory file
    spec = importlib.util.spec_from_file_location(f"inventory_{env}", env_file)
    inventory_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory_module)
    
    # Get hosts from the inventory
    hosts = []
    if hasattr(inventory_module, 'hosts'):
        hosts = inventory_module.hosts
    elif hasattr(inventory_module, 'HOSTS'):
        hosts = inventory_module.HOSTS
    
    # Convert to HostConfig if needed
    converted_hosts = []
    for host in hosts:
        if isinstance(host, HostConfig):
            converted_hosts.append(host)
        elif isinstance(host, dict):
            converted_hosts.append(HostConfig(**host))
        else:
            raise CommandError(f"Invalid host configuration: {host}")
    
    return converted_hosts


def find_git_root(start_path: Path) -> Path:
    """Find the git root directory"""
    
    current = start_path
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    
    # Default to start path if no git root found
    return start_path