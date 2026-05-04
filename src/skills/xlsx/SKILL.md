---
description: "Generate Excel spreadsheets (.xlsx) — financial models, trackers, data exports"
triggers:
  - create spreadsheet
  - excel
  - xlsx
  - financial model
  - budget template
  - pipeline tracker
  - data export
  - create table
file_types:
  - xlsx
department_hint:
  - finance
  - sales
  - marketing
version: "1.1.0"
token_estimate: 1700
---

## XLSX Skill — Excel Spreadsheet Generation

You can create Excel workbooks using the `generate_xlsx` tool.

### When to use
- User asks for a "spreadsheet", "Excel file", "xlsx", or "workbook"
- Exporting tabular data, financial models, or pipeline reports
- Creating budget templates, KPI dashboards, or data trackers

### Sheet format
Each item in `sheets` must have:
- `name` — sheet tab name (str, max 31 chars)
- `headers` — list of column header strings
- `rows` — list of row arrays (each row is a list matching header count)
- `formulas` — optional dict mapping cell addresses to Excel formula strings (e.g. `{"E2": "=C2-D2"}`)
- `charts` — optional list of chart specs embedded in the sheet

### Sheets list example
```json
[
  {
    "name": "Revenue Summary",
    "headers": ["Quarter", "Region", "Revenue", "Target", "Variance"],
    "rows": [
      ["Q3 2024", "Americas", 6100000, 5500000, null],
      ["Q3 2024", "EMEA", 5200000, 5000000, null],
      ["Q3 2024", "APAC", 2800000, 3000000, null]
    ],
    "formulas": {
      "E2": "=C2-D2",
      "E3": "=C3-D3",
      "E4": "=C4-D4"
    }
  }
]
```

### Numeric formatting
- Revenue/currency values: pass as raw numbers (float/int), not formatted strings
- Percentages: pass as decimal (0.22 for 22%); the generator applies % format
- Dates: pass as ISO 8601 strings ("2024-09-30"); the generator converts to Excel dates

### Formulas
Standard Excel formula syntax. Common patterns:
- `=SUM(C2:C10)` — sum column range
- `=C2/D2-1` — percent change
- `=IFERROR(C2/D2,"N/A")` — safe division
- `=IF(E2>0,"Above Target","Below Target")` — conditional

### Available templates
- `budget_template` — monthly budget vs actual with variance columns
- `pipeline_tracker` — sales pipeline with deal stage funnel and weighted value
- `kpi_dashboard` — KPI summary sheet + raw data sheets
- `financial_model` — 3-statement model stub (P&L, Balance Sheet, Cash Flow)
- `headcount_plan` — HC by department, hire dates, cost rollup

### Tool call example
```python
await generate_xlsx(
    title="Q3 Sales Pipeline",
    sheets=[
        {
            "name": "Pipeline",
            "headers": ["Deal", "Stage", "Value", "Close Date", "Probability", "Weighted"],
            "rows": [
                ["Acme Corp", "Negotiation", 250000, "2024-10-15", 0.7, None],
                ["Globex Inc", "Proposal", 180000, "2024-11-01", 0.4, None],
            ],
            "formulas": {"F2": "=C2*E2", "F3": "=C3*E3"},
        }
    ],
    use_template="pipeline_tracker",
)
```

### Recalculation
After generation, call `scripts/recalc.py` to force-recalculate all formulas
if the workbook will be served as a download without being opened in Excel first.
See `references/financial_models.md` for standard financial model conventions.
