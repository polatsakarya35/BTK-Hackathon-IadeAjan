"""Path B karşılaştırma scripti.

T01, T03, T06, T11 senaryoları için:
  - Score ve items_by_code dökümünü gösterir
  - LLM aktif / fallback durumunu raporlar
  - Çifte ceza var mı kontrol eder

Kullanım:
    python scripts/path_b_compare.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.state import IadeAjanState  # noqa: E402
from app.graph.workflow import build_graph  # noqa: E402
from app.schemas.penalty_codes import MANDATORY_LOCK_CODES, PENALTY_MATRIX, PenaltyCode  # noqa: E402


PRESET_ANSWERS = {
    "q_gumruk_001": "Hayır",
    "q_2no_001": "Hayır",
}

COMPARE_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "T01",
        "label": "Temiz ihracat (10 fatura)",
        "refund_answer": "ihracat",
        "df": pd.DataFrame([
            {"Fatura No": f"FAT-2025-{i:03d}", "Tip": "İhracat",
             "Tarih": "2025-03-15", "Tutar": 50000 + i * 1000, "KDV": 0,
             "VKN": "1234567890", "Tedarikçi": "ABC İhracat A.Ş."}
            for i in range(1, 11)
        ]),
        "expected_min": 25, "expected_max": 65,
    },
    {
        "id": "T03",
        "label": "Bozuk tarihler + ihracat",
        "refund_answer": "ihracat",
        "df": pd.DataFrame([
            {"Fatura No": f"FAT-{i}", "Tip": "İhracat",
             "Tarih": "bozuk_tarih", "Tutar": 25000, "KDV": 0,
             "VKN": "1111111111", "Tedarikçi": "Test A.Ş."}
            for i in range(1, 6)
        ]),
        "expected_min": 10, "expected_max": 50,
    },
    {
        "id": "T06",
        "label": "Tüm DQ sorunları",
        "refund_answer": "ihracat",
        "df": pd.DataFrame([
            {"Fatura No": "FAT-1", "Tip": "İhracat", "Tarih": "yanliş",
             "Tutar": 0, "KDV": 0, "VKN": "aaa", "Tedarikçi": "A"},
            {"Fatura No": "FAT-2", "Tip": "İhracat", "Tarih": "olmayan",
             "Tutar": 0, "KDV": 0, "VKN": "bbb", "Tedarikçi": "B"},
            {"Fatura No": "FAT-3", "Tip": "İhracat", "Tarih": "2025-01-15",
             "Tutar": 0, "KDV": 0, "VKN": "ccc", "Tedarikçi": "C"},
        ]),
        "expected_min": 0, "expected_max": 50,
    },
    {
        "id": "T11",
        "label": "Kaos: tüm sorunlar",
        "refund_answer": "ihracat",
        "df": pd.DataFrame([
            {"Fatura No": "F1", "Tip": "İhracat", "Tarih": "2025-03-15",
             "Tutar": -1000, "KDV": 0, "VKN": "abc", "Tedarikçi": "B"},
            {"Fatura No": "F2", "Tip": "İhracat", "Tarih": "olmayan",
             "Tutar": 0, "KDV": 0, "VKN": "def", "Tedarikçi": "C"},
        ]),
        "expected_min": 0, "expected_max": 50,
    },
]


async def run_scenario(
    sc: dict[str, Any],
    tmp_dir: Path,
    delay_s: float = 0.0,
) -> dict[str, Any]:
    if delay_s:
        await asyncio.sleep(delay_s)

    xlsx_path = tmp_dir / f"{sc['id']}.xlsx"
    sc["df"].to_excel(xlsx_path, index=False)

    answers = {**PRESET_ANSWERS, "q_refund_type_001": sc["refund_answer"]}
    graph = build_graph(interrupt=False)
    initial: dict[str, Any] = {
        "session_id": "path-b-compare",
        "uploaded_files": [str(xlsx_path)],
        "scenario_id": None,
        "company_info": {},
        "invoices": [],
        "normalized_invoices": [],
        "normalized_suppliers": [],
        "document_inventory": [],
        "classification_result": {},
        "risk_items": [],
        "missing_docs": [],
        "score_inputs": {},
        "analysis_status": "pending",
        "clarification_needed": False,
        "clarification_questions": [],
        "clarification_answers": answers,
        "clarification_message": "",
        "process_warnings": [],
        "final_report": {},
        "current_agent": "CollectorAgent",
        "company_profile": {},
        "refund_type": "belirsiz",
        "agent_logs": [],
        "retry_count": 0,
    }

    final = await graph.ainvoke(initial, config={"recursion_limit": 50})
    report: dict[str, Any] = final.get("final_report") or {}
    return {
        "id": sc["id"],
        "label": sc["label"],
        "score": report.get("calculated_score", 0),
        "risk_category": report.get("risk_category", "-"),
        "expected_min": sc["expected_min"],
        "expected_max": sc["expected_max"],
        "score_breakdown": report.get("score_breakdown") or {},
        "mandatory_lock_triggered": report.get("mandatory_lock_triggered") or [],
        "risk_items": final.get("risk_items") or [],
        "missing_docs": final.get("missing_docs") or [],
    }


def _print_items_by_code(items_by_code: dict[str, Any]) -> None:
    if not items_by_code:
        print("    (ceza tespit edilmedi)")
        return
    for code_val, data in items_by_code.items():
        source = data.get("source", "python")
        src_badge = {"llm": "🤖AI", "python": "⚙️PY", "hybrid": "⚙️HY", "fallback": "⚙️FB"}.get(source, source)
        lock_icon = " 🔒" if code_val in [c.value for c in MANDATORY_LOCK_CODES] else ""
        print(
            f"    {src_badge} [{code_val}{lock_icon}]  "
            f"adet={data.get('count', 1)}  "
            f"-{data.get('total_penalty', 0)} puan  "
            f"| {data.get('description', '')[:50]}"
        )


def _check_double_penalty(items_by_code: dict[str, Any]) -> None:
    """Aynı kodun hem python hem LLM kaynağından eklenip eklenmediğini kontrol eder."""
    code_sources: dict[str, list[str]] = {}
    for code_val, data in items_by_code.items():
        code_sources.setdefault(code_val, []).append(data.get("source", ""))
    duplicates = {k: v for k, v in code_sources.items() if len(v) > 1}
    if duplicates:
        print(f"    ⚠️  ÇİFT CEZA TESPİT EDİLDİ: {duplicates}")
    else:
        print("    ✅ Çifte ceza yok")


async def main() -> None:
    print("\n" + "=" * 75)
    print("PATH B — KOD BAZLI CEZA KARŞILAŞTIRMA RAPORU")
    print("=" * 75)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        tasks = [
            run_scenario(sc, tmp, delay_s=i * 0.3)
            for i, sc in enumerate(COMPARE_SCENARIOS)
        ]
        results = await asyncio.gather(*tasks)

    for res in sorted(results, key=lambda r: r["id"]):
        sc_id = res["id"]
        score = res["score"]
        sb: dict[str, Any] = res["score_breakdown"]
        items_by_code: dict[str, Any] = sb.get("items_by_code") or {}
        llm_used: bool = bool(sb.get("llm_used", False))
        mandatory_locks = res["mandatory_lock_triggered"]

        in_range = res["expected_min"] <= score <= res["expected_max"]
        range_mark = "✅" if in_range else "❌"

        print(f"\n{'─' * 60}")
        print(
            f"  {sc_id} — {res['label']}\n"
            f"  Skor: {score}/100  ({res['risk_category']})  "
            f"{range_mark} [{res['expected_min']}-{res['expected_max']}]"
        )
        print(
            f"  LLM: {'aktif' if llm_used else '⚠️ devre dışı (fallback)'}  "
            f"| py=-{sb.get('python_penalty', 0)}  "
            f"llm=-{sb.get('llm_penalty', 0)}  "
            f"belge=-{sb.get('doc_penalty', 0)}"
        )
        if mandatory_locks:
            print(f"  🔒 Finansman kilidi: {mandatory_locks}")
        print(f"  Ceza maddeleri ({len(items_by_code)}):")
        _print_items_by_code(items_by_code)
        _check_double_penalty(items_by_code)
        if sb.get("llm_summary"):
            print(f"  AI Yorumu: {sb['llm_summary'][:120]!r}")

    # Özet
    print("\n" + "=" * 75)
    passed = sum(
        1 for r in results
        if r["expected_min"] <= r["score"] <= r["expected_max"]
    )
    total = len(results)
    print(f"ÖZET: {passed}/{total} senaryo beklenen aralıkta")
    print(f"Matris versiyonu: {results[0]['score_breakdown'].get('matrix_version', '?') if results else '?'}")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(main())
