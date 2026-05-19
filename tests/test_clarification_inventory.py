"""Clarification → document_inventory mutasyon birim testleri."""

from __future__ import annotations

from app.services.document_inventory import merge_inventory_from_questions


def test_merge_inventory_gumruk_evet() -> None:
    questions = [{"question_id": "q_gumruk_001", "field": "has_customs_declarations"}]
    answers = {"q_gumruk_001": "Evet"}
    inv = merge_inventory_from_questions(
        [], questions, answers, require_verified_proof=False
    )
    assert len(inv) == 1
    assert inv[0]["type"] == "gumruk_beyannamesi"
    assert inv[0]["status"] == "mevcut"


def test_merge_inventory_hayir_no_append() -> None:
    questions = [{"question_id": "q_gumruk_001", "field": "has_customs_declarations"}]
    answers = {"q_gumruk_001": "Hayır"}
    inv = merge_inventory_from_questions(
        [], questions, answers, require_verified_proof=False
    )
    assert inv == []


def test_merge_inventory_2no_positive() -> None:
    questions = [{"question_id": "q_2no_001", "field": "has_2no_declaration"}]
    answers = {"q_2no_001": "Bir kısmı için verdim"}
    inv = merge_inventory_from_questions(
        [], questions, answers, require_verified_proof=False
    )
    assert len(inv) == 1
    assert inv[0]["type"] == "2no_kdv_beyannamesi"


def test_upsert_clarification_overwrites_eksik() -> None:
    base = [{"id": "x", "type": "gumruk_beyannamesi", "status": "eksik"}]
    questions = [{"question_id": "q_gumruk_001", "field": "has_customs_declarations"}]
    answers = {"q_gumruk_001": "Evet"}
    inv = merge_inventory_from_questions(
        base, questions, answers, require_verified_proof=False
    )
    assert len(inv) == 1
    assert inv[0]["status"] == "mevcut"
