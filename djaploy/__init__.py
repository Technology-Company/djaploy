"""
djaploy - Modular Django deployment system based on pyinfra
"""

from .config import DjaployConfig
from .deploy import deploy_project, configure_server, restore_from_backup
from .version import __version__

# Versioning utilities
from .versioning import (
    get_latest_version_tag,
    increment_version,
    create_git_tag,
    get_commits_since_tag,
    get_current_commit_hash,
    get_tag_message,
    extract_changelog_from_tag,
)

# Changelog generators
from .changelog import (
    ChangelogGenerator,
    SimpleChangelogGenerator,
    LLMChangelogGenerator,
    get_changelog_generator,
)

# Notification backends
from .notifications import (
    NotificationBackend,
    SlackNotificationBackend,
    WebhookNotificationBackend,
    get_notification_backend,
)

__all__ = [
    # Core
    "DjaployConfig",
    "deploy_project",
    "configure_server",
    "restore_from_backup",
    "__version__",
    # Versioning
    "get_latest_version_tag",
    "increment_version",
    "create_git_tag",
    "get_commits_since_tag",
    "get_current_commit_hash",
    "get_tag_message",
    "extract_changelog_from_tag",
    # Changelog
    "ChangelogGenerator",
    "SimpleChangelogGenerator",
    "LLMChangelogGenerator",
    "get_changelog_generator",
    # Notifications
    "NotificationBackend",
    "SlackNotificationBackend",
    "WebhookNotificationBackend",
    "get_notification_backend",
]