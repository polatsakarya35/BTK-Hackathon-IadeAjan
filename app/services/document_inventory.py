"""Belge envanteri — LLM sınıflandırması ve clarification çevrimleri."""

from __future__ import annotations

import uuid
from typing import Any

from app.schemas.models import DocumentClassification

CLASSIFIER_CONFIDENCE_THRESHOLD = 0.8

_INVENTORY_TYPES_FROM_CLASSIFIER = frozenset({
    "gumruk_beyannamesi",
    "ymm_tasdik_raporu",
    "2no_kdv_beyannamesi",
})


def classification_to_inventory_entry(
    classification: DocumentClassification,
) -> dict[str, Any] | None:
    """
    Yüksek güvenli GÇB/YMM sınıflandırmasını Analyzer uyumlu kayda çevirir.
    diger/bilinmiyor veya düşük güven → None.
    """
    if classification.confidence < CLASSIFIER_CONFIDENCE_THRESHOLD:
        return None
    if classification.type not in _INVENTORY_TYPES_FROM_CLASSIFIER:
        return None
    return {
        "id": f"DOC-LLM-{uuid.uuid4().hex[:12]}",
        "type": classification.type,
        "status": "mevcut",
        "required": True,
        "note": classification.evidence[:500],
        "source": "llm_classifier",
        "confidence": classification.confidence,
        "file_name": classification.file_name,
    }


def upsert_inventory(
    existing: list[dict[str, Any]],
    entry: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Aynı belge type için tek kayıt: varsa status/note güncellenir, yoksa append.
    """
    if entry is None:
        return list(existing)

    inv_type = entry.get("type")
    if not inv_type:
        return [*existing, entry]

    out: list[dict[str, Any]] = []
    merged = False
    for doc in existing:
        if doc.get("type") != inv_type:
            out.append(dict(doc))
            continue
        merged = True
        updated = dict(doc)
        updated["status"] = entry.get("status", updated.get("status", "mevcut"))
        if entry.get("note"):
            updated["note"] = entry["note"]
        if entry.get("source"):
            updated["source"] = entry["source"]
        if entry.get("confidence") is not None:
            updated["confidence"] = entry["confidence"]
        if entry.get("file_name"):
            updated["file_name"] = entry["file_name"]
        out.append(updated)

    if not merged:
        out.append(dict(entry))
    return out


def entry_from_clarification(field: str, answer: Any) -> dict[str, Any] | None:
    """
    Clarification cevabından envanter kaydı (yalnızca olumlu beyanlar).
    """
    ans = str(answer).strip() if answer is not None else ""

    if field == "has_customs_declarations":
        if ans in ("Evet", "Bir kısmı mevcut"):
            return {
                "id": f"DOC-CLR-{uuid.uuid4().hex[:12]}",
                "type": "gumruk_beyannamesi",
                "status": "mevcut",
                "required": True,
                "note": f"Kullanıcı beyanı (clarification): {ans}",
                "source": "clarification",
            }
        return None

    if field == "has_2no_declaration":
        if ans in ("Evet", "Bir kısmı için verdim"):
            return {
                "id": f"DOC-CLR-{uuid.uuid4().hex[:12]}",
                "type": "2no_kdv_beyannamesi",
                "status": "mevcut",
                "required": True,
                "note": f"Kullanıcı beyanı (clarification): {ans}",
                "source": "clarification",
            }
        return None

    return None


def entry_from_verified_proof(record: dict[str, Any]) -> dict[str, Any] | None:
    """ProofRecord (dict) üzerinden verified envanter kaydı."""
    if record.get("verification_status") != "passed":
        return None
    field = record.get("field", "")
    extraction = record.get("extraction") or {}
    classification = record.get("classification") or {}
    type_map = {
        "has_customs_declarations": "gumruk_beyannamesi",
        "has_ymm_contract": "ymm_tasdik_raporu",
        "has_2no_declaration": "2no_kdv_beyannamesi",
    }
    doc_type = type_map.get(field) or classification.get("type")
    if not doc_type:
        return None
    entry: dict[str, Any] = {
        "id": f"DOC-PRF-{record.get('record_id', 'x')[:12]}",
        "type": doc_type,
        "status": "mevcut",
        "required": True,
        "source": "verified",
        "verification_status": "passed",
        "proof_file_hash": record.get("file_sha256"),
        "note": "Sunucu doğrulamalı kanıt",
    }
    if isinstance(extraction, dict):
        for key in ("declaration_no", "amount", "exporter_vkn", "declaration_date"):
            if extraction.get(key) is not None:
                entry[key] = extraction[key]
    return entry


def merge_inventory_from_questions(
    existing: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
    *,
    proof_ledger: list[dict[str, Any]] | None = None,
    require_verified_proof: bool | None = None,
) -> list[dict[str, Any]]:
    """Tüm soru-cevap çiftleri için envanter güncellemesi."""
    import os
    from app.services.clarification_proof import is_proof_document_field

    if require_verified_proof is None:
        require_verified_proof = os.getenv("REQUIRE_VERIFIED_PROOF", "true").lower() in (
            "1",
            "true",
        )

    ledger_by_q: dict[str, dict[str, Any]] = {}
    for rec in proof_ledger or []:
        qid = rec.get("question_id")
        if qid:
            ledger_by_q[qid] = rec

    inv = list(existing)
    for q in questions:
        qid = q.get("question_id")
        field = q.get("field")
        if not qid or not field:
            continue
        if qid not in answers:
            continue

        entry = None
        if require_verified_proof and is_proof_document_field(field):
            rec = ledger_by_q.get(qid)
            if rec:
                entry = entry_from_verified_proof(rec)
        else:
            entry = entry_from_clarification(field, answers[qid])
            if entry and require_verified_proof and is_proof_document_field(field):
                entry = None

        inv = upsert_inventory(inv, entry)
    return inv
