"""Quick test: show slide 48 chunk content."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.processing.parsers.pptx_parser import PPTXParser


async def main():
    f = Path(
        r"C:\Users\mustafa.ahsan\Downloads\OneDrive_2026-03-05"
        r"\001. Business Plans (Annual)\2019-2023"
        r"\Zain_Bahrain_SBP_2019-2023.pptx"
    )
    parser = PPTXParser()
    parsed = await parser.parse(f.read_bytes(), f.name)

    for slide_idx in (48, 49):
        slide = parsed.slides[slide_idx - 1]
        print(f"\n{'='*60}")
        print(f"=== Slide {slide_idx} chunk content ===")
        print(f"{'='*60}")

        parts = []
        title = slide.get("title", "")
        if title:
            parts.append(f"# {title}")
        for content in slide.get("content", []):
            if content != title:
                parts.append(content)

        text = "\n\n".join(parts)
        print(text[:4000])
        print(f"\n--- Charts: {len(slide.get('charts', []))} | Images: {len(slide.get('images', []))} ---")


asyncio.run(main())
