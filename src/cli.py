"""Typer CLI for running pipeline stages manually."""

from __future__ import annotations

import asyncio
import json

import typer

app = typer.Typer(name="better-rag", help="Enterprise Agentic RAG CLI")


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: int = 4,
    reload: bool = False,
):
    """Start the FastAPI API server."""
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=host,
        port=port,
        workers=workers,
        reload=reload,
        loop="auto",
    )


@app.command()
def worker(
    queues: str = "rag.query",
    concurrency: int = 10,
    pool: str = "gevent",
):
    """Start a Celery worker."""
    from src.celery_app import celery_app

    celery_app.worker_main(
        argv=[
            "worker",
            f"--queues={queues}",
            f"--concurrency={concurrency}",
            f"--pool={pool}",
            "--loglevel=info",
        ]
    )


@app.command()
def beat():
    """Start the Celery Beat scheduler."""
    from src.celery_app import celery_app

    celery_app.Beat(loglevel="info").run()


@app.command()
def init_extensions():
    """Install pgvector extension only — run this before alembic upgrade head."""

    async def _init():
        from src.storage.db import init_extensions as _init_ext
        await _init_ext()
        typer.echo("Postgres extensions installed.")

    asyncio.run(_init())


@app.command()
def init_db():
    """Install extensions + create all tables directly (dev only — use Alembic in production)."""

    async def _init():
        from src.storage.db import init_db as _init_db
        await _init_db()
        typer.echo("Database tables created.")

    asyncio.run(_init())


@app.command()
def init_neo4j():
    """Initialize Neo4j schema (constraints + indexes)."""

    async def _init():
        from src.knowledge_graph.builder import init_neo4j_schema, close_neo4j_driver
        await init_neo4j_schema()
        await close_neo4j_driver()
        typer.echo("Neo4j schema initialized.")

    asyncio.run(_init())


@app.command()
def migrate(message: str = "auto migration"):
    """Run Alembic migration (generate + upgrade)."""
    import subprocess

    subprocess.run(
        ["alembic", "revision", "--autogenerate", "-m", message],
        check=True,
    )
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    typer.echo("Migration complete.")


@app.command()
def sync(
    site_id: str = typer.Argument(..., help="SharePoint site ID"),
    drive_id: str = typer.Argument(..., help="SharePoint drive ID"),
):
    """Run a delta sync for a specific SharePoint drive."""
    from src.celery_app import run_delta_sync

    run_delta_sync.delay(site_id=site_id, drive_id=drive_id)
    typer.echo(f"Delta sync dispatched for drive {drive_id}")


@app.command()
def discover_drives(
    site_urls: list[str] = typer.Argument(
        ...,
        help=(
            "SharePoint site URLs to discover drives from. "
            "Example: https://contoso.sharepoint.com/sites/engineering"
        ),
    ),
    output_format: str = typer.Option(
        "json",
        "--format",
        help="Output format: 'json' (for SHAREPOINT_DRIVES env var) or 'table'",
    ),
):
    """
    Discover SharePoint drives (document libraries) from site URLs.

    Resolves each site URL to a site ID, then lists all document libraries.
    Outputs a JSON array suitable for the SHAREPOINT_DRIVES environment variable.

    Example:
        better-rag discover-drives https://contoso.sharepoint.com/sites/hr https://contoso.sharepoint.com/sites/finance
    """

    async def _discover():
        from urllib.parse import urlparse
        from src.connectors.graph_client import GraphClientFactory, GraphNotFoundError

        client = await GraphClientFactory.create()
        drives_config: list[dict] = []
        table_rows: list[dict] = []

        try:
            for url in site_urls:
                parsed = urlparse(url)
                hostname = parsed.netloc
                site_path = parsed.path.rstrip("/") or "/"

                try:
                    site = await client.get_site_by_url(hostname, site_path)
                except GraphNotFoundError:
                    typer.echo(
                        typer.style(
                            f"  [NOT FOUND] {url}", fg=typer.colors.RED
                        ),
                        err=True,
                    )
                    continue
                except Exception as e:
                    typer.echo(
                        typer.style(
                            f"  [ERROR] {url}: {e}", fg=typer.colors.RED
                        ),
                        err=True,
                    )
                    continue

                site_id = site["id"]
                site_display_name = site.get("displayName") or site.get("name", url)

                try:
                    drives = await client.list_site_drives(site_id)
                except Exception as e:
                    typer.echo(
                        typer.style(
                            f"  [ERROR] listing drives for {url}: {e}", fg=typer.colors.RED
                        ),
                        err=True,
                    )
                    continue

                for drive in drives:
                    # Skip non-documentLibrary drive types
                    if drive.get("driveType") not in ("documentLibrary", None):
                        continue
                    drives_config.append(
                        {"site_id": site_id, "drive_id": drive["id"]}
                    )
                    table_rows.append(
                        {
                            "site": site_display_name,
                            "site_id": site_id,
                            "library": drive.get("name", ""),
                            "drive_id": drive["id"],
                            "url": drive.get("webUrl", ""),
                        }
                    )
        finally:
            await client.close()

        return drives_config, table_rows

    drives_config, table_rows = asyncio.run(_discover())

    if not drives_config:
        typer.echo("No drives discovered.", err=True)
        raise typer.Exit(1)

    if output_format == "table":
        typer.echo(
            f"\n{'Site':<30} {'Library':<30} {'Drive ID':<50} {'URL'}"
        )
        typer.echo("-" * 140)
        for row in table_rows:
            typer.echo(
                f"{row['site']:<30} {row['library']:<30} {row['drive_id']:<50} {row['url']}"
            )
        typer.echo(f"\n{len(drives_config)} drive(s) discovered.")
        typer.echo("\nAdd to your .env:")
        typer.echo(
            f"SHAREPOINT_DRIVES='{json.dumps(drives_config)}'"
        )
    else:
        # JSON output — ready to paste into .env
        typer.echo(json.dumps(drives_config, indent=2))
        typer.echo(
            typer.style(
                f"\n# {len(drives_config)} drive(s) found. "
                "Set SHAREPOINT_DRIVES to the JSON array above.",
                fg=typer.colors.GREEN,
            ),
            err=True,
        )


@app.command()
def list_subscriptions():
    """List active Microsoft Graph webhook subscriptions for this app."""

    async def _list():
        from src.connectors.graph_client import GraphClientFactory

        client = await GraphClientFactory.create()
        try:
            subs = await client.list_subscriptions()
        finally:
            await client.close()
        return subs

    subs = asyncio.run(_list())

    if not subs:
        typer.echo("No active webhook subscriptions.")
        return

    typer.echo(f"\n{'ID':<40} {'Resource':<50} {'Expiry':<30} {'Client State'}")
    typer.echo("-" * 150)
    for sub in subs:
        typer.echo(
            f"{sub.get('id', ''):<40} "
            f"{sub.get('resource', ''):<50} "
            f"{sub.get('expirationDateTime', ''):<30} "
            f"{sub.get('clientState', '')}"
        )
    typer.echo(f"\n{len(subs)} subscription(s) active.")


if __name__ == "__main__":
    app()
