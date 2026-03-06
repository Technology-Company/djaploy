"""
Notification backends for djaploy
"""

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from .certificates import OpSecret


def format_human_timestamp(iso_timestamp: str) -> str:
    """Format ISO timestamp to human-readable format (Today at 8:07 PM, Yesterday, Feb 27th)"""
    from datetime import timedelta

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        dt_local = dt.astimezone()
        now_local = now.astimezone()

        time_str = dt_local.strftime("%-I:%M %p").replace("AM", "am").replace("PM", "pm")
        yesterday = (now_local - timedelta(days=1)).date()

        if dt_local.date() == now_local.date():
            return f"Today at {time_str}"
        elif dt_local.date() == yesterday:
            return f"Yesterday at {time_str}"
        else:
            day = dt_local.day
            suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
            date_str = dt_local.strftime(f"%b {day}{suffix}")
            return f"{date_str} at {time_str}"
    except Exception:
        return iso_timestamp


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
        error_message = context.get("error_message", "")
        display_name = context.get("display_name", "unknown")
        timestamp = context.get("timestamp", "")

        if success:
            header_text = f"Deployment Successful: {display_name} {version}"
        else:
            header_text = f"Deployment Failed: {display_name} {version}"

        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text, "emoji": True}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Environment:* {env}\n*Commit:* `{commit}`"}
            }
        ]

        # Changes (success) or Error (failure) in code block
        if success and changelog:
            max_len = 2900
            if len(changelog) > max_len:
                changelog = changelog[:max_len] + "..."

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Changes:*\n```{changelog}```"}
            })
        elif not success and error_message:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Error:*\n```{error_message}```"}
            })

        # Timestamp in human-readable format
        if timestamp:
            human_timestamp = format_human_timestamp(timestamp)
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": human_timestamp}]
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
