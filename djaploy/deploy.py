"""
Main deployment functions for djaploy
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from .config import DjaployConfig, HostConfig
from .modules import load_modules
from .artifact import create_artifact


def configure_server(config: DjaployConfig, hosts: List[HostConfig], **kwargs):
    """
    Configure servers for deployment
    
    Args:
        config: DjaployConfig instance
        hosts: List of HostConfig instances
        **kwargs: Additional arguments
    """
    
    # Validate configuration
    config.validate()
    
    # Load modules (including host-specific modules)
    modules = load_modules(config.modules, config.module_configs, hosts)
    
    # Create pyinfra deployment script
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(_generate_configure_script(config, hosts, modules))
        script_path = f.name
    
    try:
        # Run pyinfra
        _run_pyinfra(script_path, hosts)
    finally:
        # Clean up
        os.unlink(script_path)


def deploy_project(config: DjaployConfig, 
                  hosts: List[HostConfig],
                  mode: str = "latest",
                  release_tag: Optional[str] = None,
                  **kwargs):
    """
    Deploy project to servers
    
    Args:
        config: DjaployConfig instance
        hosts: List of HostConfig instances
        mode: Deployment mode ("local", "latest", "release")
        release_tag: Release tag if mode is "release"
        **kwargs: Additional arguments
    """
    
    # Validate configuration
    config.validate()
    
    # Create artifact based on mode
    artifact_path = create_artifact(
        config=config,
        mode=mode,
        release_tag=release_tag
    )
    
    # Load modules (including host-specific modules)
    modules = load_modules(config.modules, config.module_configs, hosts)
    
    # Run prepare script if it exists
    prepare_script = config.project_dir / "prepare.py"
    if prepare_script.exists():
        _run_prepare(prepare_script, config)
    
    # Create pyinfra deployment script
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(_generate_deploy_script(config, hosts, modules, artifact_path))
        script_path = f.name
    
    try:
        # Run pyinfra
        _run_pyinfra(script_path, hosts)
    finally:
        # Clean up
        os.unlink(script_path)


def _generate_configure_script(config: DjaployConfig, 
                               hosts: List[HostConfig],
                               modules: List) -> str:
    """Generate pyinfra configuration script"""
    
    script = """
from pyinfra import host

# Import module implementations
"""
    
    # Add module imports
    for module in modules:
        module_path = module.__class__.__module__
        script += f"from {module_path} import {module.__class__.__name__}\n"
    
    script += """
# Get configuration from host data
config = host.data.config
project_config = host.data.project_config

# Run module configurations
"""
    
    # Add module configuration calls
    for module in modules:
        script += f"""
# Configure {module.name}
module = {module.__class__.__name__}({module.config})
module.pre_configure(host.data, project_config)
module.configure_server(host.data, project_config)
module.post_configure(host.data, project_config)
"""
    
    return script


def _generate_deploy_script(config: DjaployConfig,
                           hosts: List[HostConfig], 
                           modules: List,
                           artifact_path: Path) -> str:
    """Generate pyinfra deployment script"""
    
    script = """
from pyinfra import host
from pathlib import Path

# Import module implementations
"""
    
    # Add module imports
    for module in modules:
        module_path = module.__class__.__module__
        script += f"from {module_path} import {module.__class__.__name__}\n"
    
    script += f"""
# Get configuration from host data
config = host.data.config
project_config = host.data.project_config
artifact_path = Path("{artifact_path}")

# Run module deployments
"""
    
    # Add module deployment calls
    for module in modules:
        script += f"""
# Deploy {module.name}
module = {module.__class__.__name__}({module.config})
module.pre_deploy(host.data, project_config, artifact_path)
module.deploy(host.data, project_config, artifact_path)
module.post_deploy(host.data, project_config, artifact_path)
"""
    
    return script


def _run_pyinfra(script_path: str, hosts: List[HostConfig]):
    """Run pyinfra with the generated script"""
    
    # Create inventory
    inventory = _create_inventory(hosts)
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(inventory)
        inventory_path = f.name
    
    try:
        # Run pyinfra command
        cmd = [
            "pyinfra",
            inventory_path,
            script_path,
            "-y",  # Auto-yes
        ]
        
        subprocess.run(cmd, check=True)
    finally:
        os.unlink(inventory_path)


def _create_inventory(hosts: List[HostConfig]) -> str:
    """Create pyinfra inventory from host configurations"""
    
    inventory = "hosts = [\n"
    
    for host_config in hosts:
        host_dict = host_config.to_pyinfra_host()
        inventory += f"    {host_dict},\n"
    
    inventory += "]\n"
    
    return inventory


def _run_prepare(prepare_script: Path, config: DjaployConfig):
    """Run the prepare script if it exists"""
    
    # Change to project directory
    original_dir = os.getcwd()
    os.chdir(config.project_dir)
    
    try:
        # Run the prepare script
        subprocess.run(["python", str(prepare_script)], check=True)
    finally:
        os.chdir(original_dir)