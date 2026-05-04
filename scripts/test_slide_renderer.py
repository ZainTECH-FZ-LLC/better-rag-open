"""Quick test: render a PPTX to slide images via LibreOffice + pymupdf."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.processing.ocr.slide_renderer import render_slides


async def main():
    f = Path(
        r"C:\Users\mustafa.ahsan\Downloads\OneDrive_2026-03-05"
        r"\001. Business Plans (Annual)\2019-2023"
        r"\Zain_Bahrain_SBP_2019-2023.pptx"
    )
    print(f"Reading {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)...")
    images = await render_slides(f.read_bytes(), f.name)
    print(f"Rendered {len(images)} slide images")
    for i, img in enumerate(images):
        print(f"  Slide {i+1}: {len(img) / 1024:.0f} KB PNG")

    # Save slides 48 and 49 for visual inspection
    out_dir = Path(__file__).parent / "slide_renders"
    out_dir.mkdir(exist_ok=True)
    for idx in (48, 49):
        if idx <= len(images):
            out_path = out_dir / f"slide_{idx}.png"
            out_path.write_bytes(images[idx - 1])
            print(f"  Saved {out_path}")


asyncio.run(main())
