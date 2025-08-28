"""
Base module class for djaploy modules
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from pathlib import Path


class BaseModule(ABC):
    """Base class for all djaploy modules"""
    
    # Module metadata
    name: str = "base"
    description: str = "Base module"
    version: str = "0.1.0"
    
    # Module dependencies (other modules that must be loaded first)
    dependencies: List[str] = []
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize the module with configuration"""
        self.config = config or {}
    
    def get_required_imports(self) -> List[str]:
        """
        Get list of import statements required for this module.
        Override this in subclasses to specify imports needed.
        
        Returns:
            List of import statements as strings
        """
        return []
    
    @abstractmethod
    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """
        Configure the server for this module.
        This is run during the configureserver phase.
        
        Args:
            host_data: Host-specific configuration data
            project_config: The project's DjaployConfig instance
        """
        pass
    
    @abstractmethod
    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """
        Deploy this module's components.
        This is run during the deploy phase.
        
        Args:
            host_data: Host-specific configuration data
            project_config: The project's DjaployConfig instance
            artifact_path: Path to the deployment artifact
        """
        pass
    
    def pre_configure(self, host_data: Dict[str, Any], project_config: Any):
        """Hook called before configure_server"""
        pass
    
    def post_configure(self, host_data: Dict[str, Any], project_config: Any):
        """Hook called after configure_server"""
        pass
    
    def pre_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Hook called before deploy"""
        pass
    
    def post_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Hook called after deploy"""
        pass
    
    def validate_config(self) -> bool:
        """Validate the module configuration"""
        return True
    
    def get_required_packages(self) -> List[str]:
        """Get list of system packages required by this module"""
        return []
    
    def get_required_python_packages(self) -> List[str]:
        """Get list of Python packages required by this module"""
        return []
    
    def get_services(self) -> List[str]:
        """Get list of services managed by this module"""
        return []
    
    def get_timer_services(self) -> List[str]:
        """Get list of timer services managed by this module"""
        return []


class ModuleRegistry:
    """Registry for managing loaded modules"""
    
    _modules: Dict[str, BaseModule] = {}
    
    @classmethod
    def register(cls, module_name: str, module_instance: BaseModule):
        """Register a module instance"""
        cls._modules[module_name] = module_instance
    
    @classmethod
    def get(cls, module_name: str) -> Optional[BaseModule]:
        """Get a registered module by name"""
        return cls._modules.get(module_name)
    
    @classmethod
    def get_all(cls) -> Dict[str, BaseModule]:
        """Get all registered modules"""
        return cls._modules.copy()
    
    @classmethod
    def clear(cls):
        """Clear all registered modules"""
        cls._modules.clear()
    
    @classmethod
    def resolve_dependencies(cls, modules: List[str]) -> List[str]:
        """
        Resolve module dependencies and return ordered list of modules
        
        Args:
            modules: List of module names to load
            
        Returns:
            Ordered list of module names with dependencies resolved
        """
        resolved = []
        visited = set()
        
        def visit(module_name: str):
            if module_name in visited:
                return
            visited.add(module_name)
            
            module = cls.get(module_name)
            if module:
                for dep in module.dependencies:
                    visit(dep)
            
            if module_name not in resolved:
                resolved.append(module_name)
        
        for module_name in modules:
            visit(module_name)
        
        return resolved