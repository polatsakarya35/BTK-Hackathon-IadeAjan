"""mock_data altından demo senaryolarını güvenle okur."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

COMPANY_FILE = "company_profile.json"
INVOICES_FILE = "invoices.json"
SUPPLIERS_FILE = "suppliers.json"
DOCUMENTS_FILE = "documents.json"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MOCK_DATA_DIR = _PROJECT_ROOT / "mock_data"
_REGISTRY_FILE = _MOCK_DATA_DIR / "scenario_registry.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Senaryo verisi bozuk: {path.name}") from exc


def list_scenarios() -> list[dict[str, Any]]:
    """scenario_registry.json içindeki senaryo listesini döndürür."""
    if not _REGISTRY_FILE.is_file():
        raise RuntimeError("scenario_registry.json bulunamadı")

    try:
        registry = _read_json(_REGISTRY_FILE)
    except RuntimeError as exc:
        if "bozuk" in str(exc):
            raise RuntimeError("scenario_registry.json bozuk") from exc
        raise

    scenarios = registry.get("scenarios")
    if not isinstance(scenarios, list):
        raise RuntimeError("scenario_registry.json bozuk")

    return scenarios


def load_scenario(scenario_id: str) -> dict[str, Any]:
    """Belirtilen senaryo klasöründeki JSON dosyalarını yükler."""
    scenario_dir = _MOCK_DATA_DIR / scenario_id
    if not scenario_dir.is_dir():
        raise RuntimeError(f"Senaryo bulunamadı: {scenario_id}")

    company_profile = _read_json(scenario_dir / COMPANY_FILE)

    invoices_payload = _read_json(scenario_dir / INVOICES_FILE)
    suppliers_payload = _read_json(scenario_dir / SUPPLIERS_FILE)
    documents_payload = _read_json(scenario_dir / DOCUMENTS_FILE)

    invoices = _extract_list(invoices_payload, "invoices", INVOICES_FILE)
    suppliers = _extract_list(suppliers_payload, "suppliers", SUPPLIERS_FILE)
    documents = _extract_list(documents_payload, "documents", DOCUMENTS_FILE)

    return {
        "scenario_id": scenario_id,
        "company_profile": company_profile,
        "invoices": invoices,
        "suppliers": suppliers,
        "documents": documents,
    }


def _extract_list(payload: dict[str, Any], key: str, file_name: str) -> list[dict[str, Any]]:
    if key not in payload:
        raise RuntimeError(f"Beklenen anahtar eksik: {key} — {file_name}")
    value = payload[key]
    if not isinstance(value, list):
        raise RuntimeError(f"Beklenen anahtar eksik: {key} — {file_name}")
    return value
