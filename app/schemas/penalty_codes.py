"""İadeAjan — Ceza Kanunnamesi (tek puan kaynağı).

Sistemdeki her ceza bu dosyadan tanımlanır.
Başka hiçbir yerde sabit puan yer alamaz.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class PenaltyCode(str, Enum):
    """KDV İade Analiz Sistemi ceza kod kataloğu."""

    # ── Zorunlu Belge Eksiklikleri (Python only — mandatory lock) ──────────
    GCB_MISSING = "GCB_MISSING"
    NO2_DECLARATION_MISSING = "NO2_DECLARATION_MISSING"
    NO2_DECLARATION_UNPAID = "NO2_DECLARATION_UNPAID"
    YMM_REPORT_MISSING = "YMM_REPORT_MISSING"

    # ── Tedarikçi Riskleri (Python only) ───────────────────────────────────
    SUPPLIER_SMIYB = "SUPPLIER_SMIYB"
    SUPPLIER_HIGH_RISK = "SUPPLIER_HIGH_RISK"
    SUPPLIER_BLACKLIST_LOCK = "SUPPLIER_BLACKLIST_LOCK"

    # ── Veri Kalitesi (LLM veya Python fallback, count + tavan) ────────────
    DQ_INVALID_DATE = "DQ_INVALID_DATE"
    DQ_ZERO_AMOUNT = "DQ_ZERO_AMOUNT"
    DQ_INVALID_VKN = "DQ_INVALID_VKN"
    # RESERVED v3.3 — checksum; v3.2-macro'da PENALTY_MATRIX'e eklenmez
    # DQ_INVALID_VKN_CHECKSUM = "DQ_INVALID_VKN_CHECKSUM"

    # ── Tutar / Hesap Tutarsızlıkları (LLM veya hybrid) ───────────────────
    AMOUNT_MISMATCH_GCB = "AMOUNT_MISMATCH_GCB"
    KDV_CALC_ERROR = "KDV_CALC_ERROR"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    CHRONOLOGY_ERROR = "CHRONOLOGY_ERROR"
    KDV_RATE_MISMATCH_EXPORT = "KDV_RATE_MISMATCH_EXPORT"
    SELF_INVOICE = "SELF_INVOICE"
    FUTURE_DATED_INVOICE = "FUTURE_DATED_INVOICE"

    # ── Dönem / Analiz Periyodu (Python) ───────────────────────────────────
    PERIOD_OUT_OF_RANGE = "PERIOD_OUT_OF_RANGE"

    # ── Zamanaşımı (Python) ────────────────────────────────────────────────
    DEADLINE_CRITICAL = "DEADLINE_CRITICAL"
    DEADLINE_WARNING_IO = "DEADLINE_WARNING_IO"

    # ── Tevkifat Özel (Python) ─────────────────────────────────────────────
    TEVKIFAT_MISSING_DECL = "TEVKIFAT_MISSING_DECL"

    # ── LLM Genel Anomali (LLM — kanunname dışı) ──────────────────────────
    GENERAL_ANOMALY_HIGH = "GENERAL_ANOMALY_HIGH"
    GENERAL_ANOMALY_MEDIUM = "GENERAL_ANOMALY_MEDIUM"

    # ── Makro Bütünlük (dosya geneli; kilit) ──────────────────────────────
    DATA_INTEGRITY_FAILURE = "DATA_INTEGRITY_FAILURE"
    REFUND_LOGIC_IMPOSSIBLE = "REFUND_LOGIC_IMPOSSIBLE"

    # ── Resmi API (GİB / e-Fatura — finansman kilidi) ─────────────────────
    GIB_API_GCB_REJECTED = "GIB_API_GCB_REJECTED"
    GIB_API_EFATURA_REJECTED = "GIB_API_EFATURA_REJECTED"


class MatrixEntry(BaseModel):
    """Tek bir ceza kodu için tam tanım."""

    code: PenaltyCode
    per_unit_penalty: int
    max_penalty: int
    category: str
    description: str
    source: Literal["python", "llm", "hybrid"]
    is_mandatory_lock: bool = False
    is_missing_doc: bool = False


PENALTY_MATRIX: dict[PenaltyCode, MatrixEntry] = {
    # ── Zorunlu Belge Eksiklikleri ─────────────────────────────────────────
    PenaltyCode.GCB_MISSING: MatrixEntry(
        code=PenaltyCode.GCB_MISSING,
        per_unit_penalty=35,
        max_penalty=35,
        category="Zorunlu Belge",
        description="Gümrük Çıkış Beyannamesi (GÇB) eksik (KDVK m.11/1-a)",
        source="python",
        is_mandatory_lock=True,
        is_missing_doc=True,
    ),
    PenaltyCode.NO2_DECLARATION_MISSING: MatrixEntry(
        code=PenaltyCode.NO2_DECLARATION_MISSING,
        per_unit_penalty=35,
        max_penalty=35,
        category="Zorunlu Belge",
        description="2 No'lu KDV Beyannamesi eksik (01.03.2021 GUT güncellemesi)",
        source="python",
        is_mandatory_lock=True,
        is_missing_doc=True,
    ),
    PenaltyCode.NO2_DECLARATION_UNPAID: MatrixEntry(
        code=PenaltyCode.NO2_DECLARATION_UNPAID,
        per_unit_penalty=15,
        max_penalty=15,
        category="Zorunlu Belge",
        description="2 No'lu KDV Beyannamesi var ancak ödenmemiş",
        source="python",
        is_missing_doc=False,
    ),
    PenaltyCode.YMM_REPORT_MISSING: MatrixEntry(
        code=PenaltyCode.YMM_REPORT_MISSING,
        per_unit_penalty=35,
        max_penalty=35,
        category="Zorunlu Belge",
        description="YMM Tasdik Raporu eksik (tutar > 50.000 TL, 31.10.2024 tebliğ)",
        source="python",
        is_mandatory_lock=True,
        is_missing_doc=True,
    ),
    # ── Tedarikçi Riskleri ─────────────────────────────────────────────────
    PenaltyCode.SUPPLIER_SMIYB: MatrixEntry(
        code=PenaltyCode.SUPPLIER_SMIYB,
        per_unit_penalty=25,
        max_penalty=25,
        category="Tedarikçi Riski",
        description="Tedarikçi Özel Esaslar / SMİYB şüphesi kapsamında",
        source="python",
    ),
    PenaltyCode.SUPPLIER_HIGH_RISK: MatrixEntry(
        code=PenaltyCode.SUPPLIER_HIGH_RISK,
        per_unit_penalty=10,
        max_penalty=10,
        category="Tedarikçi Riski",
        description="Genel yüksek riskli tedarikçi",
        source="python",
    ),
    PenaltyCode.SUPPLIER_BLACKLIST_LOCK: MatrixEntry(
        code=PenaltyCode.SUPPLIER_BLACKLIST_LOCK,
        per_unit_penalty=25,
        max_penalty=25,
        category="Tedarikçi Riski",
        description="Tedarikçi Özel Esaslar kara listesinde kesinleşmiş kayıt",
        source="python",
        is_mandatory_lock=True,
    ),
    # ── Veri Kalitesi ──────────────────────────────────────────────────────
    PenaltyCode.DQ_INVALID_DATE: MatrixEntry(
        code=PenaltyCode.DQ_INVALID_DATE,
        per_unit_penalty=5,
        max_penalty=15,
        category="Veri Kalitesi",
        description="Geçersiz tarih formatı (YYYY-MM-DD dışı)",
        source="llm",
    ),
    PenaltyCode.DQ_ZERO_AMOUNT: MatrixEntry(
        code=PenaltyCode.DQ_ZERO_AMOUNT,
        per_unit_penalty=5,
        max_penalty=10,
        category="Veri Kalitesi",
        description="Sıfır veya negatif tutarlı fatura",
        source="llm",
    ),
    PenaltyCode.DQ_INVALID_VKN: MatrixEntry(
        code=PenaltyCode.DQ_INVALID_VKN,
        per_unit_penalty=3,
        max_penalty=9,
        category="Veri Kalitesi",
        description="VKN sayısal değil veya 10 hane değil",
        source="llm",
    ),
    # PenaltyCode.DQ_INVALID_VKN_CHECKSUM — RESERVED v3.3 (ENABLE_VKN_CHECKSUM_PENALTY)
    # MatrixEntry(per_unit_penalty=3, max_penalty=9, source="python", is_mandatory_lock=False)
    # ── Tutar / Hesap ──────────────────────────────────────────────────────
    PenaltyCode.AMOUNT_MISMATCH_GCB: MatrixEntry(
        code=PenaltyCode.AMOUNT_MISMATCH_GCB,
        per_unit_penalty=15,
        max_penalty=15,
        category="Tutar Tutarsızlığı",
        description="Fatura tutarı ile GÇB beyan tutarı arasında %5+ fark (KDVİRA GEK12-16)",
        source="hybrid",
    ),
    PenaltyCode.KDV_CALC_ERROR: MatrixEntry(
        code=PenaltyCode.KDV_CALC_ERROR,
        per_unit_penalty=15,
        max_penalty=15,
        category="Tutar Tutarsızlığı",
        description="KDV hesabı faturada yanlış (matrah × oran ≠ KDV tutarı)",
        source="llm",
    ),
    PenaltyCode.DUPLICATE_INVOICE: MatrixEntry(
        code=PenaltyCode.DUPLICATE_INVOICE,
        per_unit_penalty=10,
        max_penalty=10,
        category="Tutarsızlık",
        description="Aynı fatura numarası birden fazla kez geçiyor",
        source="llm",
    ),
    PenaltyCode.CHRONOLOGY_ERROR: MatrixEntry(
        code=PenaltyCode.CHRONOLOGY_ERROR,
        per_unit_penalty=8,
        max_penalty=8,
        category="Tutarsızlık",
        description="Fatura tarih kronolojisi bozuk (geriye dönük veya anlamsız sıra)",
        source="llm",
    ),
    PenaltyCode.KDV_RATE_MISMATCH_EXPORT: MatrixEntry(
        code=PenaltyCode.KDV_RATE_MISMATCH_EXPORT,
        per_unit_penalty=10,
        max_penalty=10,
        category="Tutar Tutarsızlığı",
        description="İhracat faturasında KDV oranı > 0 (ihracat KDV'den müstesna, KDVK m.11)",
        source="llm",
    ),
    PenaltyCode.SELF_INVOICE: MatrixEntry(
        code=PenaltyCode.SELF_INVOICE,
        per_unit_penalty=25,
        max_penalty=25,
        category="Sahte / Sahtecilik Riski",
        description="Alıcı ve satıcı VKN/TCKN aynı (kendine fatura, KDVK m.30/1)",
        source="llm",
    ),
    PenaltyCode.FUTURE_DATED_INVOICE: MatrixEntry(
        code=PenaltyCode.FUTURE_DATED_INVOICE,
        per_unit_penalty=15,
        max_penalty=15,
        category="Tutarsızlık",
        description="Gelecek tarihli fatura (VUK m.229, imkânsız tarih)",
        source="llm",
    ),
    # ── Dönem / Periyot ────────────────────────────────────────────────────
    PenaltyCode.PERIOD_OUT_OF_RANGE: MatrixEntry(
        code=PenaltyCode.PERIOD_OUT_OF_RANGE,
        per_unit_penalty=10,
        max_penalty=10,
        category="Dönem Uyumsuzluğu",
        description="Fatura tarihi analiz dönemi dışında",
        source="python",
    ),
    # ── Zamanaşımı ─────────────────────────────────────────────────────────
    PenaltyCode.DEADLINE_CRITICAL: MatrixEntry(
        code=PenaltyCode.DEADLINE_CRITICAL,
        per_unit_penalty=3,
        max_penalty=3,
        category="Zamanaşımı",
        description="Başvuru süresi dolmaya yakın (kalan < 180 gün)",
        source="python",
    ),
    PenaltyCode.DEADLINE_WARNING_IO: MatrixEntry(
        code=PenaltyCode.DEADLINE_WARNING_IO,
        per_unit_penalty=5,
        max_penalty=5,
        category="Zamanaşımı",
        description="İndirimli oran iadesi zamanaşımı uyarısı (kalan 180-365 gün)",
        source="python",
    ),
    # ── Tevkifat Özel ──────────────────────────────────────────────────────
    PenaltyCode.TEVKIFAT_MISSING_DECL: MatrixEntry(
        code=PenaltyCode.TEVKIFAT_MISSING_DECL,
        per_unit_penalty=5,
        max_penalty=5,
        category="Tevkifat",
        description="Tevkifat faturasında beyan bilgisi eksik",
        source="python",
    ),
    # ── LLM Genel ─────────────────────────────────────────────────────────
    PenaltyCode.GENERAL_ANOMALY_HIGH: MatrixEntry(
        code=PenaltyCode.GENERAL_ANOMALY_HIGH,
        per_unit_penalty=20,
        max_penalty=20,
        category="LLM Anomali",
        description="Kanunname dışı ciddi anomali (vergi kaçakçılığı riski vb.)",
        source="llm",
    ),
    PenaltyCode.GENERAL_ANOMALY_MEDIUM: MatrixEntry(
        code=PenaltyCode.GENERAL_ANOMALY_MEDIUM,
        per_unit_penalty=10,
        max_penalty=10,
        category="LLM Anomali",
        description="Kanunname dışı hafif anomali (şüpheli ama açıklanabilir)",
        source="llm",
    ),
    PenaltyCode.DATA_INTEGRITY_FAILURE: MatrixEntry(
        code=PenaltyCode.DATA_INTEGRITY_FAILURE,
        per_unit_penalty=40,
        max_penalty=40,
        category="Makro Bütünlük",
        description=(
            "Dosyanın toplam tutarı sıfır veya negatif — KDV iade mantığına kökten aykırı veri bütünlüğü"
        ),
        source="python",
        is_mandatory_lock=True,
    ),
    PenaltyCode.REFUND_LOGIC_IMPOSSIBLE: MatrixEntry(
        code=PenaltyCode.REFUND_LOGIC_IMPOSSIBLE,
        per_unit_penalty=50,
        max_penalty=50,
        category="Makro Bütünlük",
        description=(
            "İade talebi ile fatura seti arasında kökten mantık çöküşü (ör. çoğunlukla negatif/sıfır tutar veya "
            "ihracat beyanına aykırı yurtiçi profil / tevkifat beyanına rağmen sıfır tevkifat KDV)"
        ),
        source="hybrid",
        is_mandatory_lock=True,
    ),
    # ── Resmi API doğrulama ───────────────────────────────────────────────
    PenaltyCode.GIB_API_GCB_REJECTED: MatrixEntry(
        code=PenaltyCode.GIB_API_GCB_REJECTED,
        per_unit_penalty=50,
        max_penalty=50,
        category="Resmi API",
        description=(
            "GİB/Gümrük API doğrulaması başarısız: Beyanname resmi kayıtlarda bulunamadı."
        ),
        source="python",
        is_mandatory_lock=True,
    ),
    PenaltyCode.GIB_API_EFATURA_REJECTED: MatrixEntry(
        code=PenaltyCode.GIB_API_EFATURA_REJECTED,
        per_unit_penalty=40,
        max_penalty=40,
        category="Resmi API",
        description=(
            "GİB e-Fatura API doğrulaması başarısız: Fatura kayıt dışı veya iptal edilmiş."
        ),
        source="python",
        is_mandatory_lock=True,
    ),
}


# ── Türetilmiş setler ──────────────────────────────────────────────────────

MANDATORY_LOCK_CODES: frozenset[PenaltyCode] = frozenset(
    code for code, entry in PENALTY_MATRIX.items() if entry.is_mandatory_lock
)

LLM_ALLOWED_CODES: frozenset[PenaltyCode] = frozenset(
    code
    for code, entry in PENALTY_MATRIX.items()
    if entry.source in ("llm", "hybrid")
)

PYTHON_ONLY_CODES: frozenset[PenaltyCode] = frozenset(
    code for code, entry in PENALTY_MATRIX.items() if entry.source == "python"
)


def compute_code_penalty(code: PenaltyCode, count: int = 1) -> int:
    """
    Kanunname matrisinden `code` için toplam puan keser.

    - count < 1 → 1 olarak sınırlandırılır (LLM hatalı sayı verirse)
    - count > 100 → 100 olarak sınırlandırılır (halüsinasyon koruması)
    - Sonuç negatif tam sayı döner; tavan `max_penalty` ile korunur.
    """
    entry = PENALTY_MATRIX.get(code)
    if entry is None:
        return 0
    safe_count = max(1, min(int(count), 100))
    raw = safe_count * entry.per_unit_penalty
    capped = min(raw, entry.max_penalty)
    return -capped


def get_refund_focus_codes(refund_type: str) -> list[PenaltyCode]:
    """İade türüne göre LLM'in öncelikle bakması gereken kodlar."""
    base = [PenaltyCode.DUPLICATE_INVOICE, PenaltyCode.CHRONOLOGY_ERROR]
    if refund_type == "ihracat":
        return [
            PenaltyCode.REFUND_LOGIC_IMPOSSIBLE,
            PenaltyCode.AMOUNT_MISMATCH_GCB,
            PenaltyCode.KDV_RATE_MISMATCH_EXPORT,
            PenaltyCode.FUTURE_DATED_INVOICE,
            PenaltyCode.SELF_INVOICE,
            *base,
        ]
    if refund_type == "tevkifat":
        return [
            PenaltyCode.REFUND_LOGIC_IMPOSSIBLE,
            PenaltyCode.KDV_CALC_ERROR,
            PenaltyCode.DUPLICATE_INVOICE,
            PenaltyCode.SELF_INVOICE,
            *base,
        ]
    if refund_type == "indirimli_oran":
        return [
            PenaltyCode.KDV_CALC_ERROR,
            PenaltyCode.PERIOD_OUT_OF_RANGE,
            *base,
        ]
    return base
