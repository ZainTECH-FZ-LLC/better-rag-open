"""Quick test: send rendered slide 48 to vision model and show result."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.processing.ocr.vision_extractor import VisionSlideExtractor


async def main():
    img_path = Path(__file__).parent / "slide_renders" / "slide_48.png"
    img_bytes = img_path.read_bytes()
    print(f"Image size: {len(img_bytes) / 1024:.0f} KB")

    # Parser-extracted text for slide 48 (from test_pptx_chunk.py output)
    parser_text = (
        "PostPaid Device Broadband\n"
        "Prepaid Device Broadband\n"
        "Broadband overview - By type of connection\n"
        "BB customers* - by type of connection\n"
        "Customers in Mn"
    )

    extractor = VisionSlideExtractor()
    result = await extractor.extract_slide(img_bytes, slide_text=parser_text)
    print(f"\n{'='*60}")
    print("Vision model output:")
    print(f"{'='*60}")
    print(result)


asyncio.run(main())
