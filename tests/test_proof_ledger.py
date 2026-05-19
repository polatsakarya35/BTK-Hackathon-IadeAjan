"""Proof ledger ve retention testleri."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.verification import ProofRecord, RetentionPolicy, mask_vkn
from app.services.proof_ledger import (
    append_record,
    get_by_question,
    purge_expired,
    validate_clarification_answers,
)


def test_mask_vkn():
    assert mask_vkn("1234567890") == "12******90"


def test_proof_record_redacted():
    rec = ProofRecord(
        question_id="q1",
        file_sha256="a" * 64,
        verification_status="passed",
        expected_type="gumruk_beyannamesi",
    )
    view = rec.to_redacted()
    assert view.file_sha256_prefix == "aaaaaaaa"
    assert len(view.file_sha256_prefix) == 8


def test_validate_answers_rejects_without_ledger():
    state = {"proof_ledger": []}
    questions = [
        {
            "question_id": "q_gumruk_001",
            "field": "has_customs_declarations",
            "required": True,
        }
    ]
    answers = {"q_gumruk_001": "Evet"}
    ok, errors, _ = validate_clarification_answers(state, questions, answers)
    assert not ok
    assert any("kanıt" in e.lower() for e in errors)


def test_validate_answers_accepts_passed_ledger():
    now = datetime.now(timezone.utc)
    rec = ProofRecord(
        question_id="q_gumruk_001",
        field="has_customs_declarations",
        expected_type="gumruk_beyannamesi",
        verification_status="passed",
        expires_at=now + timedelta(hours=1),
        file_sha256="abc",
    )
    state = {"proof_ledger": [rec.model_dump(mode="json")]}
    questions = [
        {
            "question_id": "q_gumruk_001",
            "field": "has_customs_declarations",
            "required": True,
        }
    ]
    answers = {"q_gumruk_001": "Evet"}
    ok, errors, _ = validate_clarification_answers(state, questions, answers)
    assert ok
    assert not errors


def test_purge_expired_removes_record():
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    rec = ProofRecord(
        question_id="q_old",
        expires_at=past,
        verification_status="passed",
    )
    state = {"proof_ledger": [rec.model_dump(mode="json")]}
    ledger, _ = purge_expired(state)
    assert ledger == []


def test_append_record():
    state: dict = {"proof_ledger": []}
    rec = ProofRecord(question_id="q1", verification_status="passed")
    ledger = append_record(state, rec)
    assert len(ledger) == 1
    assert get_by_question({"proof_ledger": ledger}, "q1") is not None
