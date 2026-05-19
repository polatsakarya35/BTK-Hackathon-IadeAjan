"""LangGraph Clarification node — kullanıcı mesajı üretimi ve cevap tamamlama kontrolü."""

from __future__ import annotations

import app.core.env  # noqa: F401 — .env yüklemesi
import os
from datetime import datetime, timezone
from typing import Any

from app.core.state import IadeAjanState
from app.services.document_inventory import merge_inventory_from_questions
from app.services.proof_ledger import purge_expired, validate_clarification_answers

AGENT_NAME = "ClarificationAgent"
NEXT_AGENT = "AnalyzerAgent"


def _log(message: str) -> str:
    """ISO 8601 UTC timestamp ile [ClarificationAgent] prefixli log satırı üretir."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"[{ts}] [{AGENT_NAME}] {message}"


def _build_fallback_message(questions: list[dict[str, Any]], company_name: str) -> str:
    """
    LLM kullanılamadığında statik ama okunabilir bir mesaj döndürür.
    Her soru ayrı bir madde olarak listelenir.
    """
    lines = [
        f"{company_name} için KDV iade analiziniz tamamlandı.",
        "Sürecin devam edebilmesi için aşağıdaki bilgileri tamamlamanızı rica ederiz.",
        "",
    ]
    for index, question in enumerate(questions, 1):
        lines.append(f"{index}. {question.get('question_text', 'Soru')}")
    lines.append("")
    lines.append(
        "Bu bilgileri tamamladıktan sonra analiziniz otomatik olarak devam edecektir."
    )
    return "\n".join(lines)


def _generate_clarification_message(
    company_name: str,
    missing_docs: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    logs: list[str],
) -> str:
    """Gemini ile kurumsal mesaj üretir; başarısız olursa fallback kullanır."""
    questions_txt = "\n".join(
        f"- {question.get('question_text', '')} (Alan: {question.get('field', '')})"
        for question in questions
    )
    missing_txt = ", ".join(
        document.get("doc_name", "") for document in missing_docs
    ) or "bazı belgeler"

    system_prompt = (
        "Sen İadeAjan adlı bir KDV iade danışmanlık sisteminin parçasısın. "
        "Kullanıcılara her zaman kurumsal, destekleyici ve empatik bir dil kullan. "
        "Teknik jargon kullanmaktan kaçın. Türkçe yaz."
    )
    human_prompt = f"""
{company_name} şirketinin KDV iade analizi tamamlandı. Ancak sürecin devam edebilmesi için
bazı eksik bilgilerin tamamlanması gerekiyor.

Eksik/belirsiz konular: {missing_txt}

Kullanıcıya iletilmesi gereken sorular:
{questions_txt}

Lütfen bu bilgileri şu formatta ver:
1. 2-3 cümlelik empatik bir giriş paragrafı (sürecin önemini ve kolaylığını vurgula).
2. Her soru için kullanıcı dostu bir alt başlık (soru metnini koru).
3. 1 cümlelik teşvik edici bir kapanış.

Toplam uzunluk: maksimum 150 kelime.
"""

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError("GOOGLE_API_KEY tanımlı değil")

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.4,
            google_api_key=api_key,
        )
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ])
        message = str(response.content).strip()
        if message:
            logs.append(_log("LLM mesajı üretildi"))
            return message
        raise ValueError("LLM boş mesaj döndürdü")

    except (ImportError, EnvironmentError, Exception) as exc:
        logs.append(_log(f"[Fallback] LLM kullanılamadı, statik mesaj üretildi: {exc}"))
        return _build_fallback_message(questions, company_name)


def _refund_type_cross_check(
    state: IadeAjanState,
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
) -> str:
    """Kullanıcı refund_type cevabı ile Excel/algılanan tür uyumsuzluğu uyarısı."""
    cls = state.get("classification_result") or {}
    detected = str(cls.get("refund_type", "belirsiz")).lower()
    if detected != "belirsiz":
        return ""
    user_type = ""
    for q in questions:
        if q.get("field") == "refund_type":
            user_type = str(answers.get(q.get("question_id", ""), "")).lower()
            break
    if not user_type:
        return ""
    invoices = state.get("normalized_invoices") or []
    has_export = any(inv.get("is_export") or inv.get("type") == "ihracat" for inv in invoices)
    has_tevkifat = any(
        str(inv.get("type", "")).lower() in ("tevkifat", "tevkif") for inv in invoices
    )
    if user_type == "ihracat" and has_tevkifat and not has_export:
        return (
            "İade türü olarak ihracat seçildi ancak fatura profili tevkifat ağırlıklı — "
            "manuel kontrol önerilir."
        )
    if user_type == "tevkifat" and has_export and not has_tevkifat:
        return (
            "İade türü olarak tevkifat seçildi ancak ihracat faturaları mevcut — "
            "manuel kontrol önerilir."
        )
    return ""


def _production_mode(
    state: IadeAjanState,
    questions: list[dict[str, Any]],
    logs: list[str],
) -> dict[str, Any]:
    """Üretim modu — kullanıcıya gösterilecek mesajı hazırlar ve bekler."""
    logs.append(_log(f"Üretim modu — {len(questions)} soru için mesaj üretiliyor"))

    existing_message = (state.get("clarification_message") or "").strip()
    if existing_message:
        clarification_message = existing_message
        logs.append(_log("Mevcut clarification_message korundu (LLM çağrılmadı)"))
    else:
        company_name = state.get("company_name", "Şirketiniz")
        missing_docs = state.get("missing_docs", [])
        clarification_message = _generate_clarification_message(
            company_name=company_name,
            missing_docs=missing_docs,
            questions=questions,
            logs=logs,
        )

    logs.append(_log("Kullanıcıdan cevap bekleniyor — status: clarification_waiting"))

    return {
        "clarification_message": clarification_message,
        "clarification_needed": True,
        "analysis_status": "clarification_waiting",
        "current_agent": AGENT_NAME,
        "agent_logs": logs,
        "error_state": None,
    }


def _processing_mode(
    state: IadeAjanState,
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
    logs: list[str],
) -> dict[str, Any]:
    """İşleme modu — zorunlu soruların tamamlanıp tamamlanmadığını kontrol eder."""
    logs.append(_log(f"İşleme modu — {len(answers)} cevap alındı"))

    required_ids = {
        question["question_id"]
        for question in questions
        if question.get("required", True)
    }
    answered_ids = set(answers.keys())
    unanswered_ids = required_ids - answered_ids
    all_answered = len(unanswered_ids) == 0

    if all_answered:
        purge_expired(state)
        ok, val_errors, _val_warns = validate_clarification_answers(state, questions, answers)
        if not ok:
            logs.append(_log(f"Kanıt doğrulama başarısız: {val_errors}"))
            return {
                "clarification_needed": True,
                "analysis_status": "clarification_waiting",
                "current_agent": AGENT_NAME,
                "process_warnings": list(state.get("process_warnings") or [])
                + [f"Kanıt doğrulama: {e}" for e in val_errors],
                "agent_logs": logs,
                "error_state": None,
            }

        refund_warning = _refund_type_cross_check(state, questions, answers)
        process_warnings = list(state.get("process_warnings") or [])
        if refund_warning:
            process_warnings.append(refund_warning)

        logs.append(_log("Tüm zorunlu sorular cevaplandı → AnalyzerAgent'a geri dönülüyor (bütünsel yeniden tarama)"))
        existing_inv = list(state.get("document_inventory") or [])
        updated_inv = merge_inventory_from_questions(
            existing_inv,
            questions,
            answers,
            proof_ledger=list(state.get("proof_ledger") or []),
        )
        logs.append(_log("Belge envanteri clarification cevaplarıyla güncellendi"))
        return {
            "clarification_needed": False,
            "analysis_status": "running",
            "current_agent": NEXT_AGENT,
            "clarification_message": "",
            "document_inventory": updated_inv,
            "process_warnings": process_warnings,
            "agent_logs": logs,
            "error_state": None,
        }

    logs.append(
        _log(f"{len(unanswered_ids)} zorunlu soru hâlâ cevaplanmadı")
    )

    remaining_questions = [
        question
        for question in questions
        if question["question_id"] in unanswered_ids
    ]
    company_name = state.get("company_name", "Şirketiniz")
    missing_docs = state.get("missing_docs", [])
    updated_message = _generate_clarification_message(
        company_name=company_name,
        missing_docs=missing_docs,
        questions=remaining_questions,
        logs=logs,
    )

    return {
        "clarification_message": updated_message,
        "clarification_needed": True,
        "analysis_status": "clarification_waiting",
        "current_agent": AGENT_NAME,
        "agent_logs": logs,
        "error_state": None,
    }


def clarification_node(state: IadeAjanState) -> dict[str, Any]:
    """
    LangGraph node fonksiyonu — Clarification Agent.

    İki modda çalışır:
      - Üretim Modu: clarification_answers boşsa LLM ile mesaj üretir,
                     kullanıcıdan cevap bekler.
      - İşleme Modu: clarification_answers doluysa zorunlu soruların
                     tamamlanıp tamamlanmadığını kontrol eder;
                     tümü cevaplandıysa DecisionAgent'a geçer.

    State'i mutate etmez; yalnızca değişen alanları dict olarak döndürür.
    """
    logs: list[str] = []

    try:
        questions: list[dict[str, Any]] = state.get("clarification_questions", [])
        answers: dict[str, Any] = state.get("clarification_answers") or {}
        has_answers = bool(answers)

        if not has_answers:
            return _production_mode(state, questions, logs)

        return _processing_mode(state, questions, answers, logs)

    except Exception as exc:
        return {
            "error_state": f"CLARIFICATION_ERROR: {exc}",
            "analysis_status": "failed",
            "current_agent": AGENT_NAME,
            "agent_logs": [_log(f"HATA: {exc}")],
        }


if __name__ == "__main__":
    print("=" * 60)
    print("Test 1 — Durum A: Mesaj Üretim Modu")
    print("=" * 60)

    mock_state_a: dict[str, Any] = {
        "company_name": "Çelik A.Ş.",
        "clarification_questions": [
            {
                "question_id": "q_gumruk_001",
                "question_text": "Gümrük çıkış beyannameleri elinizde mevcut mu?",
                "field": "has_customs_declarations",
                "type": "choice",
                "options": ["Evet", "Hayır", "Bir kısmı mevcut"],
                "required": True,
                "reason": "İhracat iadesi için gümrük beyannamesi zorunludur.",
            }
        ],
        "missing_docs": [
            {
                "doc_name": "Gümrük Beyannamesi",
                "reason": "İhracat iadesi için zorunlu.",
            }
        ],
        "clarification_answers": {},
        "clarification_message": "",
        "agent_logs": [],
    }

    out_a = clarification_node(mock_state_a)

    assert out_a["analysis_status"] == "clarification_waiting", (
        f"Status 'clarification_waiting' olmalı, alınan: {out_a['analysis_status']}"
    )
    assert out_a["current_agent"] == "ClarificationAgent", (
        "Agent hâlâ ClarificationAgent olmalı"
    )
    assert out_a["clarification_message"], "clarification_message dolu olmalı"
    assert out_a["error_state"] is None

    print("✅ Durum A testi geçti")
    print(f"  Status      : {out_a['analysis_status']}")
    print(f"  Sonraki ajan: {out_a['current_agent']}")
    print(f"  Log sayısı  : {len(out_a['agent_logs'])}")
    print("\n--- Üretilen Mesaj ---")
    print(out_a["clarification_message"])
    print("---------------------\n")

    print("=" * 60)
    print("Test 2 — Durum B: Cevaplar Tam → DecisionAgent")
    print("=" * 60)

    mock_state_b: dict[str, Any] = {
        **mock_state_a,
        "clarification_answers": {
            "q_gumruk_001": "Hayır",
        },
        "agent_logs": [],
    }

    out_b = clarification_node(mock_state_b)

    assert out_b["analysis_status"] == "running", (
        f"Status 'running' olmalı, alınan: {out_b['analysis_status']}"
    )
    assert out_b["current_agent"] == "AnalyzerAgent", (
        f"Sonraki ajan 'AnalyzerAgent' olmalı, alınan: {out_b['current_agent']}"
    )
    assert out_b["clarification_message"] == "", "clarification_message temizlenmiş olmalı"
    assert out_b["clarification_needed"] is False
    assert out_b["error_state"] is None

    print("✅ Durum B testi geçti")
    print(f"  Status      : {out_b['analysis_status']}")
    print(f"  Sonraki ajan: {out_b['current_agent']}")
    print(f"  Log sayısı  : {len(out_b['agent_logs'])}")

    print()
    print("=" * 60)
    print("Test 3 — Durum B (Eksik): Cevaplar Tam Değil → Beklemeye Devam")
    print("=" * 60)

    mock_state_c: dict[str, Any] = {
        "company_name": "Çelik A.Ş.",
        "clarification_questions": [
            {
                "question_id": "q_gumruk_001",
                "question_text": "Gümrük çıkış beyannameleri elinizde mevcut mu?",
                "field": "has_customs_declarations",
                "type": "choice",
                "options": ["Evet", "Hayır", "Bir kısmı mevcut"],
                "required": True,
                "reason": "İhracat iadesi için gümrük beyannamesi zorunludur.",
            },
            {
                "question_id": "q_refund_type_001",
                "question_text": "İade türünüz hangisi?",
                "field": "refund_type",
                "type": "choice",
                "options": ["ihracat", "tevkifat", "indirimli_oran"],
                "required": True,
                "reason": "İade türü netleşmeden analiz konservatif kalır.",
            },
        ],
        "missing_docs": [],
        "clarification_answers": {
            "q_gumruk_001": "Evet",
        },
        "clarification_message": "",
        "agent_logs": [],
    }

    out_c = clarification_node(mock_state_c)

    assert out_c["analysis_status"] == "clarification_waiting", (
        "Eksik cevapla hâlâ bekleme modunda olmalı"
    )
    assert out_c["current_agent"] == "ClarificationAgent"
    assert out_c["clarification_message"]

    print("✅ Durum B (Eksik) testi geçti")
    print(f"  Status      : {out_c['analysis_status']}")
    print(f"  Sonraki ajan: {out_c['current_agent']}")

    print()
    print("=" * 60)
    print("Test 4 — LLM Fallback (GOOGLE_API_KEY geçici olarak kaldırılıyor)")
    print("=" * 60)

    original_key = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        fallback_state: dict[str, Any] = {
            **mock_state_a,
            "clarification_message": "",
            "agent_logs": [],
        }
        out_fallback = clarification_node(fallback_state)
        assert out_fallback["clarification_message"], "Fallback mesaj dolu olmalı"
        assert out_fallback["analysis_status"] == "clarification_waiting"
        fallback_logged = any(
            "[Fallback]" in log_line for log_line in out_fallback["agent_logs"]
        )
        assert fallback_logged, "Fallback logu agent_logs içinde görülmeli"
        print("✅ LLM Fallback testi geçti")
        preview = out_fallback["clarification_message"][:80]
        print(f"  Mesaj önizleme: {preview}...")
    finally:
        if original_key:
            os.environ["GOOGLE_API_KEY"] = original_key

    print()
    print("=" * 60)
    print("✅ Tüm Clarification Agent testleri geçti")
    print("=" * 60)
