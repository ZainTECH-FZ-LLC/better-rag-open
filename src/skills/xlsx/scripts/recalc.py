"""Force-recalculate all formulas in an XLSX file via openpyxl."""

from __future__ import annotations

import sys
from pathlib import Path


def recalc(input_path: Path | str, output_path: Path | str | None = None) -> Path:
    """
    Set the calcMode to auto and mark all cells for recalculation.

    Useful when serving XLSX as a download — Excel recalculates on open,
    but Google Sheets and other viewers may not. This sets the full-calc flag.

    Args:
        input_path: Source .xlsx file.
        output_path: Destination .xlsx (overwrites input if None).

    Returns:
        Path to the output file.
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl is required: pip install openpyxl")

    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path

    wb = openpyxl.load_workbook(input_path)

    # Set workbook calculation properties
    if wb.calculation is None:
        from openpyxl.workbook.properties import CalcProperties
        wb.calculation = CalcProperties()

    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True

    wb.save(output_path)
    print(f"Recalculation flags set: {input_path} → {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: recalc.py input.xlsx [output.xlsx]")
        sys.exit(1)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    recalc(src, dst)
