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


def configure_server(config: DjaployConfig, inventory_file: str, **kwargs):
    """
    Configure servers for deployment
    
    Args:
        config: DjaployConfig instance
        inventory_file: Path to the pyinfra inventory file
        **kwargs: Additional arguments
    """
    
    # Validate configuration
    config.validate()
    
    # Load modules
    modules = load_modules(config.modules, config.module_configs)
    
    # Pre-process inventory file to convert HostConfig objects to tuples
    processed_inventory_file = _preprocess_inventory(inventory_file)
    
    # Create pyinfra deployment script
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(_generate_configure_script(config, modules))
        script_path = f.name
    
    try:
        # Extract environment from inventory filename
        env_name = Path(inventory_file).stem
        
        # Run pyinfra with environment data
        _run_pyinfra(script_path, processed_inventory_file, data={"env": env_name})
    finally:
        # Clean up
        os.unlink(script_path)
        if processed_inventory_file != inventory_file:
            os.unlink(processed_inventory_file)


def deploy_project(config: DjaployConfig, 
                  inventory_file: str,
                  mode: str = "latest",
                  release_tag: Optional[str] = None,
                  **kwargs):
    """
    Deploy project to servers
    
    Args:
        config: DjaployConfig instance
        inventory_file: Path to the pyinfra inventory file
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
    
    # Load modules
    modules = load_modules(config.modules, config.module_configs)
    
    # Pre-process inventory file to convert HostConfig objects to tuples
    processed_inventory_file = _preprocess_inventory(inventory_file)
    
    # Run prepare script if it exists
    prepare_script = config.djaploy_dir / "prepare.py"
    if prepare_script.exists():
        _run_prepare(prepare_script, config)
    
    # Create pyinfra deployment script
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(_generate_deploy_script(config, modules, artifact_path))
        script_path = f.name
    
    try:
        # Extract environment from inventory filename
        env_name = Path(inventory_file).stem
        
        # Run pyinfra with environment data
        _run_pyinfra(script_path, processed_inventory_file, data={"env": env_name})
    finally:
        # Clean up
        os.unlink(script_path)
        if processed_inventory_file != inventory_file:
            os.unlink(processed_inventory_file)


def _generate_configure_script(config: DjaployConfig, modules: List) -> str:
    """Generate pyinfra configuration script"""
    
    # Collect all unique imports from modules
    all_imports = set()
    for module in modules:
        if hasattr(module, 'get_required_imports'):
            all_imports.update(module.get_required_imports())
    
    # Start building the script
    script = "# Auto-generated pyinfra deployment script\n\n"
    
    # Add all collected imports
    if all_imports:
        script += "# Required imports from modules\n"
        for import_stmt in sorted(all_imports):
            script += f"{import_stmt}\n"
    else:
        # Default imports if no modules specify them
        script += """from pyinfra import host
from pyinfra.operations import apt, server, pip, files, systemd
from pyinfra.facts.server import Which
from pathlib import Path
"""
    
    script += "\n# Import module implementations\n"
    
    # Add module imports
    for module in modules:
        module_path = module.__class__.__module__
        script += f"from {module_path} import {module.__class__.__name__}\n"
    
    script += f"""
# Get configuration from djaploy config
import sys
sys.path.insert(0, '{config.djaploy_dir}')
from config import config as djaploy_config

# Pass the djaploy_config object directly to modules
project_config = djaploy_config

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
                           modules: List,
                           artifact_path: Path) -> str:
    """Generate pyinfra deployment script"""
    
    # Collect all unique imports from modules
    all_imports = set()
    for module in modules:
        if hasattr(module, 'get_required_imports'):
            all_imports.update(module.get_required_imports())
    
    # Start building the script
    script = "# Auto-generated pyinfra deployment script\n\n"
    
    # Add all collected imports
    if all_imports:
        script += "# Required imports from modules\n"
        for import_stmt in sorted(all_imports):
            script += f"{import_stmt}\n"
    else:
        # Default imports if no modules specify them
        script += """from pyinfra import host
from pyinfra.operations import apt, server, pip, files, systemd
from pyinfra.facts.server import Which
from pathlib import Path
"""
    
    script += "\n# Import module implementations\n"
    
    # Add module imports
    for module in modules:
        module_path = module.__class__.__module__
        script += f"from {module_path} import {module.__class__.__name__}\n"
    
    script += f"""
# Get configuration from djaploy config
import sys
sys.path.insert(0, '{config.djaploy_dir}')
from config import config as djaploy_config

# Pass the djaploy_config object directly to modules
project_config = djaploy_config

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


def _run_pyinfra(script_path: str, inventory_path: str, data: dict = None):
    """Run pyinfra with the generated script and inventory using django_pyinfra wrapper"""
    
    # Use djaploy's built-in django_pyinfra wrapper
    import djaploy
    djaploy_path = Path(djaploy.__file__).parent
    django_pyinfra_path = djaploy_path / "bin" / "django_pyinfra.py"

    env = os.environ.copy()

    from django.conf import settings
    project_dir = str(settings.BASE_DIR)

    current_python_path = env.get('PYTHONPATH', '')
    if current_python_path:
        env['PYTHONPATH'] = f"{project_dir}:{current_python_path}"
    else:
        env['PYTHONPATH'] = project_dir
    
    cmd = [
        "python",
        str(django_pyinfra_path),
        "-y",
    ]
    
    # Add data parameters if provided
    if data:
        for key, value in data.items():
            cmd.extend(["--data", f"{key}={value}"])
    
    cmd.extend([inventory_path, script_path])
    
    subprocess.run(cmd, check=True, env=env)



def _preprocess_inventory(inventory_file: str) -> str:
    """
    Pre-process inventory file to convert HostConfig objects to pyinfra tuples
    
    Returns path to processed inventory file
    """
    # Import the inventory module to evaluate HostConfig objects
    import sys
    import importlib.util
    from pathlib import Path
    
    spec = importlib.util.spec_from_file_location("inventory", inventory_file)
    inventory_module = importlib.util.module_from_spec(spec)
    
    # Add djaploy to the module's namespace so it can import HostConfig
    original_path = sys.path[:]
    try:
        sys.modules['inventory'] = inventory_module
        spec.loader.exec_module(inventory_module)
        
        # Get the hosts from the module
        hosts = getattr(inventory_module, 'hosts', [])
        
        # Convert HostConfig objects to tuples and build new inventory content
        processed_hosts = []
        for host in hosts:
            if hasattr(host, '__iter__') and len(host) == 2:
                # Already a tuple (connection_string, host_data)
                processed_hosts.append(host)
            else:
                # Assume it's a HostConfig that needs conversion
                processed_hosts.append(host)
                
        # Create processed inventory file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("# Auto-processed inventory file\n\n")
            f.write("hosts = [\n")
            for host in processed_hosts:
                if isinstance(host, tuple) and len(host) == 2:
                    host_name, host_data = host
                    # Create a safe dictionary by converting non-serializable objects
                    safe_host_data = {}
                    for key, value in host_data.items():
                        safe_host_data[key] = _make_value_serializable(value)
                    f.write(f"    ({repr(host_name)}, {repr(safe_host_data)}),\n")
                else:
                    f.write(f"    {repr(host)},\n")
            f.write("]\n")
            
            return f.name
            
    finally:
        sys.path[:] = original_path
        if 'inventory' in sys.modules:
            del sys.modules['inventory']


def _make_value_serializable(value):
    """Convert a value to a serializable form for inventory processing"""
    if hasattr(value, '__dict__'):
        # It's an object with attributes - convert to a dict representation
        if hasattr(value, '__class__'):
            obj_dict = {
                '__class__': f"{value.__class__.__module__}.{value.__class__.__name__}",
                '__dict__': {}
            }
            # Add key attributes that are commonly needed
            for attr in ['identifier', 'domains', 'cert_file', 'key_file']:
                if hasattr(value, attr):
                    obj_dict['__dict__'][attr] = getattr(value, attr)
            return obj_dict
        else:
            return str(value)
    elif isinstance(value, list):
        # Process each item in the list
        return [_make_value_serializable(item) for item in value]
    elif isinstance(value, dict):
        # Process each value in the dict
        return {k: _make_value_serializable(v) for k, v in value.items()}
    else:
        # Already serializable (str, int, bool, etc.)
        return value


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