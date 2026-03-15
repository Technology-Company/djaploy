"""
djaploy - Modular Django deployment system based on pyinfra
"""

default_app_config = "djaploy.app.DjaployConfig"

from .config import HostConfig, BackupConfig
from .deploy import deploy_project, configure_server, restore_from_backup, rollback_project, create_janitor_user, run_command
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

# Hooks
from .hooks import (
    hook,
    deploy_hook,
    call_hook,
    get_remote_hooks,
    discover_hooks,
)

__all__ = [
    # Core
    "HostConfig",
    "BackupConfig",
    "deploy_project",
    "configure_server",
    "rollback_project",
    "restore_from_backup",
    "create_janitor_user",
    "run_command",
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
    # Hooks
    "hook",
    "deploy_hook",
    "call_hook",
    "get_remote_hooks",
    "discover_hooks",
]
