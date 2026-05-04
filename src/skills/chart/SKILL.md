---
description: "Generate chart images (bar, line, pie, heatmap, etc.) for embedding in documents or standalone download"
triggers:
  - create chart
  - generate graph
  - plot data
  - visualize
  - bar chart
  - line chart
  - pie chart
  - heatmap
  - waterfall
  - scatter plot
file_types: []
department_hint:
  - finance
  - sales
  - marketing
version: "1.0.0"
token_estimate: 1200
---

## Chart Skill — Data Visualization

You can generate chart images using the `generate_chart` tool. Charts are returned as
PNG or SVG files with a `download_url` for embedding or direct download.

### Supported chart types
| Type | Use case |
|------|----------|
| `bar` | Comparing discrete categories |
| `horizontal_bar` | Long category labels, rank comparisons |
| `line` | Time series, trends |
| `area` | Cumulative trends, stacked contributions |
| `scatter` | Correlation, distribution |
| `pie` | Part-of-whole (≤6 categories) |
| `donut` | Part-of-whole with center metric |
| `heatmap` | Matrix data, activity calendars |
| `waterfall` | Incremental contribution to total |
| `box` | Statistical distributions, quartiles |

### Dataset format
`datasets` is a list of series objects:
```json
[
  {
    "label": "Series Name",
    "data": [12.4, 14.1, 13.7, 15.2],
    "color": "#1E2761"
  }
]
```
- `label` — legend entry (str)
- `data` — numeric values matching the length of `labels`
- `color` — optional hex color; if omitted, uses department palette

### Labels
`labels` must match the `data` length in each dataset:
- For time series: `["Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024"]`
- For categories: `["Americas", "EMEA", "APAC"]`
- For heatmap: row labels (columns are dataset labels)

### Tool call examples

**Bar chart:**
```python
await generate_chart(
    chart_type="bar",
    title="Revenue by Region — Q3 2024",
    labels=["Americas", "EMEA", "APAC"],
    datasets=[
        {"label": "Actual", "data": [6.1, 5.2, 2.8], "color": "#1E4D8C"},
        {"label": "Target", "data": [5.5, 5.0, 3.0], "color": "#A8C4E0"},
    ],
    x_label="Region",
    y_label="Revenue ($M)",
    format="png",
)
```

**Waterfall (P&L bridge):**
```python
await generate_chart(
    chart_type="waterfall",
    title="Q3 EBITDA Bridge",
    labels=["Revenue", "COGS", "Gross Profit", "OpEx", "EBITDA"],
    datasets=[{"label": "Value ($M)", "data": [14.1, -6.2, 7.9, -3.5, 4.4]}],
    format="png",
)
```

**Line chart (trend):**
```python
await generate_chart(
    chart_type="line",
    title="Monthly ARR Growth",
    labels=["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
    datasets=[{"label": "ARR ($M)", "data": [8.2, 8.9, 9.4, 10.1, 11.3, 12.0]}],
    y_label="ARR ($M)",
)
```

### Best practices
1. **Pie/donut**: max 6 slices; merge small slices into "Other"
2. **Color consistency**: use the same color per series across multiple charts
3. **Axis labels**: always provide y_label for numeric axes
4. **Titles**: descriptive titles with period (e.g., "Q3 2024 Revenue by Region")
5. **Format**: use `"svg"` for documents/reports; `"png"` for chat/preview

### Chart as part of a document
Charts generated here can be embedded in PPTX/DOCX by referencing the `filepath`
from the chart tool response in a slide `content_type: "chart"` dict. See the
PPTX SKILL for embedding instructions. See `references/chart_types.md` for
detailed guidance on choosing the right chart type.
