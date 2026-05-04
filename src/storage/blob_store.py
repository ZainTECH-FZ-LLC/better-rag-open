"""Azure Blob Storage client for document staging and generated file serving."""

from __future__ import annotations

import structlog
from azure.identity import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from config.settings import get_settings

logger = structlog.get_logger()


class AzureBlobStore:
    """
    Azure Blob Storage wrapper for document staging.

    Documents are uploaded before processing and served for generated file downloads.
    Path convention: {site_name}/{library_name}/{item_id}/{filename}
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: BlobServiceClient | None = None

    async def _get_client(self) -> BlobServiceClient:
        if self._client is None:
            if not self.settings.BLOB_ACCOUNT_URL:
                raise RuntimeError("BLOB_ACCOUNT_URL not configured")
            if self.settings.BLOB_ACCOUNT_KEY:
                # Key-based auth (dev/test) — pass key string directly
                credential = self.settings.BLOB_ACCOUNT_KEY
            else:
                # Production: Managed Identity
                credential = DefaultAzureCredential()
            self._client = BlobServiceClient(
                account_url=self.settings.BLOB_ACCOUNT_URL,
                credential=credential,
            )
        return self._client

    async def upload(self, blob_path: str, data: bytes) -> str:
        """
        Upload file bytes to Azure Blob Storage.

        Args:
            blob_path: The path within the container (e.g., staging/drive123/item456/report.pdf).
            data: File content bytes.

        Returns:
            The full blob path.
        """
        client = await self._get_client()
        container = client.get_container_client(self.settings.BLOB_CONTAINER_NAME)

        # Ensure container exists
        try:
            await container.create_container()
        except Exception:
            pass  # Container already exists

        blob = container.get_blob_client(blob_path)
        await blob.upload_blob(data, overwrite=True)

        logger.info(
            "blob_store.uploaded",
            path=blob_path,
            size_bytes=len(data),
        )
        return blob_path

    async def download(self, blob_path: str) -> bytes:
        """Download file bytes from Azure Blob Storage."""
        client = await self._get_client()
        container = client.get_container_client(self.settings.BLOB_CONTAINER_NAME)
        blob = container.get_blob_client(blob_path)

        stream = await blob.download_blob()
        data = await stream.readall()

        logger.info(
            "blob_store.downloaded",
            path=blob_path,
            size_bytes=len(data),
        )
        return data

    async def delete(self, blob_path: str) -> None:
        """Delete a blob from storage."""
        client = await self._get_client()
        container = client.get_container_client(self.settings.BLOB_CONTAINER_NAME)
        blob = container.get_blob_client(blob_path)

        try:
            await blob.delete_blob()
            logger.info("blob_store.deleted", path=blob_path)
        except Exception as e:
            logger.warn("blob_store.delete_failed", path=blob_path, error=str(e))

    async def get_url(self, blob_path: str) -> str:
        """Get the full URL for a blob (for download links)."""
        return f"{self.settings.BLOB_ACCOUNT_URL}/{self.settings.BLOB_CONTAINER_NAME}/{blob_path}"

    async def get_sas_url(self, blob_path: str, expiry_hours: int = 1) -> str:
        """Generate a time-limited SAS URL for a blob (required for Azure DI url_source)."""
        from datetime import datetime, timedelta, timezone

        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        account_name = self.settings.BLOB_ACCOUNT_URL.split("//")[1].split(".")[0]
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=self.settings.BLOB_CONTAINER_NAME,
            blob_name=blob_path,
            account_key=self.settings.BLOB_ACCOUNT_KEY,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
        )
        return f"{self.settings.BLOB_ACCOUNT_URL}/{self.settings.BLOB_CONTAINER_NAME}/{blob_path}?{sas_token}"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
