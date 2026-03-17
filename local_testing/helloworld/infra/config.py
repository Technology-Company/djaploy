"""
Djaploy configuration for the helloworld test project.

Uses zero_downtime deployment strategy to exercise the full feature set.
"""

from pathlib import Path

from djaploy.config import DjaployConfig

config = DjaployConfig(
    project_name="helloworld",
    djaploy_dir=Path(__file__).parent,
    manage_py_path=Path("manage.py"),
    python_version="3.13",
    app_user="app",
    ssh_user="root",
    deployment_strategy="zero_downtime",
    keep_releases=3,
)
