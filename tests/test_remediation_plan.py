"""Skor iyileştirme planı — build_remediation_plan birim testleri."""

from __future__ import annotations

from app.services.remediation_plan import build_remediation_plan


def test_global_tech_style_remediation_projection() -> None:
    """GCB + YMM + GIB kilidi — toplam kazanç ve projected skor."""
    missing_docs = [
        {
            "doc_name": "Gümrük Çıkış Beyannamesi (GÇB)",
            "code": "GCB_MISSING",
            "reason": "İhracat dosyasında GÇB eksik.",
            "score_impact": -35,
        },
        {
            "doc_name": "YMM Raporu",
            "code": "YMM_REPORT_MISSING",
            "reason": "YMM raporu eksik.",
            "score_impact": -35,
        },
    ]
    risk_items = [
        {
            "title": "GİB e-Fatura API",
            "code": "GIB_API_EFATURA_REJECTED",
            "reason": "e-Fatura doğrulaması başarısız.",
            "score_impact": -40,
            "suggested_action": "Faturaları GİB kayıtlarıyla eşleştirin.",
            "source": "python",
        },
        {
            "title": "VKN anomali",
            "code": "INVALID_VKN",
            "reason": "VKN formatı hatalı.",
            "score_impact": -6,
            "suggested_action": "VKN alanlarını 10 haneli sayısal formata düzeltin.",
            "source": "llm",
        },
    ]
    locks = ["GIB_API_EFATURA_REJECTED", "GCB_MISSING", "YMM_REPORT_MISSING"]

    plan = build_remediation_plan(
        calculated_score=0,
        risk_items=risk_items,
        missing_docs=missing_docs,
        mandatory_lock_triggered=locks,
    )

    assert plan["current_score"] == 0
    assert plan["points_recoverable_total"] == 116
    assert plan["projected_score_if_all_resolved"] == 100
    assert plan["projected_risk_category"] == "Düşük Risk"

    steps = plan["steps"]
    assert len(steps) == 4
    lock_steps = [s for s in steps if s["is_mandatory_lock"]]
    assert len(lock_steps) == 3
    assert all(lock_steps[i]["priority"] <= lock_steps[-1]["priority"] for i in range(len(lock_steps) - 1))
    assert steps[0]["is_mandatory_lock"] is True
    assert steps[0]["points_recoverable"] >= steps[-1]["points_recoverable"]


def test_empty_items_no_recovery() -> None:
    plan = build_remediation_plan(
        calculated_score=92,
        risk_items=[],
        missing_docs=[],
        mandatory_lock_triggered=[],
    )
    assert plan["projected_score_if_all_resolved"] == 92
    assert plan["steps"] == []
    assert "korunur" in plan["headline_tr"].lower()
