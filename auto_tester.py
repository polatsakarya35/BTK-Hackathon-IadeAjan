"""İadeAjan uç senaryo otomatik test scripti (UI bağımsız)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.graph.workflow import build_graph

PROJECT_ROOT = Path(__file__).resolve().parent
MOCK_DIR = PROJECT_ROOT / "mock_data"


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "scenario_green",
        "path": MOCK_DIR / "scenario_green.json",
        "expectation": "Skor 100, Düşük Risk, clarification gereksiz",
        "answers": {},
        "assertion": lambda score, risk, clar: (
            score == 100 and risk == "Düşük Risk" and not clar
        ),
    },
    {
        "name": "scenario_red",
        "path": MOCK_DIR / "scenario_red.json",
        "expectation": "Skor < 60, Yüksek Risk",
        "answers": {
            "q_gumruk_001": "Hayır",
            "q_2no_001": "Hayır",
            "q_refund_type_001": "ihracat",
        },
        "assertion": lambda score, risk, clar: (
            score < 60 and risk == "Yüksek Risk"
        ),
    },
    {
        "name": "scenario_edge_case",
        "path": MOCK_DIR / "scenario_edge_case.json",
        "expectation": "ClarificationAgent kesin tetiklenmeli",
        "answers": {"q_refund_type_001": "ihracat"},
        "assertion": lambda score, risk, clar: clar is True,
    },
]


def _print_header() -> None:
    print(f"{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}İadeAjan Otomatik Uç Senaryo Testi{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")


def _print_result(
    name: str,
    expectation: str,
    score: int,
    risk: str,
    clarification_needed: bool,
    status: str,
) -> None:
    if status == "OK":
        color = C.GREEN
        mark = "✅"
    else:
        color = C.RED
        mark = "❌"

    print(f"\n{C.BOLD}{C.MAGENTA}--- {name} ---{C.RESET}")
    print(f"Beklenen: {expectation}")
    print(f"Nihai Skor: {C.BOLD}{score}/100{C.RESET}")
    print(f"Risk Kategorisi: {risk}")
    print(f"Clarification Gerekli mi?: {clarification_needed}")
    print(f"{color}{mark} Sonuç: {status}{C.RESET}")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _run_single(graph: Any, case: dict[str, Any]) -> tuple[int, str, bool]:
    payload_path: Path = case["path"]
    if not payload_path.exists():
        raise FileNotFoundError(f"Dosya bulunamadı: {payload_path}")

    # JSON format kontrolü (canonical payload)
    payload = _load_json(payload_path)
    for key in ("company_profile", "invoices", "suppliers", "documents"):
        if key not in payload:
            raise ValueError(f"{payload_path.name} içinde '{key}' alanı eksik.")

    initial_state: dict[str, Any] = {
        "uploaded_files": [str(payload_path)],
        "agent_logs": [],
        "clarification_answers": case["answers"],
    }

    final_state = graph.invoke(initial_state)
    report = final_state.get("final_report", {}) or {}
    score = int(report.get("calculated_score", -1))
    risk = str(report.get("risk_category", "Bilinmiyor"))

    # Analyzer çıktısından gelen sinyal: clarification gerçekten gerekli miydi?
    score_inputs = final_state.get("score_inputs", {}) or {}
    clarification_needed = bool(
        score_inputs.get(
            "clarification_needed",
            final_state.get("clarification_needed", False),
        )
    )

    return score, risk, clarification_needed


def main() -> None:
    _print_header()
    graph = build_graph()

    passed = 0
    failed = 0

    for case in SCENARIOS:
        try:
            score, risk, clarification_needed = _run_single(graph, case)
            ok = case["assertion"](score, risk, clarification_needed)
            status = "OK" if ok else "FAILED"
            _print_result(
                name=case["name"],
                expectation=case["expectation"],
                score=score,
                risk=risk,
                clarification_needed=clarification_needed,
                status=status,
            )
            if ok:
                passed += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"\n{C.BOLD}{C.MAGENTA}--- {case['name']} ---{C.RESET}")
            print(f"{C.RED}❌ Çalıştırma hatası: {exc}{C.RESET}")

    print(f"\n{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")
    if failed == 0:
        print(
            f"{C.BOLD}{C.GREEN}🎉 Tüm uç senaryo testleri geçti "
            f"({passed}/{len(SCENARIOS)}){C.RESET}"
        )
    else:
        print(
            f"{C.BOLD}{C.YELLOW}Tamamlanan: {passed}, Hatalı: {failed}, "
            f"Toplam: {len(SCENARIOS)}{C.RESET}"
        )
    print(f"{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")


if __name__ == "__main__":
    main()
