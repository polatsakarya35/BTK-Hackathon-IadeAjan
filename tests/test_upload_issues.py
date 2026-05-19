"""UploadIssue ve satır/sütun düzeyinde preflight testleri."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.schemas.upload_issues import UploadIssue, format_issue_message
from app.services.upload_loader import EXCEL_ROW_META
from app.services.upload_preflight import build_column_map, inspect_upload, repair_tabular


def test_repair_tabular_amount_and_type_issues() -> None:
    rows = [
        {
            EXCEL_ROW_META: 2,
            "Belge Kimliği": "X-1",
            "İşlem Tarihi": "2025-05-01",
            "Evrak Türü": "BilinmeyenTip",
            "Yekün (TRY)": "geçersiz",
        },
        {
            EXCEL_ROW_META: 3,
            "Belge Kimliği": "X-2",
            "İşlem Tarihi": "2025-05-02",
            "Evrak Türü": "",
            "Yekün (TRY)": 100.0,
        },
    ]
    cols = list(rows[0].keys())
    cols = [c for c in cols if c != EXCEL_ROW_META]
    repaired, issues, _fixes = repair_tabular(rows, cols)

    assert len(repaired) == 2
    amount_issues = [i for i in issues if i.field == "amount"]
    type_issues = [i for i in issues if i.field == "invoice_type"]
    assert len(amount_issues) == 1
    assert amount_issues[0].row == 2
    assert amount_issues[0].column == "Yekün (TRY)"
    assert amount_issues[0].column_letter == "D"
    assert "okunamadı" in amount_issues[0].message_tr

    assert len(type_issues) >= 2
    unknown = next(i for i in type_issues if "tanınmadı" in i.message_tr)
    assert unknown.row == 2
    assert unknown.column == "Evrak Türü"
    empty = next(i for i in type_issues if "boş" in i.message_tr)
    assert empty.row == 3


def test_format_issue_message_includes_column() -> None:
    issue = UploadIssue(
        row=1847,
        column="Evrak Türü",
        column_letter="C",
        field="invoice_type",
        message_tr="değer tanınmadı",
        severity="warning",
    )
    text = format_issue_message(issue)
    assert "1847" in text
    assert "Evrak Türü" in text
    assert "(C)" in text


def test_inspect_upload_row_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_ROWS", "5")
    path = tmp_path / "big.xlsx"
    rows = [
        {
            "Belge Kimliği": f"F-{i}",
            "Yekün (TRY)": float(i),
            "Evrak Türü": "Satış",
        }
        for i in range(10)
    ]
    pd.DataFrame(rows).to_excel(path, index=False, engine="openpyxl")

    result = inspect_upload(str(path))
    assert not result.ok
    assert any(i.severity == "error" for i in result.issues)
    assert any("üst sınır" in i.message_tr.lower() or "sınır" in i.message_tr for i in result.issues)


def test_frame_rows_have_excel_row_metadata(tmp_path: Path) -> None:
    path = tmp_path / "small.xlsx"
    pd.DataFrame(
        {"Belge Kimliği": ["A"], "Yekün (TRY)": [1.0], "Evrak Türü": ["Alış"]}
    ).to_excel(path, index=False, engine="openpyxl")
    result = inspect_upload(str(path))
    assert result.ok
    assert result.repaired_rows
    assert result.repaired_rows[0].get(EXCEL_ROW_META) == 2


def test_heuristic_convert_sets_source_location() -> None:
    from app.services.ai_converter import convert_tabular_to_canonical

    rows = [
        {
            EXCEL_ROW_META: 5,
            "Belge Kimliği": "F-1",
            "Yekün (TRY)": 1000.0,
            "Evrak Türü": "Satış",
        }
    ]
    cols = ["Belge Kimliği", "Yekün (TRY)", "Evrak Türü"]
    payload = convert_tabular_to_canonical(rows, cols, source_name="liste.xlsx")
    inv = payload["invoices"][0]
    loc = inv.get("source_location") or {}
    assert loc.get("file") == "liste.xlsx"
    assert loc.get("row") == 5


def test_build_column_map_unchanged() -> None:
    cols = ["Belge Kimliği", "Yekün (TRY)", "Evrak Türü"]
    mapping = build_column_map(cols)
    assert mapping["invoice_id"] == "Belge Kimliği"
    assert mapping["amount"] == "Yekün (TRY)"
