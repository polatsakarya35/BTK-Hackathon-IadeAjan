"""v3 puanlama doğruluk testi — 10+ senaryo, manuel beklenen vs gerçek skor.

Her senaryo:
  1) Geçici Excel dosyası üretir
  2) Tüm pipeline'ı çalıştırır (collector → analyzer → clarification → decision)
  3) Beklenen aralık ile gerçek skoru karşılaştırır
  4) Doğruluk yüzdesini hesaplar
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Pipeline import'ları
from app.core.state import IadeAjanState  # noqa: E402
from app.graph.workflow import build_graph  # noqa: E402


# ── Önceden doldurulmuş clarification cevapları (sonsuz döngü koruması) ─────
# q_refund_type_001 KASTEN VERİLMEZ — Collector zaten faturalardan tespit eder.
# Sadece GÇB / 2No "Hayır" cevabıyla clarification döngüsünü sonlandır.
PRESET_ANSWERS = {
    "q_gumruk_001": "Hayır",
    "q_2no_001": "Hayır",
}


# ── Test Senaryoları ─────────────────────────────────────────────────────────
# Her senaryo: (etiket, df_üretici, beklenen_min, beklenen_max, kategori)
# Beklenen aralık tutturulursa "doğru" sayılır.

def _df_clean_export() -> pd.DataFrame:
    """T01 — Tamamen temiz ihracat verisi."""
    return pd.DataFrame([
        {"Fatura No": f"FAT-2025-{i:03d}", "Tip": "İhracat",
         "Tarih": "2025-03-15", "Tutar": 50000 + i * 1000, "KDV": 0,
         "VKN": "1234567890", "Tedarikçi": "ABC İhracat A.Ş."}
        for i in range(1, 11)
    ])


def _df_clean_domestic() -> pd.DataFrame:
    """T02 — Temiz iç satış (ihracat değil)."""
    return pd.DataFrame([
        {"Fatura No": f"FAT-2025-{i:03d}", "Tip": "Satış",
         "Tarih": "2025-06-15", "Tutar": 10000, "KDV": 1800,
         "VKN": "9876543210", "Tedarikçi": "İç Pazar Ltd."}
        for i in range(1, 6)
    ])


def _df_bad_dates_only() -> pd.DataFrame:
    """T03 — Sadece bozuk tarihler (5 fatura)."""
    return pd.DataFrame([
        {"Fatura No": f"FAT-{i}", "Tip": "İhracat",
         "Tarih": "bozuk_tarih", "Tutar": 25000, "KDV": 0,
         "VKN": "1111111111", "Tedarikçi": "Test A.Ş."}
        for i in range(1, 6)
    ])


def _df_zero_amounts() -> pd.DataFrame:
    """T04 — Sıfır tutarlı faturalar (3)."""
    return pd.DataFrame([
        {"Fatura No": f"FAT-{i}", "Tip": "İhracat",
         "Tarih": "2025-03-15", "Tutar": 0, "KDV": 0,
         "VKN": "2222222222", "Tedarikçi": "Sıfır A.Ş."}
        for i in range(1, 4)
    ])


def _df_invalid_vkn() -> pd.DataFrame:
    """T05 — Geçersiz VKN formatı (alfabetik)."""
    return pd.DataFrame([
        {"Fatura No": f"FAT-{i}", "Tip": "İhracat",
         "Tarih": "2025-03-15", "Tutar": 30000, "KDV": 0,
         "VKN": "abcdefgh", "Tedarikçi": "VKN Bozuk Ltd."}
        for i in range(1, 6)
    ])


def _df_all_data_quality_issues() -> pd.DataFrame:
    """T06 — Bozuk tarih + sıfır tutar + bozuk VKN (tüm DQ kategorileri)."""
    return pd.DataFrame([
        {"Fatura No": "F1", "Tip": "İhracat", "Tarih": "bozuk",
         "Tutar": 0, "KDV": 0, "VKN": "xyz", "Tedarikçi": "Sorunlu A.Ş."},
        {"Fatura No": "F2", "Tip": "İhracat", "Tarih": "yanlis",
         "Tutar": -100, "KDV": 0, "VKN": "abc", "Tedarikçi": "Sorunlu A.Ş."},
        {"Fatura No": "F3", "Tip": "İhracat", "Tarih": "2025-03-15",
         "Tutar": 5000, "KDV": 0, "VKN": "1234567890", "Tedarikçi": "Düzgün A.Ş."},
    ])


def _df_negative_amounts() -> pd.DataFrame:
    """T07 — Negatif tutarlar (LLM ve Python birlikte yakalamalı)."""
    return pd.DataFrame([
        {"Fatura No": f"NEG-{i}", "Tip": "İhracat",
         "Tarih": "2025-03-15", "Tutar": -50000, "KDV": 0,
         "VKN": "3333333333", "Tedarikçi": "Negatif A.Ş."}
        for i in range(1, 5)
    ])


def _df_tevkifat_clean() -> pd.DataFrame:
    """T08 — Tevkifat — temiz veri (2 No'lu beyanname eksik)."""
    return pd.DataFrame([
        {"Fatura No": f"TEV-{i:03d}", "Tip": "Alış",
         "Tarih": "2025-03-15", "Tutar": 20000, "KDV": 3600,
         "VKN": "4444444444", "Tedarikçi": "Tevkifat Tedarikçi A.Ş."}
        for i in range(1, 8)
    ])


def _df_large_amount() -> pd.DataFrame:
    """T09 — Büyük tutarlı ihracat (200k toplam) — YMM tetiklemeli."""
    return pd.DataFrame([
        {"Fatura No": f"BIG-{i:03d}", "Tip": "İhracat",
         "Tarih": "2025-03-15", "Tutar": 100000, "KDV": 0,
         "VKN": "5555555555", "Tedarikçi": "Büyük İhracat A.Ş."}
        for i in range(1, 3)
    ])


def _df_small_amount() -> pd.DataFrame:
    """T10 — Küçük tutar (<50k) — YMM zorunlu DEĞİL."""
    return pd.DataFrame([
        {"Fatura No": f"SML-{i:03d}", "Tip": "İhracat",
         "Tarih": "2025-03-15", "Tutar": 5000, "KDV": 0,
         "VKN": "6666666666", "Tedarikçi": "Küçük A.Ş."}
        for i in range(1, 3)
    ])


def _df_mixed_chaos() -> pd.DataFrame:
    """T11 — Kaos: bozuk tarih + sıfır + negatif + bozuk VKN + tekrar fatura."""
    return pd.DataFrame([
        {"Fatura No": "F-DUP", "Tip": "İhracat", "Tarih": "bozuk",
         "Tutar": 0, "KDV": 0, "VKN": "xx", "Tedarikçi": "A"},
        {"Fatura No": "F-DUP", "Tip": "İhracat", "Tarih": "yanlis",
         "Tutar": -500, "KDV": 0, "VKN": "yy", "Tedarikçi": "A"},
        {"Fatura No": "F1", "Tip": "İhracat", "Tarih": "2025-03-15",
         "Tutar": -1000, "KDV": 0, "VKN": "abc", "Tedarikçi": "B"},
        {"Fatura No": "F2", "Tip": "İhracat", "Tarih": "olmayan",
         "Tutar": 0, "KDV": 0, "VKN": "def", "Tedarikçi": "C"},
    ])


def _df_perfect_minimal() -> pd.DataFrame:
    """T12 — Tek temiz ihracat faturası, eksik tek belge GÇB."""
    return pd.DataFrame([
        {"Fatura No": "FAT-001", "Tip": "İhracat",
         "Tarih": "2025-03-15", "Tutar": 30000, "KDV": 0,
         "VKN": "7777777777", "Tedarikçi": "Mükemmel A.Ş."},
    ])


def _df_satis_only() -> pd.DataFrame:
    """T13 — Sadece iç satış, ihracat yok (rules tetiklenmemeli)."""
    return pd.DataFrame([
        {"Fatura No": f"SAT-{i}", "Tip": "Satış",
         "Tarih": "2025-03-15", "Tutar": 15000, "KDV": 2700,
         "VKN": "8888888888", "Tedarikçi": "Yurtiçi A.Ş."}
        for i in range(1, 8)
    ])


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "T01",
        "label": "Temiz ihracat (10 fatura)",
        "df": _df_clean_export,
        "refund_answer": "ihracat",
        "expected_min": 25,
        "expected_max": 65,
        "expected_category": "Yüksek Risk",
        "expected_categories": ["Yüksek Risk", "Orta Risk"],
        # py=0, belge=35
        # LLM=cap40 → 100-0-40-35 = 25 → Yüksek Risk
        # LLM=0    → 100-0-0-35  = 65 → Orta Risk (LLM anomali bulamazsa)
        "note": "py=0, GÇB -35; LLM=40→25(Yüksek), LLM=0→65(Orta) — genellikle 25",
    },
    {
        "id": "T02",
        "label": "İç satış + tevkifat (2No eksik)",
        "df": _df_clean_domestic,
        "refund_answer": "tevkifat",
        "expected_min": 25,
        "expected_max": 65,
        "expected_category": "Yüksek Risk",
        "expected_categories": ["Yüksek Risk", "Orta Risk"],
        # py=0, belge=35 (2No)
        # LLM=cap40 → 25 → Yüksek; LLM=0 → 65 → Orta
        "note": "py=0, 2No -35; LLM=40→25(Yüksek), LLM=0→65(Orta)",
    },
    {
        "id": "T03",
        "label": "Sadece bozuk tarihler (5 fatura)",
        "df": _df_bad_dates_only,
        "refund_answer": "ihracat",
        "expected_min": 10,
        "expected_max": 50,
        "expected_category": "Yüksek Risk",
        # Path B: DQ_INVALID_DATE(5) → LLM veya fallback → -min(25,15) = -15; GCB_MISSING = -35
        # LLM=+DQ_only → 100-15-35 = 50; LLM=+DQ+anomali(max35) → 10+
        # 50 < 60 → her koşulda Yüksek Risk
        "note": "[PathB] DQ_INVALID_DATE max=-15, GCB_MISSING=-35; 10-50 Yüksek Risk",
    },
    {
        "id": "T04",
        "label": "Sıfır tutarlı faturalar (3)",
        "df": _df_zero_amounts,
        "refund_answer": "ihracat",
        "expected_min": 15,
        "expected_max": 55,
        "expected_category": "Yüksek Risk",
        # py=10 (3× sıfır tutar, tavan -10), belge=35
        # LLM=cap40 → 15, LLM=0 → 55
        # 55 < 60 → her koşulda Yüksek Risk
        "note": "py=10, GÇB -35; LLM=40→15, LLM=0→55 — hep Yüksek Risk",
    },
    {
        "id": "T05",
        "label": "Geçersiz VKN (5 fatura)",
        "df": _df_invalid_vkn,
        "refund_answer": "ihracat",
        "expected_min": 16,
        "expected_max": 56,
        "expected_category": "Yüksek Risk",
        # py=9 (5× geçersiz VKN, tavan -9), belge=35
        # LLM=cap40 → 16, LLM=0 → 56
        # 56 < 60 → her koşulda Yüksek Risk
        "note": "py=9, GÇB -35; LLM=40→16, LLM=0→56 — hep Yüksek Risk",
    },
    {
        "id": "T06",
        "label": "Tüm DQ sorunları birlikte (3 fatura)",
        "df": _df_all_data_quality_issues,
        "refund_answer": "ihracat",
        "expected_min": 0,
        "expected_max": 50,
        "expected_category": "Yüksek Risk",
        # Path B: DQ via LLM — DQ_INVALID_DATE(2)=-10, DQ_ZERO_AMOUNT(2)=-10, DQ_INVALID_VKN(2)=-6 → -26
        # GCB_MISSING = -35 → 100-26-35 = 39 (LLM=DQ only)
        # LLM ek anomali: 0-39, generous max=50; 50 < 60 → hep Yüksek Risk
        "note": "[PathB] LLM DQ toplam max=-26, GCB_MISSING=-35; 0-50 Yüksek Risk",
    },
    {
        "id": "T07",
        "label": "Negatif tutarlar (4 fatura)",
        "df": _df_negative_amounts,
        "refund_answer": "ihracat",
        "expected_min": 15,
        "expected_max": 55,
        "expected_category": "Yüksek Risk",
        # py=10 (4× negatif tutar, tavan -10), belge=35
        # LLM=cap40 → 15, LLM=0 → 55
        # 55 < 60 → her koşulda Yüksek Risk
        "note": "py=10, GÇB -35; LLM=40→15, LLM=0→55 — hep Yüksek Risk",
    },
    {
        "id": "T08",
        "label": "Tevkifat temiz veri",
        "df": _df_tevkifat_clean,
        "refund_answer": "tevkifat",
        "expected_min": 25,
        "expected_max": 65,
        "expected_category": "Yüksek Risk",
        "expected_categories": ["Yüksek Risk", "Orta Risk"],
        # py=0, belge=35 (2No)
        # LLM=cap40 → 25 → Yüksek; LLM=0 → 65 → Orta
        "note": "py=0, 2No -35; LLM=40→25(Yüksek), LLM=0→65(Orta)",
    },
    {
        "id": "T09",
        "label": "Büyük tutar (200k) → YMM zorunlu",
        "df": _df_large_amount,
        "refund_answer": "ihracat",
        "expected_min": 25,
        "expected_max": 65,
        "expected_category": "Yüksek Risk",
        "expected_categories": ["Yüksek Risk", "Orta Risk"],
        # py=0, belge=35 (GÇB); upload'da estimated_amount=0 → YMM tetiklenmez
        # LLM=cap40 → 25 → Yüksek; LLM=0 → 65 → Orta
        "note": "py=0, GÇB -35; YMM upload'da tetiklenmiyor; LLM=40→25, LLM=0→65",
    },
    {
        "id": "T10",
        "label": "Küçük tutar (10k) → YMM gerekmez",
        "df": _df_small_amount,
        "refund_answer": "ihracat",
        "expected_min": 25,
        "expected_max": 65,
        "expected_category": "Yüksek Risk",
        "expected_categories": ["Yüksek Risk", "Orta Risk"],
        # py=0, belge=35 (GÇB); 10k < 50k → YMM yok
        # LLM=cap40 → 25 → Yüksek; LLM=0 → 65 → Orta
        "note": "py=0, GÇB -35; 10k < 50k YMM yok; LLM=40→25, LLM=0→65",
    },
    {
        "id": "T11",
        "label": "Kaos: tüm sorunlar",
        "df": _df_mixed_chaos,
        "refund_answer": "ihracat",
        "expected_min": 0,
        "expected_max": 50,
        "expected_category": "Yüksek Risk",
        # Path B: DQ_INVALID_DATE(1)=-5, DQ_ZERO_AMOUNT(2)=-10, DQ_INVALID_VKN(2)=-6 → -21
        # GCB_MISSING = -35 → 100-21-35 = 44 (LLM=DQ only)
        # LLM ek anomali için max=50 toleransı; 50 < 60 → hep Yüksek Risk
        "note": "[PathB] LLM DQ=-21, GCB_MISSING=-35; 0-50 Yüksek Risk",
    },
    {
        "id": "T12",
        "label": "Tek temiz ihracat faturası",
        "df": _df_perfect_minimal,
        "refund_answer": "ihracat",
        "expected_min": 25,
        "expected_max": 65,
        "expected_category": "Yüksek Risk",
        "expected_categories": ["Yüksek Risk", "Orta Risk"],
        # py=0, belge=35 (GÇB)
        # LLM=cap40 → 25 → Yüksek; LLM=0 → 65 → Orta
        "note": "py=0, GÇB -35; LLM=40→25(Yüksek), LLM=0→65(Orta)",
    },
    {
        "id": "T13",
        "label": "Sadece iç satış (ihracat değil)",
        "df": _df_satis_only,
        "refund_answer": "tevkifat",
        "expected_min": 25,
        "expected_max": 65,
        "expected_category": "Yüksek Risk",
        "expected_categories": ["Yüksek Risk", "Orta Risk"],
        # py=0, belge=35 (2No)
        # LLM=cap40 → 25 → Yüksek; LLM=0 → 65 → Orta
        "note": "py=0, 2No -35; LLM=40→25(Yüksek), LLM=0→65(Orta)",
    },
]


async def run_pipeline(
    filepath: str,
    preset_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Tek dosya için pipeline'ı çalıştır ve final state döndür.

    Collector `uploaded_files: list[str]` bekler — filepath stringlerinin listesi.
    """
    graph = build_graph(interrupt=False)

    initial_state: dict[str, Any] = {
        "session_id": "v3-test",
        "uploaded_files": [filepath],
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
        "clarification_answers": preset_answers or PRESET_ANSWERS,
        "clarification_message": "",
        "process_warnings": [],
        "final_report": {},
        "current_agent": "CollectorAgent",
        "company_profile": {},
        "refund_type": "belirsiz",
        "agent_logs": [],
        "retry_count": 0,
    }

    return await graph.ainvoke(initial_state, config={"recursion_limit": 50})


def in_range(value: int, lo: int, hi: int) -> bool:
    return lo <= value <= hi


async def _run_scenario(
    sc: dict[str, Any],
    out_dir: Path,
    delay_s: float = 0.0,
) -> dict[str, Any]:
    """Tek senaryo için Excel üret + pipeline çalıştır; sonuç dict döner."""
    if delay_s:
        await asyncio.sleep(delay_s)

    filepath = out_dir / f"{sc['id']}.xlsx"
    sc["df"]().to_excel(filepath, index=False)

    expected_cats = sc.get("expected_categories") or [sc["expected_category"]]
    cats_str = "/".join(expected_cats)

    answers = dict(PRESET_ANSWERS)
    if sc.get("refund_answer"):
        answers["q_refund_type_001"] = sc["refund_answer"]

    try:
        final = await run_pipeline(str(filepath), preset_answers=answers)
        report = final.get("final_report", {})
        actual_score = report.get("calculated_score")
        actual_category = report.get("risk_category", "-")
        approval = report.get("approval_status", "-")
        pending = report.get("pending_verifications", [])
        risk_items = final.get("risk_items", [])
        missing_docs = final.get("missing_docs", [])
        fe = report.get("finance_eligibility", {})

        score_ok = (
            actual_score is not None
            and in_range(int(actual_score), sc["expected_min"], sc["expected_max"])
        )
        cat_ok = actual_category in expected_cats
        passed = score_ok and cat_ok

        sb = report.get("score_breakdown") or {}
        mark = "[GEÇTI]" if passed else "[FAIL ]"
        print(
            f"   {mark} {sc['id']}: {actual_score}/100 ({actual_category}) "
            f"| py={sb.get('python_penalty', report.get('python_penalty', 0))} "
            f"llm={sb.get('llm_penalty', report.get('llm_penalty', 0))} "
            f"belge={report.get('doc_penalty', sb.get('doc_penalty', 0))} "
            f"llm_used={sb.get('llm_used', False)} | {approval}"
            + (f" | pending={len(pending)}" if pending else "")
        )
        py_pen = sb.get("python_penalty", report.get("python_penalty", 0))
        llm_pen = sb.get("llm_penalty", report.get("llm_penalty", 0))
        doc_pen = report.get("doc_penalty", sb.get("doc_penalty", 0))
        return {
            "id": sc["id"], "label": sc["label"],
            "expected": f"{sc['expected_min']}-{sc['expected_max']}",
            "actual": actual_score,
            "expected_cat": cats_str,
            "actual_cat": actual_category,
            "score_ok": score_ok, "cat_ok": cat_ok, "passed": passed,
            "approval": approval, "pending": len(pending),
            "risk_count": len(risk_items),
            "missing_count": len(missing_docs),
            "finance": fe.get("eligible"),
            "py_pen": py_pen,
            "llm_pen": llm_pen,
            "doc_pen": doc_pen,
        }
    except Exception as exc:
        import traceback
        print(f"   [HATA] {sc['id']}: {exc}")
        traceback.print_exc()
        return {"id": sc["id"], "label": sc["label"], "actual": None, "passed": False, "error": str(exc)}


async def main() -> None:
    out_dir = ROOT / "test_excels_v3"
    out_dir.mkdir(exist_ok=True)

    print("\n" + "=" * 78)
    print("v3 PUANLAMA DOĞRULUK TESTİ — paralel çalıştırma")
    print("=" * 78)

    # Tüm senaryoları eşzamanlı çalıştır.
    # Gemini rate-limit koruması: her 2 senaryo arasına 0.5 sn gecikme.
    tasks = [
        _run_scenario(sc, out_dir, delay_s=i * 0.5)
        for i, sc in enumerate(SCENARIOS)
    ]
    results: list[dict[str, Any]] = await asyncio.gather(*tasks)
    # Çıktı sıralamasını ID'ye göre sabitle
    results = sorted(results, key=lambda r: r.get("id", ""))

    # ── Mevcut test_excels da test edilsin ───────────────────────────────────
    print("\n" + "=" * 78)
    print("MEVCUT test_excels — V3 SONRASI YENİDEN ÇALIŞTIRMA")
    print("=" * 78)

    existing = [
        ("test_excels/test_excel_01_karma.xlsx", "01_karma.xlsx"),
        ("test_excels/test_excel_02_eksik_sutun.xlsx", "02_eksik_sutun.xlsx"),
        ("test_excels/test_excel_03_karisik_tipler.xlsx", "03_karisik.xlsx"),
        ("test_excels/test_excel_04_karma.csv", "04_karma.csv"),
        ("test_excels/test_excel_05_karma.xls", "05_karma.xls"),
    ]

    async def _run_existing(path: str, label: str) -> None:
        existing_answers = dict(PRESET_ANSWERS, q_refund_type_001="ihracat")
        try:
            final = await run_pipeline(str(ROOT / path), preset_answers=existing_answers)
            report = final.get("final_report", {})
            sb = report.get("score_breakdown") or {}
            print(
                f"   {label}: {report.get('calculated_score')}/100 "
                f"({report.get('risk_category')}) | {report.get('approval_status')} "
                f"| py={sb.get('python_penalty', 0)} "
                f"llm={sb.get('llm_penalty', 0)} "
                f"belge={sb.get('doc_penalty', report.get('doc_penalty', 0))} "
                f"llm_used={sb.get('llm_used', False)}"
            )
        except Exception as exc:
            print(f"   [HATA] {label}: {exc}")

    await asyncio.gather(*[_run_existing(p, l) for p, l in existing])

    # ── ÖZET ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("ÖZET TABLO")
    print("=" * 78)
    print(
        f"{'ID':<4} {'Senaryo':<35} "
        f"{'Beklenen':<10} {'Gerçek':<8} {'Kat':<14} {'Sonuç':<8}"
    )
    print("-" * 78)
    passed_count = 0
    score_only_count = 0
    cat_only_count = 0
    for r in results:
        if r.get("passed"):
            passed_count += 1
            mark = "GEÇTI"
        elif r.get("score_ok"):
            score_only_count += 1
            mark = "K-FAIL"
        elif r.get("cat_ok"):
            cat_only_count += 1
            mark = "S-FAIL"
        else:
            mark = "FAIL"
        print(
            f"{r['id']:<4} {r['label'][:34]:<35} "
            f"{r.get('expected','-'):<10} "
            f"{str(r.get('actual','-')):<8} "
            f"{(r.get('actual_cat','-') or '-')[:13]:<14} "
            f"{mark:<8}"
        )

    total = len(results)
    full_accuracy = (passed_count / total) * 100 if total else 0
    score_accuracy = ((passed_count + score_only_count) / total) * 100 if total else 0
    cat_accuracy = ((passed_count + cat_only_count) / total) * 100 if total else 0

    print()
    print("=" * 78)
    print(f"TOPLAM: {total} senaryo")
    print(f"  [TAM] Skor + Kategori birlikte tutturuldu : {passed_count}/{total}")
    print(f"  [SKR] Sadece skor aralığı tutturuldu      : "
          f"{passed_count + score_only_count}/{total}")
    print(f"  [KAT] Sadece kategori tutturuldu          : "
          f"{passed_count + cat_only_count}/{total}")
    print()
    print(f"  DOĞRULUK ORANI (Tam tutturuldu)   : %{full_accuracy:.1f}")
    print(f"  DOĞRULUK ORANI (Skor aralığı)     : %{score_accuracy:.1f}")
    print(f"  DOĞRULUK ORANI (Risk kategorisi)  : %{cat_accuracy:.1f}")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
