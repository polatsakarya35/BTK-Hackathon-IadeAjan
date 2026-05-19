"""Proje kökündeki .env dosyasını yükler (GOOGLE_API_KEY vb.)."""

from __future__ import annotations

import warnings
from pathlib import Path

# Sistem env'de GEMINI_API_KEY, .env'de GOOGLE_API_KEY aynı anda tanımlıysa
# langchain_google_genai bu uyarıyı her LLM çağrısında basar. İkisi aynı key
# olduğu için uyarı bilgi içermez; bir kez bastır.
warnings.filterwarnings(
    "ignore",
    message="Both GOOGLE_API_KEY and GEMINI_API_KEY are set",
)


def load_env() -> None:
    """btk/.env dosyasını ortam değişkenlerine aktarır."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")


load_env()
