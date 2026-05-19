"""Registry doğrulama sonuç tipi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RegistryVerificationOutcome:
    verified: bool
    source: str
    message: str
    raw: dict[str, Any] | None = None
