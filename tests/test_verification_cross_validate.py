"""Çapraz doğrulama ve VKN checksum testleri."""

from __future__ import annotations

from app.schemas.verification import DocumentExtraction
from app.services.verification.cross_validate import (
    cross_validate_document,
    validate_vkn_checksum,
    validate_vkn_format,
)


def test_vkn_format():
    assert validate_vkn_format("1234567890")
    assert not validate_vkn_format("123")


def test_checksum_warning_only():
    extraction = DocumentExtraction(exporter_vkn="1111111111")
    state = {"tax_number": "1234567890", "date_range": {}}
    result = cross_validate_document(
        extraction,
        state=state,
        expected_type="gumruk_beyannamesi",
    )
    warn_codes = [w.code for w in result.warnings]
    if not validate_vkn_checksum("1111111111"):
        assert "VKN_CHECKSUM_WARN" in warn_codes


def test_gcb_amount_missing_warning_not_fail(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "false")
    extraction = DocumentExtraction(
        exporter_vkn="1234567890",
        declaration_no="DECL-1",
        amount=None,
    )
    state = {"tax_number": "1234567890", "date_range": {}}
    result = cross_validate_document(
        extraction,
        state=state,
        expected_type="gumruk_beyannamesi",
    )
    assert result.status == "passed"
    assert any(w.code == "GCB_AMOUNT_MISSING" for w in result.warnings)


def test_gib_api_verified(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "false")
    extraction = DocumentExtraction(
        exporter_vkn="1234567890",
        declaration_no="DECL-1",
        amount=50_000.0,
    )
    state = {"tax_number": "1234567890", "date_range": {}}
    result = cross_validate_document(
        extraction,
        state=state,
        expected_type="gumruk_beyannamesi",
    )
    assert result.status == "passed"
    assert any(w.code == "GIB_API_VERIFIED" for w in result.warnings)


def test_gib_api_gcb_rejected(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "false")
    extraction = DocumentExtraction(
        exporter_vkn="1234567890",
        declaration_no="SAHTE123",
        amount=50_000.0,
    )
    state = {"tax_number": "1234567890", "date_range": {}}
    result = cross_validate_document(
        extraction,
        state=state,
        expected_type="gumruk_beyannamesi",
    )
    assert result.status == "failed"
    assert any(c.code == "GIB_API_GCB_REJECTED" for c in result.checks)


def test_ownership_mismatch_fails():
    extraction = DocumentExtraction(exporter_vkn="9999999999")
    state = {"tax_number": "1234567890", "date_range": {}}
    result = cross_validate_document(
        extraction,
        state=state,
        expected_type="gumruk_beyannamesi",
    )
    assert result.status == "failed"
    assert any(c.code == "OWNERSHIP_VKN_MATCH" for c in result.checks)
