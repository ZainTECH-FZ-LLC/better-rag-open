"""Generated file serving — download endpoint with metadata."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from config.settings import get_settings

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/files", tags=["files"])
settings = get_settings()

# Allowed file extensions for the generated directory
_ALLOWED_EXTENSIONS = frozenset({
    ".pdf", ".pptx", ".docx", ".xlsx", ".png", ".svg", ".csv",
})


@router.get("/generated/{filename}")
async def download_generated_file(filename: str) -> FileResponse:
    """
    Serve a previously generated document or chart image.

    Security:
    - Filename is validated against the generated directory (no path traversal).
    - Only permitted extensions are served.
    """
    # Sanitise — reject any path components
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = settings.GENERATED_DIR / filename
    _validate_path(filepath)

    suffix = filepath.suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type '{suffix}' not served")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")

    mime_type, _ = mimetypes.guess_type(str(filepath))
    mime_type = mime_type or "application/octet-stream"

    logger.info("files.download", filename=filename, mime_type=mime_type)
    return FileResponse(
        path=str(filepath),
        media_type=mime_type,
        filename=filename,
    )


@router.get("/generated/{filename}/meta")
async def file_metadata(filename: str) -> JSONResponse:
    """
    Return metadata about a generated file without downloading it.
    Useful for the UI to display file info before the user downloads.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = settings.GENERATED_DIR / filename
    _validate_path(filepath)

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")

    stat = filepath.stat()
    mime_type, _ = mimetypes.guess_type(str(filepath))

    return JSONResponse({
        "filename": filename,
        "size_bytes": stat.st_size,
        "mime_type": mime_type or "application/octet-stream",
        "created_at": stat.st_ctime,
        "download_url": f"/api/v1/files/generated/{filename}",
    })


@router.get("/generated")
async def list_generated_files() -> JSONResponse:
    """
    List all files in the generated directory.
    Returns filenames, sizes, and download URLs.
    """
    generated_dir = settings.GENERATED_DIR
    if not generated_dir.exists():
        return JSONResponse({"files": []})

    files = []
    for fp in sorted(generated_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
        if fp.is_file() and fp.suffix.lower() in _ALLOWED_EXTENSIONS:
            stat = fp.stat()
            mime_type, _ = mimetypes.guess_type(str(fp))
            files.append({
                "filename": fp.name,
                "size_bytes": stat.st_size,
                "mime_type": mime_type or "application/octet-stream",
                "created_at": stat.st_ctime,
                "download_url": f"/api/v1/files/generated/{fp.name}",
            })

    return JSONResponse({"files": files, "count": len(files)})


def _validate_path(filepath: Path) -> None:
    """Raise 400 if filepath escapes the generated directory (path traversal guard)."""
    try:
        filepath.resolve().relative_to(settings.GENERATED_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
