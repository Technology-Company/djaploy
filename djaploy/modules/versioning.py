"""
Versioning module for djaploy - manages VERSION files and git tags
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from pyinfra.operations import files

from .base import BaseModule
from ..versioning import (
    get_latest_version_tag,
    increment_version,
    create_git_tag,
    get_current_commit_hash,
    get_commits_since_tag,
)


# Module-level storage for deployment version info
# This allows the notifications module to access version info
_deployment_version_info: Dict[str, Any] = {}


def get_deployment_version_info() -> Dict[str, Any]:
    """Get the version info from the current deployment"""
    return _deployment_version_info.copy()


def set_deployment_version_info(info: Dict[str, Any]) -> None:
    """Set the version info for the current deployment"""
    global _deployment_version_info
    _deployment_version_info = info


class VersioningModule(BaseModule):
    """
    Deploys VERSION file and creates git tags.

    Configuration (in module_configs['versioning']):
        tag_environments: List of environments to create tags for (default: ['production'])
        version_file_path: Relative path for VERSION file (default: 'VERSION')
        increment_type: 'major', 'minor', or 'patch' (default: 'patch')
        push_tags: Whether to push tags to remote (default: True)
    """

    name = "versioning"
    description = "Semantic versioning and VERSION file deployment"
    version = "0.1.0"

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._current_version: Optional[str] = None
        self._new_version: Optional[str] = None
        self._commit_hash: Optional[str] = None
        self._commits_since_tag: str = ""
        self._tag_created: bool = False

    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Nothing to configure on server"""
        pass

    def pre_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Calculate version info before deployment"""
        git_dir = project_config.git_dir

        # Get current version tag
        self._current_version = get_latest_version_tag(git_dir)

        # Get commit hash
        self._commit_hash = get_current_commit_hash(git_dir, short=False)

        # Get commits since last tag
        self._commits_since_tag = get_commits_since_tag(git_dir, self._current_version)

        # Only increment version if there are new commits since last tag
        if self._commits_since_tag:
            increment_type = self.config.get("increment_type", "patch")
            self._new_version = increment_version(self._current_version, increment_type)
        else:
            # No new commits - use current version
            self._new_version = self._current_version or "v1.0.0"

        # Store in module-level storage for notifications module
        env = host_data.get("env", "unknown")
        set_deployment_version_info({
            "current_version": self._current_version,
            "new_version": self._new_version,
            "commit": self._commit_hash,
            "commits_since_tag": self._commits_since_tag,
            "env": env,
            "project_name": project_config.project_name,
            "host_name": host_data.get("name", "unknown"),
        })

        print(f"[VERSIONING] Current version: {self._current_version or 'none'}")
        print(f"[VERSIONING] New version: {self._new_version}")
        print(f"[VERSIONING] Commit: {self._commit_hash[:7] if self._commit_hash else 'unknown'}")

    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Deploy VERSION file to server"""
        app_user = host_data.get("app_user") or project_config.app_user
        project_name = project_config.project_name
        env = host_data.get("env", "unknown")

        # Get VERSION file path
        version_file_path = self.config.get("version_file_path", "VERSION")
        app_root = f"/home/{app_user}/apps/{project_name}"
        dest_path = f"{app_root}/{version_file_path}"

        # Create VERSION file content
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        version_content = f"""VERSION={self._new_version}
COMMIT={self._commit_hash or 'unknown'}
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

        print(f"[VERSIONING] Deployed VERSION file to {dest_path}")

    def post_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Create and push git tag (for configured environments only)"""
        env = host_data.get("env")
        tag_environments = self.config.get("tag_environments", ["production"])
        push_tags = self.config.get("push_tags", True)

        # Check if this environment should be tagged
        if env not in tag_environments:
            print(f"[VERSIONING] Skipping tag creation for environment: {env}")
            return

        # Skip if no new commits since last tag (tag already exists)
        if not self._commits_since_tag:
            print(f"[VERSIONING] No new commits, using existing tag: {self._new_version}")
            return

        # Check if we already created this tag (avoid duplicate tags for multi-host deploys)
        if self._tag_created:
            print(f"[VERSIONING] Tag {self._new_version} already created, skipping")
            return

        git_dir = project_config.git_dir

        # Create tag message with changelog
        if self._commits_since_tag:
            tag_message = f"Release {self._new_version}\n\nChanges since {self._current_version or 'initial'}:\n{self._commits_since_tag}"
        else:
            tag_message = f"Release {self._new_version}"

        # Create and push tag
        success = create_git_tag(
            git_dir=git_dir,
            tag=self._new_version,
            message=tag_message,
            push=push_tags,
        )

        if success:
            self._tag_created = True
            print(f"[VERSIONING] Created and pushed tag: {self._new_version}")
        else:
            print(f"[VERSIONING] Warning: Failed to create tag {self._new_version}")


# Make the module class available for the loader
Module = VersioningModule
__all__ = ["VersioningModule", "get_deployment_version_info", "set_deployment_version_info"]
