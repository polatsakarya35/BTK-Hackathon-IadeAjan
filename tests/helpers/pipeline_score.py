"""Yüklenen xlsx için Collector → Analyzer → Decision skor çıktısı."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.collector_agent import collector_node
from app.agents.decision_agent import decision_node
from app.agents.analyzer_agent import analyzer_node
from app.agents.clarification_agent import clarification_node


def _default_answers(state: dict[str, Any]) -> dict[str, str]:
    """Sentetik Satış/Alış listesi için uyumlu clarification cevapları."""
    answers: dict[str, str] = {}
    for q in state.get("clarification_questions") or []:
        qid = q.get("question_id", "")
        field = q.get("field", "")
        if field == "refund_type":
            answers[qid] = "satis"
        elif field == "has_customs_declarations":
            answers[qid] = "Hayır"
        elif field == "has_2no_declaration":
            answers[qid] = "Hayır"
    if not any(
        q.get("field") == "refund_type"
        for q in state.get("clarification_questions") or []
    ):
        answers.setdefault("q_refund_type_001", "satis")
    return answers


def run_upload_to_final_score(path: str | Path) -> dict[str, Any]:
    """
    Upload dosyasından nihai rapora kadar node zinciri (graph.invoke yerine, test hızı).
    Döner: son state; final_report.calculated_score içerir.
    """
    file_path = str(path)
    state: dict[str, Any] = {
        "uploaded_files": [file_path],
        "agent_logs": [],
        "clarification_answers": {},
    }

    state = {**state, **collector_node(state)}
    if state.get("analysis_status") == "failed":
        return state

    state = {**state, **analyzer_node(state)}
    if state.get("analysis_status") in ("failed", "clarification_blocked"):
        return state

    if state.get("clarification_needed") is True:
        answers = _default_answers(state)
        state = {
            **state,
            **clarification_node(
                {**state, "clarification_answers": answers}
            ),
        }
        if state.get("clarification_needed") is True:
            return state
        state = {**state, **analyzer_node(state)}

    if state.get("clarification_needed") is True:
        return state

    state = {**state, **decision_node(state)}
    return state


def extract_score_report(state: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    report = state.get("final_report") or {}
    score = report.get("calculated_score")
    if score is not None:
        score = int(score)
    return score, report
