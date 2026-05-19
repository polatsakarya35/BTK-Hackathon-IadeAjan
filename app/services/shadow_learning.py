"""Shadow Learning — insan onaylı kanunname incelemesi kuyruğu.

-append-only JSONL; otomatik olarak PENALTY_MATRIX'e eklenmez.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "shadow_learning.jsonl"

SHADOW_TRACKED_CODES = frozenset(
    {
        "GENERAL_ANOMALY_HIGH",
        "GENERAL_ANOMALY_MEDIUM",
        "DATA_INTEGRITY_FAILURE",
        "REFUND_LOGIC_IMPOSSIBLE",
    }
)


def _normalize_code_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.split(".")[-1] if "." in raw else raw
    return str(raw)


def log_for_review(
    session_id: str,
    refund_type: str,
    code: str,
    evidence: str,
    raw_summary: str = "",
    macro_flag: bool = False,
    invoice_ids: list[str] | None = None,
) -> None:
    """Tek satır JSONL'ye ekler; hata durumunda analizi bloklamaz."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": session_id,
            "refund_type": refund_type,
            "code": _normalize_code_str(code),
            "evidence": evidence[:2000],
            "macro_flag": macro_flag,
            "invoice_ids": (invoice_ids or [])[:50],
            "raw_summary": (raw_summary or "")[:2000],
            "review_status": "pending",
        }
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log_risk_items_batch(
    session_id: str,
    refund_type: str,
    risk_items: list[dict[str, Any]],
    raw_summary: str = "",
) -> None:
    """risk_items içinden izlenen kodları kuyruğa düşür."""
    for r in risk_items:
        code = _normalize_code_str(r.get("code"))
        if code not in SHADOW_TRACKED_CODES:
            continue
        meta = r.get("metadata") or {}
        macro = bool(meta.get("macro_flag")) or code in (
            "DATA_INTEGRITY_FAILURE",
            "REFUND_LOGIC_IMPOSSIBLE",
        )
        log_for_review(
            session_id=session_id,
            refund_type=refund_type,
            code=code,
            evidence=str(r.get("reason", "")),
            raw_summary=raw_summary,
            macro_flag=macro,
            invoice_ids=list(r.get("invoice_ids") or []),
        )
