"""İadeAjan LangGraph iş akışı — dört ajanı koşullu yönlendirmeyle bağlar."""

from __future__ import annotations

import app.core.env  # noqa: F401 — .env yüklemesi

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.agents.clarification_agent import clarification_node
from app.agents.collector_agent import collector_node
from app.agents.analyzer_agent import analyzer_node
from app.agents.decision_agent import decision_node
from app.core.state import IadeAjanState

NODE_COLLECTOR = "CollectorAgent"
NODE_ANALYZER = "AnalyzerAgent"
NODE_CLARIFICATION = "ClarificationAgent"
NODE_DECISION = "DecisionAgent"


def route_from_start(
    state: IadeAjanState,
) -> Literal["CollectorAgent", "AnalyzerAgent"]:
    """
    Clarification sonrası devamda Collector'ı atla.
    normalized_invoices zaten yüklüyse doğrudan Analyzer'a git (çift log/LLM maliyeti önlenir).
    """
    invoices = state.get("normalized_invoices") or []
    if invoices:
        return NODE_ANALYZER
    return NODE_COLLECTOR


def route_after_collector(
    state: IadeAjanState,
) -> Literal["AnalyzerAgent", "__end__"]:
    """
    CollectorAgent sonrası koşullu yönlendirme.

    Collector hata verdiyse (analysis_status="failed") graph'ı sonlandırır;
    hata error_state'te korunur, Analyzer tarafından üzerine yazılmaz.
    Başarılıysa AnalyzerAgent'a devam eder.
    """
    if state.get("analysis_status") == "failed":
        return END
    return NODE_ANALYZER


def route_after_analyzer(
    state: IadeAjanState,
) -> Literal["ClarificationAgent", "DecisionAgent", "__end__"]:
    """
    AnalyzerAgent sonrası koşullu yönlendirme.

    clarification_blocked → END (skor üretilmez)
    clarification_needed True  → ClarificationAgent
    clarification_needed False → DecisionAgent
    """
    if state.get("analysis_status") == "clarification_blocked":
        return END
    if state.get("clarification_needed") is True:
        return NODE_CLARIFICATION
    return NODE_DECISION


def route_after_clarification(
    state: IadeAjanState,
) -> Literal["AnalyzerAgent", "ClarificationAgent"]:
    """
    ClarificationAgent sonrası koşullu yönlendirme.

    Tüm zorunlu sorular cevaplandıysa (clarification_needed=False) → AnalyzerAgent
    (bütünsel yeniden tarama döngüsü — Analyzer yeni cevaplarla tekrar çalışır,
    yeni eksik bulursa tekrar Clarification'a, bulamazsa DecisionAgent'a gider).
    Hâlâ cevaplanmamış sorular varsa → ClarificationAgent (tekrar bekle).
    """
    if state.get("clarification_needed") is False:
        return NODE_ANALYZER
    return NODE_CLARIFICATION


def build_graph(interrupt: bool = False):
    """
    İadeAjan LangGraph iş akışını oluşturur ve derlenmiş graph döndürür.

    Akış:
        START → CollectorAgent
          ├── (failed) → END
          └── (ok) → AnalyzerAgent
                ├── (clarification_needed=True)  → ClarificationAgent
                │       ├── (cevaplar tam)        → AnalyzerAgent (bütünsel yeniden tarama)
                │       │       ├── (yeni eksik)  → ClarificationAgent (döngü devam)
                │       │       └── (eksik yok)   → DecisionAgent
                │       └── (kısmi cevap)         → ClarificationAgent (bekle)
                └── (clarification_needed=False) → DecisionAgent → END

    Args:
        interrupt: True ise ClarificationAgent öncesinde graph duraklar
                   (Streamlit UI entegrasyonu için).

    Returns:
        Derlenmiş LangGraph CompiledStateGraph nesnesi.
    """
    workflow = StateGraph(IadeAjanState)

    workflow.add_node(NODE_COLLECTOR, collector_node)
    workflow.add_node(NODE_ANALYZER, analyzer_node)
    workflow.add_node(NODE_CLARIFICATION, clarification_node)
    workflow.add_node(NODE_DECISION, decision_node)

    workflow.add_edge(NODE_DECISION, END)

    workflow.add_conditional_edges(
        START,
        route_from_start,
        {NODE_COLLECTOR: NODE_COLLECTOR, NODE_ANALYZER: NODE_ANALYZER},
    )

    workflow.add_conditional_edges(
        NODE_COLLECTOR,
        route_after_collector,
        {NODE_ANALYZER: NODE_ANALYZER, END: END},
    )

    workflow.add_conditional_edges(
        NODE_ANALYZER,
        route_after_analyzer,
        {
            NODE_CLARIFICATION: NODE_CLARIFICATION,
            NODE_DECISION: NODE_DECISION,
            END: END,
        },
    )

    workflow.add_conditional_edges(
        NODE_CLARIFICATION,
        route_after_clarification,
        {
            NODE_ANALYZER: NODE_ANALYZER,
            NODE_CLARIFICATION: NODE_CLARIFICATION,
        },
    )

    compiled = workflow.compile(
        interrupt_before=[NODE_CLARIFICATION] if interrupt else []
    )
    return compiled


def print_graph_structure(graph) -> None:
    """Graph'ın Mermaid diyagramını terminale basar."""
    try:
        mermaid = graph.get_graph().draw_mermaid()
        print("\n── Mermaid Diyagramı ──────────────────────────")
        print(mermaid)
        print("───────────────────────────────────────────────\n")
    except Exception as exc:
        print(f"  [Görselleştirme atlandı: {exc}]")


def _get(state: dict[str, Any], key: str, default: Any = None) -> Any:
    """state.get() sarmalayıcı — None veya eksik anahtarlar için default döner."""
    val = state.get(key)
    return val if val is not None else default


if __name__ == "__main__":
    graph = build_graph()

    print_graph_structure(graph)

    print("=" * 65)
    print("Test 1 — Uçtan Uca | celik_as_high (cevap önceden enjekte)")
    print("=" * 65)

    initial_state_1: dict[str, Any] = {
        "scenario_id": "celik_as_high",
        "agent_logs": [],
        "uploaded_files": [],
        "clarification_answers": {
            "q_gumruk_001": "Evet",
            "q_refund_type_001": "ihracat",
            "q_2no_001": "Evet",
        },
    }

    final_state_1 = graph.invoke(initial_state_1)
    rep1 = final_state_1.get("final_report", {})

    assert final_state_1.get("analysis_status") == "completed", (
        f"Status 'completed' olmalı: {final_state_1.get('analysis_status')}"
    )
    assert rep1, "final_report dolu olmalı"
    assert 0 <= rep1.get("calculated_score", -1) <= 100, "Skor 0-100 aralığında olmalı"

    print(f"\n  {'─'*55}")
    print(f"  {'NİHAİ KARAR RAPORU':^55}")
    print(f"  {'─'*55}")
    print(f"  Şirket          : {rep1.get('company_name', '-')}")
    print(f"  Vergi No        : {rep1.get('tax_number', '-')}")
    print(f"  İade Türü       : {rep1.get('refund_type', '-')}")
    print(f"  {'─'*55}")
    print(f"  Baz Skor        : {rep1.get('base_score', 100)}")
    print(f"  Risk Cezası     : -{rep1.get('risk_penalty', 0)}")
    print(f"  Belge Cezası    : -{rep1.get('doc_penalty', 0)}")
    print(f"  Clarification   : +{rep1.get('clarification_bonus', 0)}")
    print(f"  {'─'*55}")
    print(f"  NİHAİ SKOR      : {rep1.get('calculated_score', '?')}/100")
    print(f"  Risk Kategorisi : {rep1.get('risk_category', '-')}")
    print(f"  Onay Durumu     : {rep1.get('approval_status', '-')}")
    fin1 = rep1.get("finance_eligibility", {})
    print(f"  Finansman Uygun : {'Evet' if fin1.get('eligible') else 'Hayır'}")
    print(f"  {'─'*55}")
    print(f"  Özet: {rep1.get('decision_summary', '-')}")
    print(f"  {'─'*55}")

    all_logs_1 = final_state_1.get("agent_logs", [])
    print(f"\n  Ajan Log Zinciri ({len(all_logs_1)} satır):")
    for log_line in all_logs_1:
        print(f"  {log_line}")

    print("\n✅ Test 1 geçti\n")

    print("=" * 65)
    print("Test 2 — Uçtan Uca | celik_as_medium")
    print("=" * 65)

    initial_state_2: dict[str, Any] = {
        "scenario_id": "celik_as_medium",
        "agent_logs": [],
        "uploaded_files": [],
        "clarification_answers": {
            "q_gumruk_001": "Hayır",
            "q_refund_type_001": "tevkifat",
            "q_2no_001": "Hayır",
        },
    }

    final_state_2 = graph.invoke(initial_state_2)
    rep2 = final_state_2.get("final_report", {})

    assert final_state_2.get("analysis_status") == "completed"
    assert rep2.get("calculated_score", 100) < 90, (
        f"Orta/yüksek risk senaryosu 90'ın altında olmalı: {rep2.get('calculated_score')}"
    )

    print(f"  NİHAİ SKOR      : {rep2.get('calculated_score', '?')}/100")
    print(f"  Risk Kategorisi : {rep2.get('risk_category', '-')}")
    print(f"  Onay Durumu     : {rep2.get('approval_status', '-')}")
    fin2 = rep2.get("finance_eligibility", {})
    print(f"  Finansman Uygun : {'Evet' if fin2.get('eligible') else 'Hayır'}")
    print(f"  Log sayısı      : {len(final_state_2.get('agent_logs', []))}")
    print("\n✅ Test 2 geçti\n")

    print("=" * 65)
    print("Test 3 — Hata Yönetimi | Geçersiz Senaryo")
    print("=" * 65)

    initial_state_3: dict[str, Any] = {
        "scenario_id": "OLMAYAN_SENARYO_XYZ",
        "agent_logs": [],
        "uploaded_files": [],
    }

    final_state_3 = graph.invoke(initial_state_3)

    assert final_state_3.get("analysis_status") in ("failed", "completed"), (
        f"Geçersiz senaryo: status 'failed' veya 'completed' olmalı"
    )
    error = final_state_3.get("error_state", "")
    assert error and "COLLECTOR_ERROR" in error, (
        f"error_state 'COLLECTOR_ERROR' içermeli: {error!r}"
    )

    print(f"  Status     : {final_state_3.get('analysis_status')}")
    print(f"  Hata       : {final_state_3.get('error_state')}")
    print("\n✅ Test 3 geçti\n")

    print("=" * 65)
    print("✅ Tüm Workflow testleri geçti")
    print("=" * 65)
