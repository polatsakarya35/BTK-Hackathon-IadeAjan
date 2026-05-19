"""Analyzer — e-Fatura API kuralları."""

from __future__ import annotations

from app.agents.analyzer_agent import _apply_efatura_api_rules
from app.schemas.penalty_codes import PenaltyCode


def test_apply_efatura_api_rules_rejects_sahte_invoice(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "false")
    state: dict = {"tax_number": "1234567890"}
    invoices = [
        {"id": "FAT-OK", "date": "2025-03-01", "amount": 1000},
        {"id": "SAHTE-INV", "date": "2025-03-02", "amount": 2000},
    ]
    risk_items: list = []
    warnings: list = []
    seen: set = set()
    count = _apply_efatura_api_rules(
        state,
        invoices,
        risk_items,
        warnings,
        seen,
        {"tax_number": "1234567890"},
    )
    assert count == 1
    codes = {r.get("code") for r in risk_items}
    assert PenaltyCode.GIB_API_EFATURA_REJECTED.value in codes or "GIB_API_EFATURA_REJECTED" in str(codes)
