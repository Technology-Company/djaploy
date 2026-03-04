"""
Notification backends for djaploy
"""

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

from .certificates import OpSecret


class NotificationBackend(ABC):
    """Base class for notification backends"""

    @abstractmethod
    def send(self, message: str, context: Dict[str, Any]) -> bool:
        pass


class SlackNotificationBackend(NotificationBackend):
    """Slack webhook notification backend"""

    def __init__(self, webhook_url: str, channel: Optional[str] = None):
        self.webhook_url = str(OpSecret(webhook_url))
        self.channel = channel

    def send(self, message: str, context: Dict[str, Any]) -> bool:
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
        success = context.get("success", True)
        env = context.get("env", "unknown")
        version = context.get("version", "unknown")
        commit = context.get("commit", "unknown")[:7] if context.get("commit") else "unknown"
        changelog = context.get("changelog", "")
        project_name = context.get("project_name", "unknown")
        host_name = context.get("host_name", "")
        timestamp = context.get("timestamp", "")

        if success:
            status_emoji = ":white_check_mark:"
            status_text = "Deployment Succeeded"
        else:
            status_emoji = ":x:"
            status_text = "Deployment Failed"

        header_text = f"{status_emoji} {status_text}"
        if project_name:
            header_text = f"{status_emoji} {project_name}: {status_text}"

        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text, "emoji": True}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Environment:*\n{env}"},
                    {"type": "mrkdwn", "text": f"*Version:*\n{version}"},
                    {"type": "mrkdwn", "text": f"*Commit:*\n`{commit}`"},
                ]
            }
        ]

        if host_name:
            blocks[1]["fields"].append({"type": "mrkdwn", "text": f"*Host:*\n{host_name}"})

        if changelog and success:
            max_changelog_len = 2900
            if len(changelog) > max_changelog_len:
                changelog = changelog[:max_changelog_len] + "\n..."

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Changes:*\n{changelog}"}
            })

        if timestamp:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Deployed at {timestamp}"}]
            })

        payload: Dict[str, Any] = {"blocks": blocks, "text": message}

        if self.channel:
            payload["channel"] = self.channel

        return payload


class WebhookNotificationBackend(NotificationBackend):
    """Generic webhook backend (POST JSON)"""

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, include_message_in_body: bool = True):
        self.url = str(OpSecret(url))
        self.headers = headers or {}
        self.include_message_in_body = include_message_in_body

    def send(self, message: str, context: Dict[str, Any]) -> bool:
        try:
            payload = dict(context)
            if self.include_message_in_body:
                payload["message"] = message

            headers = {"Content-Type": "application/json", **self.headers}

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


def get_notification_backend(backend_type: str = "slack", config: Optional[Dict[str, Any]] = None) -> Optional[NotificationBackend]:
    """Factory function to create a notification backend"""
    config = config or {}

    if backend_type == "slack":
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            print("[NOTIFICATIONS] Warning: No webhook_url provided for Slack backend")
            return None

        return SlackNotificationBackend(webhook_url=webhook_url, channel=config.get("channel"))

    elif backend_type == "webhook":
        url = config.get("url") or config.get("webhook_url")
        if not url:
            print("[NOTIFICATIONS] Warning: No url provided for webhook backend")
            return None

        return WebhookNotificationBackend(url=url, headers=config.get("headers"))

    else:
        print(f"[NOTIFICATIONS] Warning: Unknown backend type '{backend_type}'")
        return None
