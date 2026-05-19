#!/usr/bin/env python3
"""T03.xlsx tek senaryo doğrulama — skor ve risk dökümü."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.v3_accuracy_test import PRESET_ANSWERS, run_pipeline  # noqa: E402


async def main() -> None:
    path = ROOT / "test_excels_v3" / "T03.xlsx"
    if not path.is_file():
        print(f"Dosya yok: {path}")
        sys.exit(1)

    answers = dict(PRESET_ANSWERS, q_refund_type_001="ihracat")
    final = await run_pipeline(str(path), preset_answers=answers)
    report = final.get("final_report", {})

    score = report.get("calculated_score")
    category = report.get("risk_category")
    py = report.get("python_penalty", 0)
    llm = report.get("llm_penalty_capped", 0)
    llm_raw = report.get("llm_penalty_raw", 0)
    doc = report.get("doc_penalty", 0)

    print("=" * 60)
    print("T03 SONUÇ")
    print("=" * 60)
    print(f"Skor      : {score}/100 ({category})")
    print(f"Python    : -{py}")
    print(f"LLM       : -{llm} (ham: -{llm_raw})")
    print(f"Belge     : -{doc}")
    print(f"Beklenen  : 10-50, Yüksek Risk")
    print(f"Hak edilen (hedef): ~50 = 100 - 15(tarih) - 35(GÇB) - 0(LLM gürültü)")

    print("\n--- risk_items ---")
    for r in final.get("risk_items", []):
        src = r.get("source", "python")
        print(f"  [{src}] {r.get('score_impact')} | {r.get('title')}")

    print("\n--- missing_docs ---")
    for d in final.get("missing_docs", []):
        print(f"  {d.get('score_impact')} | {d.get('doc_name')}")

    ok_score = score is not None and 10 <= int(score) <= 50
    ok_cat = category == "Yüksek Risk"
    # Düzeltme sonrası hedef: LLM gürültüsü yok, skor 50 civarı
    ok_fair = score == 50 and llm_raw == 0
    print("\n" + "=" * 60)
    print(f"Aralık OK : {ok_score}")
    print(f"Kategori  : {ok_cat}")
    print(f"Hak edilen skor (50, LLM=0): {ok_fair}")
    print("=" * 60)

    if not (ok_score and ok_cat):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
