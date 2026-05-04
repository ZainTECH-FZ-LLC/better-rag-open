# Chart Type Selection Guide

## Decision matrix

| Goal | Recommended type |
|------|-----------------|
| Compare categories | `bar` or `horizontal_bar` |
| Show trend over time | `line` or `area` |
| Show part-of-whole | `pie` (≤6 items) or `donut` |
| Show correlation | `scatter` |
| Show distribution | `box` |
| Show matrix/intensity | `heatmap` |
| Show incremental change | `waterfall` |
| Show cumulative growth | `area` |

## Bar charts

### When to use `bar` vs `horizontal_bar`
- `bar` — short labels, up to 8 categories, vertical comparison
- `horizontal_bar` — long labels (country names, product names), rank lists, 6+ categories

### Multi-series bars
Provide multiple datasets; they render as grouped bars by default.
For stacked: add `"stacked": true` to the dataset (builder supports this).

## Line charts

### Single vs multi-series
- Single series: omit legend; use descriptive title
- Multi-series: keep ≤5 lines; use distinct colors

### Time-series axis
Labels should be uniform intervals: monthly, quarterly, yearly.

## Pie / Donut charts

### Donut center text
For a donut, the first dataset's total is shown as center text automatically.
Keep it simple: one metric only.

### Slice limit
Merge slices < 3% into an "Other" category. Max 6 slices total for readability.

## Waterfall charts

### Data convention
- First value: starting total (positive)
- Intermediate values: increments/decrements (positive or negative)
- Last value: ending total (positive)

The builder automatically colors positive increments green, negative red.

## Heatmap charts

### Data format
For heatmap, `datasets` is a list of row series:
```json
[
  {"label": "Mon", "data": [12, 23, 45, 11, 5]},
  {"label": "Tue", "data": [8, 31, 29, 14, 9]}
]
```
`labels` = column labels (e.g., hours of day, months).

## Color guidance

### Department palettes (auto-applied when no color specified)
- Finance: `#1E4D8C` (navy), `#A8C4E0` (light blue), `#E8F0F7`
- Sales: `#1A6B3C` (dark green), `#52B788` (medium green), `#D8F3E3`
- Marketing: `#8B1A4A` (magenta), `#E67FAA` (pink), `#FDE8F0`
- HR: `#4A1A8B` (purple), `#9B7FE6` (medium purple), `#EDE8F8`
- General: `#2C3E50` (dark), `#7F8C8D` (grey), `#ECF0F1`

### Accessibility
Avoid red/green only for positive/negative — add pattern or label.
Ensure 4.5:1 contrast ratio against white background.
