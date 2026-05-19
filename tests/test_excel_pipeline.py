"""
İadeAjan — Tüm Format Pipeline Testi
Desteklenen formatlar: xlsx / xls / csv / json
4 katman: dosya okuma → dönüşüm → collector → tam workflow
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# Proje kökünü sys.path'e ekle (tests/ içinden import)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import upload_loader, ai_converter
from app.agents.collector_agent import collector_node
from app.graph.workflow import build_graph


# ── Renk sabitleri ──────────────────────────────────────────────────────────

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    GREY    = "\033[90m"


# ── Test dosya tanımları ─────────────────────────────────────────────────────

TEST_FILES: list[dict[str, Any]] = [
    {
        "id":      "xlsx-01",
        "fmt":     "xlsx",
        "path":    PROJECT_ROOT / "test_excels" / "test_excel_01_karma.xlsx",
        "rows":    4,
        "cols":    7,
        "mode":    "tabular",
        "desc":    "Tam sütunlar, karma evrak türleri",
        "answers": {"q_refund_type_001": "ihracat", "q_gumruk_001": "Evet"},
    },
    {
        "id":      "xlsx-02",
        "fmt":     "xlsx",
        "path":    PROJECT_ROOT / "test_excels" / "test_excel_02_eksik_sutun.xlsx",
        "rows":    3,
        "cols":    4,
        "mode":    "tabular",
        "desc":    "Eksik Evrak Türü sütunu",
        "answers": {"q_refund_type_001": "tevkifat"},
    },
    {
        "id":      "xlsx-03",
        "fmt":     "xlsx",
        "path":    PROJECT_ROOT / "test_excels" / "test_excel_03_karisik_tipler.xlsx",
        "rows":    4,
        "cols":    7,
        "mode":    "tabular",
        "desc":    "Bozuk tarih, None, yanlış tip değerleri",
        "answers": {"q_refund_type_001": "ihracat", "q_gumruk_001": "Evet"},
    },
    {
        "id":      "csv-04",
        "fmt":     "csv",
        "path":    PROJECT_ROOT / "test_excels" / "test_excel_04_karma.csv",
        "rows":    4,
        "cols":    7,
        "mode":    "tabular",
        "desc":    "UTF-8 BOM CSV, karma evrak türleri",
        "answers": {"q_refund_type_001": "ihracat", "q_gumruk_001": "Evet"},
    },
    {
        "id":      "xls-05",
        "fmt":     "xls",
        "path":    PROJECT_ROOT / "test_excels" / "test_excel_05_karma.xls",
        "rows":    4,
        "cols":    7,
        "mode":    "tabular",
        "desc":    "Gerçek BIFF8 XLS formatı",
        "answers": {"q_refund_type_001": "ihracat", "q_gumruk_001": "Evet"},
    },
    {
        "id":      "json-06",
        "fmt":     "json",
        "path":    PROJECT_ROOT / "demo_samples" / "senaryo_a_ihracat.json",
        "rows":    15,
        "cols":    None,
        "mode":    "canonical",
        "desc":    "Canonical JSON — 15 fatura, ihracat senaryosu",
        "answers": {"q_refund_type_001": "ihracat", "q_gumruk_001": "Evet"},
    },
]

# ── Yardımcı fonksiyonlar ────────────────────────────────────────────────────

passed_total = 0
failed_total = 0
skip_total   = 0


def _ok(msg: str) -> None:
    global passed_total
    passed_total += 1
    print(f"    {C.GREEN}✅ {msg}{C.RESET}")


def _fail(msg: str) -> None:
    global failed_total
    failed_total += 1
    print(f"    {C.RED}❌ {msg}{C.RESET}")


def _skip(msg: str) -> None:
    global skip_total
    skip_total += 1
    print(f"    {C.YELLOW}⚠️  SKIP — {msg}{C.RESET}")


def _info(msg: str) -> None:
    print(f"    {C.GREY}{msg}{C.RESET}")


def _section(title: str) -> None:
    print(f"\n{C.BOLD}{C.CYAN}─── {title} {'─' * max(0, 58 - len(title))}{C.RESET}")


def _file_header(case: dict[str, Any]) -> None:
    fmt  = case["fmt"].upper().ljust(4)
    name = case["path"].name
    desc = case["desc"]
    print(f"\n  {C.BOLD}{C.MAGENTA}[{case['id']}]{C.RESET} {fmt}  {name}  — {desc}")


def _xlrd_available() -> bool:
    return importlib.util.find_spec("xlrd") is not None


def _is_real_xls(path: Path) -> bool:
    """Dosyanın gerçek BIFF8 XLS olup olmadığını magic bytes ile kontrol et."""
    try:
        magic = path.read_bytes()[:4]
        return magic == b"\xd0\xcf\x11\xe0"  # Compound Document (OLE2)
    except Exception:
        return False


# ── KATMAN 1: Dosya Okuma (upload_loader) ───────────────────────────────────

def run_layer1() -> dict[str, Any]:
    """Her dosyayı upload_loader ile oku; mode, satır ve sütun sayısını doğrula."""
    _section("KATMAN 1: Dosya Okuma (upload_loader)")
    results: dict[str, Any] = {}

    for case in TEST_FILES:
        _file_header(case)
        path = case["path"]

        # XLS özel kontrol
        if case["fmt"] == "xls":
            if not _xlrd_available():
                _skip("xlrd kurulu değil — xls okunamaz")
                results[case["id"]] = None
                continue
            if not _is_real_xls(path):
                _skip("Dosya gerçek BIFF8 XLS değil — xlrd okuyamaz")
                results[case["id"]] = None
                continue

        if not path.exists():
            _fail(f"Dosya bulunamadı: {path}")
            results[case["id"]] = None
            continue

        try:
            loaded = upload_loader.load_uploaded_file(str(path))
        except Exception as exc:
            _fail(f"upload_loader hatası: {exc}")
            results[case["id"]] = None
            continue

        results[case["id"]] = loaded
        mode = loaded.get("mode")

        # Mode kontrolü
        if mode == case["mode"]:
            _ok(f"mode={mode}")
        else:
            _fail(f"mode={mode!r} beklenen={case['mode']!r}")

        # Satır / sütun kontrolü
        if mode == "tabular":
            tabular = loaded["tabular"]
            row_count = len(tabular["rows"])
            col_count = len(tabular["columns"])
            if row_count == case["rows"]:
                _ok(f"{row_count} satır")
            else:
                _fail(f"{row_count} satır (beklenen {case['rows']})")
            if case["cols"] and col_count == case["cols"]:
                _ok(f"{col_count} sütun")
            else:
                _fail(f"{col_count} sütun (beklenen {case['cols']})")
            _info(f"Sütunlar: {tabular['columns']}")

        elif mode == "canonical":
            invoice_count = len(loaded["canonical"].get("invoices", []))
            if invoice_count == case["rows"]:
                _ok(f"{invoice_count} fatura (canonical)")
            else:
                _fail(f"{invoice_count} fatura (beklenen {case['rows']})")

    return results


# ── KATMAN 2: AI Converter (dönüşüm) ────────────────────────────────────────

def run_layer2(layer1_results: dict[str, Any]) -> dict[str, Any]:
    """Tabular dosyaları canonical formata çevir; hata vermemeli, fatura listesi dolu olmalı."""
    _section("KATMAN 2: AI Converter (tabular → canonical)")
    results: dict[str, Any] = {}

    for case in TEST_FILES:
        _file_header(case)
        loaded = layer1_results.get(case["id"])

        if case["mode"] == "canonical":
            _info("JSON canonical format — dönüşüm gerekmedi, atlanıyor")
            results[case["id"]] = loaded["canonical"] if loaded else None
            continue

        if loaded is None:
            _skip("Katman 1 başarısız — atlanıyor")
            results[case["id"]] = None
            continue

        tabular = loaded["tabular"]
        try:
            canonical = ai_converter.convert_tabular_to_canonical(
                rows=tabular["rows"],
                columns=tabular["columns"],
                source_name=tabular.get("source_name", ""),
            )
        except Exception as exc:
            _fail(f"convert_tabular_to_canonical hatası: {exc}")
            results[case["id"]] = None
            continue

        results[case["id"]] = canonical
        invoices = canonical.get("invoices", [])
        suppliers = canonical.get("suppliers", [])

        if invoices:
            _ok(f"{len(invoices)} fatura dönüştürüldü")
        else:
            _fail("invoices listesi boş")

        # Fatura alan kontrolleri
        first = invoices[0] if invoices else {}
        if first.get("id"):
            _ok(f"invoice_id mevcut: {first['id']!r}")
        else:
            _fail("invoice_id boş")

        if first.get("amount", 0) > 0:
            _ok(f"amount mevcut: {first['amount']}")
        else:
            _fail(f"amount 0 veya boş: {first.get('amount')}")

        # Tip eşleme
        types = [inv.get("type") for inv in invoices]
        _info(f"Evrak türleri: {types}")

        # Tedarikçi bilgisi
        if suppliers:
            _ok(f"{len(suppliers)} tedarikçi çıkarıldı")
        else:
            _info("Tedarikçi verisi yok (normal olabilir)")

    return results


# ── KATMAN 3: Collector Node (upload modu) ──────────────────────────────────

def run_layer3() -> dict[str, Any]:
    """
    collector_node'u her dosya için upload modu ile çalıştır.
    analysis_status='running' olmalı ve mock fallback'e DÜŞMEMELİ.
    """
    _section("KATMAN 3: Collector Node (upload modu)")
    results: dict[str, Any] = {}

    for case in TEST_FILES:
        _file_header(case)
        path = case["path"]

        # XLS gerçeklik kontrolü
        if case["fmt"] == "xls" and not _is_real_xls(path):
            _skip("Gerçek BIFF8 XLS değil — xlrd okuyamaz")
            results[case["id"]] = None
            continue

        state = {
            "uploaded_files": [str(path)],
            "agent_logs": [],
        }

        try:
            out = collector_node(state)
        except Exception as exc:
            _fail(f"collector_node çöktü: {exc}")
            results[case["id"]] = None
            continue

        results[case["id"]] = out
        status  = out.get("analysis_status")
        error   = out.get("error_state")
        invoices = out.get("normalized_invoices", [])
        refund  = out.get("classification_result", {}).get("refund_type", "?")

        # Fallback tespiti: error_state doluysa upload başarısız oldu
        upload_failed = bool(error and "COLLECTOR_UPLOAD_ERROR" in str(error))

        if status == "running" and not upload_failed:
            _ok(f"status=running | upload başarılı")
        elif upload_failed:
            _fail(f"Upload başarısız (mock senaryoya düştü): {error}")
        else:
            _fail(f"status={status!r} | error={error!r}")

        if invoices:
            _ok(f"{len(invoices)} normalize fatura")
        else:
            _fail("normalized_invoices boş")

        _info(f"İade türü tespiti: {refund}")

    return results


# ── KATMAN 4: Tam Workflow (graph.invoke) ────────────────────────────────────

def run_layer4() -> None:
    """
    Her dosya için tam graph.invoke çalıştır.
    Sistem çökmemeli; completed veya clarification_waiting ile bitmeli.
    """
    _section("KATMAN 4: Tam Workflow (graph.invoke)")
    graph = build_graph()

    for case in TEST_FILES:
        _file_header(case)
        path = case["path"]

        # XLS gerçeklik kontrolü
        if case["fmt"] == "xls" and not _is_real_xls(path):
            _skip("Gerçek BIFF8 XLS değil — xlrd okuyamaz")
            continue

        state: dict[str, Any] = {
            "uploaded_files": [str(path)],
            "agent_logs": [],
            "clarification_answers": case.get("answers", {}),
        }

        try:
            final = graph.invoke(state)
        except Exception as exc:
            _fail(f"graph.invoke çöktü: {exc}")
            continue

        status = final.get("analysis_status")
        report = final.get("final_report") or {}
        score  = report.get("calculated_score")
        logs   = final.get("agent_logs", [])
        error  = final.get("error_state")

        # Upload başarı kontrolü
        upload_ok = not (error and "COLLECTOR_UPLOAD_ERROR" in str(error))

        if status in ("completed", "clarification_waiting") and upload_ok:
            _ok(f"status={status}")
        else:
            _fail(f"status={status!r} | error={error!r}")

        if status == "completed":
            if score is not None and 0 <= score <= 100:
                _ok(f"calculated_score={score}/100")
            else:
                _fail(f"calculated_score={score!r} (0-100 aralığında değil)")

            risk = report.get("risk_category", "?")
            decision = report.get("approval_status", "?")
            _info(f"Risk: {risk} | Karar: {decision}")

        # Log zinciri kontrolü
        has_collector = any("[CollectorAgent]" in lg for lg in logs)
        has_analyzer  = any("[AnalyzerAgent]" in lg for lg in logs)
        if has_collector and has_analyzer:
            _ok(f"Log zinciri tam — {len(logs)} satır")
        else:
            _fail(
                f"Log zinciri eksik "
                f"(collector={'✓' if has_collector else '✗'}, "
                f"analyzer={'✓' if has_analyzer else '✗'})"
            )


# ── Ana çalışma ──────────────────────────────────────────────────────────────

def main() -> None:
    width = 66
    print(f"\n{C.BOLD}{C.CYAN}{'═' * width}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  İadeAjan — Tüm Format Pipeline Testi (xlsx/xls/csv/json){C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'═' * width}{C.RESET}")

    # Katman sırası: her katman bir sonrakini besliyor
    layer1_results = run_layer1()
    layer2_results = run_layer2(layer1_results)   # noqa: F841 (ileride genişletilebilir)
    _                = run_layer3()
    run_layer4()

    # ── Özet ──
    toplam = passed_total + failed_total + skip_total
    print(f"\n{C.BOLD}{C.CYAN}{'═' * width}{C.RESET}")
    if failed_total == 0:
        print(
            f"{C.BOLD}{C.GREEN}  ÖZET: {passed_total}/{toplam} test geçti"
            + (f" | {skip_total} SKIP" if skip_total else "")
            + f" | 0 hata{C.RESET}"
        )
    else:
        print(
            f"{C.BOLD}{C.YELLOW}  ÖZET: {passed_total} geçti | "
            f"{C.RED}{failed_total} hatalı{C.YELLOW} | "
            f"{skip_total} SKIP | toplam {toplam}{C.RESET}"
        )
    print(f"{C.BOLD}{C.CYAN}{'═' * width}{C.RESET}\n")


if __name__ == "__main__":
    main()
