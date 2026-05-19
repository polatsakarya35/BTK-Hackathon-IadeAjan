"""Çok satırlı Excel okuma, preflight ve heuristic dönüşüm testleri."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import upload_loader
from app.services.ai_converter import convert_tabular_to_canonical
from app.services.upload_loader import EXCEL_ROW_META
from app.services.upload_preflight import inspect_upload
from tests.helpers.large_excel import write_invoice_xlsx
from tests.helpers.pipeline_score import extract_score_report, run_upload_to_final_score


def test_load_small_excel_pandas_path(tmp_path: Path) -> None:
    path = tmp_path / "small.xlsx"
    write_invoice_xlsx(path, 100)

    loaded = upload_loader.load_uploaded_file(str(path))
    assert loaded["mode"] == "tabular"
    rows = loaded["tabular"]["rows"]
    assert len(rows) == 100
    assert rows[0].get(EXCEL_ROW_META) == 2
    assert rows[99].get(EXCEL_ROW_META) == 101


@pytest.mark.slow
def test_load_large_excel_read_only_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LARGE_EXCEL_READ_ONLY_THRESHOLD", "5000")
    path = tmp_path / "large.xlsx"
    write_invoice_xlsx(path, 12_000)

    with patch(
        "app.services.upload_loader._read_excel_openpyxl",
        wraps=upload_loader._read_excel_openpyxl,
    ) as read_only_mock:
        loaded = upload_loader.load_uploaded_file(str(path))
        read_only_mock.assert_called_once()

    rows = loaded["tabular"]["rows"]
    assert len(rows) == 12_000
    assert rows[0].get(EXCEL_ROW_META) == 2
    assert rows[-1].get(EXCEL_ROW_META) == 12_001


@pytest.mark.slow
def test_inspect_upload_large_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LARGE_EXCEL_READ_ONLY_THRESHOLD", "5000")
    path = tmp_path / "large_preflight.xlsx"
    write_invoice_xlsx(path, 12_000)

    result = inspect_upload(str(path))
    assert result.ok
    assert result.invoice_count == 12_000
    assert result.row_count == 12_000
    assert result.repaired_rows
    assert result.repaired_rows[-1].get(EXCEL_ROW_META) == 12_001


@pytest.mark.slow
def test_heuristic_convert_5k_rows(tmp_path: Path) -> None:
    path = tmp_path / "medium.xlsx"
    write_invoice_xlsx(path, 5_000)

    loaded = upload_loader.load_uploaded_file(str(path))
    rows = loaded["tabular"]["rows"]
    columns = loaded["tabular"]["columns"]

    started = time.perf_counter()
    payload = convert_tabular_to_canonical(
        rows, columns, source_name=path.name
    )
    elapsed = time.perf_counter() - started

    assert len(payload["invoices"]) == 5_000
    assert elapsed < 60.0


@pytest.mark.slow
def test_large_excel_pipeline_score_500_rows(tmp_path: Path) -> None:
    """500 satırlık dosyada tam analiz sonrası 0–100 skor üretilir."""
    path = tmp_path / "score_500.xlsx"
    write_invoice_xlsx(path, 500)

    started = time.perf_counter()
    final = run_upload_to_final_score(path)
    elapsed = time.perf_counter() - started

    assert final.get("analysis_status") == "completed", (
        f"analysis_status={final.get('analysis_status')!r} "
        f"error={final.get('error_state')!r}"
    )
    score, report = extract_score_report(final)
    assert score is not None, "calculated_score üretilmedi"
    assert 0 <= score <= 100
    assert final.get("normalized_invoices")
    assert len(final["normalized_invoices"]) == 500
    assert report.get("risk_category")
    # Sentetik 500 satır Satış/Alış: tipik band ~20–60 (makro/veri kalitesi cezaları)
    assert elapsed < 120.0


@pytest.mark.slow
def test_large_excel_pipeline_score_5k_rows(tmp_path: Path) -> None:
    """5K satır: nihai skor ve süre (yarışma ölçeği)."""
    path = tmp_path / "score_5k.xlsx"
    write_invoice_xlsx(path, 5_000)

    started = time.perf_counter()
    final = run_upload_to_final_score(path)
    elapsed = time.perf_counter() - started

    assert final.get("analysis_status") == "completed"
    score, report = extract_score_report(final)
    assert score is not None
    assert 0 <= score <= 100
    assert len(final.get("normalized_invoices") or []) == 5_000
    assert report.get("finance_eligibility") is not None
    assert elapsed < 300.0


def test_upload_row_limit_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_ROWS", "100")
    path = tmp_path / "over_limit.xlsx"
    write_invoice_xlsx(path, 101)

    result = inspect_upload(str(path))
    assert not result.ok
    assert any(i.severity == "error" for i in result.issues)
