"""analysis_status state machine — Decision guard."""

from __future__ import annotations

from app.agents.decision_agent import decision_node


def test_decision_skips_score_when_blocked():
    state = {
        "analysis_status": "clarification_blocked",
        "block_reason": "PROOF_REQUIRED",
        "company_name": "Test A.Ş.",
        "clarification_questions": [{"question_id": "q1"}],
        "risk_items": [],
        "missing_docs": [],
        "agent_logs": [],
    }
    out = decision_node(state)
    assert out["analysis_status"] == "clarification_blocked"
    assert out["final_report"]["calculated_score"] is None
    assert out["final_report"]["finance_eligibility"]["eligible"] is False


def test_decision_skips_score_when_failed():
    state = {
        "analysis_status": "failed",
        "failure_reason": "UPLOAD_FAILED",
        "error_state": "COLLECTOR_UPLOAD_FAILED",
        "agent_logs": [],
    }
    out = decision_node(state)
    assert out["analysis_status"] == "failed"
    assert out["final_report"]["status"] == "failed"
