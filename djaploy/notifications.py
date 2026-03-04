"""
Notification backends for djaploy
"""

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List


class NotificationBackend(ABC):
    """Base class for notification backends"""

    @abstractmethod
    def send(self, message: str, context: Dict[str, Any]) -> bool:
        """
        Send notification.

        Args:
            message: Fallback plain text message
            context: Dict with keys:
                - env: Environment name (e.g., 'production')
                - version: Version tag (e.g., 'v1.0.0')
                - commit: Commit hash
                - changelog: Formatted changelog string
                - success: Boolean indicating deployment success
                - timestamp: ISO format timestamp
                - project_name: Name of the project
                - host_name: Name of the deployed host

        Returns:
            True if notification was sent successfully
        """
        pass


class SlackNotificationBackend(NotificationBackend):
    """
    Slack webhook notification backend.

    Sends formatted messages using Slack Block Kit for rich formatting.
    """

    def __init__(self, webhook_url: str, channel: Optional[str] = None):
        """
        Initialize Slack notification backend.

        Args:
            webhook_url: Slack incoming webhook URL
            channel: Optional channel override
        """
        self.webhook_url = str(webhook_url)  # Convert StringLike (OpSecret) to string
        self.channel = channel

    def send(self, message: str, context: Dict[str, Any]) -> bool:
        """
        Send notification to Slack.

        Args:
            message: Fallback plain text message
            context: Deployment context dictionary

        Returns:
            True if notification was sent successfully
        """
        try:
            payload = self._build_payload(message, context)

            request = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={"Content-Type": "application/json"},
                method='POST'
            )

            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status == 200

        except Exception as e:
            print(f"[NOTIFICATIONS] Warning: Failed to send Slack notification: {e}")
            return False

    def _build_payload(self, message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Build Slack Block Kit payload"""
        success = context.get("success", True)
        env = context.get("env", "unknown")
        version = context.get("version", "unknown")
        commit = context.get("commit", "unknown")[:7] if context.get("commit") else "unknown"
        changelog = context.get("changelog", "")
        project_name = context.get("project_name", "unknown")
        host_name = context.get("host_name", "")
        timestamp = context.get("timestamp", "")

        # Status emoji and text
        if success:
            status_emoji = ":white_check_mark:"
            status_text = "Deployment Succeeded"
            color = "#36a64f"
        else:
            status_emoji = ":x:"
            status_text = "Deployment Failed"
            color = "#dc3545"

        # Build header
        header_text = f"{status_emoji} {status_text}"
        if project_name:
            header_text = f"{status_emoji} {project_name}: {status_text}"

        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header_text,
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Environment:*\n{env}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Version:*\n{version}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Commit:*\n`{commit}`"
                    },
                ]
            }
        ]

        # Add host name if available
        if host_name:
            blocks[1]["fields"].append({
                "type": "mrkdwn",
                "text": f"*Host:*\n{host_name}"
            })

        # Add changelog section if available and deployment succeeded
        if changelog and success:
            # Truncate changelog if too long for Slack
            max_changelog_len = 2900  # Slack limit is ~3000 chars per block
            if len(changelog) > max_changelog_len:
                changelog = changelog[:max_changelog_len] + "\n..."

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Changes:*\n{changelog}"
                }
            })

        # Add timestamp in context
        if timestamp:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Deployed at {timestamp}"
                    }
                ]
            })

        payload: Dict[str, Any] = {
            "blocks": blocks,
            "text": message,  # Fallback for notifications
        }

        if self.channel:
            payload["channel"] = self.channel

        return payload


class WebhookNotificationBackend(NotificationBackend):
    """
    Generic webhook backend (POST JSON).

    Sends the full context as a JSON payload to any webhook URL.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        include_message_in_body: bool = True
    ):
        """
        Initialize generic webhook backend.

        Args:
            url: Webhook URL to POST to
            headers: Optional dict of headers to include
            include_message_in_body: Whether to include message field in body
        """
        self.url = str(url)  # Convert StringLike (OpSecret) to string
        self.headers = headers or {}
        self.include_message_in_body = include_message_in_body

    def send(self, message: str, context: Dict[str, Any]) -> bool:
        """
        Send notification to generic webhook.

        Args:
            message: Fallback plain text message
            context: Deployment context dictionary

        Returns:
            True if notification was sent successfully
        """
        try:
            payload = dict(context)
            if self.include_message_in_body:
                payload["message"] = message

            headers = {
                "Content-Type": "application/json",
                **self.headers
            }

            request = urllib.request.Request(
                self.url,
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
                method='POST'
            )

            with urllib.request.urlopen(request, timeout=30) as response:
                return 200 <= response.status < 300

        except Exception as e:
            print(f"[NOTIFICATIONS] Warning: Failed to send webhook notification: {e}")
            return False


def get_notification_backend(
    backend_type: str = "slack",
    config: Optional[Dict[str, Any]] = None
) -> Optional[NotificationBackend]:
    """
    Factory function to create a notification backend.

    Args:
        backend_type: "slack" or "webhook"
        config: Configuration dict for the backend
            For 'slack': webhook_url (required), channel (optional)
            For 'webhook': url (required), headers (optional)

    Returns:
        NotificationBackend instance or None if configuration is invalid
    """
    config = config or {}

    if backend_type == "slack":
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            print("[NOTIFICATIONS] Warning: No webhook_url provided for Slack backend")
            return None

        return SlackNotificationBackend(
            webhook_url=webhook_url,
            channel=config.get("channel"),
        )

    elif backend_type == "webhook":
        url = config.get("url") or config.get("webhook_url")
        if not url:
            print("[NOTIFICATIONS] Warning: No url provided for webhook backend")
            return None

        return WebhookNotificationBackend(
            url=url,
            headers=config.get("headers"),
        )

    else:
        print(f"[NOTIFICATIONS] Warning: Unknown backend type '{backend_type}'")
        return None
