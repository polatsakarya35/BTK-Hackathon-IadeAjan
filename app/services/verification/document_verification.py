"""Birleşik belge pipeline — classify → extract → cross_validate."""

from __future__ import annotations

import os
from typing import Any

from app.schemas.models import DocumentClassification
from app.schemas.verification import (
    DocumentClassificationSnapshot,
    DocumentExtraction,
    ProofRecord,
    VerificationResult,
    retention_policy_for_env,
)
from app.services.document_classifier import classify_document
from app.services.verification.cross_validate import cross_validate_document
from app.services.verification.document_extraction import extract_document_fields


def ingest_document_file(
    file_path: str,
    *,
    expected_type: str | None = None,
    state: dict[str, Any] | None = None,
    session_id: str = "",
    question_id: str = "",
    field: str = "",
) -> tuple[ProofRecord, dict[str, Any] | None]:
    """
    Tek giriş noktası: sınıflandır, çıkar, çapraz doğrula.
    Returns: (ProofRecord, inventory_entry veya None)
    """
    state = state or {}
    policy = retention_policy_for_env()
    record = ProofRecord.from_upload(
        session_id=session_id or state.get("company_id", "session"),
        question_id=question_id,
        field=field,
        file_path=file_path,
        expected_type=expected_type or "",
        policy=policy,
    )

    classification: DocumentClassification | None = None
    method = "failed"
    try:
        classification = classify_document(file_path)
        method = getattr(classification, "method", None) or "llm"
        if hasattr(classification, "model_dump"):
            cls_dict = classification.model_dump()
            method = cls_dict.get("method", "llm")
    except Exception:
        classification = None
        method = "failed"

    if classification:
        record.classification = DocumentClassificationSnapshot(
            type=classification.type,
            confidence=classification.confidence,
            method=method if isinstance(method, str) else "llm",
            file_name=classification.file_name,
            evidence=(classification.evidence or "")[:500],
        )

    strict = os.getenv("STRICT_PROOF", "true").lower() in ("1", "true")
    from app.schemas.verification import VerificationCheck

    is_heuristic = (
        method == "filename_heuristic"
        or (classification and classification.confidence == 0.85 and method != "llm")
    )
    if is_heuristic and strict and expected_type:
        record.verification_status = "failed"
        record.failed_checks.append(
            VerificationCheck(
                code="HEURISTIC_NOT_ALLOWED",
                message="Dosya adı sezgisi kanıt olarak kabul edilmedi",
            )
        )
        return record, None

    exp_type = expected_type or (classification.type if classification else "")
    if expected_type and classification and classification.type != expected_type:
        record.verification_status = "failed"
        from app.schemas.verification import VerificationCheck

        record.failed_checks.append(
            VerificationCheck(code="TYPE_MISMATCH", message="Belge tipi uyuşmuyor")
        )
        return record, None

    if classification and classification.confidence < 0.8 and strict:
        record.verification_status = "failed"
        from app.schemas.verification import VerificationCheck

        record.failed_checks.append(
            VerificationCheck(code="LOW_CONFIDENCE", message="Sınıflandırma güveni düşük")
        )
        return record, None

    extraction: DocumentExtraction | None = None
    if exp_type:
        try:
            extraction = extract_document_fields(file_path, exp_type)
            record.extraction = extraction
        except Exception:
            extraction = None

    if extraction and state:
        result: VerificationResult = cross_validate_document(
            extraction,
            state=state,
            expected_type=exp_type,
            strict_proof=strict,
        )
        for c in result.checks:
            record.failed_checks.append(c)
        record.verification_status = result.status
        if any(c.code == "GIB_API_GCB_REJECTED" for c in result.checks):
            flags = dict(state.get("gib_api_flags") or {})
            flags["gcb_rejected"] = True
            state["gib_api_flags"] = flags
            summary = dict(state.get("verification_summary") or {})
            summary["gib_api_gcb_rejected"] = True
            state["verification_summary"] = summary
        for w in result.warnings:
            if w.code in ("GCB_AMOUNT_MISSING", "GCB_DECLARATION_NO_MISSING"):
                pw = list(state.get("process_warnings") or [])
                msg = w.message or w.code
                if msg not in pw:
                    pw.append(msg)
                state["process_warnings"] = pw
        summary = state.get("verification_summary") or {}
        if isinstance(summary, dict):
            warns = list(summary.get("warnings") or [])
            warns.extend(w.message or w.code for w in result.warnings)
            summary["warnings"] = warns
    elif classification:
        record.verification_status = "passed" if classification.confidence >= 0.8 else "failed"
    else:
        record.verification_status = "failed"

    entry = None
    if record.verification_status == "passed" and exp_type:
        import uuid

        entry = {
            "id": f"DOC-VER-{uuid.uuid4().hex[:12]}",
            "type": exp_type,
            "status": "mevcut",
            "required": True,
            "source": "verified",
            "verification_status": "passed",
            "proof_file_hash": record.file_sha256,
            "confidence": classification.confidence if classification else 1.0,
        }
        if record.extraction:
            e = record.extraction
            if e.declaration_no:
                entry["declaration_no"] = e.declaration_no
            if e.amount is not None:
                entry["amount"] = e.amount
            if e.exporter_vkn:
                entry["exporter_vkn"] = e.exporter_vkn
            if e.declaration_date:
                entry["declaration_date"] = e.declaration_date

    return record, entry
