"""Envanter güven politikası testleri."""

from app.services.verification.inventory_trust_policy import (
    is_counted_for_export,
    normalize_canonical_document,
)


def test_unverified_canonical_mevcut_becomes_eksik():
    doc = {"type": "gumruk_beyannamesi", "status": "mevcut"}
    out = normalize_canonical_document(doc)
    assert out["status"] == "eksik"


def test_verified_source_counted():
    doc = {"type": "gumruk_beyannamesi", "status": "mevcut", "source": "verified"}
    assert is_counted_for_export(doc, strict=True)


def test_scenario_source_counted():
    doc = {"type": "gumruk_beyannamesi", "status": "mevcut", "source": "scenario"}
    assert is_counted_for_export(doc, strict=True)
