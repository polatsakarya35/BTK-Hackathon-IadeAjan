"""Paylaşımlı pytest fixture'ları — E2E doğrulama akışı."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agents.analyzer_agent import analyzer_node
from app.agents.clarification_agent import clarification_node
from app.agents.collector_agent import collector_node
from app.agents.decision_agent import decision_node
from app.schemas.verification import (
    DocumentClassificationSnapshot,
    DocumentExtraction,
    ProofRecord,
)
from app.services.proof_ledger import append_record, ledger_to_state
from app.services.verification.cross_validate import cross_validate_document

CELIK_TAX_NUMBER = "1234567890"
# Ownership mismatch test VKN — 9999999999 GİB API mock red kuralı ile çakışmasın diye 888…
FRAUD_VKN = "8888888888"


@pytest.fixture(autouse=True)
def e2e_strict_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero Trust E2E ortamı — LLM kapalı, sıkı kanıt."""
    if request.node.get_closest_marker("slow") or request.node.get_closest_marker("ui"):
        return
    monkeypatch.setenv("STRICT_PROOF", "true")
    monkeypatch.setenv("REQUIRE_VERIFIED_PROOF", "true")
    monkeypatch.setenv("STRICT_INVENTORY_TRUST", "true")
    monkeypatch.setenv("ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setenv("VERIFICATION_AUDIT_ENABLED", "false")
    monkeypatch.setenv("DOCUMENT_CLASSIFIER_ENABLED", "false")
    monkeypatch.setenv("MOCK_DOCUMENT_EXTRACTION", "true")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def llm_primary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doğruluk / LLM birincil benchmark — API anahtarı korunur."""
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    monkeypatch.setenv("LLM_ANOMALY_ENABLED", "true")
    monkeypatch.setenv("MOCK_DOCUMENT_EXTRACTION", "true")
    monkeypatch.setenv("DOCUMENT_CLASSIFIER_ENABLED", "false")
    monkeypatch.setenv("STRICT_PROOF", "false")
    monkeypatch.setenv("REQUIRE_VERIFIED_PROOF", "false")
    monkeypatch.setenv("STRICT_INVENTORY_TRUST", "false")
    monkeypatch.setenv("ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setenv("VERIFICATION_AUDIT_ENABLED", "false")


def merge_state(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in patch.items():
        if key == "agent_logs" and isinstance(val, list) and isinstance(out.get(key), list):
            out[key] = [*out["agent_logs"], *val]
        else:
            out[key] = val
    return out


def normalize_code(raw: Any) -> str:
    if raw is None:
        return ""
    if hasattr(raw, "value"):
        return str(raw.value)
    s = str(raw)
    return s.split(".")[-1] if "." in s else s


def codes_in_items(items: list[dict[str, Any]]) -> set[str]:
    return {normalize_code(i.get("code")) for i in items if i.get("code")}


def has_code(state: dict[str, Any], code: str) -> bool:
    missing = codes_in_items(list(state.get("missing_docs") or []))
    risks = codes_in_items(list(state.get("risk_items") or []))
    return code in missing or code in risks


def question_id_for_field(state: dict[str, Any], field: str) -> str | None:
    for q in state.get("clarification_questions") or []:
        if q.get("field") == field:
            return q.get("question_id")
    return None


def default_clarification_answers(state: dict[str, Any]) -> dict[str, str]:
    """Segment 2 için dinamik cevap seti."""
    answers: dict[str, str] = {}
    q_gumruk = question_id_for_field(state, "has_customs_declarations")
    if q_gumruk:
        answers[q_gumruk] = "Evet"
    q_refund = question_id_for_field(state, "refund_type")
    if q_refund:
        answers[q_refund] = "ihracat"
    q_2no = question_id_for_field(state, "has_2no_declaration")
    if q_2no:
        answers[q_2no] = "Hayır"
    return answers


def run_segment1(initial: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collector → Analyzer → Clarification (üretim, cevapsız)."""
    state: dict[str, Any] = {
        "scenario_id": "celik_as_high",
        "agent_logs": [],
        "clarification_answers": {},
        "proof_ledger": [],
        **(initial or {}),
    }
    state = merge_state(state, collector_node(state))
    if state.get("analysis_status") == "failed":
        return state
    state = merge_state(state, analyzer_node(state))
    state = merge_state(state, clarification_node(state))
    return state


def run_segment2(
    state: dict[str, Any],
    answers: dict[str, Any],
    proof_ledger: list[dict[str, Any]] | None = None,
    *,
    run_decision: bool = True,
) -> dict[str, Any]:
    """Clarification işleme → Analyzer → (opsiyonel) Decision."""
    resume = dict(state)
    resume["clarification_answers"] = dict(answers)
    if proof_ledger is not None:
        resume["proof_ledger"] = proof_ledger
    resume["clarification_needed"] = False
    resume["clarification_message"] = ""

    resume = merge_state(resume, clarification_node(resume))
    if resume.get("clarification_needed") is True:
        return resume

    resume = merge_state(resume, analyzer_node(resume))
    if resume.get("clarification_needed") is True:
        return resume
    if resume.get("analysis_status") == "clarification_blocked":
        return resume

    if run_decision and resume.get("clarification_needed") is False:
        resume = merge_state(resume, decision_node(resume))
    return resume


def run_segment2_forced_bypass_clarification(
    state: dict[str, Any],
    answers: dict[str, Any],
) -> dict[str, Any]:
    """Sahte resume: cevaplar var, clarification merge atlanmış (S2b saldırı)."""
    resume = dict(state)
    resume["clarification_answers"] = dict(answers)
    resume["clarification_needed"] = False
    resume = merge_state(resume, analyzer_node(resume))
    resume = merge_state(resume, decision_node(resume))
    return resume


def build_proof_via_cross_validate(
    state: dict[str, Any],
    *,
    question_id: str = "q_gumruk_001",
    field: str = "has_customs_declarations",
    exporter_vkn: str | None = None,
    file_path: str = "/tmp/e2e_mock_gcb.pdf",
) -> tuple[ProofRecord, dict[str, Any] | None]:
    """Gerçek cross_validate ile ProofRecord üretir (ingest mock yerine)."""
    tax = str(state.get("tax_number") or CELIK_TAX_NUMBER)
    vkn = exporter_vkn if exporter_vkn is not None else tax
    expected = "gumruk_beyannamesi"

    record = ProofRecord.from_upload(
        session_id=state.get("company_id", "e2e"),
        question_id=question_id,
        field=field,
        file_path=file_path,
        expected_type=expected,
    )
    record.classification = DocumentClassificationSnapshot(
        type=expected,
        confidence=0.95,
        method="llm",
        file_name="e2e_gcb.pdf",
        evidence="E2E test belgesi",
    )
    record.extraction = DocumentExtraction(
        exporter_vkn=vkn,
        declaration_date="2025-06-15",
        declaration_no="E2E-DECL-001",
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

    entry = None
    if record.verification_status == "passed":
        entry = {
            "id": f"DOC-VER-{uuid.uuid4().hex[:12]}",
            "type": expected,
            "status": "mevcut",
            "required": True,
            "source": "verified",
            "verification_status": "passed",
            "proof_file_hash": record.file_sha256,
        }
    return record, entry


def add_proof_to_state(
    state: dict[str, Any],
    record: ProofRecord,
) -> list[dict[str, Any]]:
    ledger = append_record(state, record)
    state["proof_ledger"] = ledger
    return ledger


def write_canonical_upload(tmp_path: Path, documents: list[dict[str, Any]]) -> Path:
    """user_assertion / bypass senaryoları için canonical JSON dosyası."""
    payload = {
        "canonical": {
            "company_profile": {
                "company_id": "e2e_bypass",
                "company_name": "E2E Bypass A.Ş.",
                "tax_number": CELIK_TAX_NUMBER,
                "analysis_period": {"start": "2025-01-01", "end": "2025-09-30"},
                "estimated_refund_amount": 320_000,
            },
            "invoices": [
                {
                    "id": "F-E2E-001",
                    "type": "satis",
                    "date": "2025-03-01",
                    "amount": 50_000,
                    "kdv_rate": 20,
                    "kdv_amount": 10_000,
                    "supplier_id": "SUP-E2E",
                    "is_export": True,
                    "has_customs_declaration": False,
                    "is_tevkifat": False,
                    "period": "2025-Q1",
                }
            ],
            "suppliers": [
                {
                    "id": "SUP-E2E",
                    "name": "E2E Tedarik",
                    "tax_number": CELIK_TAX_NUMBER,
                    "risk_level": "düşük",
                }
            ],
            "documents": documents,
        }
    }
    path = tmp_path / "bypass_canonical.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def mandatory_lock_codes(state: dict[str, Any]) -> list[str]:
    fin = (state.get("final_report") or {}).get("finance_eligibility") or {}
    raw = fin.get("mandatory_lock_triggered") or []
    return [normalize_code(c) for c in raw]
