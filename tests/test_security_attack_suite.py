"""Saldırı senaryoları — bypass vs verified (özet)."""

from __future__ import annotations

import os

import pytest

from app.services.document_inventory import merge_inventory_from_questions
from app.services.verification.inventory_trust_policy import normalize_canonical_document


def test_s1_canonical_fake_documents_stripped():
    """K-01: source olmadan mevcut → eksik."""
    doc = normalize_canonical_document(
        {"type": "gumruk_beyannamesi", "status": "mevcut"}
    )
    assert doc["status"] == "eksik"


def test_s2_forged_evet_without_ledger_no_inventory(monkeypatch):
    """K-03: Evet without proof → merge skips verified entry."""
    monkeypatch.setenv("REQUIRE_VERIFIED_PROOF", "true")
    inv = merge_inventory_from_questions(
        [],
        [{"question_id": "q1", "field": "has_customs_declarations", "required": True}],
        {"q1": "Evet"},
        proof_ledger=[],
    )
    assert not any(d.get("type") == "gumruk_beyannamesi" and d.get("status") == "mevcut" for d in inv)


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Graph integration — set RUN_INTEGRATION_TESTS=1",
)
def test_s5_retry_blocked_no_decision_score():
    """C-03: max retry → blocked, Decision skor üretmez."""
    from app.agents.decision_agent import decision_node

    state = {
        "analysis_status": "clarification_blocked",
        "block_reason": "PROOF_REQUIRED",
        "agent_logs": [],
    }
    out = decision_node(state)
    assert out["final_report"].get("calculated_score") is None
