"""
Module loader for djaploy
"""

import importlib
from typing import List, Dict, Any, Optional
from pathlib import Path

from .base import BaseModule, ModuleRegistry


def load_module(module_path: str, config: Dict[str, Any] = None) -> BaseModule:
    """
    Load a single module by its import path
    
    Args:
        module_path: Python import path to the module (e.g., "djaploy.modules.nginx")
        config: Configuration dictionary for the module
        
    Returns:
        Loaded module instance
    """
    try:
        # Import the module
        module = importlib.import_module(module_path)
        
        # Find the module class (should be named Module or have a get_module function)
        if hasattr(module, 'Module'):
            module_class = module.Module
        elif hasattr(module, 'get_module'):
            module_class = module.get_module()
        else:
            # Try to find a class that inherits from BaseModule
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    issubclass(attr, BaseModule) and 
                    attr != BaseModule):
                    module_class = attr
                    break
            else:
                raise ImportError(f"No module class found in {module_path}")
        
        # Instantiate the module
        module_instance = module_class(config or {})
        
        # Register the module
        ModuleRegistry.register(module_path, module_instance)
        
        return module_instance
        
    except ImportError as e:
        raise ImportError(f"Failed to load module {module_path}: {e}")


def load_modules(module_paths: List[str], 
                 module_configs: Dict[str, Dict[str, Any]] = None) -> List[BaseModule]:
    """
    Load multiple modules and resolve their dependencies
    
    Args:
        module_paths: List of module import paths
        module_configs: Dictionary mapping module paths to their configurations
        
    Returns:
        List of loaded module instances in dependency order
    """
    module_configs = module_configs or {}
    loaded_modules = []
    
    # First, load all modules
    for module_path in module_paths:
        config = module_configs.get(module_path, {})
        try:
            module = load_module(module_path, config)
            loaded_modules.append(module)
        except ImportError as e:
            print(f"Warning: Failed to load module {module_path}: {e}")
    
    # Resolve dependencies and return in correct order
    ordered_paths = ModuleRegistry.resolve_dependencies(module_paths)
    ordered_modules = []
    
    for path in ordered_paths:
        module = ModuleRegistry.get(path)
        if module:
            ordered_modules.append(module)
    
    return ordered_modules


def discover_modules(search_path: Path = None) -> List[str]:
    """
    Discover available modules in the djaploy package and optionally in a project
    
    Args:
        search_path: Optional path to search for project-specific modules
        
    Returns:
        List of discovered module import paths
    """
    modules = []
    
    # Discover built-in modules
    import djaploy.modules as modules_package
    modules_dir = Path(modules_package.__file__).parent
    
    for module_file in modules_dir.glob("*.py"):
        if module_file.name not in ["__init__.py", "base.py", "loader.py"]:
            module_name = module_file.stem
            modules.append(f"djaploy.modules.{module_name}")
    
    # Also check subdirectories
    for subdir in modules_dir.iterdir():
        if subdir.is_dir() and not subdir.name.startswith("_"):
            init_file = subdir / "__init__.py"
            if init_file.exists():
                modules.append(f"djaploy.modules.{subdir.name}")
    
    # Discover project-specific modules if search_path provided
    if search_path and search_path.exists():
        modules_dir = search_path / "modules"
        if modules_dir.exists():
            for module_file in modules_dir.glob("*.py"):
                if module_file.name != "__init__.py":
                    module_name = module_file.stem
                    modules.append(f"modules.{module_name}")
    
    return modules