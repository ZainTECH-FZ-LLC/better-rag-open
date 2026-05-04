---
description: "Generate and edit Word documents (.docx) — reports, policies, memos, contracts"
triggers:
  - create document
  - write report
  - draft policy
  - word document
  - docx
  - memo
  - contract
  - policy document
  - meeting minutes
file_types:
  - docx
department_hint:
  - hr
  - finance
  - general
version: "1.1.0"
token_estimate: 1600
---

## DOCX Skill — Word Document Generation

You can create professional Word documents using the `generate_docx` tool.

### When to use
- User asks for a "document", "report", "policy", "memo", or "docx"
- Formalizing findings or analysis into a structured written document
- Creating HR policies, financial reports, or executive memos

### Section format
Each item in `sections` must have:
- `heading` — section heading text (str)
- `content` — section body text; supports plain paragraphs separated by `\n\n`
- `level` — heading level 1, 2, or 3 (int)

### Sections list example
```json
[
  {
    "heading": "Executive Summary",
    "content": "This report covers Q3 2024 financial performance...\n\nRevenue grew 22% year-over-year driven by EMEA expansion.",
    "level": 1
  },
  {
    "heading": "Revenue Analysis",
    "content": "Total revenue reached $14.1M in Q3 2024.",
    "level": 2
  },
  {
    "heading": "Regional Breakdown",
    "content": "Americas contributed 43% of total revenue at $6.1M.",
    "level": 3
  }
]
```

### Document structure best practices
1. **Title page section**: level-1 heading with full document title + date
2. **Executive summary**: always first substantive section (level 1)
3. **Heading hierarchy**: use level 1 for major sections, 2 for subsections, 3 for details
4. **Paragraph length**: 3-5 sentences per paragraph for readability
5. **Lists in content**: separate items with `\n- ` prefix in content string

### Available templates
- `policy_report` — formal HR/Legal policy layout with approval block
- `financial_report` — financial tables, clean serif font, page numbers
- `executive_memo` — short-form memo header (To/From/Date/Subject)
- `technical_spec` — monospace code blocks, technical diagram placeholders
- `meeting_minutes` — attendees table, action items section

### Tool call example
```python
await generate_docx(
    title="Q3 2024 Financial Performance Report",
    sections=[
        {"heading": "Executive Summary",
         "content": "Total revenue of $14.1M exceeded target by 12%...",
         "level": 1},
        {"heading": "Key Metrics",
         "content": "Revenue: $14.1M | EBITDA: 31% | Headcount: 245",
         "level": 2},
    ],
    use_template="financial_report",
)
```

### Track changes and comments
Use `scripts/accept_changes.py` to programmatically accept all tracked changes,
or `scripts/comment.py` to add review comments. See `references/xml_reference.md`
for OOXML structure guidance when troubleshooting generated document issues.
