"""Microsoft Graph authenticated client using MSAL with robust retry logic."""

from __future__ import annotations

import asyncio

import httpx
import msal
import structlog

from config.settings import get_settings

logger = structlog.get_logger()

# Retryable status codes
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_BASE_DELAY = 2  # seconds


class GraphAPIError(Exception):
    """Error from Microsoft Graph API with status code."""

    def __init__(self, status_code: int, message: str, url: str = ""):
        self.status_code = status_code
        self.message = message
        self.url = url
        super().__init__(f"Graph API {status_code}: {message} (url={url[:100]})")


class GraphTokenExpiredError(GraphAPIError):
    """Raised when a delta token or resource is gone (HTTP 410)."""
    pass


class GraphNotFoundError(GraphAPIError):
    """Raised when a resource is not found (HTTP 404)."""
    pass


class GraphAuthError(GraphAPIError):
    """Raised on 401/403 — authentication or authorization failure."""
    pass


class GraphClient:
    """
    Thin async wrapper around Microsoft Graph API.

    Features:
    - Automatic retry with exponential backoff for transient errors (429, 5xx)
    - Respects Retry-After header on 429 throttling
    - Specific exception types for different error classes
    - Full/relative URL support (handles @odata.nextLink and @odata.deltaLink)
    """

    BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, access_token: str):
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        self._token_refresher: _TokenRefresher | None = None

    def set_token_refresher(self, refresher: _TokenRefresher) -> None:
        """Enable automatic token refresh on 401."""
        self._token_refresher = refresher

    def _resolve_url(self, url: str) -> str:
        """Resolve relative paths to full URLs; pass through full URLs."""
        return url if url.startswith("http") else f"{self.BASE_URL}{url}"

    async def get(
        self,
        url: str,
        params: dict | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> httpx.Response:
        """GET with retry logic."""
        return await self._request("GET", url, params=params, max_retries=max_retries)

    async def post(
        self,
        url: str,
        json: dict | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> httpx.Response:
        """POST with retry logic."""
        return await self._request("POST", url, json=json, max_retries=max_retries)

    async def patch(
        self,
        url: str,
        json: dict | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> httpx.Response:
        """PATCH with retry logic."""
        return await self._request("PATCH", url, json=json, max_retries=max_retries)

    async def delete(
        self,
        url: str,
        max_retries: int = _MAX_RETRIES,
    ) -> httpx.Response:
        """DELETE with retry logic."""
        return await self._request("DELETE", url, max_retries=max_retries)

    async def _request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json: dict | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> httpx.Response:
        """Execute an HTTP request with exponential backoff retry."""
        full_url = self._resolve_url(url)

        for attempt in range(max_retries + 1):
            try:
                response = await self.client.request(
                    method, full_url, params=params, json=json
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                if attempt == max_retries:
                    raise GraphAPIError(0, f"Connection error after {max_retries} retries: {e}", url)
                delay = _BASE_DELAY * (2 ** attempt)
                logger.warn(
                    "graph.connection_error",
                    attempt=attempt + 1,
                    delay=delay,
                    error=str(e),
                )
                await asyncio.sleep(delay)
                continue

            status = response.status_code

            # Success
            if 200 <= status < 300:
                return response

            # 401/403 — try token refresh once
            if status in (401, 403):
                if attempt == 0 and self._token_refresher:
                    logger.warn("graph.auth_error_refreshing", status=status)
                    new_token = await self._token_refresher.refresh()
                    self.client.headers["Authorization"] = f"Bearer {new_token}"
                    continue
                error_body = _extract_error(response)
                raise GraphAuthError(status, error_body, url)

            # 404 — not found (not retryable)
            if status == 404:
                error_body = _extract_error(response)
                raise GraphNotFoundError(status, error_body, url)

            # 410 — gone (delta token expired)
            if status == 410:
                error_body = _extract_error(response)
                raise GraphTokenExpiredError(status, error_body, url)

            # 429 — throttled (respect Retry-After header)
            if status == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warn(
                    "graph.throttled",
                    retry_after=retry_after,
                    attempt=attempt + 1,
                    url=url[:100],
                )
                await asyncio.sleep(retry_after)
                continue

            # 5xx — server error (retryable)
            if status in _RETRYABLE_STATUS_CODES:
                if attempt == max_retries:
                    error_body = _extract_error(response)
                    raise GraphAPIError(status, error_body, url)
                delay = _BASE_DELAY * (2 ** attempt)
                logger.warn(
                    "graph.server_error",
                    status=status,
                    attempt=attempt + 1,
                    delay=delay,
                )
                await asyncio.sleep(delay)
                continue

            # Other client errors — not retryable
            error_body = _extract_error(response)
            raise GraphAPIError(status, error_body, url)

        # Should not reach here
        raise GraphAPIError(0, "Max retries exceeded", url)

    # ── Convenience Methods ──

    async def get_download_url(self, drive_id: str, item_id: str) -> str:
        """Get a download URL for a driveItem.

        Tries @microsoft.graph.downloadUrl first, then falls back to the
        /content endpoint (302 redirect) for Sites.Selected permission
        where @downloadUrl is not returned.
        """
        response = await self.get(
            f"/drives/{drive_id}/items/{item_id}",
            params={"$select": "id,@microsoft.graph.downloadUrl"},
        )
        data = response.json()
        download_url = data.get("@microsoft.graph.downloadUrl")
        if download_url:
            return download_url

        # Fallback: /content returns a 302 redirect to the actual download URL
        logger.info(
            "graph.download_url_fallback",
            item_id=item_id,
            msg="@downloadUrl absent, using /content redirect",
        )
        content_url = self._resolve_url(f"/drives/{drive_id}/items/{item_id}/content")
        redirect_resp = await self.client.request("GET", content_url, follow_redirects=False)

        if redirect_resp.status_code == 302:
            location = redirect_resp.headers.get("Location")
            if location:
                return location

        # /content returned 200 directly or another success — return the content URL
        # so download_file can fetch it with follow_redirects=True
        if 200 <= redirect_resp.status_code < 300:
            return content_url

        raise GraphAPIError(
            redirect_resp.status_code,
            f"No downloadUrl and /content fallback failed for item {item_id}",
            f"/drives/{drive_id}/items/{item_id}",
        )

    async def download_file(self, download_url: str) -> bytes:
        """Download file bytes from a download URL (supports redirects)."""
        response = await self.client.get(download_url, follow_redirects=True)
        response.raise_for_status()
        return response.content

    async def get_item_permissions(
        self, drive_id: str, item_id: str
    ) -> list[dict]:
        """Fetch permissions for a driveItem."""
        response = await self.get(
            f"/drives/{drive_id}/items/{item_id}/permissions"
        )
        return response.json().get("value", [])

    async def get_transitive_members(self, group_id: str) -> list[dict]:
        """Get transitive members of an Entra ID group (paginated)."""
        members: list[dict] = []
        url = f"/groups/{group_id}/transitiveMembers"
        params = {"$select": "id", "$top": "999"}

        while url:
            response = await self.get(url, params=params)
            data = response.json()
            members.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None  # nextLink includes params

        return members

    async def list_site_drives(self, site_id: str) -> list[dict]:
        """List all document libraries (drives) for a SharePoint site."""
        response = await self.get(
            f"/sites/{site_id}/drives",
            params={"$select": "id,name,driveType,webUrl"},
        )
        return response.json().get("value", [])

    async def get_site_by_url(self, hostname: str, site_path: str) -> dict:
        """
        Resolve a SharePoint site URL to a site object.

        Example: hostname="contoso.sharepoint.com", site_path="/sites/engineering"
        """
        response = await self.get(
            f"/sites/{hostname}:{site_path}",
            params={"$select": "id,name,displayName,webUrl"},
        )
        return response.json()

    async def list_subscriptions(self) -> list[dict]:
        """List all Graph webhook subscriptions for this app."""
        response = await self.get("/subscriptions")
        return response.json().get("value", [])

    async def close(self) -> None:
        await self.client.aclose()


class _TokenRefresher:
    """Encapsulates MSAL token refresh logic."""

    def __init__(self, app: msal.ConfidentialClientApplication):
        self._app = app

    async def refresh(self) -> str:
        """Acquire a fresh token from MSAL."""
        result = self._app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise GraphAuthError(
                401,
                f"Token refresh failed: {result.get('error_description', result)}",
            )
        return result["access_token"]


class GraphClientFactory:
    """Create authenticated Graph API clients using MSAL client credentials flow."""

    @staticmethod
    async def create() -> GraphClient:
        settings = get_settings()
        app = msal.ConfidentialClientApplication(
            settings.GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{settings.GRAPH_TENANT_ID}",
            client_credential=settings.GRAPH_CLIENT_SECRET,
        )
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise RuntimeError(
                f"MSAL token acquisition failed: {result.get('error_description', result)}"
            )

        client = GraphClient(result["access_token"])
        client.set_token_refresher(_TokenRefresher(app))
        return client


def _extract_error(response: httpx.Response) -> str:
    """Extract error message from Graph API error response."""
    try:
        data = response.json()
        error = data.get("error", {})
        return error.get("message", response.text[:500])
    except Exception:
        return response.text[:500]
