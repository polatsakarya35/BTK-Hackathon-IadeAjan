"""GİB API gateway — mock kuralları ve canlı mod yapılandırması."""

from __future__ import annotations

import os

import pytest

from app.services.integrations.gib_api_client import (
    verify_efatura_with_gib,
    verify_gcb_with_gib,
)


@pytest.fixture(autouse=True)
def mock_api_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "false")


def test_gcb_mock_pass():
    r = verify_gcb_with_gib("1234567890", "DECL-OK", 50_000.0)
    assert r.verified is True
    assert r.source == "gib_mock"


def test_gcb_mock_reject_sahte_declaration():
    r = verify_gcb_with_gib("1234567890", "SAHTE123")
    assert r.verified is False
    assert r.rejection_code == "GIB_API_GCB_REJECTED"


def test_gcb_mock_reject_fraud_vkn():
    r = verify_gcb_with_gib("9999999999", "DECL-1")
    assert r.verified is False


def test_efatura_mock_pass():
    r = verify_efatura_with_gib("1234567890", "FAT-001", "2025-03-01", 1000.0)
    assert r.verified is True


def test_efatura_mock_reject_sahte_id():
    r = verify_efatura_with_gib("1234567890", "SAHTE-INV-001")
    assert r.verified is False
    assert r.rejection_code == "GIB_API_EFATURA_REJECTED"


def test_live_mode_without_url_soft_fail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "true")
    monkeypatch.delenv("GIB_API_URL", raising=False)
    r = verify_gcb_with_gib("1234567890", "DECL-1")
    assert r.verified is False
    assert r.source == "gib_live_unconfigured"


def test_live_mode_with_url_raises_not_implemented(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "true")
    monkeypatch.setenv("GIB_API_URL", "https://api.gib.example/v1")
    with pytest.raises(NotImplementedError):
        verify_gcb_with_gib("1234567890", "DECL-1")
