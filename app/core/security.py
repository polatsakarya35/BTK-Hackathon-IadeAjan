"""Orta öncelik güvenlik yardımcıları — oturum, audit, hız sınırı."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable[..., object])

_rate_buckets: dict[str, list[float]] = defaultdict(list)


def check_api_access(token: str | None = None) -> bool:
    """Basit API anahtarı kontrolü (IADEAJAN_API_KEY). Tanımlı değilse geçer."""
    expected = os.getenv("IADEAJAN_API_KEY", "")
    if not expected:
        return True
    return token == expected


def rate_limit(key: str, max_calls: int = 30, window_seconds: float = 60.0) -> bool:
    """True = izin verildi, False = limit aşıldı."""
    now = time.monotonic()
    bucket = _rate_buckets[key]
    bucket[:] = [t for t in bucket if now - t < window_seconds]
    if len(bucket) >= max_calls:
        return False
    bucket.append(now)
    return True


def clear_session_sensitive_state(state: dict) -> dict:
    """Oturum sonu hassas alanları temizler."""
    state.pop("proof_ledger", None)
    state.pop("clarification_answers", None)
    return state
