"""Chart builder — matplotlib/plotly chart image generator."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# Department color palettes
DEPARTMENT_PALETTES: dict[str, list[str]] = {
    "finance": ["#1E4D8C", "#A8C4E0", "#4A90D9", "#2C5F8A", "#7BB3D6", "#E8F0F7"],
    "sales":   ["#1A6B3C", "#52B788", "#95D5B2", "#2D9E5F", "#74C69D", "#D8F3E3"],
    "marketing": ["#8B1A4A", "#E67FAA", "#F4B8D2", "#C1356B", "#E89DC0", "#FDE8F0"],
    "hr":      ["#4A1A8B", "#9B7FE6", "#C4AFEF", "#6B3DB5", "#B39DDB", "#EDE8F8"],
    "general": ["#2C3E50", "#5D6D7E", "#85929E", "#7F8C8D", "#AAB7B8", "#ECF0F1"],
}
DEFAULT_PALETTE = DEPARTMENT_PALETTES["general"]


class ChartBuilder:
    """Build chart images using matplotlib with department-specific styling."""

    def __init__(self, department: str = "general") -> None:
        self.department = department.lower()
        self.palette = DEPARTMENT_PALETTES.get(self.department, DEFAULT_PALETTE)

    async def build(
        self,
        chart_type: str,
        title: str,
        labels: list[str],
        datasets: list[dict[str, Any]],
        x_label: str | None = None,
        y_label: str | None = None,
        output_dir: Path = Path("."),
        format: str = "png",
    ) -> tuple[str, Path]:
        """
        Build a chart image and write it to output_dir.

        Returns:
            (filename, filepath) tuple.
        """
        filename = f"chart_{uuid.uuid4().hex[:12]}.{format}"
        filepath = output_dir / filename

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._render_sync,
            chart_type, title, labels, datasets, x_label, y_label, filepath, format,
        )

        logger.info(
            "chart_builder.built",
            chart_type=chart_type,
            title=title,
            filename=filename,
        )
        return filename, filepath

    def _render_sync(
        self,
        chart_type: str,
        title: str,
        labels: list[str],
        datasets: list[dict[str, Any]],
        x_label: str | None,
        y_label: str | None,
        filepath: Path,
        format: str,
    ) -> None:
        """Synchronous matplotlib rendering (runs in executor)."""
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(figsize=(10, 6))
        self._apply_style(fig, ax)

        dispatch = {
            "bar": self._bar,
            "horizontal_bar": self._horizontal_bar,
            "line": self._line,
            "area": self._area,
            "scatter": self._scatter,
            "pie": self._pie,
            "donut": self._donut,
            "heatmap": self._heatmap,
            "waterfall": self._waterfall,
            "box": self._box,
        }
        render_fn = dispatch.get(chart_type, self._bar)
        render_fn(ax, labels, datasets, np)

        ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
        if x_label:
            ax.set_xlabel(x_label, fontsize=11)
        if y_label:
            ax.set_ylabel(y_label, fontsize=11)

        plt.tight_layout()
        plt.savefig(str(filepath), format=format, dpi=150, bbox_inches="tight")
        plt.close(fig)

    def _apply_style(self, fig: Any, ax: Any) -> None:
        fig.patch.set_facecolor("white")
        ax.set_facecolor("#FAFAFA")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=10)
        ax.grid(axis="y", linestyle="--", alpha=0.5)

    def _bar(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        n_series = len(datasets)
        x = np.arange(len(labels))
        width = 0.8 / max(n_series, 1)
        offsets = np.linspace(-(n_series - 1) / 2, (n_series - 1) / 2, n_series) * width

        for i, ds in enumerate(datasets):
            color = ds.get("color") or self.palette[i % len(self.palette)]
            ax.bar(x + offsets[i], ds["data"], width, label=ds.get("label", ""), color=color)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=20 if len(labels) > 6 else 0, ha="right")
        if len(datasets) > 1:
            ax.legend(fontsize=9)

    def _horizontal_bar(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        n_series = len(datasets)
        y = np.arange(len(labels))
        height = 0.8 / max(n_series, 1)
        offsets = np.linspace(-(n_series - 1) / 2, (n_series - 1) / 2, n_series) * height

        for i, ds in enumerate(datasets):
            color = ds.get("color") or self.palette[i % len(self.palette)]
            ax.barh(y + offsets[i], ds["data"], height, label=ds.get("label", ""), color=color)

        ax.set_yticks(y)
        ax.set_yticklabels(labels)
        ax.grid(axis="x", linestyle="--", alpha=0.5)
        ax.grid(axis="y", visible=False)
        if len(datasets) > 1:
            ax.legend(fontsize=9)

    def _line(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        for i, ds in enumerate(datasets):
            color = ds.get("color") or self.palette[i % len(self.palette)]
            ax.plot(labels, ds["data"], marker="o", linewidth=2,
                    markersize=5, label=ds.get("label", ""), color=color)
        if len(datasets) > 1:
            ax.legend(fontsize=9)
        if len(labels) > 8:
            ax.set_xticks(ax.get_xticks()[::max(len(labels) // 8, 1)])
        ax.set_xticklabels(labels[::max(len(labels) // 8, 1)], rotation=20, ha="right")

    def _area(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        for i, ds in enumerate(datasets):
            color = ds.get("color") or self.palette[i % len(self.palette)]
            ax.fill_between(labels, ds["data"], alpha=0.4, color=color, label=ds.get("label", ""))
            ax.plot(labels, ds["data"], linewidth=1.5, color=color)
        if len(datasets) > 1:
            ax.legend(fontsize=9)

    def _scatter(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        for i, ds in enumerate(datasets):
            color = ds.get("color") or self.palette[i % len(self.palette)]
            x_vals = range(len(ds["data"]))
            ax.scatter(x_vals, ds["data"], color=color, label=ds.get("label", ""), s=60, alpha=0.8)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=20, ha="right")
        if len(datasets) > 1:
            ax.legend(fontsize=9)

    def _pie(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        if not datasets:
            return
        data = datasets[0]["data"]
        colors = [
            datasets[0].get("color") or self.palette[i % len(self.palette)]
            for i in range(len(data))
        ]
        ax.pie(data, labels=labels, colors=colors, autopct="%1.1f%%",
               startangle=90, pctdistance=0.85)
        ax.axis("equal")

    def _donut(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        if not datasets:
            return
        data = datasets[0]["data"]
        colors = [
            datasets[0].get("color") or self.palette[i % len(self.palette)]
            for i in range(len(data))
        ]
        wedges, _, autotexts = ax.pie(
            data, labels=labels, colors=colors, autopct="%1.1f%%",
            startangle=90, pctdistance=0.85,
            wedgeprops={"width": 0.5}
        )
        total = sum(d for d in data if d)
        ax.text(0, 0, f"{total:,.0f}", ha="center", va="center",
                fontsize=14, fontweight="bold", color="#2C3E50")
        ax.axis("equal")

    def _heatmap(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        import matplotlib.colors as mcolors
        matrix = np.array([ds["data"] for ds in datasets])
        im = ax.imshow(matrix, aspect="auto", cmap="Blues")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
        ax.set_yticks(range(len(datasets)))
        ax.set_yticklabels([ds.get("label", f"Row {i}") for i, ds in enumerate(datasets)])
        ax.figure.colorbar(im, ax=ax, shrink=0.8)
        ax.grid(visible=False)

    def _waterfall(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        if not datasets:
            return
        data = datasets[0]["data"]
        running = 0
        bottoms = []
        colors = []
        for i, val in enumerate(data):
            if i == 0 or i == len(data) - 1:
                bottoms.append(0)
                colors.append(self.palette[0])
            elif val >= 0:
                bottoms.append(running)
                colors.append("#27AE60")
            else:
                bottoms.append(running + val)
                colors.append("#E74C3C")
            if i not in (0, len(data) - 1):
                running += val

        ax.bar(range(len(data)), data, bottom=bottoms, color=colors, width=0.6)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=20, ha="right")

        # Connector lines
        for i in range(len(data) - 1):
            top = bottoms[i] + data[i]
            ax.plot([i + 0.3, i + 0.7], [top, top], "k-", linewidth=0.8, alpha=0.5)

    def _box(self, ax: Any, labels: list[str], datasets: list[dict], np: Any) -> None:
        box_data = [ds["data"] for ds in datasets]
        box_labels = [ds.get("label", f"S{i}") for i, ds in enumerate(datasets)]
        bp = ax.boxplot(box_data, labels=box_labels, patch_artist=True)
        for i, patch in enumerate(bp["boxes"]):
            patch.set_facecolor(self.palette[i % len(self.palette)])
            patch.set_alpha(0.7)
