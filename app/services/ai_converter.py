"""Tabular satırları kanonik ingestion paketine çevirir (heuristic + opsiyonel Gemini)."""

from __future__ import annotations

import app.core.env  # noqa: F401 — .env yüklemesi
import json
import os
import re
import unicodedata
from typing import Any, Optional

from pydantic import BaseModel, field_validator

from app.services.upload_loader import EXCEL_ROW_META

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice_id": (
        "id", "invoice_id", "fatura no", "fatura_no", "belge no", "belgeno",
        "belge kimliği",                      # test_excel_01/03
    ),
    "invoice_date": (
        "date", "tarih", "fatura tarih", "fatura_tarih",
        "i\u0307şlem tarihi",                         # unicode İ (büyük noktalı i)
        "işlem tarihi",                       # ASCII i versiyonu (güvenlik)
    ),
    "invoice_type": (
        "type", "tip", "alış/satış", "alis_satis", "tür", "tur",
        "evrak türü",                         # test_excel_01/03
    ),
    "amount": (
        "amount", "tutar", "matrah", "toplam", "genel toplam",
        "yekün (try)", "yekün",               # test_excel_01/02/03
    ),
    "kdv_amount": ("kdv", "kdv tutar", "kdv_tutar", "vat"),
    "kdv_rate": (
        "kdv oran", "kdv_oran", "oran", "vat_rate",
        "kesilen vergi zımbırtısı",           # test_excel_01/03
    ),
    "supplier_name": (
        "supplier", "tedarikçi", "cari", "ünvan", "unvan", "firma",
        "karşı taraf ünvanı",                 # test_excel_01/02/03
    ),
    "supplier_tax": (
        "vkn", "tckn", "vergi no", "vergi_no", "tax",
        "firma vkn numarası",                 # test_excel_01/03
    ),
}


class IngestionInvoice(BaseModel):
    """Ingestion katmanı fatura minimum şeması."""

    model_config = {"extra": "ignore"}

    id: str
    type: str = "belirsiz"
    date: str = ""
    amount: float = 0.0
    kdv_amount: float = 0.0
    kdv_rate: int = 20
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_tax_number: Optional[str] = None
    source_location: Optional[dict[str, Any]] = None

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, value: Any) -> str:
        # "İhracat".lower() → "i̇hracat" (noktalı i, U+0069 + U+0307) üretir;
        # ASCII "ihracat" bu stringde bulunamaz. Önce Türkçe İ'yi dönüştür.
        value_str = str(value).replace("İ", "i").replace("Ş", "ş").lower()
        if "alış" in value_str or "alis" in value_str:
            return "alis"
        if "satış" in value_str or "satis" in value_str:
            return "satis"
        if "ihracat" in value_str:
            return "ihracat"
        return "belirsiz"

    @field_validator("amount", "kdv_amount", mode="before")
    @classmethod
    def coerce_float(cls, value: Any) -> float:
        try:
            return float(value) if value is not None else 0.0
        except (ValueError, TypeError):
            return 0.0


class IngestionSupplier(BaseModel):
    """Ingestion katmanı tedarikçi minimum şeması."""

    model_config = {"extra": "ignore"}

    id: str
    name: str = ""
    tax_number: str = ""


class IngestionPayload(BaseModel):
    """Kanonik ingestion paketi."""

    model_config = {"extra": "ignore"}

    company_profile: dict = {}
    invoices: list[IngestionInvoice]
    suppliers: list[IngestionSupplier] = []
    documents: list[dict] = []


def convert_tabular_to_canonical(
    rows: list[dict],
    columns: list[str],
    source_name: str = "",
) -> dict[str, Any]:
    """Tabular veriyi önce heuristic, gerekirse LLM ile kanonik pakete çevirir."""
    from app.services.upload_preflight import repair_tabular

    rows, _repair_issues, _fixes = repair_tabular(rows, columns)
    heuristic = _heuristic_convert(rows, columns, source_name=source_name)
    if heuristic and heuristic.get("invoices"):
        return heuristic

    try:
        return _llm_convert(rows, columns, source_name)
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise ValueError(f"heuristic_failed: {exc}") from exc


def _normalize_column_name(name: str) -> str:
    return str(name).strip().lower()


def _build_column_map(columns: list[str]) -> dict[str, str]:
    normalized_columns = {_normalize_column_name(column): column for column in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized_columns:
                mapping[canonical] = normalized_columns[alias]
                break
    return mapping


def _get_cell(row: dict[str, Any], column_map: dict[str, str], key: str) -> Any:
    source_column = column_map.get(key)
    if not source_column:
        return None
    return row.get(source_column)


def _slugify_supplier_id(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug or "tedarikci_bilinmiyor"


def _normalize_invoice_type(raw: Any) -> str:
    if raw is None:
        return "belirsiz"
    value_str = str(raw).replace("İ", "i").replace("Ş", "ş").lower()
    if "alış" in value_str or "alis" in value_str:
        return "alis"
    if "satış" in value_str or "satis" in value_str:
        return "satis"
    if "ihracat" in value_str:
        return "ihracat"
    return "belirsiz"


def _heuristic_convert(
    rows: list[dict],
    columns: list[str],
    source_name: str = "",
) -> dict[str, Any] | None:
    column_map = _build_column_map(columns)
    if "invoice_id" not in column_map and "amount" not in column_map:
        return None

    invoices: list[dict[str, Any]] = []
    suppliers_by_id: dict[str, dict[str, str]] = {}

    for index, row in enumerate(rows):
        invoice_id = _get_cell(row, column_map, "invoice_id")
        amount_raw = _get_cell(row, column_map, "amount")
        if not invoice_id and amount_raw is None:
            continue

        invoice_type = _normalize_invoice_type(
            _get_cell(row, column_map, "invoice_type")
        )
        try:
            amount = float(amount_raw) if amount_raw is not None else 0.0
        except (ValueError, TypeError):
            amount = 0.0
        kdv_rate_raw = _get_cell(row, column_map, "kdv_rate")
        try:
            kdv_rate = int(float(kdv_rate_raw)) if kdv_rate_raw is not None else 20
        except (ValueError, TypeError):
            kdv_rate = 20

        kdv_amount_raw = _get_cell(row, column_map, "kdv_amount")
        if kdv_amount_raw is not None:
            try:
                kdv_amount = round(float(kdv_amount_raw), 2)
            except (ValueError, TypeError):
                kdv_amount = round(amount * kdv_rate / 100, 2)
        else:
            kdv_amount = round(amount * kdv_rate / 100, 2)

        # İhracat / sıfır KDV: sütun yokken %20 varsayılanı LLM'de yanlış alarm üretir
        if invoice_type == "ihracat" and kdv_amount == 0:
            kdv_rate = 0
        elif kdv_rate_raw is None and kdv_amount == 0:
            kdv_rate = 0

        supplier_name = _get_cell(row, column_map, "supplier_name")
        supplier_tax = _get_cell(row, column_map, "supplier_tax")
        supplier_id: str | None = None
        if supplier_tax:
            supplier_id = str(supplier_tax).strip()
        elif supplier_name:
            supplier_id = _slugify_supplier_id(str(supplier_name))

        invoice_date = _get_cell(row, column_map, "invoice_date")
        excel_row = row.get(EXCEL_ROW_META)
        if not isinstance(excel_row, int):
            excel_row = index + 2
        invoice = {
            "id": str(invoice_id or f"FAT-UPLOAD-{index + 1:03d}"),
            "type": invoice_type,
            "date": str(invoice_date or ""),
            "amount": amount,
            "kdv_amount": kdv_amount,
            "kdv_rate": kdv_rate,
            "supplier_id": supplier_id,
            "supplier_name": str(supplier_name) if supplier_name else None,
            "supplier_tax_number": str(supplier_tax) if supplier_tax else None,
            "source_location": {
                "file": source_name,
                "row": excel_row,
                "column": None,
            },
        }
        invoices.append(invoice)

        if supplier_id and supplier_name:
            suppliers_by_id[supplier_id] = {
                "id": supplier_id,
                "name": str(supplier_name),
                "tax_number": str(supplier_tax or supplier_id),
            }

    if not invoices:
        return None

    return _validate_payload(
        {
            "company_profile": {},
            "invoices": invoices,
            "suppliers": list(suppliers_by_id.values()),
            "documents": [],
        }
    )


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    validated = IngestionPayload.model_validate(payload)
    if not validated.invoices:
        raise ValueError("En az 1 fatura gerekli")
    return validated.model_dump(mode="python")


def _llm_convert(
    rows: list[dict],
    columns: list[str],
    source_name: str,
) -> dict[str, Any]:
    if not os.getenv("GOOGLE_API_KEY"):
        raise ValueError("heuristic_failed: GOOGLE_API_KEY eksik")

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise RuntimeError(
            "langchain_google_genai kurulu değil — pip install langchain-google-genai"
        ) from exc

    max_sample = int(os.getenv("MAX_LLM_TABULAR_ROWS", "5"))
    total_rows = len(rows)
    sample_rows = rows[:max_sample]
    system_prompt = (
        f"Aşağıda bir tablonun sütun adları ve ilk {len(sample_rows)} satır örneği var "
        f"(toplam {total_rows} satır).\n"
        "Bu tabloyu, senden istenilen IngestionPayload şemasına map et.\n"
        "Yalnızca örnek satırlardan çıkarım yap; tüm dosyayı uydurma.\n"
        "Eksik alanlar için halüsinasyon görme, null/None bırak."
    )
    human_prompt = (
        f"Kaynak: {source_name}\n"
        f"Sütunlar: {json.dumps(columns, ensure_ascii=False)}\n"
        f"Örnek satırlar: {json.dumps(sample_rows, ensure_ascii=False, default=str)}"
    )

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    structured_llm = llm.with_structured_output(IngestionPayload)

    try:
        response_obj = structured_llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]
        )
    except Exception as exc:
        raise ValueError(f"LLM Structured Output başarısız oldu: {exc}") from exc

    canonical_dict = response_obj.model_dump(mode="python")
    return _validate_payload(canonical_dict)
