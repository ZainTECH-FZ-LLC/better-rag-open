# Test Fixtures

This directory contains minimal valid test fixtures for integration and unit tests.

## Files

| File | Description |
|------|-------------|
| `sample.pdf` | 2-page PDF with text, table, and image placeholder |
| `sample.pptx` | 3-slide PPTX with title, bullets, and table slides |
| `sample.docx` | DOCX with heading hierarchy and a table |
| `sample.xlsx` | XLSX with two sheets (data + formulas) |
| `sample_scanned.pdf` | Low-resolution scanned PDF for OCR testing |

## Generating fixtures

Run the fixture generator script to regenerate all fixtures:

```bash
python tests/fixtures/generate_fixtures.py
```

Requires: `fpdf2`, `python-pptx`, `python-docx`, `openpyxl`
