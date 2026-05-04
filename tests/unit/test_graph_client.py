"""Unit tests for GraphClient retry logic and error handling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.connectors.graph_client import (
    GraphAPIError,
    GraphAuthError,
    GraphClient,
    GraphNotFoundError,
    GraphTokenExpiredError,
)


def _make_response(status_code: int, body: dict | str = "", headers: dict | None = None):
    """Build a minimal httpx.Response mock."""
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.headers = httpx.Headers(headers or {})
    if isinstance(body, dict):
        mock.json.return_value = body
        mock.text = str(body)
    else:
        mock.json.side_effect = Exception("not json")
        mock.text = body
    return mock


@pytest.fixture
def client():
    """Return a GraphClient with the underlying httpx client mocked."""
    gc = GraphClient(access_token="fake-token")
    gc.client = MagicMock()
    gc.client.request = AsyncMock()
    gc.client.headers = httpx.Headers({"Authorization": "Bearer fake-token"})
    return gc


class TestSuccessResponse:
    """Happy-path — 2xx responses are returned immediately."""

    @pytest.mark.asyncio
    async def test_200_returned(self, client):
        ok = _make_response(200, {"value": [1, 2, 3]})
        client.client.request = AsyncMock(return_value=ok)

        response = await client.get("/drives/d1/root/delta")

        assert response.status_code == 200
        client.client.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_201_returned(self, client):
        created = _make_response(201, {"id": "sub-001"})
        client.client.request = AsyncMock(return_value=created)

        response = await client.post("/subscriptions", json={"resource": "drives/d1/root"})

        assert response.status_code == 201


class TestRetryLogic:
    """5xx and 429 errors trigger exponential-backoff retries."""

    @pytest.mark.asyncio
    async def test_503_retries_then_succeeds(self, client):
        """Two 503 responses followed by 200 — should succeed on 3rd attempt."""
        fail = _make_response(503, {"error": {"message": "service unavailable"}})
        ok = _make_response(200, {"value": []})
        client.client.request = AsyncMock(side_effect=[fail, fail, ok])

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock):
            response = await client.get("/drives/d1/root/delta")

        assert response.status_code == 200
        assert client.client.request.call_count == 3

    @pytest.mark.asyncio
    async def test_5xx_exhausts_retries_raises(self, client):
        """Persistent 503 should raise GraphAPIError after max retries."""
        fail = _make_response(503, {"error": {"message": "persistent failure"}})
        client.client.request = AsyncMock(return_value=fail)

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GraphAPIError) as exc_info:
                await client.get("/drives/d1/root/delta", max_retries=2)

        assert exc_info.value.status_code == 503
        assert client.client.request.call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_500_retried(self, client):
        fail = _make_response(500, {"error": {"message": "internal error"}})
        ok = _make_response(200, {})
        client.client.request = AsyncMock(side_effect=[fail, ok])

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock):
            response = await client.get("/sites/s1")

        assert response.status_code == 200
        assert client.client.request.call_count == 2


class TestThrottling:
    """429 Too Many Requests respects the Retry-After header."""

    @pytest.mark.asyncio
    async def test_429_waits_retry_after(self, client):
        throttled = _make_response(
            429,
            {"error": {"message": "too many requests"}},
            headers={"Retry-After": "5"},
        )
        ok = _make_response(200, {"value": []})
        client.client.request = AsyncMock(side_effect=[throttled, ok])

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await client.get("/drives/d1/root/delta")

        assert response.status_code == 200
        mock_sleep.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_429_default_retry_after_when_header_missing(self, client):
        throttled = _make_response(429, {"error": {"message": "throttled"}})
        ok = _make_response(200, {})
        client.client.request = AsyncMock(side_effect=[throttled, ok])

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await client.get("/drives/d1/root/delta")

        mock_sleep.assert_called_once_with(30)  # default when no Retry-After header

    @pytest.mark.asyncio
    async def test_429_does_not_count_toward_max_retries(self, client):
        """429 should not decrement the retry counter — it's a pause, not a retry."""
        throttled = _make_response(429, headers={"Retry-After": "1"})
        ok = _make_response(200, {})
        client.client.request = AsyncMock(side_effect=[throttled, throttled, ok])

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock):
            response = await client.get("/drives/d1/root/delta", max_retries=1)

        # Would fail if 429 counted against max_retries (only 1 retry allowed)
        assert response.status_code == 200


class TestSpecificErrors:
    """HTTP 404, 410, 401, 403 map to typed exceptions."""

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self, client):
        not_found = _make_response(
            404,
            {"error": {"message": "Item not found"}},
        )
        client.client.request = AsyncMock(return_value=not_found)

        with pytest.raises(GraphNotFoundError) as exc_info:
            await client.get("/drives/d1/items/missing-item")

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_410_raises_token_expired(self, client):
        gone = _make_response(
            410,
            {"error": {"message": "Delta token expired"}},
        )
        client.client.request = AsyncMock(return_value=gone)

        with pytest.raises(GraphTokenExpiredError) as exc_info:
            await client.get("https://graph.microsoft.com/v1.0/drives/d1/root/delta?token=abc")

        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_401_without_refresher_raises_auth_error(self, client):
        """Without a token refresher, 401 should raise GraphAuthError immediately."""
        unauthorized = _make_response(401, {"error": {"message": "Unauthorized"}})
        client.client.request = AsyncMock(return_value=unauthorized)
        client._token_refresher = None

        with pytest.raises(GraphAuthError) as exc_info:
            await client.get("/drives/d1/root/delta")

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_403_raises_auth_error(self, client):
        forbidden = _make_response(403, {"error": {"message": "Forbidden"}})
        client.client.request = AsyncMock(return_value=forbidden)

        with pytest.raises(GraphAuthError) as exc_info:
            await client.get("/sites/s1/drives")

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_404_not_retried(self, client):
        """404 should raise immediately, not retry."""
        not_found = _make_response(404, {"error": {"message": "Not found"}})
        client.client.request = AsyncMock(return_value=not_found)

        with pytest.raises(GraphNotFoundError):
            await client.get("/drives/d1/items/missing")

        # Should only be called once — no retries on 404
        client.client.request.assert_called_once()


class TestTokenRefresh:
    """On 401, a configured token refresher should be called once."""

    @pytest.mark.asyncio
    async def test_401_triggers_token_refresh_and_retries(self, client):
        """First 401 → refresh token → retry succeeds."""
        unauthorized = _make_response(401, {"error": {"message": "Token expired"}})
        ok = _make_response(200, {"value": []})
        client.client.request = AsyncMock(side_effect=[unauthorized, ok])

        refresher = MagicMock()
        refresher.refresh = AsyncMock(return_value="new-token")
        client._token_refresher = refresher

        response = await client.get("/drives/d1/root/delta")

        assert response.status_code == 200
        refresher.refresh.assert_called_once()
        # Token should be updated in the client headers
        assert client.client.headers["Authorization"] == "Bearer new-token"

    @pytest.mark.asyncio
    async def test_persistent_401_raises_after_one_refresh(self, client):
        """If refresh doesn't fix the 401, stop retrying and raise."""
        unauthorized = _make_response(401, {"error": {"message": "Token invalid"}})
        client.client.request = AsyncMock(return_value=unauthorized)

        refresher = MagicMock()
        refresher.refresh = AsyncMock(return_value="new-token")
        client._token_refresher = refresher

        with pytest.raises(GraphAuthError):
            await client.get("/drives/d1/root/delta")

        # Refresh called once; not called again on second 401
        refresher.refresh.assert_called_once()


class TestConnectionErrors:
    """Network-level errors (connect timeout, read timeout) are retried."""

    @pytest.mark.asyncio
    async def test_connect_error_retried(self, client):
        ok = _make_response(200, {"value": []})
        client.client.request = AsyncMock(
            side_effect=[httpx.ConnectError("connection refused"), ok]
        )

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock):
            response = await client.get("/drives/d1/root/delta")

        assert response.status_code == 200
        assert client.client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_read_timeout_retried(self, client):
        ok = _make_response(200, {"value": []})
        client.client.request = AsyncMock(
            side_effect=[httpx.ReadTimeout("read timeout"), ok]
        )

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock):
            response = await client.get("/drives/d1/root/delta")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_persistent_connect_error_raises(self, client):
        client.client.request = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with patch("src.connectors.graph_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GraphAPIError) as exc_info:
                await client.get("/drives/d1/root/delta", max_retries=2)

        assert "Connection error after" in str(exc_info.value)
        assert client.client.request.call_count == 3


class TestUrlResolution:
    """Full URLs (nextLink/deltaLink) are passed through unchanged."""

    @pytest.mark.asyncio
    async def test_full_url_not_prefixed(self, client):
        ok = _make_response(200, {"value": []})
        client.client.request = AsyncMock(return_value=ok)

        full_url = "https://graph.microsoft.com/v1.0/drives/d1/root/delta?token=abc123"
        await client.get(full_url)

        call_args = client.client.request.call_args
        assert call_args[0][1] == full_url  # URL passed as-is

    @pytest.mark.asyncio
    async def test_relative_path_prefixed(self, client):
        ok = _make_response(200, {"value": []})
        client.client.request = AsyncMock(return_value=ok)

        await client.get("/drives/d1/root/delta")

        call_args = client.client.request.call_args
        assert call_args[0][1].startswith("https://graph.microsoft.com/v1.0")


class TestConvenienceMethods:
    """Convenience methods correctly construct API calls."""

    @pytest.mark.asyncio
    async def test_get_item_permissions(self, client):
        perms = _make_response(200, {"value": [{"id": "p1", "roles": ["read"]}]})
        client.client.request = AsyncMock(return_value=perms)

        result = await client.get_item_permissions("drive-1", "item-abc")

        assert result == [{"id": "p1", "roles": ["read"]}]
        url_called = client.client.request.call_args[0][1]
        assert "drives/drive-1/items/item-abc/permissions" in url_called

    @pytest.mark.asyncio
    async def test_get_transitive_members_paginated(self, client):
        """get_transitive_members follows @odata.nextLink pagination."""
        page1 = _make_response(200, {
            "value": [{"id": "u1", "@odata.type": "#microsoft.graph.user"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups/g1/transitiveMembers?$skiptoken=abc",
        })
        page2 = _make_response(200, {
            "value": [{"id": "u2", "@odata.type": "#microsoft.graph.user"}],
        })
        client.client.request = AsyncMock(side_effect=[page1, page2])

        members = await client.get_transitive_members("g1")

        assert len(members) == 2
        assert members[0]["id"] == "u1"
        assert members[1]["id"] == "u2"
        assert client.client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_list_site_drives(self, client):
        drives = _make_response(200, {
            "value": [
                {"id": "d1", "name": "Documents", "driveType": "documentLibrary"},
                {"id": "d2", "name": "Site Assets", "driveType": "documentLibrary"},
            ]
        })
        client.client.request = AsyncMock(return_value=drives)

        result = await client.list_site_drives("site-001")

        assert len(result) == 2
        url_called = client.client.request.call_args[0][1]
        assert "sites/site-001/drives" in url_called

    @pytest.mark.asyncio
    async def test_get_site_by_url(self, client):
        site = _make_response(200, {
            "id": "contoso.sharepoint.com,abc,def",
            "displayName": "Engineering",
            "webUrl": "https://contoso.sharepoint.com/sites/engineering",
        })
        client.client.request = AsyncMock(return_value=site)

        result = await client.get_site_by_url(
            "contoso.sharepoint.com", "/sites/engineering"
        )

        assert result["id"] == "contoso.sharepoint.com,abc,def"
        url_called = client.client.request.call_args[0][1]
        assert "contoso.sharepoint.com:/sites/engineering" in url_called

    @pytest.mark.asyncio
    async def test_get_download_url(self, client):
        item = _make_response(200, {
            "id": "item-001",
            "@microsoft.graph.downloadUrl": "https://download.example.com/file.pdf",
        })
        client.client.request = AsyncMock(return_value=item)

        url = await client.get_download_url("drive-1", "item-001")

        assert url == "https://download.example.com/file.pdf"

    @pytest.mark.asyncio
    async def test_get_download_url_raises_if_missing(self, client):
        item = _make_response(200, {"id": "item-001"})
        client.client.request = AsyncMock(return_value=item)

        with pytest.raises(GraphAPIError) as exc_info:
            await client.get_download_url("drive-1", "item-001")

        assert "No downloadUrl" in str(exc_info.value)
