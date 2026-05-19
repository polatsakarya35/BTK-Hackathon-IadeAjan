"""LangGraph Decision node — Path B skor hesaplama ve final rapor üretimi.

Path B: PENALTY_MATRIX tek puan kaynağı; LLM_PENALTY_CAP ve SEVERITY_SCORE_MAP kaldırıldı.
Tüm risk_items[*].score_impact değerleri kanunname tarafından belirlendi; toplama yeterli.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.state import IadeAjanState
from app.schemas.penalty_codes import MANDATORY_LOCK_CODES, PENALTY_MATRIX, PenaltyCode
from app.services.remediation_plan import build_remediation_plan

AGENT_NAME = "DecisionAgent"
BASE_SCORE = 100

THRESHOLD_LOW_RISK = 90
THRESHOLD_MEDIUM_RISK = 60

FINANCE_MIN_AMOUNT = 50_000

CLARIFICATION_SCORE_ADJUSTMENTS: dict[str, dict[str, tuple[int, str | None]]] = {
    "has_customs_declarations": {
        "Evet":             (+6, "pending_verification"),
        "Bir kısmı mevcut": (+3, "pending_verification"),
        "Hayır":            ( 0, None),
    },
    "has_2no_declaration": {
        "Evet":                  (+6, "pending_verification"),
        "Bir kısmı için verdim": (+3, "pending_verification"),
        "Hayır":                 ( 0, None),
    },
}

MATRIX_VERSION = "v3.2-macro"


def _log(message: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"[{ts}] [{AGENT_NAME}] {message}"


def _determine_risk_category(score: int) -> tuple[str, str, str]:
    if score >= THRESHOLD_LOW_RISK:
        return (
            "Düşük Risk",
            "approved",
            "İade talebi ön değerlendirmeyi geçmiştir. Standart süreç başlatılabilir.",
        )
    if score >= THRESHOLD_MEDIUM_RISK:
        return (
            "Orta Risk",
            "conditional_approval",
            "Şartlı onay: YMM raporu veya teminat mektubu ile süreç ilerleyebilir.",
        )
    return (
        "Yüksek Risk",
        "rejected",
        "Dosya vergi incelemesine sevk kriterlerini karşılamaktadır. Detaylı inceleme gereklidir.",
    )


def _normalize_code_str(raw: Any) -> str:
    """
    enum instance, 'PenaltyCode.GCB_MISSING' string veya 'GCB_MISSING' gibi
    farklı formatlardaki kodu standart değer stringine çevirir.
    """
    if isinstance(raw, PenaltyCode):
        return raw.value
    if isinstance(raw, str):
        # "PenaltyCode.GCB_MISSING" → "GCB_MISSING"
        return raw.split(".")[-1] if "." in raw else raw
    return str(raw) if raw else ""


def _build_items_by_code(
    risk_items: list[dict[str, Any]],
    missing_docs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Kanunname koduna göre gruplu özet oluşturur (UI ceza tablosu için)."""
    groups: dict[str, dict[str, Any]] = {}

    for item in risk_items:
        code_val = _normalize_code_str(item.get("code")) or "UNKNOWN"
        if code_val not in groups:
            entry = PENALTY_MATRIX.get(PenaltyCode(code_val)) if code_val != "UNKNOWN" else None
            groups[code_val] = {
                "code": code_val,
                "description": entry.description if entry else item.get("title", ""),
                "category": entry.category if entry else "Diğer",
                "count": 0,
                "total_penalty": 0,
                "source": item.get("source", "python"),
                "evidence_samples": [],
                "invoice_ids": [],
            }
        g = groups[code_val]
        g["count"] += item.get("count", 1)
        g["total_penalty"] += abs(int(item.get("score_impact", 0)))
        reason = item.get("reason", "")
        if reason and len(g["evidence_samples"]) < 2:
            g["evidence_samples"].append(reason[:100])
        g["invoice_ids"] = list(dict.fromkeys(
            g["invoice_ids"] + (item.get("invoice_ids") or [])
        ))[:10]

    for doc in missing_docs:
        code_val = _normalize_code_str(doc.get("code")) or "MISSING_DOC"
        if code_val not in groups:
            entry = PENALTY_MATRIX.get(PenaltyCode(code_val)) if code_val != "MISSING_DOC" else None
            groups[code_val] = {
                "code": code_val,
                "description": entry.description if entry else doc.get("doc_name", ""),
                "category": entry.category if entry else "Zorunlu Belge",
                "count": 1,
                "total_penalty": 0,
                "source": "python",
                "evidence_samples": [],
                "invoice_ids": [],
            }
        g = groups[code_val]
        g["total_penalty"] += abs(int(doc.get("score_impact", 0)))
        reason = doc.get("reason", "")
        if reason and len(g["evidence_samples"]) < 2:
            g["evidence_samples"].append(reason[:100])

    return groups


def _check_finance_eligibility(
    score: int,
    risk_category: str,
    company_profile: dict[str, Any],
    risk_items: list[dict[str, Any]],
    missing_docs: list[dict[str, Any]],
    logs: list[str],
) -> dict[str, Any]:
    """Finansman uygunluk kontrolü — Path B: MANDATORY_LOCK_CODES kilidi eklendi."""
    estimated_amount = float(company_profile.get("estimated_refund_amount", 0) or 0)
    base_eligible = risk_category == "Düşük Risk" and estimated_amount >= FINANCE_MIN_AMOUNT

    # Zorunlu belge kilidi: risk_items veya missing_docs'da MANDATORY_LOCK_CODES varsa kilit
    lock_codes_found: list[str] = []
    for item in [*risk_items, *missing_docs]:
        raw_code = item.get("code")
        if raw_code is None:
            continue
        code_str = _normalize_code_str(raw_code)
        if not code_str:
            continue
        try:
            pc = PenaltyCode(code_str)
        except ValueError:
            continue
        if pc in MANDATORY_LOCK_CODES:
            lock_codes_found.append(raw_code)

    if lock_codes_found:
        lock_reason = (
            f"Finansman kilidi: zorunlu mevzuat maddeleri tespit edildi: "
            f"{', '.join(dict.fromkeys(lock_codes_found))}. "
            "GİB bu belgeler eksik/sorunlu iken dosyayı işleme almaz."
        )
        headline = ""
        if score >= THRESHOLD_LOW_RISK:
            headline = (
                "Ön Onay: RED — Skor yüksek olmasına rağmen zorunlu evrak eksikliği "
                "veya sahtecilik şüphesi nedeniyle finansman kilitlenmiştir."
            )
        logs.append(_log(lock_reason))
        return {
            "eligible": False,
            "estimated_amount": estimated_amount,
            "reason": lock_reason,
            "headline": headline,
            "mandatory_lock_triggered": list(dict.fromkeys(lock_codes_found)),
        }

    if not base_eligible:
        reason = "Risk kategorisi veya tutar eşiği faktoring için uygun değil."
        return {
            "eligible": False,
            "estimated_amount": estimated_amount,
            "reason": reason,
            "mandatory_lock_triggered": [],
        }

    return {
        "eligible": True,
        "estimated_amount": estimated_amount,
        "reason": "Skor, tutar eşiği ve zorunlu belgeler karşılanıyor.",
        "mandatory_lock_triggered": [],
    }


def _build_decision_summary(
    company_name: str,
    score: int,
    risk_category: str,
    approval_label: str,
    refund_type: str,
    total_deduction: int,
    clarification_bonus: int,
) -> str:
    bonus_str = f" (+{clarification_bonus} düzeltme)" if clarification_bonus > 0 else ""
    return (
        f"{company_name} şirketinin KDV iade talebi ({refund_type}), "
        f"toplam {total_deduction} puan düşülerek{bonus_str} "
        f"{score}/100 puan ile '{risk_category}' olarak değerlendirilmiştir. "
        f"{approval_label}"
    )


def _proof_passed_for_field(state: dict[str, Any], field: str) -> bool:
    """Ledger'da ilgili alan için passed kanıt var mı."""
    from app.services.clarification_proof import expected_type_for_field
    from app.services.proof_ledger import ledger_from_state

    for rec in ledger_from_state(state):
        if rec.field == field and rec.verification_status == "passed" and not rec.is_expired():
            return True
    return False


def _compute_clarification_bonus(
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    bonus = 0
    pending: list[str] = []
    state = state or {}
    for question in questions:
        field = question.get("field", "")
        question_id = question.get("question_id", "")
        if not field or not question_id:
            continue
        adjustments = CLARIFICATION_SCORE_ADJUSTMENTS.get(field)
        if not adjustments:
            continue
        answer = answers.get(question_id)
        if answer is None:
            continue
        score_adj, flag = adjustments.get(str(answer), (0, None))
        bonus += score_adj
        if flag == "pending_verification":
            if _proof_passed_for_field(state, field):
                continue
            pending.append(f"{field}={answer}")
    return bonus, pending


def _minimal_blocked_report(state: IadeAjanState, logs: list[str]) -> dict[str, Any]:
    """Skor üretilmeden blocked/failed özet raporu."""
    status = state.get("analysis_status", "failed")
    company_name = state.get("company_name", "") or "Şirket"
    questions = list(state.get("clarification_questions") or [])
    report: dict[str, Any] = {
        "status": "blocked" if status == "clarification_blocked" else "failed",
        "company_name": company_name,
        "block_reason": state.get("block_reason", ""),
        "failure_reason": state.get("failure_reason", ""),
        "error_state": state.get("error_state"),
        "pending_questions": [q.get("question_id") for q in questions],
        "risk_items": state.get("risk_items", []),
        "missing_docs": state.get("missing_docs", []),
        "process_warnings": state.get("process_warnings", []),
        "verification_summary": state.get("verification_summary", {}),
        "calculated_score": None,
        "finance_eligibility": {
            "eligible": False,
            "reason": "analysis_incomplete",
        },
    }
    logs.append(_log(f"Decision atlandı — status={status}"))
    return {
        "final_report": report,
        "final_score": 0,
        "analysis_status": status,
        "current_agent": "END",
        "agent_logs": logs,
        "error_state": state.get("error_state"),
    }


def decision_node(state: IadeAjanState) -> dict[str, Any]:
    """
    Path B Decision Node.
    Puan hesaplama: sum(|score_impact|) — LLM_PENALTY_CAP yoktur.
    Finansman kilidi: MANDATORY_LOCK_CODES.
    """
    logs: list[str] = [_log("Karar hesaplama başlatıldı")]

    status = state.get("analysis_status", "running")
    if status in ("failed", "clarification_blocked"):
        return _minimal_blocked_report(state, logs)

    try:
        risk_items: list[dict[str, Any]] = list(state.get("risk_items", []) or [])
        missing_docs: list[dict[str, Any]] = list(state.get("missing_docs", []) or [])
        process_warnings: list[str] = list(state.get("process_warnings", []) or [])
        clarification_questions: list[dict[str, Any]] = list(
            state.get("clarification_questions", []) or []
        )
        clarification_answers: dict[str, Any] = dict(state.get("clarification_answers") or {})
        company_profile: dict[str, Any] = dict(state.get("company_profile") or {})
        classification: dict[str, Any] = dict(state.get("classification_result") or {})
        score_inputs: dict[str, Any] = dict(state.get("score_inputs") or {})
        risk_analysis: dict[str, Any] = dict(state.get("risk_analysis") or {})

        # Path B: tüm cezalar kanunname tablosundan geldi; kaynak ayrımı gereksiz
        total_risk_penalty = sum(
            abs(int(r.get("score_impact", 0))) for r in risk_items
        )
        doc_penalty = sum(abs(int(d.get("score_impact", 0))) for d in missing_docs)
        total_deduction = total_risk_penalty + doc_penalty

        # Kaynak bazlı istatistik (hybrid = LLM semantik + Python matris)
        llm_penalty = sum(
            abs(int(r.get("score_impact", 0)))
            for r in risk_items
            if r.get("source") in ("llm", "hybrid")
        )
        python_penalty = sum(
            abs(int(r.get("score_impact", 0)))
            for r in risk_items
            if r.get("source") in ("python", "fallback")
        )

        logs.append(_log(
            f"Ceza özeti: risk={total_risk_penalty} "
            f"(python/fallback={python_penalty}, llm/hybrid={llm_penalty}), "
            f"belge={doc_penalty}, toplam={total_deduction}"
        ))

        clarification_bonus, pending_verifications = _compute_clarification_bonus(
            clarification_questions, clarification_answers, state
        )

        verification_summary = dict(state.get("verification_summary") or {})
        verification_summary.setdefault(
            "disclaimer",
            "Finansman uygunluğu belge içerik doğrulaması değildir.",
        )
        logs.append(_log(
            f"Clarification düzeltmesi: +{clarification_bonus}"
            + (f" | bekliyor: {pending_verifications}" if pending_verifications else "")
        ))

        raw_score = BASE_SCORE - total_deduction
        calculated_score = max(0, min(100, raw_score + clarification_bonus))

        risk_category, approval_status, approval_label = _determine_risk_category(calculated_score)
        logs.append(_log(f"Nihai skor: {calculated_score}/100 → {risk_category}"))

        if pending_verifications and approval_status == "approved":
            approval_status = "conditional_approval"
            approval_label = (
                "Beyanınız alındı. İadenin onaylanması için belirttiğiniz "
                "belgeleri sisteme yüklemeniz beklenmektedir."
            )
            logs.append(_log(
                f"[Path B] Doğrulama bekliyor: {pending_verifications} — conditional_approval"
            ))

        llm_used: bool = bool(risk_analysis.get("llm_used", score_inputs.get("llm_used", False)))
        macro_skip_llm: bool = bool(risk_analysis.get("macro_skip_llm", False))
        llm_summary: str = str(risk_analysis.get("llm_summary", ""))

        items_by_code = _build_items_by_code(risk_items, missing_docs)

        score_breakdown: dict[str, Any] = {
            "base_score": BASE_SCORE,
            "total_risk_penalty": total_risk_penalty,
            "python_penalty": python_penalty,
            "llm_penalty": llm_penalty,
            "doc_penalty": doc_penalty,
            "clarification_bonus": clarification_bonus,
            "calculated_score": calculated_score,
            "llm_used": llm_used,
            "macro_skip_llm": macro_skip_llm,
            "llm_summary": llm_summary,
            "items_by_code": items_by_code,
            "matrix_version": MATRIX_VERSION,
        }

        finance_eligibility = _check_finance_eligibility(
            calculated_score, risk_category,
            company_profile, risk_items, missing_docs, logs
        )
        mandatory_lock_triggered = list(finance_eligibility.get("mandatory_lock_triggered") or [])

        macro_lock_codes = frozenset({"DATA_INTEGRITY_FAILURE", "REFUND_LOGIC_IMPOSSIBLE"})
        macro_lock_active = bool(macro_lock_codes.intersection(mandatory_lock_triggered))
        score_breakdown["macro_lock_active"] = macro_lock_active

        logs.append(_log(
            f"Finansman: {'Evet' if finance_eligibility['eligible'] else 'Hayır'}"
            + (f" (kilit: {mandatory_lock_triggered})" if mandatory_lock_triggered else "")
        ))

        refund_type = str(classification.get("refund_type", "belirsiz"))
        company_name = (
            state.get("company_name", "")
            or company_profile.get("company_name", "")
            or "Yüklenen dosya"
        )
        tax_number = state.get("tax_number", "") or company_profile.get("tax_number", "")

        decision_summary = _build_decision_summary(
            company_name=company_name,
            score=calculated_score,
            risk_category=risk_category,
            approval_label=approval_label,
            refund_type=refund_type,
            total_deduction=total_deduction,
            clarification_bonus=clarification_bonus,
        )

        decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        finance_headline = str(finance_eligibility.get("headline") or "")
        if (
            not finance_headline
            and calculated_score >= THRESHOLD_LOW_RISK
            and not finance_eligibility.get("eligible")
            and mandatory_lock_triggered
        ):
            finance_headline = (
                "Ön Onay: RED — Skor yüksek olmasına rağmen zorunlu evrak eksikliği "
                "veya sahtecilik şüphesi nedeniyle finansman kilitlenmiştir."
            )

        remediation_plan = build_remediation_plan(
            calculated_score=calculated_score,
            risk_items=risk_items,
            missing_docs=missing_docs,
            mandatory_lock_triggered=mandatory_lock_triggered,
        )
        score_breakdown["remediation_plan"] = remediation_plan

        final_report: dict[str, Any] = {
            "company_name": company_name,
            "tax_number": tax_number,
            "refund_type": refund_type,
            "finance_headline": finance_headline,
            "remediation_plan": remediation_plan,
            "base_score": BASE_SCORE,
            "total_risk_penalty": total_risk_penalty,
            "python_penalty": python_penalty,
            "llm_penalty": llm_penalty,
            "doc_penalty": doc_penalty,
            "clarification_bonus": clarification_bonus,
            "total_deduction": total_deduction,
            "calculated_score": calculated_score,
            "risk_category": risk_category,
            "approval_status": approval_status,
            "pending_verifications": pending_verifications,
            "decision_summary": decision_summary,
            "risk_items": risk_items,
            "missing_docs": missing_docs,
            "process_warnings": process_warnings,
            "finance_eligibility": finance_eligibility,
            "score_breakdown": score_breakdown,
            "mandatory_lock_triggered": mandatory_lock_triggered,
            "macro_lock_active": macro_lock_active,
            "macro_skip_llm": macro_skip_llm,
            "llm_used": llm_used,
            "decided_at": decided_at,
            "days_remaining_to_deadline": score_inputs.get("days_remaining_to_deadline"),
            "matrix_version": MATRIX_VERSION,
            "verification_summary": verification_summary,
        }

        logs.append(_log("Süreç tamamlandı — status: completed"))

        return {
            "final_report": final_report,
            "verification_summary": verification_summary,
            "risk_items": risk_items,
            "missing_docs": missing_docs,
            "company_name": company_name,
            "tax_number": tax_number,
            "analysis_status": "completed",
            "current_agent": "END",
            "agent_logs": logs,
            "error_state": None,
        }

    except Exception as exc:
        return {
            "error_state": f"DECISION_ERROR: {exc}",
            "analysis_status": "failed",
            "current_agent": AGENT_NAME,
            "agent_logs": [_log(f"HATA: {exc}")],
        }


if __name__ == "__main__":
    import sys
    from app.agents.analyzer_agent import analyzer_node
    from app.agents.collector_agent import collector_node

    def _run_pipeline(
        scenario_id: str, answers: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        col = collector_node({"scenario_id": scenario_id, "agent_logs": []})
        assert col["analysis_status"] == "running"
        ana = analyzer_node(col)
        assert ana["error_state"] is None
        state: dict[str, Any] = {**col, **ana}
        if answers:
            state["clarification_answers"] = answers
        return decision_node(state)

    print("=" * 65)
    print("Test 1 — celik_as_high")
    print("=" * 65)
    dec1 = _run_pipeline("celik_as_high")
    rep1 = dec1["final_report"]
    assert dec1["analysis_status"] == "completed"
    assert 0 <= rep1["calculated_score"] <= 100
    expected = max(0, min(100, BASE_SCORE - rep1["total_deduction"] + rep1["clarification_bonus"]))
    assert rep1["calculated_score"] == expected, f"{rep1['calculated_score']} != {expected}"
    assert "score_breakdown" in rep1
    assert "items_by_code" in rep1["score_breakdown"]

    sb = rep1["score_breakdown"]
    print(f"  Skor      : {rep1['calculated_score']}/100 ({rep1['risk_category']})")
    print(f"  python    : -{sb['python_penalty']}")
    print(f"  llm       : -{sb['llm_penalty']}")
    print(f"  belge     : -{sb['doc_penalty']}")
    print(f"  llm_used  : {sb['llm_used']}")
    print(f"  Kilit     : {rep1['mandatory_lock_triggered']}")
    print(f"  Kodlar    : {list(rep1['score_breakdown']['items_by_code'].keys())}")
    print("✅ Test 1 geçti")

    print("=" * 65)
    print("✅ Tüm Decision testleri geçti")
    print("=" * 65)
    sys.exit(0)
