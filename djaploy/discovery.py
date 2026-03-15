"""
App-based discovery for djaploy infra commands and inventory files.

Similar to Django's management command discovery, this module searches
through INSTALLED_APPS to find infra/ directories containing pyinfra
command files and inventory files. The first match wins based on app order.

Directory structure expected in each app:
    <app>/
        infra/
            commands/
                deploy.py          # pyinfra command file
                configureserver.py
                ...
            inventory/
                production.py      # inventory file
                staging.py
                ...
"""

import importlib
import pkgutil
import re
from pathlib import Path
from typing import Optional, List, Tuple

try:
    from django.apps import apps
except ImportError:
    apps = None  # Django not available; discovery functions will return empty

# Only allow safe names: alphanumeric, hyphen, underscore
_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


def get_app_infra_dirs() -> List[Tuple[str, Path]]:
    """
    Get infra/ directories from all installed Django apps, in INSTALLED_APPS order.

    Returns:
        List of (app_label, infra_dir_path) tuples for apps that have
        an infra/ directory.
    """
    if apps is None:
        return []
    result = []
    for app_config in apps.get_app_configs():
        infra_dir = Path(app_config.path) / "infra"
        if infra_dir.is_dir():
            result.append((app_config.label, infra_dir))
    return result


def find_command(command_name: str) -> Optional[Path]:
    """
    Find a pyinfra command file by name across installed apps.

    Searches each app's infra/commands/ directory in INSTALLED_APPS order
    and returns the first match.

    Args:
        command_name: Name of the command (without .py extension)

    Returns:
        Path to the command file, or None if not found.
    """
    if not _SAFE_NAME_RE.match(command_name):
        return None
    for app_label, infra_dir in get_app_infra_dirs():
        command_file = infra_dir / "commands" / f"{command_name}.py"
        if command_file.is_file():
            return command_file
    return None


def find_inventory(env_name: str) -> Optional[Path]:
    """
    Find an inventory file by environment name across installed apps.

    Searches each app's infra/inventory/ directory in INSTALLED_APPS order
    and returns the first match.

    Args:
        env_name: Environment name (without .py extension)

    Returns:
        Path to the inventory file, or None if not found.
    """
    if not _SAFE_NAME_RE.match(env_name):
        return None
    for app_label, infra_dir in get_app_infra_dirs():
        inventory_file = infra_dir / "inventory" / f"{env_name}.py"
        if inventory_file.is_file():
            return inventory_file
    return None


def find_config() -> Optional[Path]:
    """
    Find a djaploy config file across installed apps.

    Searches each app's infra/config.py in INSTALLED_APPS order
    and returns the first match.

    Returns:
        Path to the config file, or None if not found.
    """
    for app_label, infra_dir in get_app_infra_dirs():
        config_file = infra_dir / "config.py"
        if config_file.is_file():
            return config_file
    return None


def get_available_commands() -> List[Tuple[str, str]]:
    """
    Discover all available infra commands across installed apps.

    Returns commands from all apps, but only the first occurrence of each
    command name is included (first app wins).

    Returns:
        List of (command_name, app_label) tuples.
    """
    seen = {}
    for app_label, infra_dir in get_app_infra_dirs():
        commands_dir = infra_dir / "commands"
        if commands_dir.is_dir():
            for command_file in sorted(commands_dir.glob("*.py")):
                if command_file.name.startswith("_"):
                    continue
                name = command_file.stem
                if name not in seen:
                    seen[name] = app_label
    return [(name, app_label) for name, app_label in seen.items()]
