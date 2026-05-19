"""Harici doğrulama — tek kaynak: integrations.gib_api_client."""

from __future__ import annotations

import os

from app.services.integrations.gib_api_client import (
    verify_efatura_with_gib,
    verify_gcb_with_gib,
)
from app.services.verification.registry.types import RegistryVerificationOutcome


def verify_gib_declaration(vkn: str, declaration_no: str) -> RegistryVerificationOutcome:
    result = verify_gcb_with_gib(vkn, declaration_no)
    return RegistryVerificationOutcome(
        verified=result.verified,
        source=result.source,
        message=result.message,
        raw={"rejection_code": result.rejection_code},
    )


def verify_efatura_invoice(
    *,
    issuer_vkn: str,
    invoice_id: str,
    invoice_date: str | None = None,
    amount: float | None = None,
) -> RegistryVerificationOutcome:
    result = verify_efatura_with_gib(issuer_vkn, invoice_id, invoice_date, amount)
    return RegistryVerificationOutcome(
        verified=result.verified,
        source=result.source,
        message=result.message,
        raw={"rejection_code": result.rejection_code},
    )


def verify_customs_export_exit(vkn: str, declaration_no: str) -> RegistryVerificationOutcome:
    """Gümrük çıkış — şimdilik GÇB gateway ile aynı soket."""
    return verify_gib_declaration(vkn, declaration_no)


def registry_status_summary() -> dict[str, str]:
    real = os.getenv("ENABLE_REAL_GIB_API", "false").lower() in ("1", "true", "yes")
    gib_url = bool((os.getenv("GIB_API_URL") or "").strip())
    efatura_url = bool(
        (os.getenv("GIB_EFATURA_API_URL") or os.getenv("GIB_API_URL") or "").strip()
    )
    mode = "live" if real else "mock"
    return {
        "gib_mode": mode,
        "gib_configured": "yes" if (real and gib_url) else ("mock" if not real else "partial"),
        "efatura_mode": mode,
        "efatura_configured": "yes" if (real and efatura_url) else ("mock" if not real else "partial"),
        "customs_mode": mode,
        "customs_configured": "yes" if (real and gib_url) else ("mock" if not real else "partial"),
        "tam_uyumluluk": "tamam" if (real and gib_url and efatura_url) else "api_bekleniyor",
        "enable_real_gib_api": "true" if real else "false",
    }
