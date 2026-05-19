"""Çapraz doğrulama — VKN, dönem, tutar, checksum."""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from app.schemas.verification import (
    DocumentExtraction,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)

DATE_TOLERANCE_DAYS = int(__import__("os").getenv("VERIFICATION_DATE_TOLERANCE_DAYS", "30"))


def normalize_vkn(vkn: str | None) -> str:
    if not vkn:
        return ""
    return re.sub(r"\D", "", vkn)


def validate_vkn_format(vkn: str | None) -> bool:
    digits = normalize_vkn(vkn)
    return len(digits) == 10 and digits.isdigit()


def validate_vkn_checksum(vkn: str | None) -> bool:
    """Türkiye VKN mod-11 kontrol hanesi."""
    digits = normalize_vkn(vkn)
    if len(digits) != 10 or not digits.isdigit():
        return False
    if digits[0] == "0":
        return False
    total = 0
    for i, d in enumerate(digits[:9]):
        tmp = (int(d) + (9 - i)) % 10
        if tmp == 0:
            tmp = 9
        total += (tmp * (2 ** (9 - i))) % 9
    check = (10 - (total % 10)) % 10
    return check == int(digits[9])


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip()[:10], fmt)
        except ValueError:
            continue
    return None


def _period_bounds(state: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    dr = state.get("date_range") or {}
    start = _parse_date(dr.get("start"))
    end = _parse_date(dr.get("end"))
    return start, end


def cross_validate_document(
    extraction: DocumentExtraction | None,
    *,
    state: dict[str, Any],
    expected_type: str,
    strict_proof: bool = True,
) -> VerificationResult:
    checks: list[VerificationCheck] = []
    warnings: list[VerificationCheck] = []

    if extraction is None:
        checks.append(VerificationCheck(code="EXTRACTION_MISSING", message="Çıkarım yok"))
        return VerificationResult(status="failed", checks=checks, warnings=warnings)

    tax = normalize_vkn(str(state.get("tax_number", "")))
    doc_vkn = normalize_vkn(extraction.exporter_vkn)

    if expected_type in ("gumruk_beyannamesi", "ymm_tasdik_raporu") and doc_vkn and tax:
        if doc_vkn != tax:
            checks.append(
                VerificationCheck(
                    code="OWNERSHIP_VKN_MATCH",
                    severity="error",
                    message="Belge VKN şirket vergi numarası ile uyuşmuyor",
                )
            )

    if extraction.exporter_vkn:
        if not validate_vkn_format(extraction.exporter_vkn):
            checks.append(
                VerificationCheck(
                    code="VKN_FORMAT",
                    severity="error",
                    message="VKN formatı geçersiz",
                )
            )
        elif not validate_vkn_checksum(extraction.exporter_vkn):
            warnings.append(
                VerificationCheck(
                    code="VKN_CHECKSUM_WARN",
                    severity="warning",
                    message="VKN kontrol hanesi uyuşmuyor (şimdilik yalnızca uyarı)",
                )
            )

    start, end = _period_bounds(state)
    decl_dt = _parse_date(extraction.declaration_date)
    if decl_dt and start and end:
        from datetime import timedelta

        pad = timedelta(days=DATE_TOLERANCE_DAYS)
        if decl_dt < start - pad or decl_dt > end + pad:
            checks.append(
                VerificationCheck(
                    code="PERIOD_MISMATCH",
                    severity="error",
                    message="Beyan tarihi analiz dönemi dışında",
                )
            )

    if extraction.currency and extraction.currency.upper() != "TRY":
        warnings.append(
            VerificationCheck(
                code="FX_MANUAL_REVIEW",
                severity="warning",
                message="Farklı para birimi — tutar karşılaştırması atlandı",
            )
        )

    if expected_type in ("gumruk_beyannamesi",):
        if not extraction.declaration_no:
            warnings.append(
                VerificationCheck(
                    code="GCB_DECLARATION_NO_MISSING",
                    severity="warning",
                    message="GÇB belgesinden beyan numarası okunamadı — manuel eşleşme gerekiyor",
                )
            )
        if extraction.amount is None or float(extraction.amount or 0) <= 0:
            warnings.append(
                VerificationCheck(
                    code="GCB_AMOUNT_MISSING",
                    severity="warning",
                    message="Belgeden tutar okunamadı, manuel eşleşme gerekiyor",
                )
            )

        ownership_ok = not any(c.code == "OWNERSHIP_VKN_MATCH" for c in checks)
        if ownership_ok and doc_vkn and tax and doc_vkn == tax and extraction.declaration_no:
            from app.services.integrations.gib_api_client import verify_gcb_with_gib

            gib_result = verify_gcb_with_gib(
                tax,
                extraction.declaration_no or "",
                float(extraction.amount) if extraction.amount is not None else None,
            )
            if gib_result.verified:
                warnings.append(
                    VerificationCheck(
                        code="GIB_API_VERIFIED",
                        severity="info",
                        message=gib_result.message,
                    )
                )
            else:
                checks.append(
                    VerificationCheck(
                        code="GIB_API_GCB_REJECTED",
                        severity="error",
                        message=gib_result.message,
                    )
                )

    status: VerificationStatus = "failed" if checks else ("passed" if not warnings or not strict_proof else "passed")
    if checks:
        status = "failed"
    elif warnings and strict_proof:
        status = "passed"
    else:
        status = "passed" if not checks else "failed"

    return VerificationResult(status=status, checks=checks, warnings=warnings)
