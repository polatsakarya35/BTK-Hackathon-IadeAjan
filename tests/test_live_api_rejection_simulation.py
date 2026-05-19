"""
Sahtekarlık Avı — GİB / e-Fatura API mock red E2E simülasyonu.

Segment 1: Collector → Analyzer → Clarification (bekleme)
Segment 2: Kanıt defteri + cevaplar → Analyzer (yeniden) → Decision

Mock gateway (ENABLE_REAL_GIB_API=false); Excel'de SAHTE-INV, GÇB'de SAHTE123.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.schemas.penalty_codes import PenaltyCode, compute_code_penalty
from app.schemas.verification import (
    DocumentClassificationSnapshot,
    DocumentExtraction,
    ProofRecord,
)
from app.services.proof_ledger import get_by_question
from app.services.verification import document_extraction
from app.services.verification.cross_validate import cross_validate_document

from tests.conftest import (
    CELIK_TAX_NUMBER,
    add_proof_to_state,
    default_clarification_answers,
    has_code,
    mandatory_lock_codes,
    normalize_code,
    run_segment1,
    run_segment2,
)

# Minimal geçerli PDF (1 sayfa)
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
)


def write_fraud_excel(tmp_path: Path) -> Path:
    """İhracat faturaları: biri geçerli, biri SAHTE-INV (e-Fatura API red)."""
    rows = [
        {
            "Fatura No": "FAT-OK-001",
            "Tarih": "2025-03-01",
            "Tutar": 60_000.0,
            "Evrak Türü": "İhracat",
            "KDV Oranı": 20,
            "KDV Tutar": 12_000.0,
            "Karşı Taraf Ünvanı": "Çelik İhracat A.Ş.",
            "Firma VKN Numarası": CELIK_TAX_NUMBER,
        },
        {
            "Fatura No": "SAHTE-INV",
            "Tarih": "2025-03-15",
            "Tutar": 55_000.0,
            "Evrak Türü": "İhracat",
            "KDV Oranı": 20,
            "KDV Tutar": 11_000.0,
            "Karşı Taraf Ünvanı": "Çelik İhracat A.Ş.",
            "Firma VKN Numarası": CELIK_TAX_NUMBER,
        },
    ]
    path = tmp_path / "sahtekarlik_avi.xlsx"
    pd.DataFrame(rows).to_excel(path, index=False, engine="openpyxl")
    return path


def write_minimal_gcb_pdf(tmp_path: Path) -> Path:
    """Dosya adı gumruk + sahte123 → sınıflandırma + mock extraction tetikleyici."""
    path = tmp_path / "gumruk_sahte123.pdf"
    path.write_bytes(_MINIMAL_PDF)
    return path


def _mock_extraction_sahte_factory(
    original: Any,
) -> Any:
    """Orijinal _mock_extraction'ı sarmalar — SAHTE123 dosya adı tetikleyicisi."""

    def _wrapper(file_path: str, doc_type: str) -> DocumentExtraction:
        base = original(file_path, doc_type)
        if "sahte123" in os.path.basename(file_path).lower():
            return base.model_copy(
                update={
                    "declaration_no": "SAHTE123",
                    "exporter_vkn": CELIK_TAX_NUMBER,
                    "amount": 100_000.0,
                    "declaration_date": "2025-06-15",
                }
            )
        return base

    return _wrapper


def build_proof_sahte_gcb(
    state: dict[str, Any],
    *,
    file_path: str,
) -> ProofRecord:
    """UI belge yüklemesi simülasyonu — SAHTE123 beyan, cross_validate failed."""
    expected = "gumruk_beyannamesi"
    record = ProofRecord.from_upload(
        session_id=str(state.get("company_id") or "sahtekarlik_avi"),
        question_id="q_gumruk_001",
        field="has_customs_declarations",
        file_path=file_path,
        expected_type=expected,
    )
    record.classification = DocumentClassificationSnapshot(
        type=expected,
        confidence=0.95,
        method="llm",
        file_name=Path(file_path).name,
        evidence="Sahtekarlık Avı E2E — sahte GÇB",
    )
    record.extraction = DocumentExtraction(
        exporter_vkn=CELIK_TAX_NUMBER,
        declaration_date="2025-06-15",
        declaration_no="SAHTE123",
        amount=100_000.0,
        currency="TRY",
    )
    result = cross_validate_document(
        record.extraction,
        state=state,
        expected_type=expected,
    )
    record.failed_checks = list(result.checks)
    record.verification_status = result.status
    assert result.status == "failed"
    assert any(c.code == "GIB_API_GCB_REJECTED" for c in result.checks)
    return record


def _risk_impact(state: dict[str, Any], code: str) -> int | None:
    for item in state.get("risk_items") or []:
        if normalize_code(item.get("code")) == code:
            raw = item.get("score_impact")
            return int(raw) if raw is not None else None
    return None


def _print_fraud_hunt_report(state: dict[str, Any]) -> None:
    report = state.get("final_report") or {}
    fin = report.get("finance_eligibility") or {}
    locks = mandatory_lock_codes(state)
    risks = state.get("risk_items") or []

    print("\n" + "=" * 60)
    print("  SAHTEKARLIK AVI — E2E API RED RAPORU")
    print("=" * 60)
    print(f"  Nihai skor          : {report.get('calculated_score')}/100")
    print(f"  Risk kategorisi     : {report.get('risk_category')}")
    print(f"  Finansman uygun     : {fin.get('eligible')}")
    print(f"  Zorunlu kilitler    : {locks}")
    print(f"  Kilit nedeni        : {(fin.get('reason') or '')[:120]}")
    print("  Risk kalemleri (API):")
    for r in risks:
        c = normalize_code(r.get("code"))
        if c.startswith("GIB_API_"):
            print(f"    - {c}: {r.get('score_impact')} | {str(r.get('reason', ''))[:70]}")
    stored = get_by_question(state, "q_gumruk_001")
    if stored:
        failed = [c.code for c in stored.failed_checks]
        print(f"  Proof ledger GÇB  : status={stored.verification_status}, checks={failed}")
    print("=" * 60 + "\n")


@pytest.fixture
def api_mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "false")
    original = document_extraction._mock_extraction
    monkeypatch.setattr(
        document_extraction,
        "_mock_extraction",
        _mock_extraction_sahte_factory(original),
    )


class TestSahtekarlikAviApiRejection:
    """Sahte fatura + sahte GÇB → zorunlu kilit cezaları + finansman RED."""

    def test_live_api_rejection_simulation_e2e(
        self,
        tmp_path: Path,
        api_mock_env: None,
    ) -> None:
        excel_path = write_fraud_excel(tmp_path)
        pdf_path = write_minimal_gcb_pdf(tmp_path)

        # ── Segment 1 ─────────────────────────────────────────────────────
        state = run_segment1(
            {
                "uploaded_files": [str(excel_path), str(pdf_path)],
                "agent_logs": [],
                "clarification_answers": {},
            }
        )

        assert state.get("analysis_status") in (
            "clarification_waiting",
            "running",
        ), f"Beklenmeyen durum: {state.get('analysis_status')}"

        assert has_code(state, "GIB_API_EFATURA_REJECTED"), (
            "Segment 1: Analyzer e-Fatura API sahte faturayı yakalamalı"
        )
        assert str(state.get("tax_number") or "").replace(" ", "") == CELIK_TAX_NUMBER

        # Collector PDF ingest tax_number enrich öncesi çalışır; GÇB API red
        # Segment 2 öncesi proof_ledger ile doğrulanır (Zero Trust UI akışı).
        ef_impact_s1 = _risk_impact(state, "GIB_API_EFATURA_REJECTED")
        assert ef_impact_s1 is not None
        assert abs(ef_impact_s1) >= 40

        # ── Proof ledger (UI yükleme simülasyonu) ─────────────────────────
        sahte_record = build_proof_sahte_gcb(
            state,
            file_path=str(pdf_path),
        )
        add_proof_to_state(state, sahte_record)

        # ── Segment 2 ─────────────────────────────────────────────────────
        answers = default_clarification_answers(state)
        answers["q_gumruk_001"] = "Evet"
        answers["q_refund_type_001"] = answers.get("q_refund_type_001") or "ihracat"

        final = run_segment2(state, answers, proof_ledger=state.get("proof_ledger"))

        assert final.get("analysis_status") == "completed"
        assert has_code(final, "GIB_API_EFATURA_REJECTED")
        assert has_code(final, "GIB_API_GCB_REJECTED")

        gcb_impact = _risk_impact(final, "GIB_API_GCB_REJECTED")
        ef_impact = _risk_impact(final, "GIB_API_EFATURA_REJECTED")
        assert gcb_impact is not None and abs(gcb_impact) >= 50
        assert ef_impact is not None and abs(ef_impact) >= 40
        assert gcb_impact == compute_code_penalty(PenaltyCode.GIB_API_GCB_REJECTED)
        assert ef_impact == compute_code_penalty(
            PenaltyCode.GIB_API_EFATURA_REJECTED, count=1
        )

        stored = get_by_question(final, "q_gumruk_001")
        assert stored is not None
        assert stored.verification_status == "failed"
        assert any(c.code == "GIB_API_GCB_REJECTED" for c in stored.failed_checks)

        report = final.get("final_report") or {}
        fin = report.get("finance_eligibility") or {}
        assert fin.get("eligible") is False

        locks = mandatory_lock_codes(final)
        assert "GIB_API_GCB_REJECTED" in locks
        assert "GIB_API_EFATURA_REJECTED" in locks

        lock_blob = " ".join(
            [
                str(fin.get("reason") or ""),
                str(fin.get("mandatory_lock_triggered") or ""),
            ]
        )
        assert "GIB_API_GCB_REJECTED" in lock_blob or "GIB_API_GCB_REJECTED" in locks
        assert "GIB_API_EFATURA_REJECTED" in lock_blob or "GIB_API_EFATURA_REJECTED" in locks

        _print_fraud_hunt_report(final)
