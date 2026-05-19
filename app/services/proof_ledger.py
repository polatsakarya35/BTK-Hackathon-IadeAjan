"""Proof ledger — kanıt kayıtları, TTL, cevap doğrulama, audit."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.schemas.verification import (
    ProofRecord,
    RetentionPolicy,
    retention_policy_for_env,
)
from app.services.clarification_proof import (
    expected_type_for_field,
    is_proof_document_field,
)

AUDIT_LOG_PATH = Path("logs/verification_audit.jsonl")
PROOF_UPLOAD_DIR = Path("iadeajan_clarification_proofs")


def _audit_enabled() -> bool:
    return os.getenv("VERIFICATION_AUDIT_ENABLED", "true").lower() in ("1", "true", "yes")


def write_audit_line(redacted: dict[str, Any]) -> None:
    if not _audit_enabled():
        return
    policy = retention_policy_for_env()
    if policy.audit_log_retention_days <= 0 and policy.environment == "local":
        return
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": datetime.now(timezone.utc).isoformat(), **redacted}
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def ledger_from_state(state: dict[str, Any]) -> list[ProofRecord]:
    raw = state.get("proof_ledger") or []
    out: list[ProofRecord] = []
    for item in raw:
        if isinstance(item, ProofRecord):
            out.append(item)
        elif isinstance(item, dict):
            out.append(ProofRecord.model_validate(item))
    return out


def ledger_to_state(ledger: list[ProofRecord]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in ledger]


def append_record(
    state: dict[str, Any],
    record: ProofRecord,
) -> list[dict[str, Any]]:
    ledger = ledger_from_state(state)
    ledger.append(record)
    write_audit_line(record.to_redacted().model_dump())
    if record.retention_policy_snapshot and record.retention_policy_snapshot.persist_ledger_to_disk:
        _persist_test_artifact(record)
    return ledger_to_state(ledger)


def _persist_test_artifact(record: ProofRecord) -> None:
    if os.getenv("ALLOW_TEST_ARTIFACTS", "").lower() not in ("1", "true"):
        return
    dest = Path("tests/artifacts/proof_ledger")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{record.record_id}.json"
    path.write_text(
        json.dumps(record.to_redacted().model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def purge_expired(
    state: dict[str, Any],
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Süresi dolan kayıtları çıkarır; temp dosyaları siler."""
    ref = now or datetime.now(timezone.utc)
    ledger = ledger_from_state(state)
    kept: list[ProofRecord] = []
    removed_paths: list[str] = []
    for rec in ledger:
        if rec.is_expired(ref):
            if rec.temp_path and os.path.isfile(rec.temp_path):
                try:
                    os.unlink(rec.temp_path)
                    removed_paths.append(rec.temp_path)
                except OSError:
                    pass
            continue
        kept.append(rec)
    return ledger_to_state(kept), removed_paths


def schedule_file_cleanup(directory: Path | None = None, max_age_hours: float | None = None) -> int:
    """Eski proof dosyalarını temizler."""
    policy = retention_policy_for_env()
    ttl = max_age_hours if max_age_hours is not None else policy.file_ttl_hours
    root = directory or PROOF_UPLOAD_DIR
    if not root.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl)
    removed = 0
    for path in root.iterdir():
        if not path.is_file():
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def get_by_question(
    state: dict[str, Any],
    question_id: str,
) -> ProofRecord | None:
    for rec in ledger_from_state(state):
        if rec.question_id == question_id and not rec.is_expired():
            return rec
    return None


def validate_clarification_answers(
    state: dict[str, Any],
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """
    Belge kanıtı gerektiren olumlu cevapları ledger ile doğrular.
    Returns: (ok, errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []
    purge_expired(state)

    for q in questions:
        qid = q.get("question_id", "")
        field = q.get("field", "")
        if not qid or not field:
            continue
        ans = answers.get(qid)
        if ans is None:
            continue
        ans_str = str(ans).strip()
        if not is_proof_document_field(field):
            continue
        positive = ans_str in ("Evet", "Bir kısmı mevcut", "Bir kısmı için verdim")
        if not positive:
            continue

        rec = get_by_question(state, qid)
        if rec is None:
            errors.append(f"{qid}: kanıt dosyası yüklenmedi veya süresi doldu")
            continue
        if rec.is_expired():
            errors.append(f"{qid}: kanıt kaydı süresi doldu — yeniden yükleyin")
            continue
        if rec.verification_status != "passed":
            errors.append(
                f"{qid}: belge doğrulaması geçmedi ({rec.verification_status})"
            )
            continue
        expected = expected_type_for_field(field)
        if expected and rec.expected_type != expected:
            errors.append(f"{qid}: beklenen belge tipi uyuşmuyor")

    ok = len(errors) == 0
    return ok, errors, warnings


def purge_proof_ledger_and_files(state: dict[str, Any]) -> dict[str, Any]:
    """Oturum başında ledger ve temp dosyaları temizler."""
    for rec in ledger_from_state(state):
        if rec.temp_path and os.path.isfile(rec.temp_path):
            try:
                os.unlink(rec.temp_path)
            except OSError:
                pass
    schedule_file_cleanup()
    return {"proof_ledger": [], "verification_summary": {}}
