"""LangGraph için İadeAjan paylaşımlı state tip tanımı."""

from __future__ import annotations

import operator

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

from app.schemas.models import (
    ActionCard,
    ClarificationQuestion,
    CriticFeedback,
    FinancePrecheck,
    MissingDoc,
    RiskItem,
)


class IadeAjanState(TypedDict, total=False):
    """4 ajanlı KDV iade analiz grafiğinin paylaşımlı state sözleşmesi."""

    # Giriş / Senaryo
    scenario_id: str  # mock senaryo kimliği (ör. "celik_as_high")

    # Şirket Bilgileri
    company_id: str  # şirket kimliği
    company_name: str  # şirket unvanı
    tax_number: str  # vergi numarası
    date_range: dict[str, str]  # analiz dönemi (örn: start, end)
    uploaded_files: list[str]  # yüklenen dosya yolları veya kimlikleri

    # Toplanan Veri (Collector Agent çıktısı)
    collected_data: dict[str, object]  # ham toplanan veri özeti
    normalized_invoices: list[dict[str, object]]  # normalize edilmiş faturalar
    normalized_suppliers: list[dict[str, object]]  # normalize edilmiş tedarikçiler
    document_inventory: list[dict[str, object]]  # belge envanteri
    company_profile: dict[str, object]  # şirket profil özeti

    # Analiz Sonuçları (Analyzer Agent çıktısı)
    classification_result: dict[str, object]  # iade türü ve sınıflandırma sonucu
    risk_analysis: dict[str, object]  # risk analizi özet yapısı
    missing_docs: list[MissingDoc]  # eksik belge listesi
    process_warnings: list[str]  # süreç ve zamanaşımı uyarıları
    score_inputs: dict[str, object]  # skor motoruna giden girdi özeti
    risk_items: list[RiskItem]  # yapılandırılmış risk maddeleri

    # Vektör Tabanlı Mevzuat Hafızası (RAG)
    retrieved_legal_context: list[str]  # Pinecone/ChromaDB'den çekilen emsal özelgeler

    # Dinamik Alt-Döngü (Sub-graph) Yönlendirmesi
    routing_path: Literal[
        "standard",
        "construction_subgraph",
        "export_subgraph",
        "pending",
    ]  # Supervisor ajanın seçtiği yol; başlangıçta "pending" beklenir

    # Denetmen Ajan Geri Bildirimleri
    critic_feedback: list[CriticFeedback]  # öz denetim (self-reflection) çıktıları

    # Skor ve Karar (Decision Agent çıktısı)
    final_report: dict[str, object]  # nihai karar raporu (skor, risk, onay, özet)
    final_score: int  # başlangıçta 0 beklenir
    score_explanation: dict[str, object]  # skorun açıklanabilir dökümü
    top_actions: list[ActionCard]  # en yüksek etkili aksiyon önerileri
    finance_offer: FinancePrecheck  # ön finansman değerlendirmesi

    # Clarification Agent
    clarification_needed: bool  # netleştirme soruları gerekli mi
    clarification_questions: list[ClarificationQuestion]  # sorulacak sorular
    clarification_answers: dict[str, str | bool]  # kullanıcı cevapları
    clarification_message: str  # kullanıcıya gösterilecek açıklama mesajı
    skipped_questions: list[str]  # atlanan soru kimlikleri

    # Sistem Kontrol
    retry_count: int  # başlangıçta 0
    max_retries: int  # başlangıçta 3
    error_state: str | None  # normalize edilmiş hata mesajı
    current_agent: Literal[
        "SupervisorAgent",
        "CollectorAgent",
        "AnalyzerAgent",
        "CriticAgent",
        "DecisionAgent",
        "ClarificationAgent",
    ]  # başlangıçta "SupervisorAgent"
    agent_logs: Annotated[list[str], operator.add]  # demo ve izleme log satırları
    messages: Annotated[list, add_messages]  # LangGraph mesaj geçmişi

    # Doğrulama katmanı (PR-SEC-01)
    proof_ledger: list[dict[str, object]]  # ProofRecord JSON
    verification_summary: dict[str, object]  # all_proof_passed, warnings, failed_checks
    failure_reason: str  # failed durumunda kod
    block_reason: str  # clarification_blocked durumunda kod

    # Durum Takibi
    analysis_status: Literal[
        "pending",
        "running",
        "clarification_waiting",
        "clarification_blocked",
        "completed",
        "failed",
    ]  # başlangıçta "pending"
    started_at: str  # analiz başlangıç zamanı (ISO-8601 beklenir)
    finished_at: str  # analiz bitiş zamanı (ISO-8601 beklenir)


if __name__ == "__main__":
    from app.schemas.models import RiskItem
    from app.core.state import IadeAjanState

    r = RiskItem(
        title="Test",
        severity="yüksek",
        reason="Test reason",
        score_impact=-10,
    )
    print("✅ models.py OK:", r.model_dump())
    print("✅ state.py OK: IadeAjanState yüklendi")
