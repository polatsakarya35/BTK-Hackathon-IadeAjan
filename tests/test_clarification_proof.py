"""Clarification kanıt doğrulama yardımcıları — birim testleri."""

from __future__ import annotations

from app.schemas.models import DocumentClassification
from app.services.clarification_proof import (
    expected_type_for_field,
    is_proof_document_field,
    verify_classification,
)


def test_expected_type_gumruk() -> None:
    assert expected_type_for_field("has_customs_declarations") == "gumruk_beyannamesi"


def test_expected_type_ymm() -> None:
    assert expected_type_for_field("has_ymm_contract") == "ymm_tasdik_raporu"


def test_expected_type_2no() -> None:
    assert expected_type_for_field("has_2no_declaration") == "2no_kdv_beyannamesi"


def test_is_proof_document_field() -> None:
    assert is_proof_document_field("has_customs_declarations")
    assert not is_proof_document_field("refund_type")


def test_verify_classification_match() -> None:
    dc = DocumentClassification(
        file_name="gcb.pdf",
        type="gumruk_beyannamesi",
        confidence=0.9,
        evidence="GÇB",
    )
    assert verify_classification(dc, "gumruk_beyannamesi")


def test_verify_classification_low_confidence() -> None:
    dc = DocumentClassification(
        file_name="gcb.pdf",
        type="gumruk_beyannamesi",
        confidence=0.79,
        evidence="belirsiz",
    )
    assert not verify_classification(dc, "gumruk_beyannamesi")


def test_verify_classification_wrong_type() -> None:
    dc = DocumentClassification(
        file_name="ymm.pdf",
        type="ymm_tasdik_raporu",
        confidence=0.95,
        evidence="YMM",
    )
    assert not verify_classification(dc, "gumruk_beyannamesi")


def test_verify_classification_none() -> None:
    assert not verify_classification(None, "gumruk_beyannamesi")
