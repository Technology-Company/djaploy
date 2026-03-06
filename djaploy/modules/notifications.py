"""
Notifications module for djaploy - sends deployment notifications
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from .base import BaseModule
from .versioning import get_deployment_version_info
from ..versioning import get_commits_since_tag, get_latest_version_tag
from ..changelog import get_changelog_generator
from ..notifications import get_notification_backend, NotificationBackend


# Module-level storage for notification context
# This allows failure notifications to be sent from the error handler
_notification_context: Dict[str, Any] = {}
_notification_backend: Optional[NotificationBackend] = None
_changelog_generator = None


def get_notification_context() -> Dict[str, Any]:
    """Get the notification context from the current deployment"""
    return _notification_context.copy()


def get_configured_backend() -> Optional[NotificationBackend]:
    """Get the configured notification backend"""
    return _notification_backend


def send_failure_notification(error_message: str = "") -> bool:
    """Send a failure notification for the current deployment."""
    if not _notification_backend:
        return False

    context = _notification_context.copy()
    if not context:
        return False

    context["success"] = False
    context["error_message"] = error_message
    context["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    message = f"Deployment failed for {context.get('project_name', 'unknown')} to {context.get('env', 'unknown')}"
    if error_message:
        message += f": {error_message}"

    return _notification_backend.send(message, context)


def send_success_notification() -> bool:
    """Send a success notification for the current deployment."""
    if not _notification_backend:
        return False

    context = _notification_context.copy()
    if not context:
        return False

    env = context.get("env", "unknown")
    notify_environments = context.get("notify_environments")

    if notify_environments and env not in notify_environments:
        print(f"[NOTIFICATIONS] Skipping notification for environment: {env}")
        return False

    context["success"] = True
    context["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    message = f"Deployment succeeded: {context.get('project_name', 'unknown')} {context.get('version', '')} to {env}"

    success = _notification_backend.send(message, context)
    if success:
        print(f"[NOTIFICATIONS] Sent success notification for {env}")
    else:
        print(f"[NOTIFICATIONS] Warning: Failed to send notification")

    return success


class NotificationsModule(BaseModule):
    """
    Sends deployment notifications via configurable backend.

    Configuration (in module_configs['notifications']):
        backend: 'slack' or 'webhook' (default: 'slack')
        backend_config:
            webhook_url: Webhook URL (required)
            channel: Optional channel override (Slack only)
        changelog_generator: 'simple' or 'llm' (default: 'simple')
         changelog_config:
              api_key: LLM API key (required for 'llm')
              api_url: API endpoint (default: Mistral)
              model: Model to use (default: devstral-small-latest)
        notify_environments: List of environments to notify (default: all)
        notify_on_failure: Whether to send notifications on failure (default: True)
    """

    name = "notifications"
    description = "Deployment notifications via Slack/webhooks"
    version = "0.1.0"
    dependencies = ["djaploy.modules.versioning"]

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._backend: Optional[NotificationBackend] = None
        self._changelog_generator = None

    def configure_server(self, host_data: Dict[str, Any], project_config: Any):
        """Nothing to configure on server"""
        pass

    def pre_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Initialize backend, generate changelog, and store context for notifications"""
        global _notification_backend, _notification_context, _changelog_generator

        # Initialize notification backend
        backend_type = self.config.get("backend", "slack")
        backend_config = self.config.get("backend_config", {})
        self._backend = get_notification_backend(backend_type, backend_config)
        _notification_backend = self._backend

        # Initialize changelog generator
        generator_type = self.config.get("changelog_generator", "simple")
        generator_config = self.config.get("changelog_config", {})
        self._changelog_generator = get_changelog_generator(generator_type, generator_config)
        _changelog_generator = self._changelog_generator

        # Get version info
        version_info = get_deployment_version_info()
        env = host_data.get("env", "unknown")

        # Generate changelog now (before deployment operations run)
        commits = version_info.get("commits_since_tag", "")
        changelog = ""
        if commits and self._changelog_generator:
            try:
                changelog = self._changelog_generator.generate(commits)
            except Exception as e:
                print(f"[NOTIFICATIONS] Warning: Failed to generate changelog: {e}")
                changelog = commits

        # Store full context for both success and failure notifications
        _notification_context = {
            "env": env,
            "version": version_info.get("new_version", "unknown"),
            "commit": version_info.get("commit", "unknown"),
            "changelog": changelog,
            "project_name": project_config.project_name,
            "host_name": host_data.get("name", "unknown"),
            "notify_environments": self.config.get("notify_environments"),
        }

    def deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Nothing to deploy"""
        pass

    def post_deploy(self, host_data: Dict[str, Any], project_config: Any, artifact_path: Path):
        """Nothing to do here - notification sent after pyinfra operations complete"""
        pass


# Make the module class available for the loader
Module = NotificationsModule
__all__ = [
    "NotificationsModule",
    "send_success_notification",
    "send_failure_notification",
    "get_notification_context",
    "get_configured_backend",
]
