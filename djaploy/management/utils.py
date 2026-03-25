"""
Shared utilities for Django management commands
"""

from pathlib import Path

from django.conf import settings


def find_git_root(start_path: Path) -> Path:
    """Find the git root directory"""

    current = start_path
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent

    # Default to start path if no git root found
    return start_path
