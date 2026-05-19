"""Yükleme öncesi satır/sütun düzeyinde hata ve uyarı modeli."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning"]

FIELD_LABELS_TR: dict[str, str] = {
    "invoice_id": "Fatura no",
    "invoice_date": "Tarih",
    "invoice_type": "Evrak türü",
    "amount": "Tutar",
    "kdv_amount": "KDV tutarı",
    "kdv_rate": "KDV oranı",
    "supplier_name": "Ünvan",
    "supplier_tax": "VKN/TCKN",
}


@dataclass
class UploadIssue:
    """Excel/CSV satırında tespit edilen sorun."""

    message_tr: str
    severity: Severity = "warning"
    row: int | None = None
    column: str | None = None
    column_letter: str | None = None
    field: str | None = None

    @property
    def field_label_tr(self) -> str:
        if self.field and self.field in FIELD_LABELS_TR:
            return FIELD_LABELS_TR[self.field]
        return self.field or "—"


def format_issue_message(issue: UploadIssue) -> str:
    """Geriye uyumlu düz metin (eski warnings / blocking_errors)."""
    if issue.row is not None:
        col_part = ""
        if issue.column:
            letter = f" ({issue.column_letter})" if issue.column_letter else ""
            col_part = f", sütun «{issue.column}»{letter}"
        return f"Satır {issue.row}{col_part}: {issue.message_tr}"
    return issue.message_tr


def issues_to_legacy_strings(issues: list[UploadIssue]) -> tuple[list[str], list[str]]:
    """UploadIssue listesinden blocking_errors ve warnings üretir."""
    blocking: list[str] = []
    warnings: list[str] = []
    for issue in issues:
        text = format_issue_message(issue)
        if issue.severity == "error":
            if text not in blocking:
                blocking.append(text)
        elif text not in warnings:
            warnings.append(text)
    return blocking, warnings


def column_name_to_letter(columns: list[str], column_name: str | None) -> str | None:
    if not column_name or column_name not in columns:
        return None
    try:
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None
    return get_column_letter(columns.index(column_name) + 1)
