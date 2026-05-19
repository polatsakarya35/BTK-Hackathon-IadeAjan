"""LangGraph Analyzer node — hibrit (Python + LLM Kanunname) risk ve belge analizi.

Path B Mimarisi:
    1. Python Mevzuat Katmanı — belge eksiklikleri, tedarikçi, zamanaşımı, periyot
       (her zaman çalışır, API bağımlılığı yok)
    2. LLM Denetçi — PENALTY_MATRIX'ten kanunname kodu seçer; puan Python keser
    3. Fallback — LLM down ise DQ kuralları Python tabanlı devreye girer
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any

from app.core.state import IadeAjanState
from app.schemas.models import ClarificationQuestion, LLMAnalysisResult, MissingDoc, RiskItem
from app.schemas.penalty_codes import (
    PENALTY_MATRIX,
    PenaltyCode,
    compute_code_penalty,
    get_refund_focus_codes,
    LLM_ALLOWED_CODES,
    PYTHON_ONLY_CODES,
)
from app.services import shadow_learning

AGENT_NAME = "AnalyzerAgent"
NEXT_AGENT_IF_CLARIFICATION = "ClarificationAgent"
NEXT_AGENT_IF_NO_CLARIFICATION = "DecisionAgent"

DOC_GUMRUK_BEYANNAME = "Gümrük beyannamesi"
DOC_2NO_KDV_BEYANNAME = "2 No'lu KDV Beyannamesi"
DOC_YMM_TASDIK_RAPORU = "YMM Tasdik Raporu"

YMM_MANDATORY_THRESHOLD = 50_000
AMOUNT_MISMATCH_THRESHOLD = 0.05


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcı Loglama
# ─────────────────────────────────────────────────────────────────────────────

def _log(message: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"[{ts}] [{AGENT_NAME}] {message}"


# ─────────────────────────────────────────────────────────────────────────────
# Ceza Yardımcıları (tek noktalı üretim)
# ─────────────────────────────────────────────────────────────────────────────

def _severity_for_code(code: PenaltyCode) -> str:
    """Kanunname koduna göre Türkçe severity döner."""
    entry = PENALTY_MATRIX.get(code)
    if entry is None:
        return "orta"
    cat = entry.category
    if cat in ("Sahte / Sahtecilik Riski", "Tedarikçi Riski"):
        return "yüksek"
    if entry.per_unit_penalty >= 20:
        return "yüksek"
    if entry.per_unit_penalty >= 10:
        return "orta"
    return "düşük"


def add_penalty(
    risk_items: list[dict[str, Any]],
    seen_risk_keys: set[tuple[str, str]],
    code: PenaltyCode,
    reason: str,
    suggested_action: str = "",
    count: int = 1,
    invoice_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str = "python",
    dedupe_suffix: str = "",
) -> bool:
    """
    Kanunname matrisinden puan kesip risk_items'a ekler.
    Aynı (code, suffix) çifti iki kez eklenmez.
    Döner: eklendi mi?
    """
    dedupe_key = (code.value, dedupe_suffix)
    if dedupe_key in seen_risk_keys:
        return False
    seen_risk_keys.add(dedupe_key)

    score_impact = compute_code_penalty(code, count)
    if score_impact == 0:
        return False

    entry = PENALTY_MATRIX[code]
    title = f"[{code.value}] {entry.description[:55]}"

    risk = RiskItem(
        title=title,
        severity=_severity_for_code(code),
        reason=reason,
        score_impact=score_impact,
        suggested_action=suggested_action or None,
        code=code,
        count=count,
        invoice_ids=invoice_ids or [],
        metadata=metadata or {},
        source=source,  # type: ignore[arg-type]
    )
    # mode='json' → enum'lar string değer olarak serialize edilir (LangGraph uyumluluğu)
    risk_items.append(risk.model_dump(mode="json"))
    return True


def add_missing_doc_penalty(
    missing_docs: list[dict[str, Any]],
    seen_doc_names: set[str],
    doc_name: str,
    code: PenaltyCode,
    reason: str,
) -> bool:
    """Eksik belge kaydı ekler; aynı belge iki kez eklenemez."""
    if doc_name in seen_doc_names:
        return False
    seen_doc_names.add(doc_name)
    score_impact = compute_code_penalty(code)
    if score_impact == 0:
        return False
    doc = MissingDoc(
        doc_name=doc_name,
        reason=reason,
        score_impact=score_impact,
        code=code,
    )
    missing_docs.append(doc.model_dump(mode="json"))
    return True


# ── Makro bütünlük (v3.2) ───────────────────────────────────────────────────

MACRO_PURGE_CODES: tuple[PenaltyCode, ...] = (
    PenaltyCode.DQ_ZERO_AMOUNT,
    PenaltyCode.DQ_INVALID_DATE,
)


def _risk_item_code_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.split(".")[-1] if "." in raw else raw
    return str(raw)


def _purge_risk_codes(
    risk_items: list[dict[str, Any]],
    seen_risk_keys: set[tuple[str, str]],
    codes: list[PenaltyCode] | tuple[PenaltyCode, ...],
) -> None:
    """Çifte ceza önleme: belirli kanunname kodlarına ait risk maddelerini siler."""
    purge_vals = {c.value for c in codes}
    risk_items[:] = [
        r for r in risk_items
        if _risk_item_code_str(r.get("code")) not in purge_vals
    ]
    seen_risk_keys -= {k for k in seen_risk_keys if k[0] in purge_vals}


def _apply_macro_integrity_rules(
    normalized_invoices: list[dict[str, Any]],
    refund_type: str,
    risk_items: list[dict[str, Any]],
    seen_risk_keys: set[tuple[str, str]],
    logs: list[str],
    session_id: str,
) -> bool:
    """
    Dosya geneli makro kontrol. Döner: True ise LLM atlanır (makro kilit).

    1) REFUND_LOGIC_IMPOSSIBLE (Python): negatif/sıfır tutarlı fatura oranı >= %75
       ve iade türü deterministik (ihracat/tevkifat/indirimli_oran).

    2) DATA_INTEGRITY_FAILURE: toplam tutar <= 0 ve yukarıdaki Python kolu tetiklenmediyse.
    """
    if not normalized_invoices:
        return False

    refund_eligible = {"ihracat", "tevkifat", "indirimli_oran"}

    amounts: list[float] = []
    for inv in normalized_invoices:
        try:
            amounts.append(float(inv.get("amount") or 0))
        except (TypeError, ValueError):
            amounts.append(0.0)

    total = len(amounts)
    neg_zero = sum(1 for a in amounts if a <= 0)
    ratio = neg_zero / total if total else 0.0
    total_sum = sum(amounts)

    if ratio >= 0.75 and refund_type in refund_eligible:
        _purge_risk_codes(risk_items, seen_risk_keys, MACRO_PURGE_CODES)
        reason = (
            f"{neg_zero}/{total} faturada tutar sıfır veya negatif "
            f"({ratio * 100:.0f}% ≥ %75 eşiği). KDV iade talebine uygun net tahakkuk profili yok."
        )
        add_penalty(
            risk_items,
            seen_risk_keys,
            PenaltyCode.REFUND_LOGIC_IMPOSSIBLE,
            reason=reason,
            suggested_action=(
                "Faturaları kontrol edin; negatif/iade faturası ile ihracat/tevkifat iadesi "
                "aynı sette kökten uyumsuz olabilir."
            ),
            count=1,
            metadata={
                "macro_trigger": "python_ge_75pct_nonpositive",
                "neg_zero_count": neg_zero,
                "macro_flag": True,
            },
            source="python",
        )
        logs.append(_log(f"[Makro] REFUND_LOGIC_IMPOSSIBLE (Python oran): {reason[:90]}"))
        return True

    if total_sum <= 0.0:
        _purge_risk_codes(risk_items, seen_risk_keys, MACRO_PURGE_CODES)
        tsum_str = f"{total_sum:,.2f}".rstrip("0").rstrip(".")
        reason = (
            f"Toplam fatura tutarı {tsum_str} TL (≤0). Dosya bütünlüğü KDV iade mantığına aykırı."
        )
        add_penalty(
            risk_items,
            seen_risk_keys,
            PenaltyCode.DATA_INTEGRITY_FAILURE,
            reason=reason,
            suggested_action="Tutarları düzeltin veya geçerli pozitif işlem içeren dosya yükleyin.",
            count=1,
            metadata={"macro_trigger": "python_total_sum_nonpositive", "macro_flag": True},
            source="python",
        )
        logs.append(_log(f"[Makro] DATA_INTEGRITY_FAILURE: toplam tutar ≤0"))
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Python Mevzuat Kuralları (her durumda çalışır)
# ─────────────────────────────────────────────────────────────────────────────

def _inventory_entry_present(doc: dict[str, Any]) -> bool:
    """Envanter kaydı GÇB/YMM için 'elde var' sayılır mı."""
    import os
    from app.services.verification.inventory_trust_policy import is_counted_for_export

    status = str(doc.get("status", "")).strip().lower()
    if status == "eksik":
        return False
    strict = os.getenv("STRICT_INVENTORY_TRUST", "true").lower() in ("1", "true")
    if strict:
        return is_counted_for_export(doc, strict=True)
    return True


def _invoice_labeled_export(inv: dict[str, Any]) -> bool:
    return bool(inv.get("is_export") or inv.get("type") == "ihracat")


def _invoice_zero_kdv_export(inv: dict[str, Any]) -> bool:
    """İhracat faturasında beklenen KDV: %0 veya KDV tutarı sıfır."""
    if not _invoice_labeled_export(inv):
        return False
    kdv_rate = int(inv.get("kdv_rate") if inv.get("kdv_rate") is not None else 20)
    kdv_amount = float(inv.get("kdv_amount") or 0)
    return kdv_rate == 0 or kdv_amount == 0


def _apply_export_rules(
    document_inventory: list[dict[str, Any]],
    normalized_invoices: list[dict[str, Any]],
    missing_docs: list[dict[str, Any]],
    process_warnings: list[str],
    seen_doc_names: set[str],
) -> None:
    """İhracat: GÇB zorunluluğu (KDVK m.11/1-a)."""
    for doc in document_inventory:
        if (
            doc.get("type") == "gumruk_beyannamesi"
            and doc.get("status") == "eksik"
            and doc.get("required") is True
        ):
            add_missing_doc_penalty(
                missing_docs, seen_doc_names,
                DOC_GUMRUK_BEYANNAME,
                PenaltyCode.GCB_MISSING,
                "İhracat iadesi için GÇB zorunludur (GİB mevzuatı). "
                "Eksik GÇB iade talebini reddedilebilir kılar.",
            )
            return

    labeled_exports = [inv for inv in normalized_invoices if _invoice_labeled_export(inv)]
    strong_exports = [inv for inv in labeled_exports if _invoice_zero_kdv_export(inv)]
    weak_exports = [inv for inv in labeled_exports if inv not in strong_exports]

    if weak_exports:
        process_warnings.append(
            f"{len(weak_exports)} fatura Excel'de ihracat işaretli ancak KDV oranı/tutarı "
            "%0 değil; ihracat iadesi ve GÇB eşleşmesi yalnızca etikete dayanmamalıdır — "
            "manuel kontrol gerekir."
        )

    has_ihracat_invoice = len(strong_exports) > 0
    gumruk_confirmed = any(
        d.get("type") == "gumruk_beyannamesi" and _inventory_entry_present(d)
        for d in document_inventory
    )
    if not gumruk_confirmed and has_ihracat_invoice:
        add_missing_doc_penalty(
            missing_docs, seen_doc_names,
            DOC_GUMRUK_BEYANNAME,
            PenaltyCode.GCB_MISSING,
            "Gümrük çıkış beyannamesi (GÇB) envanterde doğrulanamadı. "
            "İhracat KDV iadesi için GÇB zorunludur; lütfen yükleyin.",
        )
        process_warnings.append(
            "Belge envanteri boş veya gümrük beyannamesi içermiyor; "
            "ihracat faturaları için kullanıcı doğrulaması gerekiyor."
        )


def _apply_amount_mismatch_rules(
    normalized_invoices: list[dict[str, Any]],
    document_inventory: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
    seen_risk_keys: set[tuple[str, str]],
) -> None:
    """KDVİRA GEK12-16: Fatura ↔ GÇB tutar uyuşmazlığı (>%5)."""
    gcb_amount_by_no: dict[str, float] = {
        d.get("declaration_no", ""): float(d.get("amount") or 0)
        for d in document_inventory
        if d.get("type") == "gumruk_beyannamesi"
        and _inventory_entry_present(d)
        and d.get("declaration_no")
        and d.get("amount")
    }

    mismatched: list[tuple[str, float, float]] = []
    for inv in normalized_invoices:
        if not (inv.get("is_export") or inv.get("type") == "ihracat"):
            continue
        inv_amount = float(inv.get("amount") or 0)
        if inv_amount <= 0:
            continue
        gcb_amount = float(inv.get("customs_declaration_amount") or 0)
        if not gcb_amount:
            decl_no = inv.get("customs_declaration_no") or ""
            gcb_amount = gcb_amount_by_no.get(decl_no, 0.0)
        if gcb_amount <= 0:
            continue
        diff_ratio = abs(inv_amount - gcb_amount) / gcb_amount
        if diff_ratio > AMOUNT_MISMATCH_THRESHOLD:
            mismatched.append((str(inv.get("id", "?")), inv_amount, gcb_amount))

    if not mismatched:
        return

    detail = "; ".join(
        f"{inv_id} ({inv_amt:,.0f}₺ ↔ {gcb_amt:,.0f}₺)"
        for inv_id, inv_amt, gcb_amt in mismatched[:3]
    )
    add_penalty(
        risk_items, seen_risk_keys,
        PenaltyCode.AMOUNT_MISMATCH_GCB,
        reason=(
            f"{len(mismatched)} ihracat faturasında GÇB tutarı ile %"
            f"{int(AMOUNT_MISMATCH_THRESHOLD * 100)} üstü fark (KDVİRA GEK12-16). "
            f"{detail}"
        ),
        suggested_action=(
            "Fatura ve GÇB tutarlarını karşılaştırın; düzeltme beyannamesi gerekebilir."
        ),
        invoice_ids=[m[0] for m in mismatched],
        metadata={"mismatched_count": len(mismatched)},
        source="hybrid",
    )


def _apply_ymm_rules(
    company_profile: dict[str, Any],
    document_inventory: list[dict[str, Any]],
    missing_docs: list[dict[str, Any]],
    seen_doc_names: set[str],
) -> None:
    """YMM Tasdik Raporu zorunluluğu (31.10.2024 KDV Uygulama Tebliği)."""
    estimated_amount = float(company_profile.get("estimated_refund_amount") or 0)
    if estimated_amount < YMM_MANDATORY_THRESHOLD:
        return
    ymm_confirmed = any(
        d.get("type") in ("ymm_tasdik_raporu", "ymm_raporu")
        and _inventory_entry_present(d)
        for d in document_inventory
    )
    if not ymm_confirmed:
        add_missing_doc_penalty(
            missing_docs, seen_doc_names,
            DOC_YMM_TASDIK_RAPORU,
            PenaltyCode.YMM_REPORT_MISSING,
            f"Tahmini iade tutarı {estimated_amount:,.0f} TL, "
            f"{YMM_MANDATORY_THRESHOLD:,} TL eşiğini aşıyor. "
            "31.10.2024 KDV Uygulama Tebliği: YMM KDV İadesi Tasdik Raporu zorunludur.",
        )


def _apply_tevkifat_rules(
    document_inventory: list[dict[str, Any]],
    normalized_invoices: list[dict[str, Any]],
    missing_docs: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
    seen_doc_names: set[str],
    seen_risk_keys: set[tuple[str, str]],
) -> None:
    """Tevkifat: 2 No'lu KDV Beyannamesi (01.03.2021 GUT)."""
    two_no_found = False
    for doc in document_inventory:
        if doc.get("type") != "2no_kdv_beyannamesi":
            continue
        two_no_found = True
        if doc.get("status") == "eksik" and doc.get("required") is True:
            add_missing_doc_penalty(
                missing_docs, seen_doc_names,
                DOC_2NO_KDV_BEYANNAME,
                PenaltyCode.NO2_DECLARATION_MISSING,
                "Tevkifat iadesi için 2 No'lu KDV Beyannamesi zorunludur "
                "(01.03.2021 GUT güncellemesi). Eksik beyanname iade talebini reddedilebilir kılar.",
            )
            break
        if not doc.get("is_paid", False):
            add_penalty(
                risk_items, seen_risk_keys,
                PenaltyCode.NO2_DECLARATION_UNPAID,
                reason=(
                    "2 No'lu KDV beyannamesinin tahakkuku yapılmış ancak ödemesi "
                    "gerçekleşmemiş. GİB, iade öncesi ödemeyi zorunlu tutmaktadır."
                ),
                suggested_action=(
                    "2 No'lu KDV Beyannamesi borcunu ödeyin ve ödeme dekontunu ekleyin."
                ),
                source="python",
            )
            break

    if not two_no_found:
        add_missing_doc_penalty(
            missing_docs, seen_doc_names,
            DOC_2NO_KDV_BEYANNAME,
            PenaltyCode.NO2_DECLARATION_MISSING,
            "Belge envanterinde 2 No'lu KDV Beyannamesi bulunamadı. "
            "Tevkifat iadesi için zorunludur.",
        )

    count_missing_tevkifat = sum(
        1
        for inv in normalized_invoices
        if inv.get("is_tevkifat") and not inv.get("has_tevkifat_declaration", False)
    )
    if count_missing_tevkifat > 0:
        add_penalty(
            risk_items, seen_risk_keys,
            PenaltyCode.TEVKIFAT_MISSING_DECL,
            reason=f"{count_missing_tevkifat} tevkifat faturasında beyan bilgisi eksik.",
            suggested_action="İlgili faturaların tevkifat beyan belgelerini temin edin.",
            count=count_missing_tevkifat,
            source="python",
        )


def _apply_supplier_risk_rules(
    normalized_suppliers: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
    seen_risk_keys: set[tuple[str, str]],
) -> tuple[int, bool]:
    """Tedarikçi risk kuralları. Dönüş: (yüksek_risk_sayısı, force_clarification)."""
    seen_supplier_ids: set[str] = set()
    high_risk_count = 0
    force_clarification = False

    for supplier in normalized_suppliers:
        supplier_id = str(supplier.get("id", ""))
        risk_level = supplier.get("risk_level", "")
        if risk_level not in ("kritik", "yüksek"):
            continue
        high_risk_count += 1
        if supplier_id in seen_supplier_ids:
            continue
        seen_supplier_ids.add(supplier_id)

        risk_note = supplier.get("risk_note", "")
        supplier_name = supplier.get("name", supplier_id)

        if risk_level == "kritik":
            force_clarification = True
            is_blacklist = bool(supplier.get("is_blacklisted", False))
            code = PenaltyCode.SUPPLIER_BLACKLIST_LOCK if is_blacklist else PenaltyCode.SUPPLIER_SMIYB
            reason = (
                f"Tedarikçi '{supplier_name}' "
                + ("Özel Esaslar kara listesinde kesinleşmiş kayıt." if is_blacklist
                   else "Özel Esaslar listesinde veya SMİYB şüphesi kapsamında.")
            )
            if risk_note:
                reason = f"{reason} {risk_note}"
            add_penalty(
                risk_items, seen_risk_keys,
                code,
                reason=reason.strip(),
                suggested_action=(
                    "GİB Özel Esaslar sorgulaması yapın; tedarikçiden "
                    "iade hakkı belgesi veya YMM teyit yazısı talep edin."
                ),
                source="python",
                dedupe_suffix=supplier_id,
            )
        else:
            reason = f"Tedarikçi '{supplier_name}' yüksek riskli."
            if risk_note:
                reason = f"{reason} {risk_note}"
            add_penalty(
                risk_items, seen_risk_keys,
                PenaltyCode.SUPPLIER_HIGH_RISK,
                reason=reason.strip(),
                suggested_action="Alternatif uyum belgesi veya teyit evrakı talep edin.",
                source="python",
                dedupe_suffix=supplier_id,
            )

    return high_risk_count, force_clarification


def _apply_deadline_rules(
    company_profile: dict[str, Any],
    risk_items: list[dict[str, Any]],
    process_warnings: list[str],
    score_inputs: dict[str, Any],
    seen_risk_keys: set[tuple[str, str]],
    refund_type: str = "belirsiz",
) -> None:
    """Zamanaşımı kuralları — KDV Kanunu md.29/2 ve md.32."""
    analysis_period = company_profile.get("analysis_period", {})
    period_end_str = analysis_period.get("end", "")
    if not period_end_str:
        return
    try:
        period_end = date.fromisoformat(period_end_str)
        if refund_type == "indirimli_oran":
            deadline = date(period_end.year + 1, 12, 31)
            deadline_note = "indirimli oran — izleyen yılın Kasım/Aralık dönemi"
        else:
            deadline = date(period_end.year + 2, 12, 31)
            deadline_note = "tam istisna/tevkifat — izleyen 2. yılın sonu"

        today_utc = datetime.now(timezone.utc).date()
        days_remaining = (deadline - today_utc).days
        score_inputs["days_remaining_to_deadline"] = days_remaining
        process_warnings.append(
            f"Zamanaşımı kontrolü ({deadline_note}): son başvuru tarihi "
            f"{deadline.isoformat()} (kalan gün: {days_remaining})."
        )

        if days_remaining < 180:
            add_penalty(
                risk_items, seen_risk_keys,
                PenaltyCode.DEADLINE_CRITICAL,
                reason=(
                    f"Başvuru süresi dolmaya yakın "
                    f"(kalan: {days_remaining} gün, son tarih: {deadline.isoformat()})."
                ),
                suggested_action="İade başvurusunu en kısa sürede tamamlayın.",
                metadata={"days_remaining": days_remaining},
                source="python",
            )

        if refund_type == "indirimli_oran" and 180 <= days_remaining < 365:
            add_penalty(
                risk_items, seen_risk_keys,
                PenaltyCode.DEADLINE_WARNING_IO,
                reason=(
                    f"İndirimli oran iadelerinde zamanaşımı süresi daha kısa. "
                    f"Kalan: {days_remaining} gün."
                ),
                suggested_action=(
                    "İndirimli oran iadesi için izleyen yılın Kasım/Aralık "
                    "beyannamesine kadar başvurunuzu tamamlayın."
                ),
                dedupe_suffix="io_ek",
                source="python",
            )

    except ValueError:
        process_warnings.append(
            f"Zamanaşımı hesaplanamadı: analysis_period.end ayrıştırılamadı ({period_end_str!r})."
        )


def _apply_period_rules(
    normalized_invoices: list[dict[str, Any]],
    company_profile: dict[str, Any],
    risk_items: list[dict[str, Any]],
    process_warnings: list[str],
    seen_risk_keys: set[tuple[str, str]],
) -> None:
    """Fatura tarihleri analiz dönemi dışında mı? (PERIOD_OUT_OF_RANGE)"""
    analysis_period = company_profile.get("analysis_period", {})
    start_str = analysis_period.get("start", "")
    end_str = analysis_period.get("end", "")
    if not start_str or not end_str:
        return
    try:
        period_start = date.fromisoformat(start_str)
        period_end = date.fromisoformat(end_str)
    except ValueError:
        return

    out_of_range: list[str] = []
    for inv in normalized_invoices:
        inv_date_str = inv.get("date", "")
        if not inv_date_str:
            continue
        try:
            inv_date = date.fromisoformat(str(inv_date_str))
        except ValueError:
            continue
        if inv_date < period_start or inv_date > period_end:
            out_of_range.append(str(inv.get("id", "?")))

    if out_of_range:
        add_penalty(
            risk_items, seen_risk_keys,
            PenaltyCode.PERIOD_OUT_OF_RANGE,
            reason=(
                f"{len(out_of_range)} faturanın tarihi analiz dönemi dışında "
                f"({start_str} – {end_str}): {out_of_range[:3]}"
            ),
            suggested_action="Dönem dışı faturaları inceleyin veya analiz dönemini güncelleyin.",
            invoice_ids=out_of_range,
            count=len(out_of_range),
            metadata={"period_start": start_str, "period_end": end_str},
            source="python",
        )
        process_warnings.append(
            f"Dönem kontrolü: {len(out_of_range)} fatura analiz dönemi dışında."
        )


def _apply_efatura_api_rules(
    state: dict[str, Any],
    normalized_invoices: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
    process_warnings: list[str],
    seen_risk_keys: set[tuple[str, str]],
    company_profile: dict[str, Any],
) -> int:
    """Katman 1 — GİB e-Fatura API doğrulaması (satır bazlı)."""
    from app.services.integrations.gib_api_client import verify_efatura_with_gib

    issuer_vkn = str(
        company_profile.get("tax_number")
        or state.get("tax_number")
        or ""
    ).strip()
    failed_ids: list[str] = []

    for inv in normalized_invoices:
        invoice_uuid = str(inv.get("id") or "").strip()
        if not invoice_uuid:
            continue
        issue_date = str(inv.get("date") or "") or None
        try:
            amount = float(inv.get("amount")) if inv.get("amount") is not None else None
        except (TypeError, ValueError):
            amount = None

        result = verify_efatura_with_gib(
            issuer_vkn,
            invoice_uuid,
            issue_date,
            amount,
        )
        if not result.verified:
            failed_ids.append(invoice_uuid)

    if not failed_ids:
        return 0

    add_penalty(
        risk_items,
        seen_risk_keys,
        PenaltyCode.GIB_API_EFATURA_REJECTED,
        reason=(
            f"{len(failed_ids)} fatura GİB e-Fatura API doğrulamasından geçemedi "
            f"(kayıt dışı veya iptal): {failed_ids[:5]}"
        ),
        suggested_action=(
            "e-Fatura kayıtlarını GİB üzerinden doğrulayın; iptal veya kayıt dışı "
            "faturaları dosyadan çıkarın."
        ),
        count=len(failed_ids),
        invoice_ids=failed_ids[:20],
        source="python",
    )
    process_warnings.append(
        f"{len(failed_ids)} fatura e-Fatura API doğrulamasından geçemedi."
    )
    return len(failed_ids)


def _ledger_has_gcb_api_rejection(state: dict[str, Any]) -> bool:
    """proof_ledger veya verification_summary üzerinden GÇB API red bayrağı."""
    summary = state.get("verification_summary") or {}
    if isinstance(summary, dict) and summary.get("gib_api_gcb_rejected"):
        return True
    flags = state.get("gib_api_flags") or {}
    if isinstance(flags, dict) and flags.get("gcb_rejected"):
        return True
    for raw in state.get("proof_ledger") or []:
        if isinstance(raw, dict):
            codes = list(raw.get("failed_check_codes") or [])
            for chk in raw.get("failed_checks") or []:
                if isinstance(chk, dict) and chk.get("code"):
                    codes.append(chk["code"])
                elif hasattr(chk, "code"):
                    codes.append(chk.code)
            if "GIB_API_GCB_REJECTED" in codes:
                return True
        else:
            codes = [c.code for c in getattr(raw, "failed_checks", [])]
            if "GIB_API_GCB_REJECTED" in codes:
                return True
    return False


def _apply_gcb_api_penalty_from_ledger(
    state: dict[str, Any],
    risk_items: list[dict[str, Any]],
    seen_risk_keys: set[tuple[str, str]],
) -> bool:
    """Kanıt defterinde GÇB API reddi varsa zorunlu kilit cezasını uygular."""
    if not _ledger_has_gcb_api_rejection(state):
        return False
    add_penalty(
        risk_items,
        seen_risk_keys,
        PenaltyCode.GIB_API_GCB_REJECTED,
        reason=(
            "GİB/Gümrük API doğrulaması başarısız: Beyanname resmi kayıtlarda bulunamadı."
        ),
        suggested_action=(
            "Gümrük çıkış beyannamesinin GİB kayıtlarında doğrulanmış bir kopyasını yükleyin."
        ),
        count=1,
        source="python",
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# DQ Kuralları (yalnızca LLM fallback modunda çalışır)
# ─────────────────────────────────────────────────────────────────────────────

def _is_valid_date(date_str: str) -> bool:
    try:
        date.fromisoformat(str(date_str))
        return True
    except (ValueError, TypeError):
        return False


def _apply_data_quality_rules_fallback(
    normalized_invoices: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
    process_warnings: list[str],
    seen_risk_keys: set[tuple[str, str]],
) -> None:
    """
    DQ kuralları — YALNIZCA LLM başarısızsa (fallback) çalışır.
    LLM başarılıysa DQ kodlarını LLM üretir; çifte ceza önlenir.
    """
    bad_date_ids = [
        str(inv.get("id", "?"))
        for inv in normalized_invoices
        if inv.get("date") and not _is_valid_date(inv.get("date", ""))
    ]
    if bad_date_ids:
        add_penalty(
            risk_items, seen_risk_keys,
            PenaltyCode.DQ_INVALID_DATE,
            reason=(
                f"{len(bad_date_ids)} faturada geçersiz tarih formatı: {bad_date_ids[:3]}"
            ),
            suggested_action="Fatura tarihlerini YYYY-MM-DD formatında düzeltin.",
            count=len(bad_date_ids),
            invoice_ids=bad_date_ids,
            source="fallback",
        )
        process_warnings.append(
            f"Fallback DQ: {len(bad_date_ids)} faturada tarih ayrıştırılamadı"
        )

    zero_amount_ids = [
        str(inv.get("id", "?"))
        for inv in normalized_invoices
        if (inv.get("amount") or 0) <= 0
    ]
    if zero_amount_ids:
        add_penalty(
            risk_items, seen_risk_keys,
            PenaltyCode.DQ_ZERO_AMOUNT,
            reason=(
                f"{len(zero_amount_ids)} faturada sıfır veya negatif tutar: {zero_amount_ids[:3]}"
            ),
            suggested_action="Tutar alanlarını kontrol edin.",
            count=len(zero_amount_ids),
            invoice_ids=zero_amount_ids,
            source="fallback",
        )
        process_warnings.append(
            f"Fallback DQ: {len(zero_amount_ids)} faturada sıfır/negatif tutar"
        )

    invalid_vkn_ids = [
        str(inv.get("id", "?"))
        for inv in normalized_invoices
        if inv.get("supplier_id")
        and not str(inv.get("supplier_id", "")).replace(".", "").isdigit()
    ]
    if invalid_vkn_ids:
        add_penalty(
            risk_items, seen_risk_keys,
            PenaltyCode.DQ_INVALID_VKN,
            reason=(
                f"{len(invalid_vkn_ids)} faturada VKN sayısal değil: {invalid_vkn_ids[:3]}"
            ),
            suggested_action="VKN alanlarının 10 haneli sayısal olduğunu doğrulayın.",
            count=len(invalid_vkn_ids),
            invoice_ids=invalid_vkn_ids,
            source="fallback",
        )
        process_warnings.append(
            f"Fallback DQ: {len(invalid_vkn_ids)} faturada VKN formatı geçersiz"
        )


# ─────────────────────────────────────────────────────────────────────────────
# LLM Denetçi — Dinamik Prompt + Validation Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _build_llm_system_prompt(refund_type: str) -> str:
    """PENALTY_MATRIX'ten refund_type'a göre dinamik prompt üretir."""

    llm_codes_lines: list[str] = []
    for code, entry in PENALTY_MATRIX.items():
        if entry.source in ("llm", "hybrid"):
            llm_codes_lines.append(
                f"  - {code.value}: {entry.description} "
                f"(ceza: {entry.per_unit_penalty}/adet, tavan: {entry.max_penalty})"
            )
    llm_codes_block = "\n".join(llm_codes_lines)

    python_codes_str = ", ".join(code.value for code in PYTHON_ONLY_CODES)

    focus_codes = get_refund_focus_codes(refund_type)
    focus_lines = "\n".join(
        f"  - {code.value}: {PENALTY_MATRIX[code].description}"
        for code in focus_codes
        if code in PENALTY_MATRIX
    )

    today = datetime.now(timezone.utc).date().isoformat()

    return f"""Sen Türkiye KDV mevzuatını bilen titiz bir vergi müfettişisin.
Bugünün tarihi: {today}
İade türü: {refund_type}

## GÖREV
Verilen KDV iade dosyasını inceleyip YALNIZCA aşağıdaki Ceza Kanunnamesi kodlarını kullanarak
anormallikler tespit et.

## KULLANABİLECEĞİN KODLAR (LLM ve hibrit):
{llm_codes_block}

## ÖNCE BAKACAKLARIN ({refund_type} iadesi için):
{focus_lines}

## MAKRO VİZYON (dosya geneli — tekil satırdan öte)
- Sadece fatura satırlarına değil dosyanın **genel hikayesine** bak: tedarikçi çeşitliliği,
  tarih kronolojisi, fatura numarası düzeni, genel KDV oranı tutarlılığı.
- **REFUND_LOGIC_IMPOSSIBLE** kodunu yalnızca münferit (tekil) fatura hatalarında DEĞİL,
  KDV iade talebinin **doğasını kökten geçersiz kılan dosya bazlı büyük mantık çöküşlerinde**
  kullan. Örnekler (veride açıksa):
  - **ihracat** iadesi beyanına rağmen faturaların tamamı veya ezici çoğunluğu **standart 10 haneli
    yurtiçi VKN** profilinde (ihracat dış satıcı beklenir).
  - **tevkifat** iadesi beyanına rağmen tevkifat kapsamındaki faturalarda **tevkifat KDV tutarları
    hep sıfır** (tevki yok sayılır).
- Kanunnamede **birebir kodu olmayan** ama iade mantığına tamamen ters bir **makro pattern** görürsen:
  yeni kod uydurma — **GENERAL_ANOMALY_HIGH** veya **GENERAL_ANOMALY_MEDIUM** seç,
  `evidence`'e Türkçe yaz ve **`macro_flag: true`** ver.

Not: **DATA_INTEGRITY_FAILURE** ve Python tetikli **REFUND_LOGIC_IMPOSSIBLE** dosyalara
sen erişemezsin; bunlar sistem tarafından kesilir.

## KESINLIKLE RAPORLAMA (Python zaten işledi):
Şu kodlar sistem tarafından deterministik olarak üretiliyor; sen bunları tespit etme:
  {python_codes_str}

## ÇIKTI KURALLARI
1. SADECE JSON object döndür: {{"summary": "...", "penalties": [...]}}
2. summary: dosya hakkında max 2 cümle Türkçe yorum
3. Her ceza için:
   - code: Kanunnamedeki tam kod adı (yukarıdaki listeden)
   - evidence: max 200 karakter Türkçe kanıt açıklaması
   - invoice_ids: etkilenen fatura numaraları (varsa, liste)
   - count: etkilenen kayıt adedi (DQ_* kodları için zorunlu; varsayılan 1)
   - macro_flag: boolean (varsayılan false). Dosya geneline yayılan şüpheli pattern ise true.
4. Aynı kodu iki kez listeleme; birleştir ve count topla
5. Listede karşılığı olmayan anomali varsa GENERAL_ANOMALY_HIGH veya GENERAL_ANOMALY_MEDIUM
6. Hiçbir anomali yoksa: {{"summary": "Dosya incelendi, ek anomali tespit edilmedi.", "penalties": []}}
7. Tahmin yapma — sadece veride açıkça görülen sorunları raporla"""


def _sanitize_for_llm(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """VKN kısmen maskeler; hassas veri koruması."""
    import re

    def _mask_vkn(vkn: str) -> str:
        if not isinstance(vkn, str) or len(vkn) < 5:
            return "***"
        return vkn[:3] + "***" + vkn[-2:]

    result = []
    for inv in invoices:
        slim: dict[str, Any] = {
            "id": inv.get("id"),
            "type": inv.get("type"),
            "date": inv.get("date"),
            "amount": inv.get("amount"),
            "kdv_amount": inv.get("kdv_amount"),
            "kdv_rate": inv.get("kdv_rate"),
            "is_export": inv.get("is_export"),
            "is_tevkifat": inv.get("is_tevkifat"),
        }
        raw_vkn = inv.get("supplier_tax_number") or inv.get("supplier_id") or ""
        if raw_vkn:
            slim["supplier_vkn_masked"] = _mask_vkn(str(raw_vkn))
        slim = {k: v for k, v in slim.items() if v is not None}
        result.append(slim)
    return result


def _validate_llm_result(
    result: LLMAnalysisResult,
    logs: list[str],
) -> list[dict[str, Any]]:
    """
    LLM çıktısını validate eder; kanunname dışı kodları skip, count'u sınırlar,
    aynı kodları merge eder. Risk items formatında döner.
    """
    merged: dict[PenaltyCode, dict[str, Any]] = {}

    for det in result.penalties:
        code = det.code
        if code not in LLM_ALLOWED_CODES:
            logs.append(_log(f"[LLM Filtre] {code.value} LLM'e izinli değil — atlandı"))
            continue

        safe_count = max(1, min(int(det.count), 100))
        inv_ids = [str(i) for i in (det.invoice_ids or [])][:20]

        if code in merged:
            merged[code]["count"] += safe_count
            merged[code]["inv_ids"] = list(
                dict.fromkeys(merged[code]["inv_ids"] + inv_ids)
            )[:20]
            merged[code]["evidence"] = (
                merged[code]["evidence"] + "; " + det.evidence
            )[:200]
            merged[code]["macro_flag"] = merged[code].get("macro_flag", False) or det.macro_flag
        else:
            merged[code] = {
                "count": safe_count,
                "inv_ids": inv_ids,
                "evidence": det.evidence[:200],
                "macro_flag": det.macro_flag,
            }

    risk_items: list[dict[str, Any]] = []
    seen_risk_keys: set[tuple[str, str]] = set()
    for code, data in merged.items():
        md: dict[str, Any] = {}
        if data.get("macro_flag"):
            md["macro_flag"] = True
        llm_src = (
            "hybrid" if code == PenaltyCode.REFUND_LOGIC_IMPOSSIBLE else "llm"
        )
        add_penalty(
            risk_items, seen_risk_keys,
            code,
            reason=data["evidence"],
            suggested_action="Belirtilen anomaliyi belgelerle doğrulayın veya düzeltin.",
            count=data["count"],
            invoice_ids=data["inv_ids"],
            metadata=md or None,
            source=llm_src,
        )
        logs.append(_log(
            f"[LLM] {code.value} | count={data['count']} | "
            f"{data['evidence'][:60]}"
        ))

    return risk_items


def _detect_anomalies_with_llm(
    normalized_invoices: list[dict[str, Any]],
    normalized_suppliers: list[dict[str, Any]],
    refund_type: str,
    logs: list[str],
) -> tuple[list[dict[str, Any]], str, bool]:
    """
    Gemini LLM çağrısı — kanunname kodu seçer, puan Python keser.
    Döner: (risk_items, llm_summary, llm_used)
    """
    if os.getenv("LLM_ANOMALY_ENABLED", "true").lower() != "true":
        logs.append(_log("LLM anomali tespiti devre dışı (LLM_ANOMALY_ENABLED!=true)"))
        return [], "", False

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        logs.append(_log("UYARI: API key bulunamadı — fallback devreye girecek"))
        return [], "", False

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        logs.append(_log("UYARI: google-genai paketi bulunamadı — fallback"))
        return [], "", False

    try:
        sanitized_invoices = _sanitize_for_llm(normalized_invoices)
        slim_suppliers = [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "risk_level": s.get("risk_level"),
                **({"risk_note": s["risk_note"]} if s.get("risk_note") else {}),
            }
            for s in normalized_suppliers
        ]

        payload = {
            "invoices": sanitized_invoices[:30],
            "suppliers": slim_suppliers[:15],
            "refund_type": refund_type,
        }
        payload_str = json.dumps(payload, ensure_ascii=False, indent=2)
        # Büyük payload'larda LLM response kesilmesin diye 5000 char sınırı
        if len(payload_str) > 5000:
            payload_str = payload_str[:5000] + '\n...\n]}\n}'

        logs.append(_log(
            f"LLM denetimi başlatıldı — iade türü: {refund_type}, "
            f"payload: {len(payload_str)} karakter"
        ))

        system_prompt = _build_llm_system_prompt(refund_type)
        today = datetime.now(timezone.utc).date().isoformat()
        user_message = (
            f"Referans tarih: {today}\n"
            f"Aşağıdaki KDV iade verisini kanunname kodlarıyla denetle:\n\n"
            f"```json\n{payload_str}\n```"
        )

        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=model_name,
            contents=user_message,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.05,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=LLMAnalysisResult,
            ),
        )

        raw_text = (response.text or "").strip()
        if not raw_text:
            logs.append(_log("LLM boş yanıt döndü"))
            return [], "", False

        llm_result = LLMAnalysisResult.model_validate_json(raw_text)
        risk_items = _validate_llm_result(llm_result, logs)
        logs.append(_log(
            f"LLM tamamlandı: {len(risk_items)} kanunname maddesi, "
            f"summary: {llm_result.summary[:60]!r}"
        ))
        return risk_items, llm_result.summary, True

    except Exception as exc:
        logs.append(_log(f"UYARI: LLM çağrısı başarısız ({exc!r}) — fallback devreye girecek"))
        return [], "", False


# ─────────────────────────────────────────────────────────────────────────────
# Clarification Yardımcıları
# ─────────────────────────────────────────────────────────────────────────────

def _build_clarification_questions(
    refund_type: str,
    missing_docs: list[dict[str, Any]],
    clarification_answers: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    """Yeni veya cevaplanmamış soruları üretir."""
    already_answered = set(clarification_answers.keys())
    has_missing_gumruk = any(
        doc["doc_name"] == DOC_GUMRUK_BEYANNAME for doc in missing_docs
    )
    has_missing_2no = any(
        doc["doc_name"] == DOC_2NO_KDV_BEYANNAME for doc in missing_docs
    )

    questions: list[ClarificationQuestion] = []

    # İade türü yalnızca Collector tespitine güvenilir; radyo bypass kaldırıldı (C-01).

    if has_missing_gumruk and "q_gumruk_001" not in already_answered:
        questions.append(
            ClarificationQuestion(
                question_id="q_gumruk_001",
                question_text="Gümrük çıkış beyannameleri elinizde mevcut mu?",
                field="has_customs_declarations",
                type="choice",
                options=["Evet", "Hayır", "Bir kısmı mevcut"],
                required=True,
                reason="İhracat iadesi için gümrük beyannamesi zorunludur.",
            )
        )

    if has_missing_2no and "q_2no_001" not in already_answered:
        questions.append(
            ClarificationQuestion(
                question_id="q_2no_001",
                question_text="İlgili dönemler için 2 No'lu KDV beyannamesi verdiniz mi?",
                field="has_2no_declaration",
                type="choice",
                options=["Evet", "Hayır", "Bir kısmı için verdim"],
                required=True,
                reason="Tevkifat iadesi için 2 No'lu beyanname zorunludur.",
            )
        )

    questions = questions[:3]
    clarification_needed = len(questions) > 0
    return clarification_needed, [q.model_dump() for q in questions]


# ─────────────────────────────────────────────────────────────────────────────
# LangGraph Node
# ─────────────────────────────────────────────────────────────────────────────

def analyzer_node(state: IadeAjanState) -> dict[str, Any]:
    """
    Path B Analyzer Node.
    1. Python mevzuat kuralları (her zaman)
    2. LLM denetçi (kanunname kodları)
    3. Fallback DQ (LLM down ise)
    """
    new_logs: list[str] = []

    try:
        normalized_invoices = list(state.get("normalized_invoices") or [])
        normalized_suppliers = list(state.get("normalized_suppliers") or [])
        document_inventory = list(state.get("document_inventory") or [])
        company_profile = dict(state.get("company_profile") or {})
        classification = dict(state.get("classification_result") or {})
        clarification_answers = dict(state.get("clarification_answers") or {})

        profile = dict(company_profile)
        refund_type = str(
            profile.get("refund_type")
            or classification.get("refund_type", "belirsiz")
        )
        if clarification_answers.get("q_refund_type_001"):
            new_logs.append(_log(
                "q_refund_type_001 cevabı yok sayıldı — iade türü yalnızca fatura tespitine dayanır (C-01)."
            ))

        new_logs.append(_log(
            f"Analiz başlatıldı — iade türü: {refund_type} "
            f"(cevap geçmişi: {len(clarification_answers)} kayıt)"
        ))

        missing_docs: list[dict[str, Any]] = []
        risk_items: list[dict[str, Any]] = []
        process_warnings: list[str] = []
        score_inputs: dict[str, Any] = {}

        seen_doc_names: set[str] = set()
        seen_risk_keys: set[tuple[str, str]] = set()

        # ── KATMAN 1: Python Mevzuat Kuralları ───────────────────────────────
        if refund_type == "ihracat":
            _apply_export_rules(
                document_inventory, normalized_invoices,
                missing_docs, process_warnings, seen_doc_names,
            )
            _apply_amount_mismatch_rules(
                normalized_invoices, document_inventory,
                risk_items, seen_risk_keys,
            )

        if refund_type == "tevkifat":
            _apply_tevkifat_rules(
                document_inventory, normalized_invoices,
                missing_docs, risk_items,
                seen_doc_names, seen_risk_keys,
            )

        high_risk_supplier_count, force_clarification_supplier = (
            _apply_supplier_risk_rules(normalized_suppliers, risk_items, seen_risk_keys)
        )
        new_logs.append(_log(
            f"Tedarikçi riskleri tarandı — {high_risk_supplier_count} yüksek/kritik"
            + (" (clarification zorunlu)" if force_clarification_supplier else "")
        ))

        _apply_ymm_rules(
            company_profile, document_inventory, missing_docs, seen_doc_names,
        )

        _apply_deadline_rules(
            company_profile, risk_items, process_warnings,
            score_inputs, seen_risk_keys, refund_type=refund_type,
        )

        _apply_period_rules(
            normalized_invoices, company_profile,
            risk_items, process_warnings, seen_risk_keys,
        )

        efatura_rejects = _apply_efatura_api_rules(
            state,
            normalized_invoices,
            risk_items,
            process_warnings,
            seen_risk_keys,
            company_profile,
        )
        if efatura_rejects:
            new_logs.append(_log(f"e-Fatura API: {efatura_rejects} fatura reddedildi"))

        if _apply_gcb_api_penalty_from_ledger(state, risk_items, seen_risk_keys):
            new_logs.append(_log("GÇB API doğrulaması başarısız — GIB_API_GCB_REJECTED uygulandı"))

        new_logs.append(_log(
            f"Mevzuat katmanı tamamlandı — "
            f"{len(risk_items)} risk, {len(missing_docs)} eksik belge"
        ))

        session_id = str(state.get("session_id") or "")

        macro_skip_llm = _apply_macro_integrity_rules(
            normalized_invoices,
            refund_type,
            risk_items,
            seen_risk_keys,
            new_logs,
            session_id,
        )

        llm_risk_items: list[dict[str, Any]] = []
        llm_summary = ""
        llm_used = False

        if macro_skip_llm:
            llm_summary = "Makro kilit tetiklendi — AI denetimi atlandı."
            new_logs.append(_log("Makro bütünlük kilidi — LLM ve fallback DQ atlandı"))
        else:
            llm_risk_items, llm_summary, llm_used = _detect_anomalies_with_llm(
                normalized_invoices, normalized_suppliers, refund_type, new_logs
            )

        if not llm_used:
            if not macro_skip_llm:
                _apply_data_quality_rules_fallback(
                    normalized_invoices, risk_items, process_warnings, seen_risk_keys
                )
                new_logs.append(_log("Fallback DQ kuralları çalıştırıldı"))
        else:
            risk_items = risk_items + llm_risk_items

        shadow_learning.log_risk_items_batch(
            session_id=session_id,
            refund_type=refund_type,
            risk_items=risk_items,
            raw_summary=llm_summary or "",
        )

        python_risk_count = len(risk_items) - len(llm_risk_items) if llm_used else len(risk_items)
        new_logs.append(_log(
            f"Toplam risk: {len(risk_items)} "
            f"(Python: {python_risk_count}, LLM: {len(llm_risk_items) if llm_used else 0}), "
            f"llm_used={llm_used}, macro_skip_llm={macro_skip_llm}"
        ))

        # ── Risk Analizi Özeti ────────────────────────────────────────────────
        high_risk_suppliers = [
            s.get("name", s.get("id"))
            for s in normalized_suppliers
            if s.get("risk_level") == "yüksek"
        ]

        risk_analysis = {
            "total_risk_count": len(risk_items),
            "high_risk_count": sum(1 for r in risk_items if r.get("severity") == "yüksek"),
            "medium_risk_count": sum(1 for r in risk_items if r.get("severity") == "orta"),
            "low_risk_count": sum(1 for r in risk_items if r.get("severity") == "düşük"),
            "missing_doc_count": len(missing_docs),
            "total_score_impact": (
                sum(r.get("score_impact", 0) for r in risk_items)
                + sum(d.get("score_impact", 0) for d in missing_docs)
            ),
            "refund_type": refund_type,
            "high_risk_suppliers": high_risk_suppliers,
            "llm_risk_count": len(llm_risk_items) if llm_used else 0,
            "python_risk_count": python_risk_count,
            "llm_used": llm_used,
            "llm_summary": llm_summary,
            "macro_skip_llm": macro_skip_llm,
        }

        # ── Clarification ─────────────────────────────────────────────────────
        clarification_needed, clarification_questions = _build_clarification_questions(
            refund_type, missing_docs, clarification_answers
        )

        if force_clarification_supplier and not clarification_needed:
            clarification_needed = True
            new_logs.append(_log(
                "[Kritik Tedarikçi] SMİYB riski nedeniyle clarification döngüsü açıldı."
            ))

        retry_count = (state.get("retry_count") or 0) + 1
        max_retries = state.get("max_retries") or 5
        block_reason = ""
        analysis_status_override: str | None = None
        if clarification_needed and retry_count >= max_retries:
            new_logs.append(_log(
                f"[Güvenlik] retry={retry_count}>={max_retries}; "
                "clarification_blocked — Decision atlanacak."
            ))
            analysis_status_override = "clarification_blocked"
            block_reason = "PROOF_REQUIRED"
            process_warnings.append(
                "Zorunlu belge kanıtları tamamlanamadı. Analiz skoru üretilmedi."
            )

        score_inputs.update({
            "risk_item_count": len(risk_items),
            "high_risk_supplier_count": high_risk_supplier_count,
            "missing_doc_count": len(missing_docs),
            "total_score_penalty": risk_analysis["total_score_impact"],
            "clarification_needed": clarification_needed,
            "refund_type": refund_type,
            "invoice_count": len(normalized_invoices),
            "supplier_count": len(normalized_suppliers),
            "llm_used": llm_used,
            "has_ymm_contract": company_profile.get("has_ymm_contract", False),
            "has_previous_refund": company_profile.get("has_previous_refund", False),
            "estimated_refund_amount": company_profile.get("estimated_refund_amount", 0),
            "clarification_answers": clarification_answers,
            "clarification_round": retry_count,
        })

        current_agent = (
            NEXT_AGENT_IF_CLARIFICATION if clarification_needed
            else NEXT_AGENT_IF_NO_CLARIFICATION
        )
        if analysis_status_override:
            analysis_status = analysis_status_override
            current_agent = "ClarificationAgent"
        else:
            analysis_status = "clarification_waiting" if clarification_needed else "running"

        new_logs.append(_log(f"Analyzer tamamlandı → {current_agent}"))

        out: dict[str, Any] = {
            "risk_items": risk_items,
            "missing_docs": missing_docs,
            "process_warnings": process_warnings,
            "risk_analysis": risk_analysis,
            "score_inputs": score_inputs,
            "clarification_needed": clarification_needed,
            "clarification_questions": clarification_questions,
            "current_agent": current_agent,
            "analysis_status": analysis_status,
            "retry_count": retry_count,
            "agent_logs": new_logs,
            "error_state": None,
        }
        if block_reason:
            out["block_reason"] = block_reason
        return out

    except Exception as exc:
        new_logs.append(_log(f"HATA: {exc}"))
        return {
            "error_state": f"ANALYZER_ERROR: {exc}",
            "analysis_status": "failed",
            "current_agent": AGENT_NAME,
            "agent_logs": new_logs,
        }


if __name__ == "__main__":
    import sys
    from app.agents.collector_agent import collector_node

    def _print_result(label: str, out: dict[str, Any]) -> None:
        ra = out.get("risk_analysis", {})
        llm_risks = [r for r in out.get("risk_items", []) if r.get("source") == "llm"]
        py_risks = [r for r in out.get("risk_items", []) if r.get("source") not in ("llm",)]
        print(f"\n  {label}")
        print(f"  Python riskleri: {len(py_risks)}")
        print(f"  LLM riskleri   : {len(llm_risks)}")
        print(f"  Eksik belge    : {ra.get('missing_doc_count', 0)}")
        print(f"  llm_used       : {ra.get('llm_used')}")
        print(f"  LLM summary    : {ra.get('llm_summary', '')[:70]!r}")
        print(f"  Status         : {out.get('analysis_status')}")
        for r in out.get("risk_items", [])[:6]:
            code_str = r.get("code") or ""
            print(f"    [{r['source']}] {code_str} {r['score_impact']} | {r['reason'][:60]}")

    print("=" * 65)
    print("Test 1 — celik_as_high (ihracat)")
    print("=" * 65)
    col = collector_node({"scenario_id": "celik_as_high", "agent_logs": []})
    assert col["analysis_status"] == "running"
    out = analyzer_node(col)
    assert out["error_state"] is None, out["error_state"]
    assert any(d["doc_name"] == DOC_GUMRUK_BEYANNAME for d in out["missing_docs"])
    _print_result("✅ celik_as_high geçti", out)

    print("\n" + "=" * 65)
    print("Test 2 — LLM devre dışı (fallback)")
    print("=" * 65)
    os.environ["LLM_ANOMALY_ENABLED"] = "false"
    out_no_llm = analyzer_node(col)
    os.environ["LLM_ANOMALY_ENABLED"] = "true"
    assert out_no_llm["error_state"] is None
    assert not out_no_llm["risk_analysis"]["llm_used"]
    _print_result("✅ fallback geçti", out_no_llm)

    print("\n✅ Tüm Analyzer testleri geçti")
    sys.exit(0)
