# Financial Model Conventions

## Three-Statement Model Structure

### Sheet order
1. **Assumptions** — all hard-coded inputs (rates, growth, headcount)
2. **P&L** (Income Statement) — Revenue → EBITDA → Net Income
3. **Balance Sheet** — Assets, Liabilities, Equity
4. **Cash Flow** — Operating, Investing, Financing activities
5. **Supporting schedules** (Depreciation, Debt, Working Capital)
6. **Charts** — visual summaries referencing the above sheets

### P&L structure
| Row | Description |
|-----|-------------|
| Revenue | Net revenue by product/segment |
| COGS | Cost of goods sold |
| Gross Profit | `=Revenue - COGS` |
| Gross Margin % | `=Gross Profit / Revenue` |
| OpEx | Operating expenses (S&M, R&D, G&A) |
| EBITDA | `=Gross Profit - OpEx` |
| D&A | Depreciation & amortization |
| EBIT | `=EBITDA - D&A` |
| Interest | Net interest expense |
| EBT | `=EBIT - Interest` |
| Tax | `=EBT * Tax Rate` |
| Net Income | `=EBT - Tax` |

## Budget vs Actual Template

### Column pattern
`Month | Budget | Actual | Variance | Variance %`

Variance: `=Actual - Budget`
Variance %: `=IFERROR(Variance/ABS(Budget), 0)`

Conditional formatting: red for negative variance, green for positive.

## Pipeline Tracker

### Stage funnel structure
Stages: Prospect → Qualified → Discovery → Proposal → Negotiation → Closed Won/Lost

Weighted pipeline: `=Deal Value * Stage Probability`

Standard probabilities:
| Stage | Probability |
|-------|-------------|
| Prospect | 10% |
| Qualified | 25% |
| Discovery | 40% |
| Proposal | 60% |
| Negotiation | 80% |
| Closed Won | 100% |
| Closed Lost | 0% |

## Formatting conventions
- **Currency**: `$#,##0.00` or `$#,##0` for round numbers
- **Percentage**: `0.0%` (one decimal)
- **Thousands separator**: always for revenue > $10K
- **Negative numbers**: `($#,##0)` in red
- **Header rows**: bold, colored background (#1E4D8C for finance department)
- **Total rows**: bold, top border
