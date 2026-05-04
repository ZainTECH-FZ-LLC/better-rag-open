# PPTX Editing Reference

## Template editing workflow

### 1. Unpack (PPTX → directory)
```bash
python src/skills/pptx/scripts/office/unpack.py input.pptx /tmp/unpacked/
```
Explodes the PPTX zip into its constituent XML files under `/tmp/unpacked/`.

### 2. Edit XML directly
Slides are in `ppt/slides/slide{N}.xml`. Layouts in `ppt/slideLayouts/`.
Themes in `ppt/theme/theme1.xml`.

Key XML namespaces:
- `a:` — DrawingML (shapes, text)
- `p:` — PresentationML (slides, layouts)
- `r:` — Relationships

### 3. Repack (directory → PPTX)
```bash
python src/skills/pptx/scripts/office/pack.py /tmp/unpacked/ output.pptx
```

### 4. Validate
Open in LibreOffice headless:
```bash
soffice --headless --convert-to pptx output.pptx
```

## Slide thumbnail generation
```bash
python src/skills/pptx/scripts/thumbnail.py input.pptx /tmp/thumbnails/ --width 400
```
Produces PNG per slide at the specified width.

## Common edits

### Change slide background color
In `ppt/slides/slide1.xml`, find `<p:bg>` and update `<a:srgbClr val="RRGGBB"/>`.

### Update placeholder text
Find `<p:sp>` with matching `<p:ph type="title"/>` and update `<a:t>` text node.

### Add speaker notes
Add `<p:notes>` element after `<p:spTree>`:
```xml
<p:notes>
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p><a:r><a:t>Your notes here</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:notes>
```
