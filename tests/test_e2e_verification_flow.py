"""
E2E — Proof Ledger + Zero Trust + LangGraph segment akışı.

Segment 1: Collector → Analyzer → Clarification (bekleme)
Segment 2: Cevaplar + proof_ledger → Clarification → Analyzer → Decision
"""

from __future__ import annotations

from app.agents.analyzer_agent import _inventory_entry_present, analyzer_node
from app.agents.clarification_agent import clarification_node
from app.agents.collector_agent import collector_node
from app.services.proof_ledger import get_by_question
from app.services.verification.inventory_trust_policy import is_counted_for_export

from tests.conftest import (
    CELIK_TAX_NUMBER,
    FRAUD_VKN,
    add_proof_to_state,
    build_proof_via_cross_validate,
    codes_in_items,
    default_clarification_answers,
    has_code,
    mandatory_lock_codes,
    merge_state,
    question_id_for_field,
    run_segment1,
    run_segment2,
    run_segment2_forced_bypass_clarification,
    write_canonical_upload,
)


class TestE2EScenario3ZeroTrustCanonical:
    """K-01: user_assertion ile sahte mevcut belge sayılmaz."""

    def test_user_assertion_document_not_counted_and_gcb_penalty(self, tmp_path) -> None:
        json_path = write_canonical_upload(
            tmp_path,
            documents=[
                {
                    "id": "DOC-FAKE",
                    "type": "gumruk_beyannamesi",
                    "status": "mevcut",
                    "source": "user_assertion",
                    "required": True,
                }
            ],
        )
        state = merge_state(
            {"agent_logs": [], "clarification_answers": {}},
            collector_node(
                {
                    "scenario_id": "unused",
                    "uploaded_files": [str(json_path)],
                    "agent_logs": [],
                }
            ),
        )
        assert state.get("analysis_status") == "running"

        state = merge_state(state, analyzer_node(state))

        inv = state.get("document_inventory") or []
        gcb_docs = [d for d in inv if d.get("type") == "gumruk_beyannamesi"]
        assert gcb_docs, "GÇB envanter kaydı olmalı"
        for doc in gcb_docs:
            assert doc.get("status") == "eksik" or not is_counted_for_export(doc, strict=True)

        assert has_code(state, "GCB_MISSING")
        assert state.get("clarification_needed") is True
        assert question_id_for_field(state, "has_customs_declarations")


class TestE2EScenario1HappyPath:
    """Kusursuz akış: verified GÇB → GCB_MISSING kalkar, finansman kilidi GÇB'den açılır."""

    def test_verified_gcb_clears_mandatory_lock(self) -> None:
        state = run_segment1()

        assert state.get("analysis_status") in (
            "clarification_waiting",
            "running",
        )
        assert state.get("clarification_needed") is True
        assert has_code(state, "GCB_MISSING")
        assert question_id_for_field(state, "has_customs_declarations") == "q_gumruk_001"

        record, _entry = build_proof_via_cross_validate(
            state,
            question_id="q_gumruk_001",
            field="has_customs_declarations",
            exporter_vkn=CELIK_TAX_NUMBER,
        )
        assert record.verification_status == "passed"
        add_proof_to_state(state, record)

        ledger = state.get("proof_ledger") or []
        assert ledger
        stored = get_by_question(state, "q_gumruk_001")
        assert stored is not None
        assert stored.verification_status == "passed"

        answers = default_clarification_answers(state)
        final = run_segment2(state, answers, proof_ledger=ledger)

        assert final.get("clarification_needed") is False
        assert final.get("analysis_status") == "completed"
        assert not has_code(final, "GCB_MISSING")

        inv = final.get("document_inventory") or []
        verified_gcb = [
            d
            for d in inv
            if d.get("type") == "gumruk_beyannamesi"
            and d.get("source") == "verified"
            and _inventory_entry_present(d)
        ]
        assert verified_gcb, "Verified GÇB envanterde sayılmalı"

        report = final.get("final_report") or {}
        assert report.get("calculated_score") is not None
        assert "GCB_MISSING" not in mandatory_lock_codes(final)

        fin = report.get("finance_eligibility") or {}
        locks = mandatory_lock_codes(final)
        assert "GCB_MISSING" not in locks
        if fin.get("eligible") is False:
            assert "GCB_MISSING" not in str(fin.get("reason", ""))


class TestE2EScenario2FraudDetection:
    """Sahte VKN: ledger failed, GCB_MISSING kalır, finansman kilitli."""

    def test_wrong_vkn_fails_proof_and_keeps_gcb_lock(self) -> None:
        state = run_segment1()
        assert has_code(state, "GCB_MISSING")

        record, entry = build_proof_via_cross_validate(
            state,
            question_id="q_gumruk_001",
            field="has_customs_declarations",
            exporter_vkn=FRAUD_VKN,
        )
        assert record.verification_status == "failed"
        assert entry is None
        assert any(c.code == "OWNERSHIP_VKN_MATCH" for c in record.failed_checks)
        add_proof_to_state(state, record)

        answers = default_clarification_answers(state)
        answers["q_gumruk_001"] = "Evet"

        # 2a: Clarification gate reddeder
        clar_out = merge_state(
            dict(state),
            clarification_node(
                {
                    **state,
                    "clarification_answers": answers,
                    "clarification_needed": False,
                }
            ),
        )
        assert clar_out.get("clarification_needed") is True
        assert clar_out.get("analysis_status") == "clarification_waiting"
        warnings = clar_out.get("process_warnings") or []
        assert any("Kanıt doğrulama" in w for w in warnings)

        stored = get_by_question(state, "q_gumruk_001")
        assert stored is not None
        assert stored.verification_status == "failed"

        # 2b: Sahte resume (clarification merge atlanmış) — Zero Trust
        attacked = run_segment2_forced_bypass_clarification(state, answers)
        assert has_code(attacked, "GCB_MISSING")
        assert attacked.get("analysis_status") == "completed"

        report = attacked.get("final_report") or {}
        fin = report.get("finance_eligibility") or {}
        assert fin.get("eligible") is False
        assert "GCB_MISSING" in mandatory_lock_codes(attacked) or has_code(
            attacked, "GCB_MISSING"
        )

        verified = [
            d
            for d in (attacked.get("document_inventory") or [])
            if d.get("type") == "gumruk_beyannamesi" and d.get("source") == "verified"
        ]
        assert not verified
