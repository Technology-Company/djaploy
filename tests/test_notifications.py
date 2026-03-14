"""Tests for djaploy notification backends."""

import unittest
from unittest.mock import patch

from djaploy.notifications import (
    format_slack_timestamp,
    SlackNotificationBackend,
    get_notification_backend,
)


class TestFormatSlackTimestamp(unittest.TestCase):
    """Test Slack timestamp formatting."""

    def test_utc_iso_format(self):
        result = format_slack_timestamp("2025-01-15T12:30:00Z")
        self.assertIn("<!date^", result)
        self.assertIn("|2025-01-15T12:30:00Z>", result)

    def test_iso_with_timezone_offset(self):
        result = format_slack_timestamp("2025-01-15T12:30:00+00:00")
        self.assertIn("<!date^", result)

    def test_invalid_timestamp_returns_original(self):
        result = format_slack_timestamp("not-a-date")
        self.assertEqual(result, "not-a-date")


class TestSlackPayloadBuilder(unittest.TestCase):
    """Test SlackNotificationBackend._build_payload."""

    def setUp(self):
        with patch("djaploy.notifications.OpSecret", side_effect=lambda x: x):
            self.backend = SlackNotificationBackend(webhook_url="https://hooks.slack.com/test")

    def test_success_payload_has_header(self):
        payload = self.backend._build_payload("msg", {
            "success": True, "env": "production", "version": "v1.0.0",
            "commit": "abc1234", "display_name": "MyApp", "changelog": "Fixed bugs.",
        })
        header = payload["blocks"][0]["text"]["text"]
        self.assertIn("Successful", header)
        self.assertIn("MyApp", header)

    def test_failure_payload_has_error(self):
        payload = self.backend._build_payload("msg", {
            "success": False, "env": "staging", "version": "v1.0.0",
            "commit": "abc1234", "display_name": "MyApp",
            "error_message": "Connection refused",
        })
        header = payload["blocks"][0]["text"]["text"]
        self.assertIn("Failed", header)
        # Error block present
        error_blocks = [b for b in payload["blocks"] if "Error" in b.get("text", {}).get("text", "")]
        self.assertTrue(len(error_blocks) > 0)

    def test_changelog_truncated_when_too_long(self):
        long_changelog = "x" * 5000
        payload = self.backend._build_payload("msg", {
            "success": True, "env": "prod", "version": "v1.0.0",
            "commit": "abc", "display_name": "App", "changelog": long_changelog,
        })
        for block in payload["blocks"]:
            text = block.get("text", {}).get("text", "")
            if "Changes" in text:
                self.assertLessEqual(len(text), 3100)

    def test_channel_included_when_set(self):
        with patch("djaploy.notifications.OpSecret", side_effect=lambda x: x):
            backend = SlackNotificationBackend(webhook_url="https://test", channel="#deploys")
        payload = backend._build_payload("msg", {"success": True, "env": "prod"})
        self.assertEqual(payload["channel"], "#deploys")

    def test_timestamp_block_added(self):
        payload = self.backend._build_payload("msg", {
            "success": True, "env": "prod",
            "timestamp": "2025-01-15T12:00:00Z",
        })
        context_blocks = [b for b in payload["blocks"] if b["type"] == "context"]
        self.assertTrue(len(context_blocks) > 0)


class TestGetNotificationBackend(unittest.TestCase):
    """Test the factory function."""

    def test_slack_with_url(self):
        with patch("djaploy.notifications.OpSecret", side_effect=lambda x: x):
            backend = get_notification_backend("slack", {"webhook_url": "https://test"})
        self.assertIsInstance(backend, SlackNotificationBackend)

    def test_slack_without_url_returns_none(self):
        backend = get_notification_backend("slack", {})
        self.assertIsNone(backend)

    def test_unknown_type_returns_none(self):
        backend = get_notification_backend("telegram", {})
        self.assertIsNone(backend)

    def test_webhook_type(self):
        from djaploy.notifications import WebhookNotificationBackend
        with patch("djaploy.notifications.OpSecret", side_effect=lambda x: x):
            backend = get_notification_backend("webhook", {"url": "https://test"})
        self.assertIsInstance(backend, WebhookNotificationBackend)


if __name__ == "__main__":
    unittest.main()
