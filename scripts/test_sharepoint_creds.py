"""
Test SharePoint / Microsoft Graph credentials and permissions.

Usage:
    python scripts/test_sharepoint_creds.py

Checks:
  1. MSAL client-credentials token acquisition
  2. GET /sites/{site_id} — validates site access
  3. List drives (document libraries) on the site
  4. List root children of each drive (first 5 items)
  5. File metadata — lastModifiedBy, lastModifiedDateTime, createdBy, size
  6. File download URL — @microsoft.graph.downloadUrl
  7. File permissions — sharing / access grants
  8. Delta query — change tracking support
  9. Subscriptions (webhooks) — list existing
"""

from __future__ import annotations

import asyncio
import sys

import msal
import httpx

# ── Credentials (override via env vars or edit here) ──
TENANT_ID = ""
CLIENT_ID = ""
CLIENT_SECRET = ""

# Site ID from SharePoint (composite format)
SITE_ID = ""

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Track pass/fail for summary
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def acquire_token() -> str:
    """Step 1: acquire an access token via MSAL client credentials."""
    print("\n[1/9] Acquiring token via MSAL client credentials flow ...")
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        msg = f"{result.get('error')}: {result.get('error_description')}"
        print(f"  FAILED: {msg}")
        record("Token acquisition", False, msg)
        sys.exit(1)

    token = result["access_token"]
    print(f"  OK — token acquired (expires in {result.get('expires_in', '?')}s)")
    record("Token acquisition", True)

    # Decode token to show app roles (best-effort)
    try:
        import base64, json as _json
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(payload))
        roles = claims.get("roles", [])
        if roles:
            print(f"  Granted app roles: {', '.join(roles)}")
        else:
            print("  WARNING: No app roles found in token — check API permissions in Azure AD")
    except Exception:
        pass

    return token


async def test_site(client: httpx.AsyncClient) -> dict | None:
    """Step 2: fetch the SharePoint site."""
    print(f"\n[2/9] Fetching site: {SITE_ID[:60]}...")
    resp = await client.get(
        f"{GRAPH_BASE}/sites/{SITE_ID}",
        params={"$select": "id,displayName,webUrl"},
    )
    if resp.status_code != 200:
        msg = resp.text[:300]
        print(f"  FAILED ({resp.status_code}): {msg}")
        record("Site access (Sites.Read.All)", False, f"HTTP {resp.status_code}")
        return None

    data = resp.json()
    print(f"  OK — Site: {data.get('displayName')}  ({data.get('webUrl')})")
    record("Site access (Sites.Read.All)", True)
    return data


async def list_drives(client: httpx.AsyncClient) -> list[dict]:
    """Step 3: list document libraries (drives) on the site."""
    print(f"\n[3/9] Listing drives on site ...")
    resp = await client.get(
        f"{GRAPH_BASE}/sites/{SITE_ID}/drives",
        params={"$select": "id,name,driveType,webUrl"},
    )
    if resp.status_code != 200:
        msg = resp.text[:300]
        print(f"  FAILED ({resp.status_code}): {msg}")
        record("List drives", False, f"HTTP {resp.status_code}")
        return []

    drives = resp.json().get("value", [])
    if not drives:
        print("  OK — but no drives found (check app permissions: Sites.Read.All)")
        record("List drives", True, "0 drives")
        return []

    print(f"  OK — {len(drives)} drive(s) found:")
    for d in drives:
        print(f"    - {d['name']}  (type={d.get('driveType', '?')}, id={d['id']})")
    record("List drives", True, f"{len(drives)} drives")
    return drives


async def find_files_recursive(
    client: httpx.AsyncClient, drive_id: str, drive_name: str,
    folder_path: str = "root", depth: int = 0, max_depth: int = 8, max_files: int = 10,
) -> list[dict]:
    """Recursively list items, drilling into folders to find actual files."""
    indent = "    " + "  " * depth
    resp = await client.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{folder_path}/children",
        params={
            "$select": "id,name,size,file,folder,lastModifiedDateTime,lastModifiedBy,createdBy,createdDateTime",
            "$top": "10",
        },
    )
    if resp.status_code != 200:
        print(f"{indent}FAILED ({resp.status_code})")
        return []

    items = resp.json().get("value", [])
    found_files: list[dict] = []

    for item in items:
        kind = "folder" if "folder" in item else "file"
        if kind == "file":
            size = f" ({item.get('size', 0):,} bytes)"
            modified = item.get("lastModifiedDateTime", "?")
            modified_by = item.get("lastModifiedBy", {}).get("user", {}).get("displayName", "?")
            print(f"{indent}- [file] {item['name']}{size}  modified={modified}  by={modified_by}")
            item["_drive_id"] = drive_id
            found_files.append(item)
        else:
            child_count = item.get("folder", {}).get("childCount", "?")
            print(f"{indent}- [folder] {item['name']}/  ({child_count} children)")
            if depth < max_depth and len(found_files) < max_files:
                sub_files = await find_files_recursive(
                    client, drive_id, drive_name,
                    folder_path=item["id"], depth=depth + 1,
                    max_depth=max_depth, max_files=max_files - len(found_files),
                )
                found_files.extend(sub_files)

        if len(found_files) >= max_files:
            break

    return found_files


async def list_items(client: httpx.AsyncClient, drives: list[dict]) -> dict | None:
    """Step 4: recursively list items in each drive, return first file found."""
    print(f"\n[4/9] Listing items (recursive, up to 8 levels deep) ...")
    all_files: list[dict] = []

    for d in drives:
        print(f"  Drive '{d['name']}':")
        files = await find_files_recursive(client, d["id"], d["name"])
        all_files.extend(files)

    if all_files:
        print(f"\n  Found {len(all_files)} file(s) total")
        record("List items + metadata", True, f"{len(all_files)} files")
        return all_files[0]
    else:
        print(f"\n  No files found (only empty folders)")
        record("List items + metadata", True, "No files found")
        return None


async def test_file_metadata(client: httpx.AsyncClient, drive_id: str, item_id: str, name: str) -> None:
    """Step 5: fetch full file metadata."""
    print(f"\n[5/9] Fetching full metadata for '{name}' ...")
    resp = await client.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}",
        params={"$select": "id,name,size,file,lastModifiedDateTime,lastModifiedBy,createdBy,createdDateTime,webUrl,parentReference"},
    )
    if resp.status_code != 200:
        print(f"  FAILED ({resp.status_code}): {resp.text[:300]}")
        record("File metadata (lastModified, createdBy, etc.)", False, f"HTTP {resp.status_code}")
        return

    data = resp.json()
    print(f"  OK — Full metadata:")
    print(f"    name:             {data.get('name')}")
    print(f"    size:             {data.get('size', 0):,} bytes")
    print(f"    createdDateTime:  {data.get('createdDateTime', '?')}")
    print(f"    createdBy:        {data.get('createdBy', {}).get('user', {}).get('displayName', '?')}")
    print(f"    lastModified:     {data.get('lastModifiedDateTime', '?')}")
    print(f"    lastModifiedBy:   {data.get('lastModifiedBy', {}).get('user', {}).get('displayName', '?')}")
    print(f"    mimeType:         {data.get('file', {}).get('mimeType', '?')}")
    print(f"    webUrl:           {data.get('webUrl', '?')}")
    record("File metadata (lastModified, createdBy, etc.)", True)


async def test_download_url(client: httpx.AsyncClient, drive_id: str, item_id: str, name: str) -> None:
    """Step 6: get download URL for a file."""
    print(f"\n[6/9] Getting download URL for '{name}' ...")
    resp = await client.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}",
        params={"$select": "id,@microsoft.graph.downloadUrl"},
    )
    if resp.status_code != 200:
        print(f"  FAILED ({resp.status_code}): {resp.text[:300]}")
        record("File download URL", False, f"HTTP {resp.status_code}")
        return

    data = resp.json()
    dl_url = data.get("@microsoft.graph.downloadUrl")
    if dl_url:
        print(f"  OK — @downloadUrl obtained ({len(dl_url)} chars)")
        try:
            dl_resp = await client.get(dl_url, headers={"Range": "bytes=0-1023"})
            print(f"  OK — download test: HTTP {dl_resp.status_code}, got {len(dl_resp.content)} bytes")
            record("File download (@downloadUrl)", True)
        except Exception as e:
            print(f"  @downloadUrl obtained but download failed: {e}")
            record("File download (@downloadUrl)", True, "URL ok, download failed")
    else:
        print("  No @downloadUrl (normal for Sites.Selected)")
        print("  Trying /content endpoint instead ...")
        content_resp = await client.get(
            f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content",
            follow_redirects=False,
        )
        if content_resp.status_code in (200, 302):
            if content_resp.status_code == 302:
                redirect_url = content_resp.headers.get("Location", "")
                print(f"  OK — /content returned 302 redirect ({len(redirect_url)} chars)")
                # Follow redirect to download first 1KB
                try:
                    dl_resp = await client.get(redirect_url, headers={"Range": "bytes=0-1023"})
                    print(f"  OK — download test: HTTP {dl_resp.status_code}, got {len(dl_resp.content)} bytes")
                except Exception as e:
                    print(f"  Redirect obtained but download failed: {e}")
            else:
                print(f"  OK — /content returned 200, got {len(content_resp.content):,} bytes directly")
            record("File download (/content fallback)", True)
        else:
            print(f"  FAILED — /content returned HTTP {content_resp.status_code}: {content_resp.text[:200]}")
            record("File download (/content fallback)", False, f"HTTP {content_resp.status_code} — need Files.Read.All")


async def test_file_permissions(client: httpx.AsyncClient, drive_id: str, item_id: str, name: str) -> None:
    """Step 7: fetch permissions for a file."""
    print(f"\n[7/9] Fetching permissions for '{name}' ...")
    resp = await client.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/permissions",
    )
    if resp.status_code == 403:
        print(f"  DENIED (403) — need Sites.FullControl.All or Sites.Manage.All for item permissions")
        record("File permissions (RBAC)", False, "HTTP 403 — insufficient permission")
        return
    if resp.status_code != 200:
        print(f"  FAILED ({resp.status_code}): {resp.text[:300]}")
        record("File permissions (RBAC)", False, f"HTTP {resp.status_code}")
        return

    perms = resp.json().get("value", [])
    print(f"  OK — {len(perms)} permission(s) found:")
    for p in perms[:5]:
        roles = p.get("roles", [])
        granted = p.get("grantedToV2") or p.get("grantedTo") or {}
        user = granted.get("user", {}).get("displayName") or granted.get("siteUser", {}).get("displayName")
        group = granted.get("group", {}).get("displayName") or granted.get("siteGroup", {}).get("displayName")
        identity = user or group or p.get("link", {}).get("scope", "unknown")
        print(f"    - {', '.join(roles):20s} -> {identity}")
    if len(perms) > 5:
        print(f"    ... and {len(perms) - 5} more")
    record("File permissions (RBAC)", True, f"{len(perms)} permissions")


async def test_delta_query(client: httpx.AsyncClient, drives: list[dict]) -> None:
    """Step 8: test delta query (change tracking)."""
    if not drives:
        record("Delta query (change tracking)", False, "No drives")
        return

    drive = drives[0]
    print(f"\n[8/9] Testing delta query on drive '{drive['name']}' ...")
    resp = await client.get(
        f"{GRAPH_BASE}/drives/{drive['id']}/root/delta",
        params={"$select": "id,name,deleted", "$top": "5"},
    )
    if resp.status_code != 200:
        print(f"  FAILED ({resp.status_code}): {resp.text[:300]}")
        record("Delta query (change tracking)", False, f"HTTP {resp.status_code}")
        return

    data = resp.json()
    items = data.get("value", [])
    has_delta_link = "@odata.deltaLink" in data
    has_next_link = "@odata.nextLink" in data
    print(f"  OK — {len(items)} change(s) in first page, deltaLink={'yes' if has_delta_link else 'no'}, nextLink={'yes' if has_next_link else 'no'}")
    record("Delta query (change tracking)", True)


async def test_subscriptions(client: httpx.AsyncClient) -> None:
    """Step 9: list webhook subscriptions."""
    print(f"\n[9/9] Listing webhook subscriptions ...")
    resp = await client.get(f"{GRAPH_BASE}/subscriptions")
    if resp.status_code == 403:
        print(f"  DENIED (403) — would need additional permissions for webhook management")
        record("Subscriptions (webhooks)", False, "HTTP 403")
        return
    if resp.status_code != 200:
        print(f"  FAILED ({resp.status_code}): {resp.text[:300]}")
        record("Subscriptions (webhooks)", False, f"HTTP {resp.status_code}")
        return

    subs = resp.json().get("value", [])
    print(f"  OK — {len(subs)} active subscription(s)")
    for s in subs[:3]:
        print(f"    - resource={s.get('resource', '?')[:60]}  expires={s.get('expirationDateTime', '?')}")
    record("Subscriptions (webhooks)", True, f"{len(subs)} subscriptions")


def print_summary() -> None:
    """Print a summary table of all permission checks."""
    print("\n" + "=" * 70)
    print("PERMISSION SUMMARY")
    print("=" * 70)

    needed_permissions = {
        "Token acquisition": "Application registration + client secret",
        "Site access (Sites.Read.All)": "Sites.Read.All",
        "List drives": "Sites.Read.All",
        "List items + metadata": "Sites.Read.All / Files.Read.All",
        "File metadata (lastModified, createdBy, etc.)": "Sites.Read.All / Files.Read.All",
        "File download URL + content": "Sites.Read.All / Files.Read.All",
        "File permissions (RBAC)": "Sites.FullControl.All (or Sites.Read.All with admin consent)",
        "Delta query (change tracking)": "Sites.Read.All / Files.Read.All",
        "Subscriptions (webhooks)": "Sites.Read.All + Subscription.ReadWrite.All (optional)",
    }

    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        perm = needed_permissions.get(name, "?")
        extra = f"  ({detail})" if detail else ""
        print(f"  [{status}]  {name:50s} requires: {perm}{extra}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n  {passed}/{total} checks passed")

    # Guidance
    failed = [(name, detail) for name, ok, detail in results if not ok]
    if failed:
        print("\n  Recommended Graph API permissions for this RAG system:")
        print("    - Sites.Read.All          (read sites, drives, files, metadata)")
        print("    - Files.Read.All          (read/download file content)")
        print("    - Sites.FullControl.All   (read item-level permissions for RBAC)")
        print("    - Subscription.ReadWrite.All  (optional, for webhook change notifications)")
        print("    - GroupMember.Read.All    (optional, for resolving group memberships in RBAC)")
    print("")


async def main() -> None:
    token = acquire_token()

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    ) as client:
        site = await test_site(client)
        if not site:
            print_summary()
            return

        drives = await list_drives(client)
        first_file = await list_items(client, drives) if drives else None

        if first_file:
            drive_id = first_file["_drive_id"]
            item_id = first_file["id"]
            name = first_file["name"]
            await test_file_metadata(client, drive_id, item_id, name)
            await test_download_url(client, drive_id, item_id, name)
            await test_file_permissions(client, drive_id, item_id, name)
        else:
            print("\n  No files found to test metadata/download/permissions on.")
            record("File metadata (lastModified, createdBy, etc.)", False, "No files found")
            record("File download URL + content", False, "No files found")
            record("File permissions (RBAC)", False, "No files found")

        await test_delta_query(client, drives)
        await test_subscriptions(client)

    print_summary()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
