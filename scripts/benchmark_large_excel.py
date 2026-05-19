#!/usr/bin/env python3
"""Büyük Excel okuma / preflight süre ve bellek ölçümü (manuel)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import upload_loader
from app.services.upload_preflight import inspect_upload
from tests.helpers.large_excel import write_invoice_xlsx
from tests.helpers.pipeline_score import extract_score_report, run_upload_to_final_score


def _memory_mb() -> float | None:
    try:
        import psutil

        proc = psutil.Process()
        return proc.memory_info().rss / (1024 * 1024)
    except ImportError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Büyük Excel benchmark")
    parser.add_argument("--rows", type=int, default=40_000, help="Veri satır sayısı")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "tests" / "artifacts" / "benchmark_large.xlsx",
        help="Çıktı xlsx yolu",
    )
    parser.add_argument(
        "--with-score",
        action="store_true",
        help="Tam analiz (Collector→Decision) ve calculated_score yazdır",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Üretiliyor: {args.rows} satır → {out}")
    t0 = time.perf_counter()
    write_invoice_xlsx(out, args.rows)
    print(f"  yazma: {time.perf_counter() - t0:.2f}s")

    mem_before = _memory_mb()
    if mem_before is not None:
        print(f"  bellek (önce): {mem_before:.1f} MB")

    t1 = time.perf_counter()
    loaded = upload_loader.load_uploaded_file(str(out))
    load_s = time.perf_counter() - t1
    row_count = len(loaded["tabular"]["rows"])
    print(f"Okuma: {load_s:.2f}s — {row_count} satır")

    t2 = time.perf_counter()
    result = inspect_upload(str(out))
    preflight_s = time.perf_counter() - t2
    print(
        f"Preflight: {preflight_s:.2f}s — ok={result.ok} "
        f"fatura={result.invoice_count}"
    )

    mem_after = _memory_mb()
    if mem_after is not None:
        print(f"  bellek (sonra): {mem_after:.1f} MB")

    if not result.ok:
        for issue in result.issues[:5]:
            print(f"  ! {issue.message_tr}")
        sys.exit(1)

    if args.with_score:
        t3 = time.perf_counter()
        final = run_upload_to_final_score(out)
        score_s = time.perf_counter() - t3
        status = final.get("analysis_status")
        score, report = extract_score_report(final)
        print(f"Analiz: {score_s:.2f}s — status={status}")
        if score is not None:
            print(f"  Nihai skor: {score}/100")
            print(f"  Risk bandı: {report.get('risk_category', '?')}")
            fin = report.get("finance_eligibility") or {}
            print(f"  Finansman: {fin.get('eligible', '?')} — {fin.get('reason', '')}")
        else:
            print(f"  Skor yok — error={final.get('error_state')!r}")
            sys.exit(1)

    print("Tamamlandı.")


if __name__ == "__main__":
    main()
