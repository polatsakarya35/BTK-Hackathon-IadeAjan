"""Geriye uyumluluk — yeni gateway'e yönlendirir (ENABLE_GIB_MOCK deprecated)."""

from __future__ import annotations

from typing import Any

from app.services.integrations.gib_api_client import verify_gcb_with_gib


def verify_with_gib(vkn: str, declaration_no: str) -> dict[str, Any]:
    """
    Eski mock arayüzü — verify_gcb_with_gib sarmalayıcısı.

    ENABLE_GIB_MOCK artık zorunlu değil; gateway her zaman mock/real env ile çalışır.
    """
    result = verify_gcb_with_gib(vkn, declaration_no)
    return {
        "verified": result.verified,
        "source": result.source.replace("gib_", "gib_") if result.source else "gib_mock",
        "vkn": "".join(c for c in str(vkn) if c.isdigit()),
        "declaration_no": str(declaration_no or "").strip(),
        "message": result.message,
    }
