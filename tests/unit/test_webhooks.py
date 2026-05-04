"""Unit tests for the webhook notification endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    with patch("config.settings.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.APP_NAME = "better-rag-test"
        settings.GENERATED_DIR.mkdir = lambda parents=True, exist_ok=True: None
        settings.BETTER_RAG_API_KEY = ""
        settings.WEBHOOK_CLIENT_STATE = ""
        settings.PUBLIC_BASE_URL = "https://test.example.com"

        from src.main import app
        yield TestClient(app, raise_server_exceptions=False)


class TestWebhookValidation:
    """Tests for webhook subscription validation flow."""

    def test_validation_echoes_token(self, client):
        """Graph sends validationToken on subscription creation; we echo it."""
        response = client.post(
            "/webhooks/graph/notify?validationToken=abc123-test-token"
        )
        assert response.status_code == 200
        assert response.text == "abc123-test-token"
        assert response.headers["content-type"] == "text/plain; charset=utf-8"

    def test_validation_with_special_characters(self, client):
        """Validation tokens may contain URL-encoded special chars."""
        token = "Validation%3Atest%2Btoken%3D123"
        response = client.post(
            f"/webhooks/graph/notify?validationToken={token}"
        )
        assert response.status_code == 200


class TestWebhookNotification:
    """Tests for webhook change notification processing."""

    @patch("src.api.routes.webhooks.run_delta_sync")
    def test_notification_dispatches_delta_sync(self, mock_task, client):
        """Valid notification dispatches a delta sync Celery task."""
        mock_task.apply_async = AsyncMock()

        payload = {
            "value": [
                {
                    "resource": "drives/drive-abc/root",
                    "changeType": "updated",
                    "clientState": "site-xyz:drive-abc",
                    "subscriptionId": "sub-001",
                }
            ]
        }

        response = client.post("/webhooks/graph/notify", json=payload)
        assert response.status_code == 202

        mock_task.apply_async.assert_called_once_with(
            kwargs={"site_id": "site-xyz", "drive_id": "drive-abc"},
            queue="rag.ingestion",
            task_id="delta_sync_drive-abc",
        )

    @patch("src.api.routes.webhooks.run_delta_sync")
    def test_multiple_notifications_dispatched(self, mock_task, client):
        """Multiple notifications in one payload dispatch multiple tasks."""
        mock_task.apply_async = AsyncMock()

        payload = {
            "value": [
                {
                    "resource": "drives/drive-001/root",
                    "changeType": "updated",
                    "clientState": "site-a:drive-001",
                },
                {
                    "resource": "drives/drive-002/root",
                    "changeType": "updated",
                    "clientState": "site-b:drive-002",
                },
            ]
        }

        response = client.post("/webhooks/graph/notify", json=payload)
        assert response.status_code == 202
        assert mock_task.apply_async.call_count == 2

    @patch("src.api.routes.webhooks.run_delta_sync")
    def test_empty_notifications_returns_202(self, mock_task, client):
        """Empty notification array still returns 202."""
        payload = {"value": []}
        response = client.post("/webhooks/graph/notify", json=payload)
        assert response.status_code == 202
        mock_task.apply_async.assert_not_called()

    @patch("src.api.routes.webhooks.run_delta_sync")
    def test_unparseable_resource_skipped(self, mock_task, client):
        """Notifications with unparseable resource paths are skipped."""
        mock_task.apply_async = AsyncMock()

        payload = {
            "value": [
                {
                    "resource": "some/invalid/path",
                    "changeType": "updated",
                    "clientState": "site:drive",
                }
            ]
        }

        response = client.post("/webhooks/graph/notify", json=payload)
        assert response.status_code == 202
        mock_task.apply_async.assert_not_called()


class TestWebhookClientStateVerification:
    """Tests for clientState verification security."""

    @patch("src.api.routes.webhooks.get_settings")
    @patch("src.api.routes.webhooks.run_delta_sync")
    def test_valid_client_state_accepted(self, mock_task, mock_settings, client):
        """Notification with matching clientState secret is accepted."""
        mock_task.apply_async = AsyncMock()
        mock_settings.return_value.WEBHOOK_CLIENT_STATE = "my-secret"

        payload = {
            "value": [
                {
                    "resource": "drives/drive-abc/root",
                    "changeType": "updated",
                    "clientState": "site-xyz:drive-abc:my-secret",
                }
            ]
        }

        response = client.post("/webhooks/graph/notify", json=payload)
        assert response.status_code == 202
        mock_task.apply_async.assert_called_once()

    @patch("src.api.routes.webhooks.get_settings")
    @patch("src.api.routes.webhooks.run_delta_sync")
    def test_invalid_client_state_rejected(self, mock_task, mock_settings, client):
        """Notification with wrong clientState secret is rejected."""
        mock_task.apply_async = AsyncMock()
        mock_settings.return_value.WEBHOOK_CLIENT_STATE = "my-secret"

        payload = {
            "value": [
                {
                    "resource": "drives/drive-abc/root",
                    "changeType": "updated",
                    "clientState": "site-xyz:drive-abc:wrong-secret",
                }
            ]
        }

        response = client.post("/webhooks/graph/notify", json=payload)
        assert response.status_code == 202
        # Task should NOT be dispatched due to invalid clientState
        mock_task.apply_async.assert_not_called()

    @patch("src.api.routes.webhooks.get_settings")
    @patch("src.api.routes.webhooks.run_delta_sync")
    def test_missing_client_state_secret_rejected(self, mock_task, mock_settings, client):
        """Notification without secret portion is rejected when secret is configured."""
        mock_task.apply_async = AsyncMock()
        mock_settings.return_value.WEBHOOK_CLIENT_STATE = "my-secret"

        payload = {
            "value": [
                {
                    "resource": "drives/drive-abc/root",
                    "changeType": "updated",
                    "clientState": "site-xyz:drive-abc",
                }
            ]
        }

        response = client.post("/webhooks/graph/notify", json=payload)
        assert response.status_code == 202
        mock_task.apply_async.assert_not_called()
