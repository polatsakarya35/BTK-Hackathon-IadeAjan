#!/usr/bin/env python3
"""İadeAjan doğruluk benchmark — ground-truth YAML vs pipeline çıktısı.

Kullanım:
  PYTHONPATH=. python tests/accuracy_benchmark.py --mode llm
  PYTHONPATH=. python tests/accuracy_benchmark.py --mode deterministic
  PYTHONPATH=. python tests/accuracy_benchmark.py --mode both
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.graph.workflow import build_graph  # noqa: E402
from tests.fixtures.accuracy_scenarios import ALL_SCENARIOS, SCENARIO_BY_ID  # noqa: E402
from tests.fixtures.pdf_generator import write_minimal_pdf  # noqa: E402

GT_PATH = Path(__file__).parent / "fixtures" / "accuracy_ground_truth.yaml"
REPORTS_DIR = Path(__file__).parent / "reports"
OUT_EXCEL_DIR = ROOT / "test_excels_benchmark"


def _normalize_code(raw: Any) -> str:
    if raw is None:
        return ""
    if hasattr(raw, "value"):
        return str(raw.value)
    s = str(raw)
    return s.split(".")[-1] if "." in s else s


def _codes_from_state(state: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for item in list(state.get("risk_items") or []) + list(state.get("missing_docs") or []):
        c = _normalize_code(item.get("code"))
        if c:
            codes.add(c)
    report = state.get("final_report") or {}
    for c in report.get("mandatory_lock_triggered") or []:
        nc = _normalize_code(c)
        if nc:
            codes.add(nc)
    return codes


def _mandatory_locks(state: dict[str, Any]) -> list[str]:
    report = state.get("final_report") or {}
    fin = report.get("finance_eligibility") or {}
    raw = fin.get("mandatory_lock_triggered") or report.get("mandatory_lock_triggered") or []
    return [_normalize_code(c) for c in raw if _normalize_code(c)]


def _load_ground_truth() -> dict[str, Any]:
    with GT_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _apply_mode_env(mode: str, gt: dict[str, Any]) -> None:
    cfg = (gt.get("modes") or {}).get(mode) or {}
    for key, val in (cfg.get("env") or {}).items():
        os.environ[key] = str(val)
    if mode == "llm":
        os.environ.setdefault("LLM_ANOMALY_ENABLED", "true")
    elif mode == "deterministic":
        os.environ["LLM_ANOMALY_ENABLED"] = "false"


def _has_llm_api_key() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def _build_preset(gt_scenario: dict[str, Any], defaults: dict[str, Any]) -> dict[str, str]:
    base = dict((defaults or {}).get("preset_answers") or {})
    refund = gt_scenario.get("refund_answer")
    if refund:
        base["q_refund_type_001"] = str(refund)
    return base


async def run_pipeline(
    filepaths: list[str],
    preset_answers: dict[str, str],
) -> dict[str, Any]:
    graph = build_graph(interrupt=False)
    initial_state: dict[str, Any] = {
        "session_id": "accuracy-benchmark",
        "uploaded_files": filepaths,
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
        "clarification_answers": preset_answers,
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


def _score_bounds(gt_scenario: dict[str, Any], mode: str) -> tuple[int, int]:
    if mode == "deterministic":
        lo = gt_scenario.get("deterministic_score_min")
        hi = gt_scenario.get("deterministic_score_max")
        if lo is not None and hi is not None:
            return int(lo), int(hi)
    if mode == "llm":
        lo = gt_scenario.get("llm_score_min")
        hi = gt_scenario.get("llm_score_max")
        if lo is not None and hi is not None:
            return int(lo), int(hi)
    return int(gt_scenario["expected_score_min"]), int(gt_scenario["expected_score_max"])


def _evaluate(
    scenario_id: str,
    gt_scenario: dict[str, Any],
    state: dict[str, Any],
    mode: str,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    report = state.get("final_report") or {}
    actual_score = report.get("calculated_score")
    actual_cat = report.get("risk_category", "-")
    codes = _codes_from_state(state)
    locks = _mandatory_locks(state)
    warnings = " ".join(state.get("process_warnings") or [])

    lo, hi = _score_bounds(gt_scenario, mode)
    cats = gt_scenario.get("expected_categories") or defaults.get("expected_categories") or []

    score_ok = actual_score is not None and lo <= int(actual_score) <= hi
    cat_ok = not cats or actual_cat in cats

    must_inc = set(gt_scenario.get("must_include_codes") or [])
    must_not = set(gt_scenario.get("must_not_include") or [])
    codes_ok = must_inc <= codes and not (must_not & codes)

    exp_locks = set(gt_scenario.get("expected_mandatory_locks") or [])
    locks_ok = exp_locks <= set(locks) if exp_locks else True

    warn_sub = gt_scenario.get("expect_process_warning_contains")
    warn_ok = True
    if warn_sub:
        warn_ok = warn_sub in warnings

    pdf_type = gt_scenario.get("expect_pdf_inventory_type")
    pdf_ok = True
    if pdf_type:
        inv = state.get("document_inventory") or []
        pdf_ok = any(d.get("type") == pdf_type for d in inv)

    passed = score_ok and cat_ok and codes_ok and locks_ok and warn_ok and pdf_ok

    return {
        "id": scenario_id,
        "label": gt_scenario.get("label", scenario_id),
        "mode": mode,
        "actual_score": actual_score,
        "actual_category": actual_cat,
        "expected_score": f"{lo}-{hi}",
        "expected_categories": cats,
        "codes_found": sorted(codes),
        "must_include": sorted(must_inc),
        "must_not": sorted(must_not),
        "locks": locks,
        "score_ok": score_ok,
        "cat_ok": cat_ok,
        "codes_ok": codes_ok,
        "locks_ok": locks_ok,
        "warn_ok": warn_ok,
        "pdf_ok": pdf_ok,
        "passed": passed,
        "process_warnings": state.get("process_warnings") or [],
        "score_breakdown": report.get("score_breakdown") or {},
        "finance_eligible": (report.get("finance_eligibility") or {}).get("eligible"),
        "error": None,
    }


async def _run_one(
    scenario_id: str,
    gt: dict[str, Any],
    out_dir: Path,
    mode: str,
    delay_s: float,
) -> dict[str, Any]:
    if delay_s:
        await asyncio.sleep(delay_s)

    sc = SCENARIO_BY_ID.get(scenario_id)
    gt_scenario = (gt.get("scenarios") or {}).get(scenario_id)
    if not sc or not gt_scenario:
        return {
            "id": scenario_id,
            "passed": False,
            "error": "Senaryo veya ground-truth bulunamadı",
            "mode": mode,
        }

    defaults = gt.get("defaults") or {}
    preset = _build_preset(gt_scenario, defaults)

    excel_path = out_dir / f"{scenario_id}.xlsx"
    sc["df"]().to_excel(excel_path, index=False)
    filepaths = [str(excel_path)]

    if gt_scenario.get("use_pdf") or sc.get("kind") == "excel_pdf":
        pdf_path = out_dir / f"{scenario_id}_gumruk_beyanname.pdf"
        write_minimal_pdf(pdf_path, title="GCB Mock")
        filepaths.append(str(pdf_path))

    try:
        state = await run_pipeline(filepaths, preset)
        return _evaluate(scenario_id, gt_scenario, state, mode, defaults)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return {
            "id": scenario_id,
            "label": gt_scenario.get("label", scenario_id),
            "mode": mode,
            "passed": False,
            "error": str(exc),
        }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    full = sum(1 for r in results if r.get("passed"))
    score_only = sum(
        1 for r in results
        if not r.get("passed") and r.get("score_ok") and r.get("cat_ok")
    )
    codes_only = sum(1 for r in results if r.get("codes_ok") and not r.get("passed"))
    return {
        "total": total,
        "full_pass": full,
        "full_accuracy_pct": round(100.0 * full / total, 1) if total else 0.0,
        "score_or_cat_partial": score_only,
        "codes_ok_count": sum(1 for r in results if r.get("codes_ok")),
        "codes_accuracy_pct": round(
            100.0 * sum(1 for r in results if r.get("codes_ok")) / total, 1
        )
        if total
        else 0.0,
        "codes_only": codes_only,
    }


def _write_report(mode: str, results: list[dict[str, Any]], summary: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    md_path = REPORTS_DIR / f"accuracy_{mode}_{ts}.md"
    json_path = REPORTS_DIR / f"accuracy_{mode}_{ts}.json"

    lines = [
        f"# İadeAjan doğruluk raporu — {mode}",
        f"",
        f"Tarih (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"",
        f"## Özet",
        f"",
        f"| Metrik | Değer |",
        f"|--------|-------|",
        f"| Senaryo sayısı | {summary['total']} |",
        f"| Tam isabet | {summary['full_pass']}/{summary['total']} (%{summary['full_accuracy_pct']}) |",
        f"| Kod isabeti | {summary['codes_ok_count']}/{summary['total']} (%{summary['codes_accuracy_pct']}) |",
        f"",
        f"## Senaryo detayları",
        f"",
        f"| ID | Sonuç | Skor | Kategori | Kodlar OK | Not |",
        f"|----|-------|------|----------|-----------|-----|",
    ]
    for r in sorted(results, key=lambda x: x.get("id", "")):
        mark = "GEÇTI" if r.get("passed") else "FAIL"
        if r.get("error"):
            note = r["error"][:60]
        elif not r.get("passed"):
            parts = []
            if not r.get("score_ok"):
                parts.append(f"skor≠{r.get('expected_score')}")
            if not r.get("cat_ok"):
                parts.append("kategori")
            if not r.get("codes_ok"):
                parts.append("kod")
            if not r.get("warn_ok"):
                parts.append("uyarı")
            if not r.get("pdf_ok"):
                parts.append("pdf")
            note = ",".join(parts) or "-"
        else:
            note = "-"
        lines.append(
            f"| {r.get('id','?')} | {mark} | {r.get('actual_score','-')} | "
            f"{r.get('actual_category','-')} | {r.get('codes_ok', '-')} | {note} |"
        )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps({"mode": mode, "summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_path


async def run_mode(mode: str, gt: dict[str, Any], *, ids: list[str] | None = None) -> tuple[list[dict[str, Any]], Path]:
    _apply_mode_env(mode, gt)
    if mode == "llm" and not _has_llm_api_key():
        print(f"[UYARI] {mode}: API anahtarı yok — LLM kapalı davranışa düşebilir.")

    out_dir = OUT_EXCEL_DIR / mode
    out_dir.mkdir(parents=True, exist_ok=True)

    scenario_ids = ids or list((gt.get("scenarios") or {}).keys())
    print(f"\n{'=' * 72}\nDoğruluk benchmark — mod: {mode} ({len(scenario_ids)} senaryo)\n{'=' * 72}")

    tasks = [
        _run_one(sid, gt, out_dir, mode, delay_s=i * 0.6)
        for i, sid in enumerate(scenario_ids)
    ]
    results = await asyncio.gather(*tasks)
    results = sorted(results, key=lambda r: r.get("id", ""))

    for r in results:
        mark = "[GEÇTI]" if r.get("passed") else "[FAIL ]"
        sb = r.get("score_breakdown") or {}
        err = f" | HATA: {r['error']}" if r.get("error") else ""
        print(
            f"  {mark} {r.get('id')}: {r.get('actual_score')}/100 ({r.get('actual_category')}) "
            f"| kodlar={r.get('codes_found', [])}{err}"
        )
        if sb:
            print(
                f"         py={sb.get('python_penalty', 0)} llm={sb.get('llm_penalty', 0)} "
                f"belge={sb.get('doc_penalty', 0)} llm_used={sb.get('llm_used', False)}"
            )

    summary = _aggregate(results)
    print(
        f"\nÖzet [{mode}]: tam isabet {summary['full_pass']}/{summary['total']} "
        f"(%{summary['full_accuracy_pct']}), kod isabeti %{summary['codes_accuracy_pct']}"
    )
    report_path = _write_report(mode, results, summary)
    print(f"Rapor: {report_path}")
    return results, report_path


async def main_async(modes: list[str], scenario_filter: list[str] | None) -> None:
    load_dotenv(ROOT / ".env")
    gt = _load_ground_truth()
    all_results: dict[str, Any] = {}

    for mode in modes:
        results, path = await run_mode(mode, gt, ids=scenario_filter)
        all_results[mode] = {"summary": _aggregate(results), "report": str(path)}

    if len(modes) > 1:
        cmp_path = REPORTS_DIR / f"accuracy_compare_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        cmp_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nKarşılaştırma: {cmp_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="İadeAjan doğruluk benchmark")
    parser.add_argument(
        "--mode",
        choices=["llm", "deterministic", "both"],
        default="llm",
        help="Çalıştırma modu (varsayılan: llm)",
    )
    parser.add_argument(
        "--ids",
        nargs="*",
        help="Yalnızca bu senaryo ID'leri (örn. T01 T14 P01)",
    )
    args = parser.parse_args()
    modes = ["llm", "deterministic"] if args.mode == "both" else [args.mode]
    asyncio.run(main_async(modes, args.ids or None))


if __name__ == "__main__":
    main()
