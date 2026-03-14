"""
Shared utilities for djaploy pyinfra command files.
"""

import sys


def load_project_config(host):
    """Load the DjaployConfig from the djaploy_dir passed via pyinfra --data."""
    djaploy_dir = getattr(host.data, "djaploy_dir", None)
    if not djaploy_dir:
        raise RuntimeError(
            "djaploy_dir not set. Pass it via --data djaploy_dir=<path>"
        )

    if djaploy_dir not in sys.path:
        sys.path.insert(0, djaploy_dir)

    from config import config as project_config

    return project_config
