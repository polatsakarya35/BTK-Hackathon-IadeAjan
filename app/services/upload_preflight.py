"""Yükleme öncesi kontrol, kullanıcı uyarıları ve tabular veri otomatik düzeltme."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.schemas.upload_issues import (
    UploadIssue,
    column_name_to_letter,
    issues_to_legacy_strings,
)
from app.services import upload_loader
from app.services.upload_loader import EXCEL_ROW_META

# ai_converter ile aynı alias seti (sütun eşleme tutarlılığı)
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice_id": (
        "id", "invoice_id", "fatura no", "fatura_no", "belge no", "belgeno",
        "belge kimliği",
    ),
    "invoice_date": (
        "date", "tarih", "fatura tarih", "fatura_tarih",
        "i\u0307şlem tarihi", "işlem tarihi",
    ),
    "invoice_type": (
        "type", "tip", "alış/satış", "alis_satis", "tür", "tur", "evrak türü",
    ),
    "amount": (
        "amount", "tutar", "matrah", "toplam", "genel toplam",
        "yekün (try)", "yekün",
    ),
    "kdv_amount": ("kdv", "kdv tutar", "kdv_tutar", "vat"),
    "kdv_rate": (
        "kdv oran", "kdv_oran", "oran", "vat_rate", "kesilen vergi zımbırtısı",
    ),
    "supplier_name": (
        "supplier", "tedarikçi", "cari", "ünvan", "unvan", "firma",
        "karşı taraf ünvanı",
    ),
    "supplier_tax": (
        "vkn", "tckn", "vergi no", "vergi_no", "tax", "firma vkn numarası",
    ),
}

_DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT_NOISE = re.compile(r"[^\d,.\-]")


@dataclass
class PreflightResult:
    """Dosya yükleme öncesi / sonrası inceleme sonucu."""

    ok: bool
    blocking_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    issues: list[UploadIssue] = field(default_factory=list)
    column_map: dict[str, str] = field(default_factory=dict)
    row_count: int = 0
    invoice_count: int = 0
    repaired_rows: list[dict[str, Any]] | None = None
    repaired_columns: list[str] | None = None


def _normalize_column_name(name: str) -> str:
    return str(name).strip().lower()


def build_column_map(columns: list[str]) -> dict[str, str]:
    normalized = {_normalize_column_name(c): c for c in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[canonical] = normalized[alias]
                break
    return mapping


def _get_cell(row: dict[str, Any], column_map: dict[str, str], key: str) -> Any:
    col = column_map.get(key)
    if not col:
        return None
    return row.get(col)


def _excel_row_number(row: dict[str, Any], index: int) -> int:
    raw = row.get(EXCEL_ROW_META)
    if isinstance(raw, int) and raw >= 2:
        return raw
    return index + 2


def _make_issue(
    *,
    message_tr: str,
    severity: str,
    columns: list[str],
    column_map: dict[str, str],
    field: str | None = None,
    row: int | None = None,
) -> UploadIssue:
    col_name = column_map.get(field) if field else None
    return UploadIssue(
        row=row,
        column=col_name,
        column_letter=column_name_to_letter(columns, col_name),
        field=field,
        message_tr=message_tr,
        severity=severity,  # type: ignore[arg-type]
    )


def _sync_legacy_strings(result: PreflightResult) -> None:
    blocking, warnings = issues_to_legacy_strings(result.issues)
    for msg in blocking:
        if msg not in result.blocking_errors:
            result.blocking_errors.append(msg)
    for msg in warnings:
        if msg not in result.warnings:
            result.warnings.append(msg)


def _normalize_type_raw(value: Any) -> str:
    if value is None:
        return ""
    value_str = str(value).replace("İ", "i").replace("Ş", "ş").lower().strip()
    if "alış" in value_str or "alis" in value_str:
        return "alis"
    if "satış" in value_str or "satis" in value_str:
        return "satis"
    if "ihracat" in value_str or "ihrac" in value_str:
        return "ihracat"
    if value_str in ("1", "a", "alim"):
        return "alis"
    if value_str in ("2", "s"):
        return "satis"
    return value_str


def _type_display(canonical: str) -> str:
    return {"alis": "Alış", "satis": "Satış", "ihracat": "İhracat"}.get(
        canonical, str(canonical)
    )


def _try_fix_date(value: Any) -> tuple[str | None, bool]:
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        return None, False
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan", "-"):
        return None, False
    if _DATE_ISO.match(s):
        return s, False
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d"), True
        except ValueError:
            continue
    return s, False


def _try_fix_amount(value: Any) -> tuple[float | None, bool]:
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        return None, False
    if isinstance(value, (int, float)):
        try:
            return float(value), False
        except (ValueError, TypeError):
            return None, False
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan", "-", "yanlis", "hatali"):
        return None, False
    cleaned = _AMOUNT_NOISE.sub("", s)
    if not cleaned:
        return None, False
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned), True
    except ValueError:
        return None, False


def _try_fix_vkn(value: Any) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    s = str(value).strip()
    if not s:
        return None, False
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s, False
    if len(digits) in (10, 11):
        return digits, digits != s
    return s, False


def repair_tabular(
    rows: list[dict[str, Any]],
    columns: list[str],
) -> tuple[list[dict[str, Any]], list[UploadIssue], list[str]]:
    """
    Tabular satırlarda güvenli otomatik düzeltmeler uygular.
    Döner: (düzeltilmiş_satırlar, yapılandırılmış_uyarılar, yapılan_düzeltmeler).
    """
    column_map = build_column_map(columns)
    issues: list[UploadIssue] = []
    fixes: list[str] = []
    repaired: list[dict[str, Any]] = []
    fix_counts: dict[str, int] = {}

    for index, row in enumerate(rows):
        new_row = dict(row)
        excel_row = _excel_row_number(new_row, index)
        inv_id = _get_cell(new_row, column_map, "invoice_id")
        amount_raw = _get_cell(new_row, column_map, "amount")

        if not inv_id and amount_raw is None:
            continue

        if not inv_id and amount_raw is not None:
            generated = f"FAT-AUTO-{index + 1:03d}"
            id_col = column_map.get("invoice_id")
            if id_col:
                new_row[id_col] = generated
                fix_counts["fatura_no"] = fix_counts.get("fatura_no", 0) + 1

        date_col = column_map.get("invoice_date")
        if date_col and date_col in new_row:
            fixed_date, changed = _try_fix_date(new_row[date_col])
            if changed and fixed_date:
                new_row[date_col] = fixed_date
                fix_counts["tarih"] = fix_counts.get("tarih", 0) + 1

        amount_col = column_map.get("amount")
        if amount_col and amount_col in new_row:
            fixed_amt, changed = _try_fix_amount(new_row[amount_col])
            if changed and fixed_amt is not None:
                new_row[amount_col] = fixed_amt
                fix_counts["tutar"] = fix_counts.get("tutar", 0) + 1
            elif fixed_amt is None and new_row[amount_col] not in (None, ""):
                issues.append(
                    _make_issue(
                        row=excel_row,
                        field="amount",
                        message_tr=(
                            f"tutar okunamadı ({new_row[amount_col]!r}) "
                            "— analizde sıfır/ceza riski"
                        ),
                        severity="warning",
                        columns=columns,
                        column_map=column_map,
                    )
                )

        type_col = column_map.get("invoice_type")
        if type_col and type_col in new_row:
            raw_type = new_row[type_col]
            canonical = _normalize_type_raw(raw_type)
            if canonical in ("alis", "satis", "ihracat"):
                display = _type_display(canonical)
                if str(raw_type).strip() != display:
                    new_row[type_col] = display
                    fix_counts["evrak_turu"] = fix_counts.get("evrak_turu", 0) + 1
            elif raw_type is not None and str(raw_type).strip():
                issues.append(
                    _make_issue(
                        row=excel_row,
                        field="invoice_type",
                        message_tr=(
                            f"değer {raw_type!r} tanınmadı — "
                            "'Alış', 'Satış' veya 'İhracat' yazın"
                        ),
                        severity="warning",
                        columns=columns,
                        column_map=column_map,
                    )
                )
            else:
                issues.append(
                    _make_issue(
                        row=excel_row,
                        field="invoice_type",
                        message_tr="evrak türü boş — analizde 'belirsiz' sayılır",
                        severity="warning",
                        columns=columns,
                        column_map=column_map,
                    )
                )

        tax_col = column_map.get("supplier_tax")
        if tax_col and tax_col in new_row:
            raw_tax = new_row[tax_col]
            fixed_vkn, changed = _try_fix_vkn(raw_tax)
            if changed and fixed_vkn:
                new_row[tax_col] = fixed_vkn
                fix_counts["vkn"] = fix_counts.get("vkn", 0) + 1
            elif raw_tax is not None and str(raw_tax).strip():
                digits = re.sub(r"\D", "", str(raw_tax))
                if digits and len(digits) not in (10, 11):
                    issues.append(
                        _make_issue(
                            row=excel_row,
                            field="supplier_tax",
                            message_tr=(
                                f"VKN/TCKN {len(digits)} hane — "
                                "10 veya 11 rakam olmalı"
                            ),
                            severity="warning",
                            columns=columns,
                            column_map=column_map,
                        )
                    )

        repaired.append(new_row)

    if fix_counts.get("fatura_no"):
        fixes.append(
            f"{fix_counts['fatura_no']} satıra otomatik fatura numarası verildi"
        )
    if fix_counts.get("tarih"):
        fixes.append(f"{fix_counts['tarih']} tarih YYYY-MM-DD formatına çevrildi")
    if fix_counts.get("tutar"):
        fixes.append(f"{fix_counts['tutar']} tutar sayıya çevrildi")
    if fix_counts.get("evrak_turu"):
        fixes.append(f"{fix_counts['evrak_turu']} evrak türü standartlaştırıldı")
    if fix_counts.get("vkn"):
        fixes.append(f"{fix_counts['vkn']} VKN/TCKN temizlendi (sadece rakam)")

    return repaired, issues, fixes


def inspect_upload(file_path: str, *, apply_repairs: bool = True) -> PreflightResult:
    """Yüklenen dosyayı okur; bloklayıcı hata, uyarı ve isteğe bağlı düzeltme üretir."""
    issues: list[UploadIssue] = []
    fixes: list[str] = []

    try:
        loaded = upload_loader.load_uploaded_file(file_path)
    except Exception as exc:
        msg = str(exc)
        if "UPLOAD_ROW_LIMIT_EXCEEDED" in msg:
            issues.append(
                UploadIssue(
                    message_tr=msg.split(":", 1)[-1].strip()
                    if ":" in msg
                    else msg,
                    severity="error",
                )
            )
        else:
            issues.append(
                UploadIssue(
                    message_tr=f"Dosya okunamadı: {exc}",
                    severity="error",
                )
            )
        blocking, warnings = issues_to_legacy_strings(issues)
        return PreflightResult(
            ok=False,
            blocking_errors=blocking,
            warnings=warnings,
            issues=issues,
        )

    if loaded["mode"] == "canonical":
        invoices = loaded.get("canonical", {}).get("invoices", [])
        if not invoices:
            issues.append(
                UploadIssue(
                    message_tr="JSON dosyasında hiç fatura kaydı yok.",
                    severity="error",
                )
            )
        blocking, warnings = issues_to_legacy_strings(issues)
        return PreflightResult(
            ok=not issues or all(i.severity != "error" for i in issues),
            blocking_errors=blocking,
            warnings=warnings,
            issues=issues,
            fixes_applied=fixes,
            row_count=len(invoices),
            invoice_count=len(invoices),
        )

    tabular = loaded["tabular"]
    rows = tabular["rows"]
    columns = tabular["columns"]
    column_map = build_column_map(columns)

    if not rows:
        issues.append(
            UploadIssue(
                message_tr="Dosyada veri satırı bulunamadı.",
                severity="error",
            )
        )
        blocking, warnings = issues_to_legacy_strings(issues)
        return PreflightResult(ok=False, blocking_errors=blocking, warnings=warnings, issues=issues)

    if "invoice_id" not in column_map and "amount" not in column_map:
        issues.append(
            UploadIssue(
                message_tr=(
                    "Fatura numarası veya tutar sütunu bulunamadı. "
                    "En az birini şu adlardan biriyle ekleyin: "
                    "'Belge Kimliği', 'Fatura No', 'Yekün (TRY)', 'Tutar'."
                ),
                severity="error",
            )
        )
        blocking, warnings = issues_to_legacy_strings(issues)
        return PreflightResult(
            ok=False,
            blocking_errors=blocking,
            warnings=warnings,
            issues=issues,
            column_map=column_map,
            row_count=len(rows),
        )

    if "invoice_type" not in column_map:
        issues.append(
            UploadIssue(
                message_tr=(
                    "'Evrak Türü' sütunu yok — faturalar 'belirsiz' sayılır; "
                    "ihracat/tevkifat kuralları yanlış uygulanabilir."
                ),
                severity="warning",
            )
        )
    if "invoice_date" not in column_map:
        issues.append(
            UploadIssue(
                message_tr="'İşlem Tarihi' sütunu yok — tarih cezaları uygulanmaz.",
                severity="warning",
            )
        )
    if "supplier_tax" not in column_map:
        issues.append(
            UploadIssue(
                message_tr="'VKN' sütunu yok — vergi no kontrolleri atlanır.",
                severity="warning",
            )
        )

    repaired_rows = rows
    repaired_columns = columns
    if apply_repairs:
        repaired_rows, repair_issues, fixes = repair_tabular(rows, columns)
        issues.extend(repair_issues)

    invoice_count = sum(
        1
        for row in repaired_rows
        if _get_cell(row, column_map, "invoice_id")
        or _get_cell(row, column_map, "amount") is not None
    )

    if invoice_count == 0:
        issues.append(
            UploadIssue(
                message_tr="Geçerli fatura satırı çıkarılamadı (boş veya okunamayan satırlar).",
                severity="error",
            )
        )

    blocking, warnings = issues_to_legacy_strings(issues)
    result = PreflightResult(
        ok=not any(i.severity == "error" for i in issues),
        blocking_errors=blocking,
        warnings=warnings,
        issues=issues,
        fixes_applied=fixes,
        column_map=column_map,
        row_count=len(rows),
        invoice_count=invoice_count,
        repaired_rows=repaired_rows if apply_repairs else None,
        repaired_columns=repaired_columns,
    )
    return result


def write_repaired_excel(rows: list[dict[str, Any]], columns: list[str], dest: Path) -> None:
    """Düzeltilmiş tabloyu xlsx olarak yazar."""
    import pandas as pd

    clean_rows = [upload_loader.strip_row_metadata(r) for r in rows]
    frame = pd.DataFrame(clean_rows, columns=columns if columns else None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_excel(dest, index=False, engine="openpyxl")


def issues_to_csv_bytes(issues: list[UploadIssue]) -> bytes:
    """UTF-8 BOM ile CSV — Excel'de Türkçe karakterler için."""
    import csv

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Satır", "Sütun", "Sütun Harfi", "Alan", "Önem", "Mesaj"])
    for issue in issues:
        writer.writerow(
            [
                issue.row if issue.row is not None else "",
                issue.column or "",
                issue.column_letter or "",
                issue.field_label_tr,
                "Hata" if issue.severity == "error" else "Uyarı",
                issue.message_tr,
            ]
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def get_template_excel_bytes() -> bytes:
    """Kullanıcıya indirilecek örnek şablon Excel."""
    import pandas as pd

    data = {
        "Belge Kimliği": ["FAT-001", "FAT-002", "FAT-003"],
        "İşlem Tarihi": ["2025-05-01", "2025-05-10", "2025-05-15"],
        "Evrak Türü": ["İhracat", "Satış", "Alış"],
        "Yekün (TRY)": [45000.0, 15000.0, 8000.0],
        "Kesilen Vergi Zımbırtısı": [0, 20, 20],
        "Karşı Taraf Ünvanı": [
            "Global Tech LLC",
            "Bursa Demir Çelik A.Ş.",
            "Ankara Lojistik Ltd.",
        ],
        "Firma VKN Numarası": ["", "1112223334", "9998887776"],
    }
    buffer = io.BytesIO()
    pd.DataFrame(data).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()
