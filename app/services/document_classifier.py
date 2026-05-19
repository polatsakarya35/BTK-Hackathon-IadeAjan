"""Gemini ile PDF/görsel belge sınıflandırması — DocumentClassification çıktısı."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from app.schemas.models import DocumentClassification, DocumentClassificationType

_SUPPORTED_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp"})

_MIME_BY_SUFFIX: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_SYSTEM_PROMPT = """Sen Türkiye'de gümrük ve vergi belgelerini ayırt eden bir uzman modelisin.
Görev: Verilen dosya (PDF veya görsel) ve dosya adına göre belgeyi sınıflandır.

Sınıflar:
- gumruk_beyannamesi: Gümrük çıkış beyannamesi (GÇB), ihracat gümrük beyanı.
- ymm_tasdik_raporu: YMM KDV iadesi tasdik raporu / serbest muhasebeci tasdik raporu (KDV iade).
- 2no_kdv_beyannamesi: 2 No'lu KDV beyannamesi (tevkifat / indirimli oran iadesi beyanı).
- diger: Yukarıdakilerden biri değil ama belge tanınabiliyorsa.
- bilinmiyor: İçerik okunamıyor veya emin olunamıyorsa.

confidence: 0.0–1.0 (yüksek güven yalnızca içerikte açık işaretler varsa).
evidence: Türkçe, tek cümle — belgede gördüğün başlık, ibare veya yapı (max 300 karakter).
file_name çıktıda girişte verilen dosya adıyla birebir olmalı."""

_USER_TEMPLATE = """Dosya adı: {file_name}

Bu dosyayı sınıflandır. Kanıt için belgedeki başlık/numara alanlarına referans ver.
Belirsizsen type=bilinmiyor ve düşük confidence ver."""


def is_supported_document_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _SUPPORTED_SUFFIXES


def _normalize_ascii_lower(s: str) -> str:
    norm = unicodedata.normalize("NFKD", s)
    ascii_text = norm.encode("ascii", "ignore").decode("ascii")
    return ascii_text.lower()


def _filename_heuristic(path: Path) -> DocumentClassification | None:
    """API kapalıyken veya hata — dosya adından sınırlı tahmin."""
    stem = _normalize_ascii_lower(path.stem)
    name = _normalize_ascii_lower(path.name)

    gumruk_keys = ("gumruk", "gcb", "gumruk_cikis", "ihracat_beyan", "customs")
    ymm_keys = ("ymm", "tasdik", "tasdikraporu", "kdv_iade")
    kdv2_keys = ("2no", "2_no", "ikinci_no", "kdv_2", "beyan_2", "tevkifat_beyan")

    doc_type: DocumentClassificationType | None = None
    evidence = ""

    if any(k in stem or k in name for k in gumruk_keys):
        doc_type = "gumruk_beyannamesi"
        evidence = "Dosya adında gümrük/GÇB ile ilişkili anahtar kelimeler."
    elif any(k in stem or k in name for k in ymm_keys):
        doc_type = "ymm_tasdik_raporu"
        evidence = "Dosya adında YMM/tasdik ile ilişkili anahtar kelimeler."
    elif any(k in stem or k in name for k in kdv2_keys):
        doc_type = "2no_kdv_beyannamesi"
        evidence = "Dosya adında 2 No'lu KDV beyannamesi ile ilişkili anahtar kelimeler."

    if doc_type is None:
        return None

    return DocumentClassification(
        file_name=path.name,
        type=doc_type,
        confidence=0.85,
        evidence=evidence,
    )


def classify_document(path: str | Path) -> DocumentClassification | None:
    """
    Tek belge dosyasını sınıflandırır. Başarısızlıkta dosya adı sezgisine düşer.
    """
    p = Path(path)
    if not p.is_file():
        return None

    suffix = p.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        return None

    if os.getenv("DOCUMENT_CLASSIFIER_ENABLED", "true").lower() != "true":
        return _filename_heuristic(p)

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _filename_heuristic(p)

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return _filename_heuristic(p)

    mime = _MIME_BY_SUFFIX[suffix]
    try:
        data = p.read_bytes()
    except OSError:
        return _filename_heuristic(p)

    if not data:
        return _filename_heuristic(p)

    user_text = _USER_TEMPLATE.format(file_name=p.name)
    parts = [
        genai_types.Part.from_text(text=user_text),
        genai_types.Part.from_bytes(data=data, mime_type=mime),
    ]
    contents = [genai_types.Content(role="user", parts=parts)]

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.05,
                max_output_tokens=2048,
                response_mime_type="application/json",
                response_schema=DocumentClassification,
            ),
        )
        raw = (response.text or "").strip()
        if not raw:
            return _filename_heuristic(p)
        result = DocumentClassification.model_validate_json(raw)
        if result.file_name.strip() != p.name:
            result = result.model_copy(update={"file_name": p.name})
        return result
    except Exception:
        return _filename_heuristic(p)
