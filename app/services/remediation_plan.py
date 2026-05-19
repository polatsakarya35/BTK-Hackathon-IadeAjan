"""Nihai rapor — hedef skora ulaşmak için yapılacaklar ve tahmini skor projeksiyonu."""

from __future__ import annotations

from typing import Any

from app.schemas.penalty_codes import MANDATORY_LOCK_CODES, PENALTY_MATRIX, PenaltyCode

TARGET_SCORE_FINANCE = 90
THRESHOLD_LOW_RISK = 90
THRESHOLD_MEDIUM_RISK = 60

MACRO_INVALID_CODES = frozenset({"DATA_INTEGRITY_FAILURE", "REFUND_LOGIC_IMPOSSIBLE"})


def _normalize_code_str(raw: Any) -> str:
    if isinstance(raw, PenaltyCode):
        return raw.value
    if isinstance(raw, str):
        return raw.split(".")[-1] if "." in raw else raw
    return str(raw) if raw else ""


def _risk_category_label(score: int) -> str:
    if score >= THRESHOLD_LOW_RISK:
        return "Düşük Risk"
    if score >= THRESHOLD_MEDIUM_RISK:
        return "Orta Risk"
    return "Yüksek Risk"


def _action_text(
    *,
    suggested_action: str | None,
    code_str: str,
    reason: str,
    doc_name: str | None = None,
) -> str:
    if suggested_action and suggested_action.strip():
        return suggested_action.strip()
    parts: list[str] = []
    if doc_name:
        parts.append(f"«{doc_name}» belgesini temin edin veya yükleyin.")
    if code_str:
        try:
            entry = PENALTY_MATRIX.get(PenaltyCode(code_str))
            if entry and entry.description:
                parts.append(entry.description)
        except ValueError:
            pass
    if reason and reason.strip():
        parts.append(reason.strip()[:200])
    return " ".join(parts) if parts else "İlgili maddeyi düzeltin veya kanıt yükleyin."


def _step_from_risk(
    item: dict[str, Any],
    mandatory_locks: set[str],
) -> dict[str, Any]:
    code_str = _normalize_code_str(item.get("code")) or "UNKNOWN"
    points = abs(int(item.get("score_impact", 0)))
    is_lock = code_str in mandatory_locks
    return {
        "code": code_str,
        "label": str(item.get("title") or code_str).strip(),
        "action": _action_text(
            suggested_action=item.get("suggested_action"),
            code_str=code_str,
            reason=str(item.get("reason") or ""),
        ),
        "points_recoverable": points,
        "is_mandatory_lock": is_lock,
    }


def _step_from_missing_doc(
    doc: dict[str, Any],
    mandatory_locks: set[str],
) -> dict[str, Any]:
    code_str = _normalize_code_str(doc.get("code")) or "MISSING_DOC"
    points = abs(int(doc.get("score_impact", 0)))
    doc_name = str(doc.get("doc_name") or code_str)
    is_lock = code_str in mandatory_locks
    return {
        "code": code_str,
        "label": doc_name,
        "action": _action_text(
            suggested_action=None,
            code_str=code_str,
            reason=str(doc.get("reason") or ""),
            doc_name=doc_name,
        ),
        "points_recoverable": points,
        "is_mandatory_lock": is_lock,
    }


def build_remediation_plan(
    *,
    calculated_score: int,
    risk_items: list[dict[str, Any]],
    missing_docs: list[dict[str, Any]],
    mandatory_lock_triggered: list[str] | None = None,
) -> dict[str, Any]:
    """
    Tüm açık ceza/belge maddeleri giderildiğinde tahmini skor ve aksiyon listesi üretir.
    """
    current = max(0, min(int(calculated_score), 100))
    lock_set = {str(c) for c in (mandatory_lock_triggered or [])}

    raw_steps: list[dict[str, Any]] = []
    for item in risk_items:
        step = _step_from_risk(item, lock_set)
        if step["points_recoverable"] > 0 or step["code"] != "UNKNOWN":
            raw_steps.append(step)
    for doc in missing_docs:
        step = _step_from_missing_doc(doc, lock_set)
        if step["points_recoverable"] > 0 or step["label"]:
            raw_steps.append(step)

    raw_steps.sort(
        key=lambda s: (
            0 if s["is_mandatory_lock"] else 1,
            -int(s["points_recoverable"]),
            s["code"],
        ),
    )

    for idx, step in enumerate(raw_steps, start=1):
        step["priority"] = idx

    points_total = sum(int(s["points_recoverable"]) for s in raw_steps)
    projected = max(0, min(current + points_total, 100))
    projected_category = _risk_category_label(projected)

    has_macro = bool(MACRO_INVALID_CODES.intersection(lock_set)) or any(
        s["code"] in MACRO_INVALID_CODES for s in raw_steps
    )

    if not raw_steps:
        headline = (
            f"Ek düzeltme maddesi tespit edilmedi; mevcut skor {current}/100 korunur. "
            f"Ön finansman için genelde ≥{TARGET_SCORE_FINANCE} puan hedeflenir."
        )
    elif has_macro:
        headline = (
            f"Tüm listelenen maddeler giderilirse tahmini skor **{projected}/100** olur "
            f"(şu an **{current}/100**). "
            "Dosyada yapısal/makro kilit var; önce fatura seti ve iade türünü GİB düzenine "
            "göre düzeltin. Ön finansman için ≥90 puan ve finansman kilidi olmaması gerekir."
        )
    else:
        headline = (
            f"Tüm listelenen maddeler giderilirse tahmini skor **{projected}/100** olur "
            f"(şu an **{current}/100**, +{points_total} puan). "
            f"Tahmini risk bandı: **{projected_category}**. "
            f"Ön finansman için genelde ≥{TARGET_SCORE_FINANCE} puan (Düşük Risk) hedeflenir."
        )

    return {
        "current_score": current,
        "target_score_finance": TARGET_SCORE_FINANCE,
        "projected_score_if_all_resolved": projected,
        "projected_risk_category": projected_category,
        "points_recoverable_total": points_total,
        "steps": raw_steps,
        "headline_tr": headline,
        "has_macro_lock": has_macro,
    }
