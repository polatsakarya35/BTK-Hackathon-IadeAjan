"""LangGraph mock — Streamlit UI testleri için anında sahte stream chunk'ları."""

from __future__ import annotations

from typing import Any, Iterator

CELIK_TAX_NUMBER = "1234567890"

CLARIFICATION_QUESTIONS: list[dict[str, Any]] = [
    {
        "question_id": "q_gumruk_001",
        "question_text": "İhracat işlemlerinize ait Gümrük Çıkış Beyannamesi (GÇB) mevcut mu?",
        "field": "has_customs_declarations",
        "options": ["Evet", "Hayır", "Bilmiyorum"],
        "required": True,
    },
]

_BASE_INVOICES: list[dict[str, Any]] = [
    {
        "id": "FAT-UI-001",
        "type": "ihracat",
        "date": "2025-03-01",
        "amount": 60_000.0,
        "kdv_rate": 20,
        "supplier_id": "SUP-UI",
        "is_export": True,
    },
]

_COMPANY_PROFILE: dict[str, Any] = {
    "company_id": "ui_test_co",
    "company_name": "UI Test İhracat A.Ş.",
    "tax_number": CELIK_TAX_NUMBER,
    "estimated_refund_amount": 120_000.0,
    "analysis_period": {"start": "2025-01-01", "end": "2025-03-31"},
    "refund_type": "ihracat",
}

_FINAL_REPORT: dict[str, Any] = {
    "company_name": "UI Test İhracat A.Ş.",
    "tax_number": CELIK_TAX_NUMBER,
    "refund_type": "ihracat",
    "calculated_score": 72,
    "risk_category": "Orta Risk",
    "approval_status": "conditional_approval",
    "total_deduction": 28,
    "total_risk_penalty": 0,
    "doc_penalty": 28,
    "clarification_bonus": 0,
    "finance_eligibility": {
        "eligible": False,
        "estimated_amount": 120_000.0,
        "reason": "Finansman kilidi: zorunlu mevzuat maddeleri tespit edildi: GCB_MISSING.",
        "headline": "",
        "mandatory_lock_triggered": ["GCB_MISSING"],
    },
    "mandatory_lock_triggered": ["GCB_MISSING"],
    "risk_items": [],
    "missing_docs": [],
    "score_breakdown": {
        "base_score": 100,
        "calculated_score": 72,
        "items_by_code": {},
        "llm_used": False,
    },
    "decision_summary": "UI test mock karar özeti.",
}


def _log(agent: str, msg: str) -> str:
    return f"[2025-01-01T00:00:00Z] [{agent}] {msg}"


class FakeCompiledGraph:
    """build_graph() yerine geçer; stream() senaryoya göre chunk üretir."""

    def __init__(self, scenario: str = "segment1_clarification") -> None:
        self.scenario = scenario
        self._segment2_mode = scenario == "segment2_done"

    def set_scenario(self, scenario: str) -> None:
        self.scenario = scenario
        self._segment2_mode = scenario == "segment2_done"

    def stream(
        self,
        state: dict[str, Any],
        stream_mode: str = "updates",
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        del stream_mode, kwargs
        if self.scenario == "segment1_error":
            yield from self._stream_segment1_error()
            return
        if self._segment2_mode or self.scenario == "segment2_done":
            yield from self._stream_segment2_done(state)
            return
        if self.scenario == "segment2_blocked":
            yield from self._stream_segment2_blocked()
            return
        yield from self._stream_segment1_clarification()

    def _stream_segment1_clarification(self) -> Iterator[dict[str, Any]]:
        yield {
            "CollectorAgent": {
                "analysis_status": "running",
                "company_profile": dict(_COMPANY_PROFILE),
                "normalized_invoices": list(_BASE_INVOICES),
                "normalized_suppliers": [],
                "document_inventory": [],
                "classification_result": {
                    "refund_type": "ihracat",
                    "detection_confidence": 0.95,
                },
                "tax_number": CELIK_TAX_NUMBER,
                "company_name": _COMPANY_PROFILE["company_name"],
                "agent_logs": [_log("CollectorAgent", "Mock collector tamamlandı")],
            }
        }
        yield {
            "AnalyzerAgent": {
                "clarification_needed": True,
                "clarification_questions": list(CLARIFICATION_QUESTIONS),
                "clarification_message": (
                    "Analizinizin tamamlanabilmesi için gümrük beyanı bilgisine ihtiyaç duyulmaktadır."
                ),
                "analysis_status": "clarification_waiting",
                "risk_items": [],
                "missing_docs": [
                    {
                        "code": "GCB_MISSING",
                        "doc_name": "Gümrük Çıkış Beyannamesi",
                        "score_impact": -35,
                    }
                ],
                "agent_logs": [_log("AnalyzerAgent", "Mock analyzer — clarification gerekli")],
            }
        }
        yield {
            "ClarificationAgent": {
                "clarification_needed": True,
                "clarification_message": (
                    "Analizinizin tamamlanabilmesi için gümrük beyanı bilgisine ihtiyaç duyulmaktadır."
                ),
                "agent_logs": [_log("ClarificationAgent", "Mock clarification — bekleme")],
            }
        }

    def _stream_segment1_error(self) -> Iterator[dict[str, Any]]:
        yield {
            "CollectorAgent": {
                "analysis_status": "failed",
                "error_state": "MOCK_COLLECTOR_FAILED",
                "agent_logs": [_log("CollectorAgent", "Mock hata")],
            }
        }

    def _stream_segment2_done(self, state: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield {
            "AnalyzerAgent": {
                "clarification_needed": False,
                "analysis_status": "running",
                "agent_logs": [_log("AnalyzerAgent", "Mock analyzer segment 2")],
            }
        }
        report = dict(_FINAL_REPORT)
        yield {
            "DecisionAgent": {
                "analysis_status": "completed",
                "final_report": report,
                "risk_items": report.get("risk_items") or [],
                "missing_docs": report.get("missing_docs") or [],
                "agent_logs": [_log("DecisionAgent", "Mock decision tamamlandı")],
            }
        }

    def _stream_segment2_blocked(self) -> Iterator[dict[str, Any]]:
        yield {
            "AnalyzerAgent": {
                "analysis_status": "clarification_blocked",
                "clarification_needed": True,
                "clarification_blocked": True,
                "agent_logs": [_log("AnalyzerAgent", "Mock blocked")],
            }
        }


def make_fake_graph(scenario: str = "segment1_clarification") -> FakeCompiledGraph:
    return FakeCompiledGraph(scenario)
