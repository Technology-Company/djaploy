"""
Versioning module for djaploy - deploys VERSION file to server
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from pyinfra.operations import files

from .base import BaseModule


class VersioningModule(BaseModule):
    """
    Deploys VERSION file to server.

    Version info is calculated in the main process and passed via pyinfra data.
    Git tags are created in the main process after successful deployment.

    Configuration (in module_configs['versioning']):
        version_file_path: Relative path for VERSION file (default: 'VERSION')
        tag_environments: List of environments to create tags for (default: ['production'])
        increment_type: 'major', 'minor', or 'patch' (default: 'patch')
        push_tags: Whether to push tags to remote (default: True)
    """

    name = "versioning"
    description = "VERSION file deployment"
    version = "0.2.0"

    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Nothing to configure on server"""
        pass

    def pre_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Nothing to do - version is calculated in main process"""
        pass

    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Deploy VERSION file to server"""
        # Get version info from pyinfra data (passed from main process)
        version = host_data.get("version")
        commit = host_data.get("commit", "unknown")
        env = host_data.get("env", "unknown")

        if not version:
            print("[VERSIONING] No version info provided, skipping VERSION file deployment")
            return

        app_user = host_data.get("app_user") or project_config.app_user
        project_name = project_config.project_name

        # Get VERSION file path from module config
        version_file_path = self.config.get("version_file_path", "VERSION")
        app_root = f"/home/{app_user}/apps/{project_name}"
        dest_path = f"{app_root}/{version_file_path}"

        # Create VERSION file content
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        version_content = f"""VERSION={version}
COMMIT={commit}
DEPLOYED_AT={timestamp}
ENVIRONMENT={env}
"""

        # Write to temp file and deploy
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(version_content)
            temp_path = f.name

        files.put(
            name=f"Deploy VERSION file to {dest_path}",
            src=temp_path,
            dest=dest_path,
            user=app_user,
            group=app_user,
            mode="644",
            _sudo=True,
        )

        print(f"[VERSIONING] Deployed VERSION file: {version} ({commit[:7]})")

    def post_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Nothing to do - tag creation happens in main process"""
        pass


Module = VersioningModule
__all__ = ["VersioningModule"]
