"""
Main deployment functions for djaploy
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
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
                  skip_prepare: bool = False,
                  version_bump: Optional[str] = None,
                  **kwargs):
    """
    Deploy project to servers

    Args:
        config: DjaployConfig instance
        inventory_file: Path to the pyinfra inventory file
        mode: Deployment mode ("local", "latest", "release")
        release_tag: Release tag if mode is "release"
        skip_prepare: Skip running prepare.py script (useful for non-deployment operations)
        version_bump: Override version increment type ("major", "minor", "patch")
        **kwargs: Additional arguments
    """

    # Validate configuration
    config.validate()

    # Apply version bump override if specified
    if version_bump:
        if "versioning" not in config.module_configs:
            config.module_configs["versioning"] = {}
        config.module_configs["versioning"]["increment_type"] = version_bump

    # Run prepare script if it exists (BEFORE artifact creation)
    if not skip_prepare:
        prepare_script = config.djaploy_dir / "prepare.py"
        if prepare_script.exists():
            _run_prepare(prepare_script, config)

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

    # Create pyinfra deployment script
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(_generate_deploy_script(config, modules, artifact_path))
        script_path = f.name

    # Extract environment from inventory filename
    env_name = Path(inventory_file).stem

    # Set up failure notification context (for sending from main process if pyinfra fails)
    failure_notifier = _setup_failure_notification(config, env_name, version_bump)

    try:
        # Run pyinfra with environment data
        _run_pyinfra(script_path, processed_inventory_file, data={"env": env_name})

        # Send success notification after pyinfra operations complete
        _send_success_notification(config, env_name, version_bump)

    except subprocess.CalledProcessError as e:
        # Send failure notification if configured
        if failure_notifier:
            failure_notifier(f"Deployment failed with exit code {e.returncode}")
        raise
    except Exception as e:
        # Send failure notification for any other exception
        if failure_notifier:
            failure_notifier(str(e))
        raise
    finally:
        # Clean up
        os.unlink(script_path)
        if processed_inventory_file != inventory_file:
            os.unlink(processed_inventory_file)


def _setup_failure_notification(config: DjaployConfig, env_name: str, version_bump: Optional[str] = None):
    """
    Set up failure notification capability for the deployment.

    Returns a callable that can send failure notifications, or None if
    notifications are not configured.
    """
    # Check if notifications module is enabled
    if "djaploy.modules.notifications" not in config.modules:
        return None

    notifications_config = (
        config.module_configs.get("notifications")
        or config.module_configs.get("djaploy.modules.notifications")
        or {}
    )
    if not notifications_config:
        return None

    # Check if failure notifications are enabled
    if not notifications_config.get("notify_on_failure", True):
        return None

    # Check if this environment should be notified
    notify_environments = notifications_config.get("notify_environments")
    if notify_environments and env_name not in notify_environments:
        return None

    # Set up the notification backend
    try:
        from .notifications import get_notification_backend
        from .versioning import get_latest_version_tag, get_current_commit_hash, increment_version
        from .changelog import get_changelog_generator

        backend_type = notifications_config.get("backend", "slack")
        backend_config = notifications_config.get("backend_config", {})
        backend = get_notification_backend(backend_type, backend_config)

        if not backend:
            return None

        # Pre-compute version info
        git_dir = config.git_dir
        current_version = get_latest_version_tag(git_dir)
        commit = get_current_commit_hash(git_dir, short=False)

        # Only increment version if there are new commits
        from .versioning import get_commits_since_tag
        commits = get_commits_since_tag(git_dir, current_version)
        if commits:
            increment_type = version_bump or config.module_configs.get("versioning", {}).get("increment_type", "patch")
            new_version = increment_version(current_version, increment_type)
        else:
            new_version = current_version or "v1.0.0"

        def send_failure(error_message: str = "") -> bool:
            """Send failure notification"""
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            context = {
                "env": env_name,
                "version": new_version,
                "commit": commit or "unknown",
                "changelog": "",
                "success": False,
                "timestamp": timestamp,
                "project_name": config.project_name,
                "display_name": notifications_config.get("display_name", config.project_name),
                "error_message": error_message,
            }
            message = f"Deployment failed for {config.project_name} to {env_name}"
            if error_message:
                message += f": {error_message}"

            try:
                return backend.send(message, context)
            except Exception as e:
                print(f"[NOTIFICATIONS] Warning: Failed to send failure notification: {e}")
                return False

        return send_failure

    except ImportError:
        return None
    except Exception as e:
        print(f"[NOTIFICATIONS] Warning: Failed to set up failure notifications: {e}")
        return None


def _send_success_notification(config: DjaployConfig, env_name: str, version_bump: Optional[str] = None):
    """Send success notification after deployment completes."""
    if "djaploy.modules.notifications" not in config.modules:
        return

    notifications_config = (
        config.module_configs.get("notifications")
        or config.module_configs.get("djaploy.modules.notifications")
        or {}
    )
    if not notifications_config:
        return

    # Check if this environment should be notified
    notify_environments = notifications_config.get("notify_environments")
    if notify_environments and env_name not in notify_environments:
        print(f"[NOTIFICATIONS] Skipping notification for environment: {env_name}")
        return

    try:
        from .notifications import get_notification_backend
        from .versioning import get_latest_version_tag, get_current_commit_hash, increment_version, get_commits_since_tag
        from .changelog import get_changelog_generator

        # Set up backend
        backend_type = notifications_config.get("backend", "slack")
        backend_config = notifications_config.get("backend_config", {})
        backend = get_notification_backend(backend_type, backend_config)

        if not backend:
            return

        # Get version info
        git_dir = config.git_dir
        current_version = get_latest_version_tag(git_dir)
        commit = get_current_commit_hash(git_dir, short=False)
        commits = get_commits_since_tag(git_dir, current_version)

        # Only increment version if there are new commits
        if commits:
            increment_type = version_bump or config.module_configs.get("versioning", {}).get("increment_type", "patch")
            new_version = increment_version(current_version, increment_type)
        else:
            new_version = current_version or "v1.0.0"

        # Generate changelog
        generator_type = notifications_config.get("changelog_generator", "simple")
        generator_config = notifications_config.get("changelog_config", {})
        changelog_generator = get_changelog_generator(generator_type, generator_config)
        changelog = ""
        if commits and changelog_generator:
            try:
                changelog = changelog_generator.generate(commits)
            except Exception as e:
                print(f"[NOTIFICATIONS] Warning: Failed to generate changelog: {e}")
                changelog = commits

        # Build context
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        context = {
            "env": env_name,
            "version": new_version,
            "commit": commit or "unknown",
            "changelog": changelog,
            "success": True,
            "timestamp": timestamp,
            "project_name": config.project_name,
            "display_name": notifications_config.get("display_name", config.project_name),
        }

        message = f"Deployment succeeded: {config.project_name} {new_version} to {env_name}"

        if backend.send(message, context):
            print(f"[NOTIFICATIONS] Sent success notification for {env_name}")
        else:
            print(f"[NOTIFICATIONS] Warning: Failed to send notification")

    except Exception as e:
        print(f"[NOTIFICATIONS] Warning: Failed to send success notification: {e}")


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
        env['PYTHONPATH'] = f"{project_dir}{os.pathsep}{current_python_path}"
    else:
        env['PYTHONPATH'] = project_dir
    
    cmd = [
        sys.executable,
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
    from dataclasses import is_dataclass, asdict

    if is_dataclass(value) and not isinstance(value, type):
        # Handle dataclass objects (like BackupConfig) - flatten to dict with all fields
        result = {k: _make_value_serializable(v) for k, v in asdict(value).items()}
        result['__class__'] = value.__class__.__name__
        return result
    elif hasattr(value, '__dict__') and not isinstance(value, type):
        # It's an object with attributes - flatten to dict
        result = {}
        for attr, attr_value in value.__dict__.items():
            if not attr.startswith('_'):
                result[attr] = _make_value_serializable(attr_value)
        result['__class__'] = value.__class__.__name__
        return result
    elif isinstance(value, list):
        # Process each item in the list
        return [_make_value_serializable(item) for item in value]
    elif isinstance(value, dict):
        # Process each value in the dict
        return {k: _make_value_serializable(v) for k, v in value.items()}
    elif isinstance(value, Path):
        # Convert Path objects to strings
        return str(value)
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
        subprocess.run([sys.executable, str(prepare_script)], check=True)
    finally:
        os.chdir(original_dir)