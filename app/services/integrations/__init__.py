"""Harici devlet API entegrasyonları — GİB / e-Fatura gateway."""

from app.services.integrations.gib_api_client import (
    GibApiVerificationResult,
    verify_efatura_with_gib,
    verify_gcb_with_gib,
)

__all__ = [
    "GibApiVerificationResult",
    "verify_gcb_with_gib",
    "verify_efatura_with_gib",
]
