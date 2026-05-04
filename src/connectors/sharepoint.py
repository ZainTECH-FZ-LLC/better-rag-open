"""SharePoint connector — fetch documents and metadata via Microsoft Graph API."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import structlog

from config.settings import get_settings
from src.connectors.graph_client import GraphClient, GraphClientFactory, GraphNotFoundError

logger = structlog.get_logger()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}


@dataclass
class SharePointDocument:
    """Metadata and content for a SharePoint document."""

    drive_id: str
    item_id: str
    name: str
    file_type: str  # pdf, docx, pptx, xlsx
    site_id: str
    site_name: str
    library_name: str
    sharepoint_url: str
    content_hash: str  # eTag / cTag for change detection
    author: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    modified_by: str | None = None
    size_bytes: int = 0
    content: bytes | None = None
    metadata: dict = field(default_factory=dict)


class SharePointConnector:
    """
    Fetches documents and metadata from SharePoint/OneDrive via Microsoft Graph API.

    Responsibilities:
    - List document libraries (drives) for a SharePoint site
    - Download document content (bytes) for processing
    - Extract rich metadata from Graph API response
    - Resolve site URLs to site IDs
    """

    def __init__(self, client: GraphClient | None = None) -> None:
        self._client = client
        self.settings = get_settings()

    async def _get_client(self) -> GraphClient:
        if self._client is not None:
            return self._client
        return await GraphClientFactory.create()

    async def resolve_site(self, site_url: str) -> dict:
        """
        Resolve a SharePoint site URL to its Graph API site object.

        Args:
            site_url: Full SharePoint URL e.g. "https://contoso.sharepoint.com/sites/engineering"

        Returns:
            Site dict with id, name, displayName, webUrl.
        """
        parsed = urlparse(site_url)
        hostname = parsed.hostname  # e.g. contoso.sharepoint.com
        site_path = parsed.path     # e.g. /sites/engineering

        client = await self._get_client()
        site = await client.get_site_by_url(hostname, site_path)
        logger.info("sharepoint.site_resolved", site_id=site.get("id"), url=site_url)
        return site

    async def list_drives(self, site_id: str) -> list[dict]:
        """List all document libraries (drives) for a SharePoint site."""
        client = await self._get_client()
        drives = await client.list_site_drives(site_id)
        logger.info("sharepoint.drives_listed", site_id=site_id, count=len(drives))
        return drives

    async def fetch_document(
        self,
        drive_id: str,
        item_id: str,
        site_id: str = "",
        site_name: str = "",
        library_name: str = "",
    ) -> SharePointDocument | None:
        """
        Fetch a document's metadata and content bytes from SharePoint.

        Args:
            drive_id: The drive ID containing the document.
            item_id: The driveItem ID.
            site_id: Site ID for metadata enrichment.
            site_name: Site display name for metadata.
            library_name: Drive/library name for metadata.

        Returns:
            SharePointDocument with content bytes, or None if unsupported file type.
        """
        client = await self._get_client()

        # Get item metadata
        response = await client.get(
            f"/drives/{drive_id}/items/{item_id}",
            params={
                "$select": (
                    "id,name,file,size,webUrl,eTag,cTag,"
                    "createdDateTime,lastModifiedDateTime,"
                    "createdBy,lastModifiedBy,parentReference,"
                    "@microsoft.graph.downloadUrl"
                )
            },
        )
        item = response.json()

        name = item.get("name", "")
        suffix = Path(name).suffix.lower()

        if suffix not in SUPPORTED_EXTENSIONS:
            logger.debug("sharepoint.unsupported_file", name=name, suffix=suffix)
            return None

        file_type = suffix.lstrip(".")
        sharepoint_url = item.get("webUrl", "")
        content_hash = item.get("cTag") or item.get("eTag", "")

        # Extract author/modifier info
        created_by_data = item.get("createdBy", {}).get("user", {})
        modified_by_data = item.get("lastModifiedBy", {}).get("user", {})
        author = created_by_data.get("displayName") or created_by_data.get("email")
        modified_by = modified_by_data.get("displayName") or modified_by_data.get("email")

        # Resolve library name from parentReference if not provided
        if not library_name:
            parent_ref = item.get("parentReference", {})
            library_name = parent_ref.get("name", "")

        # Download content
        download_url = item.get("@microsoft.graph.downloadUrl")
        if download_url:
            content = await client.download_file(download_url)
        else:
            # Fallback: use the download URL endpoint
            download_url = await client.get_download_url(drive_id, item_id)
            content = await client.download_file(download_url)

        doc = SharePointDocument(
            drive_id=drive_id,
            item_id=item_id,
            name=name,
            file_type=file_type,
            site_id=site_id,
            site_name=site_name,
            library_name=library_name,
            sharepoint_url=sharepoint_url,
            content_hash=content_hash,
            author=author,
            created_at=item.get("createdDateTime"),
            modified_at=item.get("lastModifiedDateTime"),
            modified_by=modified_by,
            size_bytes=item.get("size", 0),
            content=content,
            metadata={
                "graph_item_id": item_id,
                "drive_id": drive_id,
                "site_id": site_id,
                "site_name": site_name,
                "library_name": library_name,
            },
        )

        logger.info(
            "sharepoint.document_fetched",
            name=name,
            file_type=file_type,
            size_bytes=doc.size_bytes,
        )
        return doc

    async def fetch_document_from_drive_item(
        self,
        drive_id: str,
        item: dict,
        site_id: str = "",
        site_name: str = "",
        library_name: str = "",
    ) -> SharePointDocument | None:
        """
        Build a SharePointDocument from an already-fetched driveItem dict.
        Downloads content only if the item is a supported file type.

        Used during delta sync where we already have the item metadata.
        """
        name = item.get("name", "")
        suffix = Path(name).suffix.lower()

        if suffix not in SUPPORTED_EXTENSIONS:
            return None

        if "deleted" in item:
            return None  # Deleted items have no content

        item_id = item.get("id", "")
        file_type = suffix.lstrip(".")
        sharepoint_url = item.get("webUrl", "")
        content_hash = item.get("cTag") or item.get("eTag", "")

        created_by_data = item.get("createdBy", {}).get("user", {})
        modified_by_data = item.get("lastModifiedBy", {}).get("user", {})
        author = created_by_data.get("displayName") or created_by_data.get("email")
        modified_by = modified_by_data.get("displayName") or modified_by_data.get("email")

        if not library_name:
            parent_ref = item.get("parentReference", {})
            library_name = parent_ref.get("name", "")

        client = await self._get_client()

        # Try inline download URL first (often included in delta responses)
        download_url = item.get("@microsoft.graph.downloadUrl")
        try:
            if download_url:
                content = await client.download_file(download_url)
            else:
                download_url = await client.get_download_url(drive_id, item_id)
                content = await client.download_file(download_url)
        except GraphNotFoundError:
            logger.warn("sharepoint.item_not_found", item_id=item_id)
            return None

        return SharePointDocument(
            drive_id=drive_id,
            item_id=item_id,
            name=name,
            file_type=file_type,
            site_id=site_id,
            site_name=site_name,
            library_name=library_name,
            sharepoint_url=sharepoint_url,
            content_hash=content_hash,
            author=author,
            created_at=item.get("createdDateTime"),
            modified_at=item.get("lastModifiedDateTime"),
            modified_by=modified_by,
            size_bytes=item.get("size", 0),
            content=content,
            metadata={
                "graph_item_id": item_id,
                "drive_id": drive_id,
                "site_id": site_id,
                "site_name": site_name,
                "library_name": library_name,
            },
        )

    async def list_items_in_drive(
        self,
        drive_id: str,
        folder_id: str = "root",
        recursive: bool = True,
    ) -> list[dict]:
        """
        List all driveItems in a drive (or subfolder), optionally recursively.

        Returns raw driveItem dicts for further processing.
        """
        client = await self._get_client()
        items: list[dict] = []
        folders_to_scan = [folder_id]

        while folders_to_scan:
            folder = folders_to_scan.pop(0)
            url = f"/drives/{drive_id}/items/{folder}/children"
            params = {
                "$select": (
                    "id,name,file,folder,size,webUrl,eTag,cTag,"
                    "createdDateTime,lastModifiedDateTime,"
                    "createdBy,lastModifiedBy,parentReference"
                ),
                "$top": "200",
            }

            while url:
                response = await client.get(url, params=params)
                data = response.json()

                for item in data.get("value", []):
                    if "file" in item:
                        name = item.get("name", "")
                        if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
                            items.append(item)
                    elif "folder" in item and recursive:
                        folders_to_scan.append(item["id"])

                url = data.get("@odata.nextLink")
                params = None  # nextLink already has params

        logger.info(
            "sharepoint.items_listed",
            drive_id=drive_id,
            item_count=len(items),
        )
        return items

    async def get_item_metadata(self, drive_id: str, item_id: str) -> dict:
        """
        Fetch full metadata for a single driveItem.
        Returns raw Graph API response dict.
        """
        client = await self._get_client()
        response = await client.get(
            f"/drives/{drive_id}/items/{item_id}",
            params={
                "$select": (
                    "id,name,file,size,webUrl,eTag,cTag,"
                    "createdDateTime,lastModifiedDateTime,"
                    "createdBy,lastModifiedBy,parentReference"
                )
            },
        )
        return response.json()
