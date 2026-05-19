"""Belge alan çıkarma — LLM veya mock."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from app.schemas.verification import DocumentExtraction


def _is_gcb_doc_type(doc_type: str) -> bool:
    dt = (doc_type or "").lower()
    return any(k in dt for k in ("gumruk", "gcb", "beyanname", "customs"))


def extract_document_fields(
    file_path: str,
    doc_type: str,
) -> DocumentExtraction:
    """PDF/görselden yapılandırılmış alan çıkarımı."""
    if os.getenv("MOCK_DOCUMENT_EXTRACTION", "").lower() in ("1", "true"):
        return _mock_extraction(file_path, doc_type)

    try:
        return _llm_extract(file_path, doc_type)
    except Exception:
        return _heuristic_extract(file_path, doc_type)


def _mock_extraction(file_path: str, doc_type: str) -> DocumentExtraction:
    name = os.path.basename(file_path).lower()
    return DocumentExtraction(
        declaration_no="MOCK-DECL-001" if "gumruk" in doc_type or "gcb" in name else None,
        declaration_date="2024-06-15",
        amount=100_000.0,
        currency="TRY",
        exporter_vkn="1234567890",
        exporter_name="Mock Firma",
    )


def _heuristic_extract(file_path: str, doc_type: str) -> DocumentExtraction:
    name = os.path.basename(file_path)
    vkn_match = re.search(r"\b(\d{10})\b", name)
    return DocumentExtraction(
        declaration_no=None,
        exporter_vkn=vkn_match.group(1) if vkn_match else None,
        raw_evidence=f"heuristic:{name}",
    )


def _gcb_prompt_suffix() -> str:
    return (
        " GÇB / gümrük beyannamesi için ZORUNLU (CRITICAL) alanlar: "
        "declaration_no (beyan numarası) ve amount (tutar, TRY). "
        "Bu alanlar okunamazsa JSON'da null yaz; tahmin etme. "
        "declaration_date ve exporter_vkn mümkünse doldur."
    )


def _llm_extract(file_path: str, doc_type: str) -> DocumentExtraction:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY tanımlı değil")

    gcb_note = _gcb_prompt_suffix() if _is_gcb_doc_type(doc_type) else ""
    prompt = (
        f"Bu belge tipi: {doc_type}.{gcb_note} JSON döndür: "
        '{"declaration_no","declaration_date","amount","currency",'
        '"exporter_vkn","exporter_name","invoice_no","invoice_date"}'
    )
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0,
        google_api_key=api_key,
    )
    human = f"Dosya: {os.path.basename(file_path)}\n{prompt}"
    response = llm.invoke([
        SystemMessage(
            content=(
                "Yalnızca geçerli JSON döndür. "
                "GÇB belgelerinde declaration_no ve amount CRITICAL zorunlu alanlardır."
            ),
        ),
        HumanMessage(content=human),
    ])
    text = str(response.content).strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    return DocumentExtraction.model_validate({k: data.get(k) for k in DocumentExtraction.model_fields})
