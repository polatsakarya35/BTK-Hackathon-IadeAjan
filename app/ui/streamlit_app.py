"""
app/ui/streamlit_app.py
-----------------------
İadeAjan Streamlit arayüzü.
Upload bölümü: kullanıcının yüklediği dosyayı geçici dizine kaydeder
ve Collector'ın kullanacağı state alanını doldurur.
"""

import tempfile
from pathlib import Path

import app.core.env  # noqa: F401 — .env yüklemesi
import streamlit as st

st.set_page_config(page_title="İadeAjan", layout="wide")
st.title("İadeAjan — KDV İade Uygunluk Skoru")

# ── Upload bölümü ─────────────────────────────────────────────────────────
st.subheader("📂 Dosya Yükle (CSV / Excel / JSON)")

uploaded = st.file_uploader(
    "Fatura veya hesap dökümünüzü yükleyin",
    type=["csv", "xlsx", "xls", "json"],
    help="Desteklenen formatlar: CSV, Excel (.xlsx/.xls), JSON",
)

if uploaded is not None:
    tmp_dir = Path(tempfile.gettempdir()) / "iadeajan_uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / uploaded.name
    tmp_path.write_bytes(uploaded.getbuffer())
    st.success(f"✅ Dosya yüklendi: `{tmp_path.name}`")
    st.session_state["uploaded_files"] = [str(tmp_path)]
else:
    if "uploaded_files" in st.session_state:
        del st.session_state["uploaded_files"]

# ── Senaryo seçimi (Upload yokken görünür) ────────────────────────────────
if not st.session_state.get("uploaded_files"):
    st.subheader("🗂️ Demo Senaryosu Seç")
    senaryo = st.selectbox(
        "Senaryo",
        ["celik_as_high", "celik_as_medium"],
        format_func=lambda x: {
            "celik_as_high": "Senaryo A — Yüksek Skor (İhracat)",
            "celik_as_medium": "Senaryo B — Orta Skor (Tevkifat)",
        }.get(x, x),
    )
    st.session_state["scenario_id"] = senaryo
