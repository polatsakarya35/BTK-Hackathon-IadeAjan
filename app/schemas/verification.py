"""Doğrulama katmanı şemaları — proof ledger, extraction, retention."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

VerificationEnvironment = Literal["production", "staging", "test", "local"]
VerificationStatus = Literal["passed", "failed", "unverified", "pending"]
CheckSeverity = Literal["error", "warning", "info"]


class RetentionPolicy(BaseModel):
    """Ortam bazlı proof ledger saklama politikası."""

    environment: VerificationEnvironment = "local"
    ledger_ttl_hours: float = 8.0
    file_ttl_hours: float = 2.0
    audit_log_retention_days: int = 0
    persist_ledger_to_disk: bool = False
    redact_pii_in_logs: bool = True


def retention_policy_for_env(env: str | None = None) -> RetentionPolicy:
    """VERIFICATION_ENV veya APP_ENV ile politika seçer."""
    key = (env or os.getenv("VERIFICATION_ENV") or os.getenv("APP_ENV") or "local").lower()
    presets: dict[str, RetentionPolicy] = {
        "production": RetentionPolicy(
            environment="production",
            ledger_ttl_hours=24,
            file_ttl_hours=1,
            audit_log_retention_days=90,
            persist_ledger_to_disk=False,
        ),
        "staging": RetentionPolicy(
            environment="staging",
            ledger_ttl_hours=48,
            file_ttl_hours=6,
            audit_log_retention_days=30,
            persist_ledger_to_disk=False,
        ),
        "test": RetentionPolicy(
            environment="test",
            ledger_ttl_hours=2,
            file_ttl_hours=0.5,
            audit_log_retention_days=7,
            persist_ledger_to_disk=os.getenv("ALLOW_TEST_ARTIFACTS", "").lower() in ("1", "true"),
        ),
        "ci": RetentionPolicy(
            environment="test",
            ledger_ttl_hours=2,
            file_ttl_hours=0.5,
            audit_log_retention_days=7,
            persist_ledger_to_disk=os.getenv("ALLOW_TEST_ARTIFACTS", "").lower() in ("1", "true"),
        ),
    }
    return presets.get(key, RetentionPolicy(environment="local"))


class DocumentClassificationSnapshot(BaseModel):
    """ProofRecord içinde saklanan sınıflandırma özeti."""

    type: str
    confidence: float = 0.0
    method: str = "llm"
    file_name: str = ""
    evidence: str = ""


class DocumentExtraction(BaseModel):
    """Belgeden çıkarılan yapılandırılmış alanlar (agent içi tam kayıt)."""

    declaration_no: str | None = None
    declaration_date: str | None = None
    amount: float | None = None
    currency: str | None = "TRY"
    exporter_vkn: str | None = None
    exporter_name: str | None = None
    invoice_no: str | None = None
    invoice_date: str | None = None
    raw_evidence: str | None = None


class DocumentExtractionRedacted(BaseModel):
    """Audit için maskelenmiş extraction."""

    declaration_no: str | None = None
    declaration_date: str | None = None
    amount: float | None = None
    currency: str | None = None
    exporter_vkn_masked: str | None = None


class VerificationCheck(BaseModel):
    code: str
    severity: CheckSeverity = "error"
    message: str = ""


class VerificationResult(BaseModel):
    status: VerificationStatus = "unverified"
    checks: list[VerificationCheck] = Field(default_factory=list)
    warnings: list[VerificationCheck] = Field(default_factory=list)


def mask_vkn(vkn: str | None) -> str | None:
    if not vkn:
        return None
    digits = re.sub(r"\D", "", vkn)
    if len(digits) < 4:
        return "****"
    return f"{digits[:2]}******{digits[-2:]}"


def redact_filename(name: str) -> str:
    if not name:
        return "document.pdf"
    if "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        return f"document.{ext}"
    return "document.bin"


def redact_text(text: str | None, max_len: int = 80) -> str:
    if not text:
        return ""
    s = text[:max_len]
    s = re.sub(r"\b\d{10,11}\b", "[VKN]", s)
    return s


class ProofRecordRedactedView(BaseModel):
    record_id: str
    question_id: str
    file_sha256_prefix: str
    verification_status: VerificationStatus
    expected_type: str
    classification_type: str
    classification_confidence: float
    failed_check_codes: list[str] = Field(default_factory=list)
    exporter_vkn_masked: str | None = None


class ProofRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: f"PR-{uuid4().hex[:16]}")
    session_id: str = ""
    question_id: str = ""
    field: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    temp_path: str | None = None
    file_sha256: str = ""
    file_size_bytes: int = 0
    mime_type: str = ""
    original_filename_redacted: str = "document.pdf"
    expected_type: str = ""
    classification: DocumentClassificationSnapshot | None = None
    extraction: DocumentExtraction | None = None
    verification_status: VerificationStatus = "pending"
    failed_checks: list[VerificationCheck] = Field(default_factory=list)
    retention_policy_snapshot: RetentionPolicy | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        ref = now or datetime.now(timezone.utc)
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return ref >= exp

    def to_redacted(self) -> ProofRecordRedactedView:
        cls = self.classification
        vkn = self.extraction.exporter_vkn if self.extraction else None
        return ProofRecordRedactedView(
            record_id=self.record_id,
            question_id=self.question_id,
            file_sha256_prefix=(self.file_sha256 or "")[:8],
            verification_status=self.verification_status,
            expected_type=self.expected_type,
            classification_type=cls.type if cls else "",
            classification_confidence=cls.confidence if cls else 0.0,
            failed_check_codes=[c.code for c in self.failed_checks],
            exporter_vkn_masked=mask_vkn(vkn),
        )

    def extraction_redacted(self) -> DocumentExtractionRedacted | None:
        if not self.extraction:
            return None
        e = self.extraction
        return DocumentExtractionRedacted(
            declaration_no=e.declaration_no,
            declaration_date=e.declaration_date,
            amount=e.amount,
            currency=e.currency,
            exporter_vkn_masked=mask_vkn(e.exporter_vkn),
        )

    @classmethod
    def from_upload(
        cls,
        *,
        session_id: str,
        question_id: str,
        field: str,
        file_path: str,
        expected_type: str,
        policy: RetentionPolicy | None = None,
    ) -> ProofRecord:
        policy = policy or retention_policy_for_env()
        path = file_path
        data = b""
        if path and os.path.isfile(path):
            with open(path, "rb") as f:
                data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=policy.ledger_ttl_hours)
        return cls(
            session_id=session_id,
            question_id=question_id,
            field=field,
            temp_path=path,
            file_sha256=sha,
            file_size_bytes=len(data),
            mime_type="application/pdf",
            original_filename_redacted=redact_filename(os.path.basename(path or "")),
            expected_type=expected_type,
            expires_at=expires,
            retention_policy_snapshot=policy,
        )


class VerificationSummary(BaseModel):
    all_proof_passed: bool = False
    failed_checks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Finansman uygunluğu ve skor, yüklenen belgelerin içerik doğrulaması "
        "yerine mevzuat kuralları ve kullanıcı beyanlarına dayanabilir."
    )


def proof_record_to_dict(record: ProofRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def proof_record_from_dict(data: dict[str, Any]) -> ProofRecord:
    return ProofRecord.model_validate(data)
