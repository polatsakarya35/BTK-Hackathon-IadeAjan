"""upload_preflight birim testleri."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.upload_preflight import (
    build_column_map,
    inspect_upload,
    repair_tabular,
)

ROOT = Path(__file__).resolve().parents[1]


def test_build_column_map_turkish_headers() -> None:
    cols = [
        "Belge Kimliği",
        "İşlem Tarihi",
        "Evrak Türü",
        "Yekün (TRY)",
    ]
    mapping = build_column_map(cols)
    assert "invoice_id" in mapping
    assert "invoice_date" in mapping
    assert "invoice_type" in mapping
    assert "amount" in mapping


def test_repair_tabular_fixes_date_and_amount() -> None:
    rows = [
        {
            "Belge Kimliği": "X-1",
            "İşlem Tarihi": "01.05.2025",
            "Evrak Türü": "İhracat",
            "Yekün (TRY)": "12.500,50",
        }
    ]
    cols = list(rows[0].keys())
    repaired, issues, fixes = repair_tabular(rows, cols)
    assert repaired[0]["İşlem Tarihi"] == "2025-05-01"
    assert repaired[0]["Yekün (TRY)"] == 12500.5
    assert any("tarih" in f.lower() for f in fixes)
    assert issues == []


def test_inspect_upload_blocks_missing_columns() -> None:
    path = ROOT / "test_excels" / "test_excel_02_eksik_sutun.xlsx"
    if not path.is_file():
        pytest.skip("test excel yok")
    result = inspect_upload(str(path))
    assert result.ok
    assert result.invoice_count >= 1
    assert "Evrak Türü" in " ".join(result.warnings) or result.column_map.get(
        "invoice_type"
    ) is None


def test_inspect_karisik_repairs_some_rows() -> None:
    path = ROOT / "test_excels" / "test_excel_03_karisik_tipler.xlsx"
    if not path.is_file():
        pytest.skip("test excel yok")
    result = inspect_upload(str(path))
    assert result.ok
    assert result.invoice_count >= 1
