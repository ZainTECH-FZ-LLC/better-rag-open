# DOCX OOXML Reference

## Namespace prefixes
- `w:` — WordprocessingML (paragraphs, runs, tables, styles)
- `r:` — Relationships
- `a:` — DrawingML (images, charts)
- `wps:` — Word Processing Shapes

## Key elements

### Paragraph (`w:p`)
```xml
<w:p>
  <w:pPr>
    <w:pStyle w:val="Heading1"/>
    <w:numPr>
      <w:ilvl w:val="0"/>
      <w:numId w:val="1"/>
    </w:numPr>
  </w:pPr>
  <w:r>
    <w:rPr><w:b/></w:rPr>
    <w:t>Bold text</w:t>
  </w:r>
</w:p>
```

### Table (`w:tbl`)
```xml
<w:tbl>
  <w:tblPr>
    <w:tblStyle w:val="TableGrid"/>
    <w:tblW w:w="5000" w:type="pct"/>
  </w:tblPr>
  <w:tr>
    <w:tc>
      <w:tcPr><w:tcW w:w="2500" w:type="pct"/></w:tcPr>
      <w:p><w:r><w:t>Cell text</w:t></w:r></w:p>
    </w:tc>
  </w:tr>
</w:tbl>
```

### Heading styles
| Style val | Maps to |
|-----------|---------|
| `Heading1` | `<h1>` |
| `Heading2` | `<h2>` |
| `Heading3` | `<h3>` |
| `Normal` | Body paragraph |
| `ListParagraph` | Bulleted/numbered list item |

## File structure
```
word/
  document.xml        # main body
  styles.xml          # paragraph/character styles
  numbering.xml       # list definitions
  settings.xml        # document settings
  _rels/document.xml.rels
[Content_Types].xml
```

## Tracked changes
- `w:ins` — inserted text
- `w:del` — deleted text
- `w:rPrChange` — formatting change

Programmatically accept all: `scripts/accept_changes.py`
