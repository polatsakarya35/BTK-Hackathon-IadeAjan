"""Çok satırlı test Excel dosyaları üretir (repo'ya commit edilmez)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

CHUNK_SIZE = 5000

COLUMNS = ("Belge Kimliği", "Yekün (TRY)", "Evrak Türü")


def _chunk_rows(start: int, count: int) -> dict[str, list]:
    end = start + count
    return {
        "Belge Kimliği": [f"FAT-{i:06d}" for i in range(start, end)],
        "Yekün (TRY)": [float(1000 + (i % 500)) for i in range(start, end)],
        "Evrak Türü": ["Satış" if i % 2 == 0 else "Alış" for i in range(start, end)],
    }


def write_invoice_xlsx(path: Path, row_count: int) -> None:
    """Minimal fatura şemasında row_count satırlık xlsx yazar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if row_count <= 0:
        pd.DataFrame(columns=list(COLUMNS)).to_excel(
            path, index=False, engine="openpyxl"
        )
        return

    parts: list[pd.DataFrame] = []
    written = 0
    while written < row_count:
        take = min(CHUNK_SIZE, row_count - written)
        parts.append(pd.DataFrame(_chunk_rows(written, take)))
        written += take

    frame = pd.concat(parts, ignore_index=True)
    frame.to_excel(path, index=False, engine="openpyxl")
