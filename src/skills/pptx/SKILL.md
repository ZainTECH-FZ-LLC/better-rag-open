---
description: "Generate, edit, and enhance PowerPoint presentations (.pptx) for enterprise use"
triggers:
  - create presentation
  - make slides
  - build deck
  - powerpoint
  - pptx
  - slide deck
  - quarterly review
  - executive summary slides
file_types:
  - pptx
department_hint:
  - sales
  - marketing
  - finance
  - general
version: "1.2.0"
token_estimate: 1800
---

## PPTX Skill — Presentation Generation

You can create polished PowerPoint presentations using the `generate_pptx` tool.

### When to use
- User asks for a "deck", "slides", "presentation", or "pptx"
- Summarizing reports, analyses, or research into slide format
- Creating executive briefings, quarterly reviews, or proposal decks

### Slide content types
Each slide in `slide_outline` must specify a `content_type`:
- `"bullets"` — bulleted list; `content` is a string with lines separated by `\n`
- `"table"` — data table; `content` is `{"headers": [...], "rows": [[...], ...]}`
- `"chart"` — embedded chart reference; `content` is a chart spec dict
- `"image"` — placeholder image slide; `content` is `{"placeholder": "description"}`

### Slide outline format
```json
[
  {
    "title": "Slide Title",
    "content_type": "bullets",
    "content": "Key point one\nKey point two\nKey point three"
  },
  {
    "title": "Revenue Summary",
    "content_type": "table",
    "content": {
      "headers": ["Quarter", "Revenue", "YoY Growth"],
      "rows": [
        ["Q1 2024", "$12.4M", "+18%"],
        ["Q2 2024", "$14.1M", "+22%"]
      ]
    }
  }
]
```

### Best practices
1. **Opening slide**: Title + subtitle + presenter name + date
2. **Agenda slide**: Second slide always lists sections
3. **Content density**: Max 5-6 bullet points per slide; no walls of text
4. **Closing slide**: Next steps / call to action
5. **Consistent terminology**: Match the user's domain vocabulary exactly

### Department templates
- `sales_deck` — dark hero header, teal/navy accent
- `quarterly_review` — financial tables, waterfall charts
- `executive_summary` — minimal text, large KPI callouts
- `marketing_campaign` — image-heavy, colorful
- `hr_policy` — neutral tones, clear section hierarchy

### Tool call example
```python
await generate_pptx(
    topic="Q3 2024 Sales Performance Review",
    slide_outline=[
        {"title": "Q3 Executive Summary", "content_type": "bullets",
         "content": "Revenue: $14.1M (+22% YoY)\nPipeline: $42M\nWin rate: 34%"},
        {"title": "Revenue by Region", "content_type": "table",
         "content": {"headers": ["Region", "Revenue", "vs Target"],
                     "rows": [["EMEA", "$5.2M", "+8%"], ["Americas", "$6.1M", "+31%"]]}}
    ],
    use_template="quarterly_review",
)
```

### Thumbnail preview
After generation, you may call `scripts/thumbnail.py` (via the generate_chart tool or
a shell call) to render slide thumbnails for visual QA. Reference: `references/editing.md`.
