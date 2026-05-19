"""Envanter güven politikası — hangi kayıtlar sayılır."""

from __future__ import annotations

from typing import Any

TRUSTED_SOURCES = frozenset({"verified", "scenario"})
COUNTED_STATUSES = frozenset({"mevcut"})


def is_verified_inventory_entry(doc: dict[str, Any]) -> bool:
    source = (doc.get("source") or "").lower()
    vstat = (doc.get("verification_status") or "").lower()
    if source == "verified" or vstat == "passed":
        return True
    if source == "scenario":
        return True
    return False


def is_counted_for_export(doc: dict[str, Any], *, strict: bool | None = None) -> bool:
    """
    İhracat/YMM kurallarında 'mevcut' sayılır mı?
    strict=True (varsayılan): yalnızca verified/scenario.
    """
    if strict is None:
        import os
        strict = os.getenv("STRICT_INVENTORY_TRUST", "true").lower() in ("1", "true")

    status = doc.get("status", "")
    if status not in COUNTED_STATUSES:
        return False
    if not strict:
        return True
    return is_verified_inventory_entry(doc)


def normalize_canonical_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Kanonik JSON'da source olmadan mevcut → unverified/eksik."""
    out = dict(doc)
    source = (out.get("source") or "").lower()
    status = out.get("status", "")
    if status == "mevcut" and source not in TRUSTED_SOURCES:
        out["status"] = "eksik"
        out["verification_status"] = "unverified"
        out["note"] = (out.get("note") or "") + " [Kaynak doğrulanmadı]"
    return out
