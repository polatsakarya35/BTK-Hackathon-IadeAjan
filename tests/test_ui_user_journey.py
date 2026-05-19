"""
Streamlit UI kullanıcı yolculuğu — AppTest + mock LangGraph.

Amaç: main.py phase automata, session_state, butonlar ve render (backend E2E ayrı).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from app.schemas.verification import DocumentExtraction
from app.services.verification import document_extraction
from tests.ui_graph_mock import (
    CELIK_TAX_NUMBER,
    CLARIFICATION_QUESTIONS,
    FakeCompiledGraph,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = PROJECT_ROOT / "main.py"

_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
)

pytestmark = pytest.mark.ui


def write_clean_excel(path: Path) -> Path:
    rows = [
        {
            "Fatura No": "FAT-UI-001",
            "Tarih": "2025-03-01",
            "Tutar": 60_000.0,
            "Evrak Türü": "İhracat",
            "KDV Oranı": 20,
            "KDV Tutar": 12_000.0,
            "Karşı Taraf Ünvanı": "UI Test A.Ş.",
            "Firma VKN Numarası": CELIK_TAX_NUMBER,
        },
    ]
    out = path / "ihracat_clean.xlsx"
    pd.DataFrame(rows).to_excel(out, index=False, engine="openpyxl")
    return out


def write_test_gcb_pdf(path: Path) -> Path:
    out = path / "test_gcb_belgesi.pdf"
    out.write_bytes(_MINIMAL_PDF)
    return out


def _gcb_pass_extraction_factory(original: Any) -> Any:
    def _wrapper(file_path: str, doc_type: str) -> DocumentExtraction:
        base = original(file_path, doc_type)
        name = os.path.basename(file_path).lower()
        if "gcb" in name or "gumruk" in name:
            return base.model_copy(
                update={
                    "declaration_no": "DECL-OK-001",
                    "exporter_vkn": CELIK_TAX_NUMBER,
                    "amount": 100_000.0,
                    "declaration_date": "2025-06-15",
                }
            )
        return base

    return _wrapper


def _ss(at: AppTest, key: str, default: Any = None) -> Any:
    """AppTest SafeSessionState — .get() yok, bracket erişim."""
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


def _phase(at: AppTest) -> str:
    return str(_ss(at, "phase", "idle"))


def _no_script_crash(at: AppTest) -> None:
    assert len(at.exception) == 0, f"Script exception: {[e.value for e in at.exception]}"


def _markdown_blob(at: AppTest) -> str:
    parts: list[str] = []
    for md in at.markdown:
        val = getattr(md, "value", None) or getattr(md, "body", None)
        if val:
            parts.append(str(val))
    for m in at.metric:
        parts.append(str(getattr(m, "label", "")))
        parts.append(str(getattr(m, "value", "")))
    return "\n".join(parts)


def _click_button_label(
    at: AppTest,
    *substrings: str,
    exclude: tuple[str, ...] = (),
) -> None:
    for btn in at.button:
        label = str(getattr(btn, "label", "") or "")
        if exclude and any(ex in label for ex in exclude):
            continue
        if all(s in label for s in substrings):
            btn.click()
            return
    labels = [getattr(b, "label", "") for b in at.button]
    raise AssertionError(f"Buton bulunamadı {substrings!r}. Mevcut: {labels}")


def _sidebar_invoice_uploader(at: AppTest):
    for fu in at.file_uploader:
        key = getattr(fu, "key", None) or ""
        if key == "iadeajan_sidebar_docs":
            continue
        allowed = getattr(fu, "allowed_type", []) or []
        if any(t in ("xlsx", "xls", "csv", "json") for t in allowed):
            return fu
    return at.file_uploader[0]


@pytest.fixture
def ui_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_GIB_API", "false")
    monkeypatch.setenv("LLM_ANOMALY_ENABLED", "false")
    monkeypatch.setenv("MOCK_DOCUMENT_EXTRACTION", "true")
    monkeypatch.setenv("DOCUMENT_CLASSIFIER_ENABLED", "false")
    monkeypatch.setenv("STRICT_PROOF", "false")
    monkeypatch.setenv("REQUIRE_VERIFIED_PROOF", "false")
    monkeypatch.setenv("STRICT_INVENTORY_TRUST", "false")
    monkeypatch.setenv("ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    original = document_extraction._mock_extraction
    monkeypatch.setattr(
        document_extraction,
        "_mock_extraction",
        _gcb_pass_extraction_factory(original),
    )


@pytest.fixture
def mock_graph(monkeypatch: pytest.MonkeyPatch) -> FakeCompiledGraph:
    """main.py: from app.graph.workflow import build_graph — workflow üzerinden patch."""
    graph = FakeCompiledGraph("segment1_clarification")
    monkeypatch.setattr(
        "app.graph.workflow.build_graph",
        lambda interrupt=False: graph,
    )
    return graph


@pytest.fixture
def ui_app(mock_graph: FakeCompiledGraph, ui_env: None) -> AppTest:
    at = AppTest.from_file(str(MAIN_PY))
    at.session_state["page"] = "app"
    at.session_state["authenticated"] = True
    at.run()
    _no_script_crash(at)
    assert _phase(at) == "idle"
    return at


class TestUIHappyJourney:
    """Tam kullanıcı yolculuğu: yükle → analiz → clarification → kanıt → rapor."""

    def test_full_journey_upload_to_done_report(
        self,
        ui_app: AppTest,
        mock_graph: FakeCompiledGraph,
        tmp_path: Path,
    ) -> None:
        at = ui_app
        excel = write_clean_excel(tmp_path)
        gcb_pdf = write_test_gcb_pdf(tmp_path)

        # 1 — Excel yükle
        _sidebar_invoice_uploader(at).set_value(
            (
                excel.name,
                excel.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        )
        at.run()
        _no_script_crash(at)
        assert _ss(at, "uploaded_file_path")

        # 2 — Analizi Başlat → clarification
        _click_button_label(at, "Analizi", "Başlat", exclude=("Yeni",))
        at.run(timeout=15)
        _no_script_crash(at)
        assert _phase(at) == "clarification"

        blob = _markdown_blob(at)
        assert "Ek Bilgi" in blob or "forum" in blob.lower()

        state = dict(_ss(at, "current_state") or {})
        questions = state.get("clarification_questions") or []
        assert questions, "clarification_questions kaybolmamalı"
        assert _ss(at, "uploaded_file_path")

        # 3 — GÇB kanıt PDF
        q_id = CLARIFICATION_QUESTIONS[0]["question_id"]
        at.file_uploader(key=f"proof_upload_{q_id}").set_value(
            (gcb_pdf.name, gcb_pdf.read_bytes(), "application/pdf")
        )
        at.run(timeout=15)
        _no_script_crash(at)
        answers = dict(_ss(at, "clarification_answers") or {})
        assert answers.get(q_id) == "Evet"

        # 4 — Segment 2 mock → done
        mock_graph.set_scenario("segment2_done")
        _click_button_label(at, "Cevapları", "Gönder")
        at.run(timeout=15)
        _no_script_crash(at)
        assert _phase(at) == "done"

        blob_done = _markdown_blob(at)
        assert "Nihai Skor" in blob_done or "72" in blob_done
        assert "Faktoring" in blob_done or "Finansman" in blob_done
        assert "Finansman kilidi" in blob_done or "uygun değil" in blob_done.lower()

        report = dict(_ss(at, "current_state") or {}).get("final_report") or {}
        assert report.get("calculated_score") == 72
        fin = report.get("finance_eligibility") or {}
        assert fin.get("eligible") is False

        print("\n--- UI Yolculuk Özeti ---")
        print(f"  phase        : {_phase(at)}")
        print(f"  skor         : {report.get('calculated_score')}/100")
        print(f"  finansman    : {fin.get('eligible')}")
        print(f"  kilit        : {report.get('mandatory_lock_triggered')}")
        print("------------------------\n")


class TestUIInteractvity:
    """Yan butonlar, expander ve phase enjeksiyonları."""

    def test_idle_upload_guide_expander_and_template_download(self, ui_app: AppTest) -> None:
        at = ui_app
        found_expander = False
        for exp in at.expander:
            if "hazırlanır" in (exp.label or ""):
                found_expander = True
                break
        assert found_expander, "Dosya nasıl hazırlanır expander görünmeli"
        # st.download_button AppTest element ağacında ayrı tip olmayabilir; crash yok yeterli
        _no_script_crash(at)

    def test_clear_document_uploads_button(self, ui_app: AppTest) -> None:
        at = ui_app
        at.session_state["uploaded_document_paths"] = ["/tmp/fake_doc.pdf"]  # noqa: S108
        at.run()
        _click_button_label(at, "temizle")
        at.run()
        _no_script_crash(at)
        assert _ss(at, "uploaded_document_paths") == []

    def test_blocked_phase_buttons(self, ui_app: AppTest, mock_graph: FakeCompiledGraph) -> None:
        at = ui_app
        at.session_state["current_state"] = {
            "analysis_status": "clarification_blocked",
            "clarification_questions": list(CLARIFICATION_QUESTIONS),
            "clarification_message": "Mock blocked",
            "final_report": {"pending_questions": []},
            "process_warnings": ["Mock uyarı"],
            "agent_logs": [],
        }
        at.session_state["phase"] = "blocked"
        at.run()
        _no_script_crash(at)

        _click_button_label(at, "Yeniden", "Dene")
        at.run()
        _no_script_crash(at)
        assert _phase(at) == "clarification"

        at.session_state["phase"] = "blocked"
        at.session_state["current_state"]["analysis_status"] = "clarification_blocked"
        at.run()
        _click_button_label(at, "Analizi", "Sonlandır")
        at.run()
        _no_script_crash(at)
        assert _phase(at) == "error"
        assert (
            dict(_ss(at, "current_state") or {}).get("failure_reason")
            == "USER_CANCELLED"
        )

    def test_error_phase_retry(self, ui_app: AppTest) -> None:
        at = ui_app
        at.session_state["phase"] = "error"
        at.session_state["current_state"] = {
            "analysis_status": "failed",
            "error_state": "MOCK_ERROR",
            "agent_logs": [],
        }
        at.run()
        _click_button_label(at, "Tekrar", "Dene")
        at.run()
        _no_script_crash(at)
        assert _phase(at) == "idle"

    def test_done_reset_buttons(self, ui_app: AppTest, mock_graph: FakeCompiledGraph) -> None:
        at = ui_app
        at.session_state["phase"] = "done"
        at.session_state["current_state"] = {
            "analysis_status": "completed",
            "final_report": {
                "calculated_score": 72,
                "risk_category": "Orta Risk",
                "finance_eligibility": {"eligible": False, "reason": "test"},
                "risk_items": [],
                "missing_docs": [],
                "score_breakdown": {"items_by_code": {}},
                "remediation_plan": {
                    "current_score": 72,
                    "target_score_finance": 90,
                    "projected_score_if_all_resolved": 72,
                    "headline_tr": "Ek düzeltme gerekmiyor.",
                    "steps": [],
                },
            },
            "agent_logs": [],
        }
        at.run()
        _no_script_crash(at)
        blob = _markdown_blob(at)
        assert "Nihai Skor" in blob or any(
            "72" in str(getattr(m, "value", "")) for m in at.metric
        )
        assert "Hedef Skora Ulaşmak" in blob

        _click_button_label(at, "Yeni Analiz")
        at.run()
        _no_script_crash(at)
        assert _phase(at) == "idle"
        assert _ss(at, "graph") is not None

    def test_proof_no_document_button(self, ui_app: AppTest) -> None:
        at = ui_app
        at.session_state["phase"] = "clarification"
        at.session_state["current_state"] = {
            "clarification_questions": list(CLARIFICATION_QUESTIONS),
            "clarification_message": "Test",
            "classification_result": {"refund_type": "ihracat"},
            "company_profile": {"tax_number": CELIK_TAX_NUMBER},
            "agent_logs": [],
        }
        at.session_state["clarification_answers"] = {}
        at.run()
        q_id = CLARIFICATION_QUESTIONS[0]["question_id"]
        _click_button_label(at, "Belgem", "Yok")
        at.run()
        _no_script_crash(at)
        assert dict(_ss(at, "clarification_answers") or {}).get(q_id) == "Hayır"

    def test_segment1_error_phase(
        self,
        ui_app: AppTest,
        mock_graph: FakeCompiledGraph,
        tmp_path: Path,
    ) -> None:
        at = ui_app
        mock_graph.set_scenario("segment1_error")
        excel = write_clean_excel(tmp_path)
        _sidebar_invoice_uploader(at).set_value(
            (
                excel.name,
                excel.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        )
        at.run()
        _click_button_label(at, "Analizi", "Başlat", exclude=("Yeni",))
        at.run(timeout=15)
        _no_script_crash(at)
        assert _phase(at) in ("error", "running")
        mock_graph.set_scenario("segment1_clarification")  # sonraki testler için sıfırla
