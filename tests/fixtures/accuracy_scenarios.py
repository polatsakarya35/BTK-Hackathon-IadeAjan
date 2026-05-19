"""Doğruluk benchmark senaryoları — DataFrame üreticileri (T01–T16, P01)."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

# T01–T13: v3_accuracy_test ile paylaşılan tanımlar
from tests.v3_accuracy_test import (  # noqa: F401
    SCENARIOS as V3_SCENARIOS,
    _df_all_data_quality_issues,
    _df_bad_dates_only,
    _df_clean_domestic,
    _df_clean_export,
    _df_invalid_vkn,
    _df_large_amount,
    _df_mixed_chaos,
    _df_negative_amounts,
    _df_perfect_minimal,
    _df_satis_only,
    _df_small_amount,
    _df_tevkifat_clean,
    _df_zero_amounts,
)


def _df_weak_export_kdv() -> pd.DataFrame:
    """T14 — İhracat etiketi var, KDV > 0 (zayıf ihracat; GÇB güçlü tetik yok)."""
    return pd.DataFrame([
        {
            "Fatura No": f"WEAK-{i:03d}",
            "Tip": "İhracat",
            "Tarih": "2025-03-15",
            "Tutar": 40000 + i * 1000,
            "KDV": 7200,
            "VKN": "1212121212",
            "Tedarikçi": "Zayıf İhracat Ltd.",
        }
        for i in range(1, 6)
    ])


def _df_large_export_ymm() -> pd.DataFrame:
    """T15 — Güçlü ihracat + yüksek tutar → YMM + GÇB eksik."""
    return pd.DataFrame([
        {
            "Fatura No": f"YMM-{i:03d}",
            "Tip": "İhracat",
            "Tarih": "2025-03-15",
            "Tutar": 120000,
            "KDV": 0,
            "VKN": "1313131313",
            "Tedarikçi": "Büyük İhracat A.Ş.",
        }
        for i in range(1, 4)
    ])


def _df_tevkifat_alis() -> pd.DataFrame:
    """T16 — Tevkifat alış faturaları → 2 No eksik."""
    return pd.DataFrame([
        {
            "Fatura No": f"TEV2-{i:03d}",
            "Tip": "Alış",
            "Tarih": "2025-04-10",
            "Tutar": 25000,
            "KDV": 4500,
            "VKN": "1414141414",
            "Tedarikçi": "Tevkifat Tedarik A.Ş.",
        }
        for i in range(1, 6)
    ])


def _df_clean_export_for_pdf() -> pd.DataFrame:
    """P01 — Excel eşlikçisi: temiz ihracat + PDF GÇB mock."""
    return pd.DataFrame([
        {
            "Fatura No": "PDF-001",
            "Tip": "İhracat",
            "Tarih": "2025-05-01",
            "Tutar": 35000,
            "KDV": 0,
            "VKN": "1515151515",
            "Tedarikçi": "PDF Test A.Ş.",
        },
    ])


EXTRA_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "T14",
        "label": "Zayıf ihracat (KDV>0)",
        "df": _df_weak_export_kdv,
        "refund_answer": "ihracat",
        "kind": "excel",
    },
    {
        "id": "T15",
        "label": "Büyük tutar YMM eşiği",
        "df": _df_large_export_ymm,
        "refund_answer": "ihracat",
        "kind": "excel",
    },
    {
        "id": "T16",
        "label": "Tevkifat 2 No eksik",
        "df": _df_tevkifat_alis,
        "refund_answer": "tevkifat",
        "kind": "excel",
    },
    {
        "id": "P01",
        "label": "Excel + PDF GÇB mock",
        "df": _df_clean_export_for_pdf,
        "refund_answer": "ihracat",
        "kind": "excel_pdf",
    },
]

ALL_SCENARIOS: list[dict[str, Any]] = list(V3_SCENARIOS) + EXTRA_SCENARIOS

SCENARIO_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in ALL_SCENARIOS}
