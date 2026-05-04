"""Permission Resolver — captures RBAC from Entra ID via Microsoft Graph API."""

from __future__ import annotations

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.connectors.graph_client import GraphClient, GraphNotFoundError
from src.models.document import Document, DocumentPermission, DocumentUserAccess

logger = structlog.get_logger()


class PermissionResolver:
    """
    Resolves document permissions from SharePoint and expands group memberships
    into the document_user_access table for fast query-time RBAC filtering.
    """

    def __init__(self, graph: GraphClient, db: AsyncSession):
        self.graph = graph
        self.db = db

    async def resolve_and_store(self, document: Document) -> int:
        """
        Fetch permissions for a document from Graph API, store them,
        and expand group memberships into user access entries.

        Returns the number of user access entries created.
        """
        # 1. Fetch permissions from Graph API
        permissions = await self._fetch_permissions(
            document.drive_id, document.drive_item_id
        )

        # 2. Clear existing permissions for this document
        await self.db.execute(
            delete(DocumentPermission).where(
                DocumentPermission.document_id == document.id
            )
        )
        await self.db.execute(
            delete(DocumentUserAccess).where(
                DocumentUserAccess.document_id == document.id
            )
        )

        # 3. Store permissions and expand groups
        all_user_ids: set[str] = set()

        for perm in permissions:
            granted_to = perm.get("grantedToV2") or perm.get("grantedTo") or {}
            roles = perm.get("roles", ["read"])
            role = roles[0] if roles else "read"

            # User permission
            user = granted_to.get("user")
            if user and user.get("id"):
                user_id = user["id"]
                self.db.add(
                    DocumentPermission(
                        document_id=document.id,
                        principal_type="user",
                        principal_id=user_id,
                        role=role,
                    )
                )
                all_user_ids.add(user_id)

            # Group permission — expand to member users
            group = granted_to.get("group")
            if group and group.get("id"):
                group_id = group["id"]
                self.db.add(
                    DocumentPermission(
                        document_id=document.id,
                        principal_type="group",
                        principal_id=group_id,
                        role=role,
                    )
                )
                member_ids = await self._expand_group(group_id)
                all_user_ids.update(member_ids)

            # siteUser (SharePoint sharing link targets)
            site_user = granted_to.get("siteUser")
            if site_user and site_user.get("id"):
                login = site_user.get("loginName", site_user["id"])
                self.db.add(
                    DocumentPermission(
                        document_id=document.id,
                        principal_type="site_user",
                        principal_id=login,
                        role=role,
                    )
                )

        # 4. Store expanded user access entries
        for user_id in all_user_ids:
            self.db.add(
                DocumentUserAccess(
                    document_id=document.id,
                    user_id=user_id,
                )
            )

        await self.db.flush()

        logger.info(
            "permissions.resolved",
            document_id=str(document.id),
            permission_count=len(permissions),
            user_access_count=len(all_user_ids),
        )
        return len(all_user_ids)

    async def _fetch_permissions(
        self, drive_id: str, item_id: str
    ) -> list[dict]:
        """Fetch permissions for a driveItem from Graph API."""
        try:
            return await self.graph.get_item_permissions(drive_id, item_id)
        except GraphNotFoundError:
            logger.warn(
                "permissions.item_not_found",
                drive_id=drive_id,
                item_id=item_id,
            )
            return []
        except Exception as e:
            logger.warn(
                "permissions.fetch_failed",
                drive_id=drive_id,
                item_id=item_id,
                error=str(e),
            )
            return []

    async def _expand_group(self, group_id: str) -> set[str]:
        """Expand a group to its transitive member user IDs."""
        user_ids: set[str] = set()
        try:
            members = await self.graph.get_transitive_members(group_id)
            for member in members:
                odata_type = member.get("@odata.type", "")
                if odata_type == "#microsoft.graph.user":
                    user_ids.add(member["id"])
        except GraphNotFoundError:
            logger.warn(
                "permissions.group_not_found",
                group_id=group_id,
            )
        except Exception as e:
            logger.warn(
                "permissions.group_expand_failed",
                group_id=group_id,
                error=str(e),
            )

        return user_ids

    async def refresh_all_permissions(self, documents: list[Document]) -> int:
        """Re-resolve permissions for a batch of documents."""
        total = 0
        for doc in documents:
            try:
                count = await self.resolve_and_store(doc)
                total += count
            except Exception as e:
                logger.warn(
                    "permissions.refresh_single_failed",
                    doc_id=str(doc.id),
                    error=str(e),
                )
        return total
