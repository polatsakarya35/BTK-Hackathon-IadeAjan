"""Belge sınıflandırma şeması ve envanter birleştirme birim testleri."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.models import DocumentClassification
from app.services.document_inventory import classification_to_inventory_entry, upsert_inventory


def test_document_classification_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        DocumentClassification(
            file_name="x.pdf",
            type="diger",
            confidence=1.01,
            evidence="test",
        )


def test_classification_to_inventory_high_confidence_gcb() -> None:
    dc = DocumentClassification(
        file_name="gcb.pdf",
        type="gumruk_beyannamesi",
        confidence=0.9,
        evidence="Gümrük çıkış beyannamesi başlığı görülüyor.",
    )
    entry = classification_to_inventory_entry(dc)
    assert entry is not None
    assert entry["type"] == "gumruk_beyannamesi"
    assert entry["status"] == "mevcut"
    assert entry["source"] == "llm_classifier"


def test_classification_low_confidence_returns_none() -> None:
    dc = DocumentClassification(
        file_name="x.pdf",
        type="gumruk_beyannamesi",
        confidence=0.79,
        evidence="belirsiz",
    )
    assert classification_to_inventory_entry(dc) is None


def test_document_classification_2no_type_valid() -> None:
    dc = DocumentClassification(
        file_name="2no.pdf",
        type="2no_kdv_beyannamesi",
        confidence=0.88,
        evidence="2 No'lu KDV beyannamesi başlığı.",
    )
    assert dc.type == "2no_kdv_beyannamesi"


def test_classification_diger_returns_none() -> None:
    dc = DocumentClassification(
        file_name="x.pdf",
        type="diger",
        confidence=0.99,
        evidence="fatura",
    )
    assert classification_to_inventory_entry(dc) is None


def test_upsert_updates_same_type() -> None:
    inv = [{"id": "a", "type": "gumruk_beyannamesi", "status": "eksik"}]
    new_entry = {
        "id": "b",
        "type": "gumruk_beyannamesi",
        "status": "mevcut",
        "source": "clarification",
        "note": "onay",
    }
    out = upsert_inventory(inv, new_entry)
    assert len(out) == 1
    assert out[0]["status"] == "mevcut"
    assert out[0]["source"] == "clarification"
