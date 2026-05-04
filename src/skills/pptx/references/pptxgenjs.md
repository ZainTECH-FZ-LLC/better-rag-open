# Creating PPTX from Scratch with python-pptx

Reference guide for generating PowerPoint presentations programmatically when no
template is available. Use this when `document_type=pptx` and `use_template=false`.

---

## Quick Start

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

prs = Presentation()
prs.slide_width  = Inches(13.33)   # 16:9 widescreen
prs.slide_height = Inches(7.5)

slide_layout = prs.slide_layouts[6]   # Blank layout
slide = prs.slides.add_slide(slide_layout)
```

---

## Slide Layouts Index

| Index | Name |
|-------|------|
| 0 | Title Slide |
| 1 | Title and Content |
| 2 | Title and Two Content |
| 5 | Title Only |
| 6 | Blank |

---

## Adding Text Boxes

```python
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

txBox = slide.shapes.add_textbox(
    left=Inches(0.5), top=Inches(0.3),
    width=Inches(12), height=Inches(1.2)
)
tf = txBox.text_frame
tf.word_wrap = True

p = tf.paragraphs[0]
p.alignment = PP_ALIGN.LEFT
run = p.add_run()
run.text = "Slide Title"
run.font.bold = True
run.font.size = Pt(36)
run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)  # Corporate blue
```

---

## Adding Images / Charts

```python
from pptx.util import Inches

# Add image (PNG/JPG/SVG)
slide.shapes.add_picture(
    "chart_output.png",
    left=Inches(1), top=Inches(2),
    width=Inches(10), height=Inches(4.5),
)
```

For chart images, generate with `ChartBuilder` first, then embed the PNG.

---

## Department Color Palettes

Load from `src/skills/pptx/assets/color_palettes.json`:

```python
import json, pathlib

palettes = json.loads(
    (pathlib.Path(__file__).parent.parent / "assets" / "color_palettes.json").read_text()
)
colors = palettes["finance"]  # {"primary": "#1F497D", "accent": "#ED7D31", ...}
```

---

## Adding Tables

```python
from pptx.util import Inches, Pt

rows, cols = 4, 3
table = slide.shapes.add_table(
    rows, cols,
    left=Inches(1), top=Inches(2),
    width=Inches(10), height=Inches(3),
).table

# Header row
for col_idx, heading in enumerate(["Metric", "Q3", "Q4"]):
    cell = table.cell(0, col_idx)
    cell.text = heading
    cell.text_frame.paragraphs[0].runs[0].font.bold = True
    cell.fill.solid()
    cell.fill.fore_color.rgb = RGBColor(0x1F, 0x49, 0x7D)
    cell.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
```

---

## Slide Backgrounds

```python
from pptx.dml.color import RGBColor

background = slide.background
fill = background.fill
fill.solid()
fill.fore_color.rgb = RGBColor(0xF2, 0xF2, 0xF2)
```

---

## Speaker Notes

```python
notes_slide = slide.notes_slide
tf = notes_slide.notes_text_frame
tf.text = "Talking points: ..."
```

---

## Saving

```python
import uuid, pathlib

output_path = pathlib.Path("generated") / f"presentation_{uuid.uuid4().hex[:8]}.pptx"
output_path.parent.mkdir(parents=True, exist_ok=True)
prs.save(str(output_path))
```

---

## Best Practices

- **One idea per slide** — never exceed 6 bullet points.
- **36pt+ for titles**, 24pt for body, 18pt minimum for footnotes.
- **Aspect ratio**: Always use 13.33 × 7.5 inches (16:9) for modern displays.
- **Contrast**: Ensure text/background contrast ratio ≥ 4.5:1 (WCAG AA).
- **Alt text**: Call `shape.name = "Chart: Revenue Q4 2024"` for accessibility.
- **Consistent margins**: 0.5 in from slide edges on all sides.
- **Max slides**: Aim for 1 slide per minute of talk time.
