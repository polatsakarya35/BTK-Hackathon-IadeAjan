"""Clarification belge kanıtı — alan→tip eşlemesi ve sınıflandırma doğrulaması."""

from __future__ import annotations

from app.schemas.models import DocumentClassification

CONFIDENCE_THRESHOLD = 0.8

PROOF_DOCUMENT_FIELDS = frozenset({
    "has_customs_declarations",
    "has_ymm_contract",
    "has_2no_declaration",
})

_FIELD_TO_TYPE: dict[str, str] = {
    "has_customs_declarations": "gumruk_beyannamesi",
    "has_ymm_contract": "ymm_tasdik_raporu",
    "has_2no_declaration": "2no_kdv_beyannamesi",
}


def is_proof_document_field(field: str) -> bool:
    return field in PROOF_DOCUMENT_FIELDS


def expected_type_for_field(field: str) -> str | None:
    return _FIELD_TO_TYPE.get(field)


def verify_classification(
    result: DocumentClassification | None,
    expected_type: str,
) -> bool:
    if result is None:
        return False
    if result.type != expected_type:
        return False
    return result.confidence >= CONFIDENCE_THRESHOLD
