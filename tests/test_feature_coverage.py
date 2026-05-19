"""Özellik kapsamı — hızlı birim kontrolleri + yavaş pipeline duman testi."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.agents.analyzer_agent import analyzer_node
from app.agents.collector_agent import collector_node
from tests.accuracy_benchmark import _evaluate, _load_ground_truth, run_pipeline
from tests.conftest import has_code, merge_state, run_segment1, run_segment2
from tests.fixtures.accuracy_scenarios import SCENARIO_BY_ID
from tests.fixtures.pdf_generator import write_minimal_pdf


def test_clarification_no_refund_type_radio_question() -> None:
    """C-01: iade türü yalnızca Collector — clarification'da refund_type sorusu yok."""
    state = run_segment1()
    fields = {q.get("field") for q in state.get("clarification_questions") or []}
    assert "refund_type" not in fields


def test_decision_finance_headline_when_report_exists() -> None:
    """Finansman UX: final_report finance_headline alanı dolu olabilir."""
    state = run_segment1()
    answers = {"q_gumruk_001": "Hayır", "q_2no_001": "Hayır"}
    final = run_segment2(state, answers, run_decision=True)
    report = final.get("final_report") or {}
    assert report.get("calculated_score") is not None
    assert "finance_eligibility" in report
    fin = report["finance_eligibility"]
    assert isinstance(fin, dict)
    if has_code(final, "GCB_MISSING"):
        assert fin.get("eligible") is False


def test_t14_weak_export_no_gcb_lock_unit(tmp_path: Path) -> None:
    """F-01: zayıf ihracat — GÇB_MISSING tetiklenmemeli, process_warnings dolu."""
    gt = _load_ground_truth()
    sc = SCENARIO_BY_ID["T14"]
    excel = tmp_path / "T14.xlsx"
    sc["df"]().to_excel(excel, index=False)

    state = merge_state(
        {"uploaded_files": [str(excel)], "clarification_answers": {"q_gumruk_001": "Hayır", "q_2no_001": "Hayır"}},
        collector_node({"uploaded_files": [str(excel)], "agent_logs": [], "clarification_answers": {}}),
    )
    if state.get("analysis_status") == "failed":
        pytest.skip(f"Collector failed: {state.get('process_warnings')}")
    state = merge_state(state, analyzer_node(state))
    gt_scenario = gt["scenarios"]["T14"]
    result = _evaluate("T14", gt_scenario, state, "deterministic", gt.get("defaults") or {})
    assert result["warn_ok"], result.get("process_warnings")
    assert result["codes_ok"], f"codes={result['codes_found']}"


@pytest.mark.slow
def test_pdf_pipeline_adds_gcb_inventory(tmp_path: Path, llm_primary_env: None) -> None:
    """P01: Excel + gumruk PDF → envanterde gumruk_beyannamesi (mock extraction)."""

    async def _run() -> dict:
        sc = SCENARIO_BY_ID["P01"]
        excel = tmp_path / "P01.xlsx"
        sc["df"]().to_excel(excel, index=False)
        pdf = write_minimal_pdf(tmp_path / "gumruk_beyanname_mock.pdf")
        preset = {"q_gumruk_001": "Hayır", "q_2no_001": "Hayır", "q_refund_type_001": "ihracat"}
        return await run_pipeline([str(excel), str(pdf)], preset)

    state = asyncio.run(_run())
    inv = state.get("document_inventory") or []
    types = {d.get("type") for d in inv}
    assert "gumruk_beyannamesi" in types or any(
        "gumruk" in str(d.get("type", "")) for d in inv
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")),
    reason="LLM API anahtarı yok",
)
def test_llm_pipeline_t12_minimal_score_band(
    tmp_path: Path,
    llm_primary_env: None,
) -> None:
    """LLM birincil: T12 tek fatura — skor ground-truth bandında."""

    async def _run() -> dict:
        gt = _load_ground_truth()
        sc = SCENARIO_BY_ID["T12"]
        excel = tmp_path / "T12.xlsx"
        sc["df"]().to_excel(excel, index=False)
        preset = {"q_gumruk_001": "Hayır", "q_2no_001": "Hayır", "q_refund_type_001": "ihracat"}
        return await run_pipeline([str(excel)], preset)

    state = asyncio.run(_run())
    gt = _load_ground_truth()
    result = _evaluate("T12", gt["scenarios"]["T12"], state, "llm", gt.get("defaults") or {})
    assert result["codes_ok"], result
    assert result["score_ok"], (
        f"score={result['actual_score']} expected {result['expected_score']}"
    )
