"""Chart generation tool — matplotlib/plotly chart image builder."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from langchain_core.tools import tool

from config.settings import get_settings

logger = structlog.get_logger()

SUPPORTED_CHART_TYPES = {
    "bar", "horizontal_bar", "line", "area", "scatter",
    "pie", "donut", "heatmap", "waterfall", "box",
}


def create_chart_tool(department: str):
    """Create a chart generation tool with department color palette."""
    settings = get_settings()

    @tool
    async def generate_chart(
        chart_type: str,
        title: str,
        labels: list[str],
        datasets: list[dict[str, Any]],
        x_label: str | None = None,
        y_label: str | None = None,
        format: str = "png",
    ) -> dict[str, str]:
        """
        Generate a chart image from data.

        Args:
            chart_type: Type of chart. Options: bar, horizontal_bar, line, area,
                       scatter, pie, donut, heatmap, waterfall, box.
            title: Chart title.
            labels: Category labels (x-axis for bar/line, slice labels for pie).
            datasets: List of dataset dicts, each with:
                      - label: series name (str)
                      - data: list of numeric values (list[float])
                      - color: optional hex color (str), e.g. "#1E2761"
            x_label: Optional x-axis label.
            y_label: Optional y-axis label.
            format: Output format: "png" | "svg" (default: png).

        Returns:
            {"filename": "...", "download_url": "...", "mime_type": "image/png"}
        """
        from src.document_generation.chart_builder import ChartBuilder

        output_dir = settings.GENERATED_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        if chart_type not in SUPPORTED_CHART_TYPES:
            chart_type = "bar"

        builder = ChartBuilder(department=department)
        filename, filepath = await builder.build(
            chart_type=chart_type,
            title=title,
            labels=labels,
            datasets=datasets,
            x_label=x_label,
            y_label=y_label,
            output_dir=output_dir,
            format=format,
        )

        download_url = f"/api/v1/files/generated/{filename}"
        mime_type = "image/svg+xml" if format == "svg" else "image/png"

        logger.info(
            "chart_tool.generated",
            chart_type=chart_type,
            title=title,
            filename=filename,
        )

        return {
            "filename": filename,
            "download_url": download_url,
            "mime_type": mime_type,
            "filepath": str(filepath),
        }

    return generate_chart
