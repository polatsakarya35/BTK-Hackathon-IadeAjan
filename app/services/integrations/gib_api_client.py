"""GİB / e-Fatura API gateway — mock (varsayılan) veya canlı (ENABLE_REAL_GIB_API)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

SourceKind = Literal["gib_mock", "gib_live", "gib_live_unconfigured"]

MOCK_REJECT_VKN = "9999999999"
MOCK_REJECT_DECLARATION = "SAHTE123"


@dataclass(frozen=True)
class GibApiVerificationResult:
    verified: bool
    source: SourceKind
    message: str
    rejection_code: str | None = None
    raw: dict[str, Any] | None = None


def _normalize_vkn(vkn: str | None) -> str:
    if not vkn:
        return ""
    return re.sub(r"\D", "", str(vkn))


def _is_real_api_enabled() -> bool:
    return os.getenv("ENABLE_REAL_GIB_API", "false").lower() in ("1", "true", "yes")


def _api_timeout() -> float:
    try:
        return float(os.getenv("GIB_API_TIMEOUT_SEC", "15"))
    except ValueError:
        return 15.0


def _mock_verify_gcb(
    vkn: str,
    declaration_no: str,
    amount: float | None,
) -> GibApiVerificationResult:
    vkn_digits = _normalize_vkn(vkn)
    decl = str(declaration_no or "").strip()
    if not vkn_digits or not decl:
        return GibApiVerificationResult(
            verified=False,
            source="gib_mock",
            message="VKN veya beyan numarası eksik",
            rejection_code="GIB_API_GCB_REJECTED",
        )
    if vkn_digits == MOCK_REJECT_VKN or decl.upper() == MOCK_REJECT_DECLARATION:
        return GibApiVerificationResult(
            verified=False,
            source="gib_mock",
            message=(
                "GİB/Gümrük API doğrulaması başarısız (mock): "
                "Beyanname resmi kayıtlarda bulunamadı."
            ),
            rejection_code="GIB_API_GCB_REJECTED",
        )
    amt_note = f", tutar={amount:,.2f}" if amount is not None else ""
    return GibApiVerificationResult(
        verified=True,
        source="gib_mock",
        message=f"GİB mock: beyan kaydı doğrulandı (VKN={vkn_digits}, no={decl}{amt_note})",
    )


def _mock_verify_efatura(
    vkn: str,
    invoice_uuid: str,
    issue_date: str | None,
    amount: float | None,
) -> GibApiVerificationResult:
    vkn_digits = _normalize_vkn(vkn)
    uid = str(invoice_uuid or "").strip()
    if not vkn_digits or not uid:
        return GibApiVerificationResult(
            verified=False,
            source="gib_mock",
            message="VKN veya fatura UUID eksik",
            rejection_code="GIB_API_EFATURA_REJECTED",
        )
    blob = f"{vkn_digits}|{uid}|{issue_date or ''}".upper()
    if (
        vkn_digits == MOCK_REJECT_VKN
        or "SAHTE" in blob
        or "IPTAL" in blob
        or uid.upper().startswith("SAHTE")
    ):
        return GibApiVerificationResult(
            verified=False,
            source="gib_mock",
            message=(
                "GİB e-Fatura API doğrulaması başarısız (mock): "
                "Fatura kayıt dışı veya iptal edilmiş."
            ),
            rejection_code="GIB_API_EFATURA_REJECTED",
        )
    return GibApiVerificationResult(
        verified=True,
        source="gib_mock",
        message=f"GİB e-Fatura mock: fatura doğrulandı ({uid})",
    )


def _live_verify_gcb(
    vkn: str,
    declaration_no: str,
    amount: float | None,
) -> GibApiVerificationResult:
    base_url = (os.getenv("GIB_API_URL") or "").strip()
    if not base_url:
        return GibApiVerificationResult(
            verified=False,
            source="gib_live_unconfigured",
            message="GIB_API_URL tanımlı değil — canlı GÇB doğrulaması yapılamadı",
            rejection_code="GIB_API_GCB_REJECTED",
        )
    # TODO: GİB resmi sözleşmeye göre path, auth ve payload
    raise NotImplementedError(
        f"Canlı GÇB doğrulama henüz uygulanmadı (endpoint={base_url!r}, "
        f"vkn={_normalize_vkn(vkn)}, declaration_no={declaration_no}, amount={amount})"
    )


def _live_verify_efatura(
    vkn: str,
    invoice_uuid: str,
    issue_date: str | None,
    amount: float | None,
) -> GibApiVerificationResult:
    base_url = (
        (os.getenv("GIB_EFATURA_API_URL") or os.getenv("GIB_API_URL") or "").strip()
    )
    if not base_url:
        return GibApiVerificationResult(
            verified=False,
            source="gib_live_unconfigured",
            message="GIB_EFATURA_API_URL / GIB_API_URL tanımlı değil",
            rejection_code="GIB_API_EFATURA_REJECTED",
        )
    # TODO: GİB e-Fatura resmi endpoint
    raise NotImplementedError(
        f"Canlı e-Fatura doğrulama henüz uygulanmadı (endpoint={base_url!r}, "
        f"vkn={_normalize_vkn(vkn)}, uuid={invoice_uuid})"
    )


def verify_gcb_with_gib(
    vkn: str,
    declaration_no: str,
    amount: float | None = None,
) -> GibApiVerificationResult:
    """Gümrük çıkış beyannamesini GİB/Gümrük API ile doğrular (mock veya canlı)."""
    if _is_real_api_enabled():
        return _live_verify_gcb(vkn, declaration_no, amount)
    return _mock_verify_gcb(vkn, declaration_no, amount)


def verify_efatura_with_gib(
    vkn: str,
    invoice_uuid: str,
    issue_date: str | None = None,
    amount: float | None = None,
) -> GibApiVerificationResult:
    """e-Fatura kaydını GİB API ile doğrular (mock veya canlı)."""
    if _is_real_api_enabled():
        return _live_verify_efatura(vkn, invoice_uuid, issue_date, amount)
    return _mock_verify_efatura(vkn, invoice_uuid, issue_date, amount)
