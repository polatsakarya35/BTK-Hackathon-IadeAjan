"""
İadeAjan — Streamlit UI
=======================
LangGraph tabanlı KDV İade Analiz Sistemi'nin interaktif arayüzü.

Mimari:
    5 fazlı state-machine (idle → running → clarification → done | error).
    Clarification desteği için iki segmentli graph.invoke() kullanılır
    (interrupt_before yerine; checkpointer gerektirmez).
"""

from __future__ import annotations

import base64
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import app.core.env  # noqa: F401 — .env yüklemesi
from app.graph.workflow import build_graph
from app.services.clarification_proof import (
    expected_type_for_field,
    is_proof_document_field,
    verify_classification,
)
from app.services.document_classifier import classify_document
from app.services.proof_ledger import append_record, purge_expired, purge_proof_ledger_and_files
from app.services.verification.document_verification import ingest_document_file
from app.schemas.upload_issues import UploadIssue
from app.services.upload_preflight import (
    PreflightResult,
    get_template_excel_bytes,
    inspect_upload,
    issues_to_csv_bytes,
    write_repaired_excel,
)

# ─────────────────────────────────────────────────────────────
# SABİTLER
# ─────────────────────────────────────────────────────────────

SCENARIOS: dict[str, str] = {
    "celik_as_high": "Çelik A.Ş. — İhracat KDV İadesi (Yüksek Ceza Senaryosu)",
    "celik_as_medium": "Çelik A.Ş. — Tevkifat KDV İadesi (Orta Ceza Senaryosu)",
}

SESSION_DEFAULTS: dict[str, Any] = {
    "page": "landing",           # landing | login | signup | app
    "authenticated": False,    # Vitrin oturum (mock)
    "graph": None,             # Derlenmiş LangGraph (build_graph(interrupt=False))
    "current_state": {},       # Graph'ın son bilinen state'i (dict)
    "phase": "idle",           # idle | running | clarification | blocked | done | error
    "clarification_answers": {},
    "selected_scenario": None,
    "progress_log": [],        # Hangi ajanlar çalıştı
    "uploaded_file_name": None,
    "uploaded_file_path": None,
    "uploaded_document_paths": [],  # PDF/görsel belge yolları (çoklu)
    "upload_preflight": None,  # PreflightResult | None
}

# Ajan adı → Material ikon (Streamlit :material/...: sözdizimi)
AGENT_ICONS: dict[str, str] = {
    "CollectorAgent": ":material/inbox:",
    "AnalyzerAgent": ":material/analytics:",
    "ClarificationAgent": ":material/forum:",
    "DecisionAgent": ":material/gavel:",
}

# Risk şiddet seviyesi → CSS sınıfı (Türkçe değerler; models.py Literal)
SEVERITY_CLASS: dict[str, str] = {
    "kritik": "severity-kritik",
    "yüksek": "severity-yuksek",
    "orta": "severity-orta",
    "düşük": "severity-dusuk",
}

# Onay durumu → Türkçe etiket (emoji yok)
APPROVAL_LABELS: dict[str, str] = {
    "approved": "ONAYLANDI — Standart iade süreci başlatılabilir.",
    "conditional_approval": "ŞARTLI ONAY — YMM raporu veya teminat mektubu gerekli.",
    "rejected": "REDDEDİLDİ — Vergi incelemesine sevk kriterleri karşılanıyor.",
}

# Idle ekranı ajan kartları
AGENT_CARDS: list[tuple[str, str, str]] = [
    (
        "CollectorAgent",
        ":material/cloud_download:",
        "Verileri toplar; faturaları ve tedarikçileri normalize eder.",
    ),
    (
        "AnalyzerAgent",
        ":material/rule:",
        "Riskleri ve eksik belgeleri tespit eder; skor girdilerini üretir.",
    ),
    (
        "DecisionAgent",
        ":material/assessment:",
        "Nihai KDV iade skorunu ve karar raporunu üretir.",
    ),
]

# agent_logs satırlarından ajan adı çıkarmak için regex
_AGENT_LOG_RE = re.compile(r"\[([A-Za-z]+Agent)\]")

# ─────────────────────────────────────────────────────────────
# SAYFA AYARLARI
# ─────────────────────────────────────────────────────────────

_APP_DIR = Path(__file__).resolve().parent
BRAND_LOGO_PATH = _APP_DIR / "Ekran_Resmi_2026-05-18_03.14.28-removebg-preview.png"

st.set_page_config(
    page_title="İadeAjan | KDV İade Analiz Sistemi",
    page_icon=str(BRAND_LOGO_PATH) if BRAND_LOGO_PATH.is_file() else "◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0');

:root {
    --bg-app: #0E1117;
    --bg-card: #262730;
    --bg-elevated: #1a1d24;
    --accent: #10B981;
    --accent-dim: rgba(16, 185, 129, 0.15);
    --accent-glow: rgba(16, 185, 129, 0.45);
    --text-primary: #FAFAFA;
    --text-muted: #9CA3AF;
    --border: rgba(255, 255, 255, 0.08);
    --border-accent: rgba(16, 185, 129, 0.35);
    --radius: 8px;
}

.stApp, [data-testid="stAppViewContainer"] {
    background-color: var(--bg-app) !important;
    color: var(--text-primary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

[data-testid="stSidebar"] {
    background-color: var(--bg-elevated) !important;
    border-right: 1px solid var(--border) !important;
}

[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p {
    color: var(--text-primary) !important;
}

h1, h2, h3, h4, [data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2 {
    color: var(--text-primary) !important;
    font-weight: 600 !important;
    letter-spacing: -0.02em;
}

p, li, .stCaption, [data-testid="stMarkdownContainer"] p {
    color: var(--text-muted) !important;
}

/* Metrik kartları */
[data-testid="stMetric"] {
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    padding: 1.25rem 1.5rem !important;
    min-height: 7.5rem !important;
    box-sizing: border-box !important;
}

[data-testid="stMetricLabel"] {
    color: var(--text-muted) !important;
    font-size: 0.8rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

[data-testid="stMetricValue"] {
    color: var(--accent) !important;
    font-size: 2rem !important;
    font-weight: 700 !important;
}

[data-testid="stMetricDelta"] {
    font-size: 0.8rem !important;
}

/* Butonlar */
[data-testid="stButton"] > button {
    border-radius: var(--radius) !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em;
    transition: box-shadow 0.2s ease, border-color 0.2s ease !important;
    border: 1px solid var(--border) !important;
    background: var(--bg-card) !important;
    color: var(--text-primary) !important;
}

[data-testid="stButton"] > button[kind="primary"] {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    gap: 0.4rem !important;
    background: var(--accent) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    border: 1px solid var(--accent) !important;
}

[data-testid="stButton"] > button[kind="primary"] p,
[data-testid="stButton"] > button[kind="primary"] span,
[data-testid="stButton"] > button[kind="primary"] div {
    color: #FFFFFF !important;
    font-weight: 600 !important;
}

[data-testid="stButton"] > button[kind="primary"]:hover,
[data-testid="stButton"] > button[kind="primary"]:focus {
    background: var(--accent) !important;
    color: #FFFFFF !important;
    box-shadow: 0 0 20px var(--accent-glow) !important;
    border-color: var(--accent) !important;
}

[data-testid="stButton"] > button[kind="primary"]:hover p,
[data-testid="stButton"] > button[kind="primary"]:hover span,
[data-testid="stButton"] > button[kind="primary"]:hover div,
[data-testid="stButton"] > button[kind="primary"]:focus p,
[data-testid="stButton"] > button[kind="primary"]:focus span,
[data-testid="stButton"] > button[kind="primary"]:focus div {
    color: #FFFFFF !important;
}

[data-testid="stButton"] > button[kind="primary"] svg {
    fill: #FFFFFF !important;
    color: #FFFFFF !important;
}

[data-testid="stButton"] > button:not([kind="primary"]):hover {
    border-color: var(--border-accent) !important;
    box-shadow: 0 0 12px var(--accent-dim) !important;
}

/* Alert kutuları — renkli zemin yerine çerçeveli koyu kart */
[data-testid="stAlert"] {
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    color: var(--text-primary) !important;
}

div[data-baseweb="notification"] {
    background: var(--bg-card) !important;
}

/* Expander */
[data-testid="stExpander"] {
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
}

[data-testid="stExpander"] summary {
    font-weight: 600 !important;
    color: var(--text-primary) !important;
}

[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    padding-bottom: 1.25rem !important;
}

.upload-guide-body {
    padding: 0.25rem 0.15rem 1rem 0.15rem;
}

.upload-guide-body table {
    width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    margin: 0.75rem 0 1rem 0;
    font-size: 0.85rem;
}

.upload-guide-body th,
.upload-guide-body td {
    padding: 0.55rem 0.65rem;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
    word-wrap: break-word;
    overflow-wrap: anywhere;
    line-height: 1.45;
}

.upload-guide-body th {
    color: var(--text-primary) !important;
    font-weight: 600;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

.upload-guide-body td {
    color: var(--text-muted) !important;
}

.upload-guide-body tr:last-child td,
.upload-guide-body tr:last-child th {
    border-bottom: none;
}

.usage-guide-body {
    padding: 0.25rem 0.15rem 1rem 0.15rem;
}

.usage-guide-body ol {
    margin: 0.5rem 0 0 0;
    padding-left: 1.35rem;
    color: var(--text-muted);
    font-size: 0.92rem;
    line-height: 1.65;
}

.usage-guide-body ol li {
    margin-bottom: 0.65rem;
}

.usage-guide-body ol li strong {
    color: var(--text-primary);
}

/* Landing / login — sidebar gizle */
body.iadeajan-hide-sidebar section[data-testid="stSidebar"] {
    display: none !important;
}

body.iadeajan-hide-sidebar [data-testid="stAppViewContainer"] > section.main {
    margin-left: 0 !important;
}

.landing-navbar-brand {
    display: inline-flex;
    align-items: center;
    gap: 0.7rem;
    white-space: nowrap;
    padding: 0.35rem 0;
    min-width: 10.5rem;
}

.landing-navbar-brand img {
    height: 2.75rem;
    width: auto;
    flex-shrink: 0;
    display: block;
    object-fit: contain;
}

.landing-navbar-name {
    font-size: 1.45rem;
    font-weight: 800;
    letter-spacing: -0.04em;
    line-height: 1;
    color: var(--text-primary);
    white-space: nowrap;
}

.landing-navbar-tagline {
    display: block;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-top: 0.2rem;
}

.landing-hero {
    text-align: center;
    margin: 0.5rem 0 1.5rem 0;
    padding: 1.5rem 0 0.5rem 0;
}

.landing-hero-logo {
    display: flex;
    justify-content: center;
    margin-bottom: 1.25rem;
}

.landing-hero-logo img {
    max-height: 5rem;
    width: auto;
    object-fit: contain;
}

.landing-cta-wrap {
    margin: 0 0 2.5rem 0;
}

.pitch-finance-wrap {
    margin: 0.5rem 0 1.5rem 0;
}

.pitch-finance-score {
    text-align: center;
    padding: 2rem 1.25rem 1.75rem;
    border: 1px solid var(--border-accent);
    border-radius: 12px;
    background: linear-gradient(
        160deg,
        rgba(16, 185, 129, 0.14) 0%,
        rgba(16, 185, 129, 0.04) 55%,
        rgba(255, 255, 255, 0.02) 100%
    );
    box-shadow: 0 0 48px rgba(16, 185, 129, 0.12);
}

.pitch-finance-score-label {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin: 0 0 0.5rem 0;
}

.pitch-finance-score-value {
    font-size: 4.25rem;
    font-weight: 800;
    letter-spacing: -0.04em;
    line-height: 1;
    color: #00df89;
    margin: 0;
}

.pitch-finance-score-hint {
    font-size: 0.88rem;
    color: var(--text-muted);
    margin: 0.75rem 0 0 0;
}

.pitch-finance-outcome-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 0.65rem 0;
}

.pitch-finance-chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 1rem;
}

.pitch-finance-chip {
    display: inline-block;
    padding: 0.35rem 0.75rem;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    border: 1px solid var(--border-accent);
    color: #00df89;
    background: rgba(16, 185, 129, 0.1);
}

.login-panel {
    max-width: 28rem;
    margin: 2rem auto 0 auto;
}

/* Divider */
hr {
    border-color: var(--border) !important;
    margin: 1.5rem 0 !important;
}

/* Progress bar */
.stProgress > div > div {
    background-color: var(--accent) !important;
}

/* Radio / select */
[data-testid="stWidgetLabel"] {
    color: var(--text-primary) !important;
}

/* Kod blokları */
[data-testid="stCode"] pre,
.stCodeBlock {
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
}

/* Özel UI bileşenleri */
.brand-title {
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--accent);
    margin: 0;
    letter-spacing: -0.03em;
}

.brand-sub {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin: 0.25rem 0 0 0;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}

.brand-logo-wrap {
    display: flex;
    justify-content: center;
    margin: 0.25rem 0 0.5rem 0;
}

.brand-logo-wrap img {
    max-height: 4.5rem;
    width: auto;
    object-fit: contain;
}

.agent-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.35rem 1.5rem;
    height: 100%;
    min-height: 11.25rem;
    box-sizing: border-box;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.agent-card:hover {
    border-color: var(--border-accent);
    box-shadow: 0 0 16px var(--accent-dim);
}

.agent-card-title {
    color: var(--text-primary);
    font-weight: 600;
    font-size: 0.95rem;
    margin: 0 0 0.5rem 0;
}

.agent-card-desc {
    color: var(--text-muted);
    font-size: 0.875rem;
    line-height: 1.55;
    margin: 0;
}

.lead-text {
    color: var(--text-muted);
    font-size: 1.05rem;
    line-height: 1.65;
    max-width: 52rem;
}

/* Idle kart grid — eşit genişlik ve yükseklik (dış kutu) */
.material-sym {
    font-family: 'Material Symbols Outlined', sans-serif;
    font-weight: normal;
    font-style: normal;
    font-size: 1.65rem;
    line-height: 1;
    color: var(--accent);
    flex-shrink: 0;
    user-select: none;
    font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
}

.idle-saas-grid {
    display: grid;
    gap: 1rem;
    width: 100%;
    margin: 0.25rem 0 1rem 0;
    align-items: stretch;
}

.idle-saas-grid--2x2 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
}

.idle-saas-grid--3 {
    grid-template-columns: repeat(3, minmax(0, 1fr));
}

.idle-saas-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.25rem 1.35rem;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 10.75rem;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.idle-saas-card:hover {
    border-color: var(--border-accent);
    box-shadow: 0 0 16px var(--accent-dim);
}

.idle-saas-card-head {
    display: flex;
    align-items: flex-start;
    gap: 0.55rem;
    margin-bottom: 0.75rem;
    min-height: 2.85rem;
}

.idle-saas-title {
    color: var(--text-primary);
    font-size: 0.92rem;
    font-weight: 600;
    line-height: 1.35;
    flex: 1;
}

.idle-saas-desc {
    color: var(--text-muted);
    font-size: 0.86rem;
    line-height: 1.5;
    margin: 0;
    flex: 1;
}

/* Bordered idle kartları — eşit yükseklik (2 kolon satırları) + alt boşluk */
[data-testid="stHorizontalBlock"]:has(
    [data-testid="column"] [data-testid="stVerticalBlockBorderWrapper"]
) {
    align-items: stretch !important;
    margin-bottom: 0.85rem !important;
}

[data-testid="stHorizontalBlock"]:has(
    [data-testid="column"] [data-testid="stVerticalBlockBorderWrapper"]
):last-of-type {
    margin-bottom: 0 !important;
}

[data-testid="column"]:has([data-testid="stVerticalBlockBorderWrapper"]) {
    display: flex !important;
    flex-direction: column !important;
}

[data-testid="column"]:has([data-testid="stVerticalBlockBorderWrapper"])
    > [data-testid="stVerticalBlock"] {
    flex: 1 1 auto !important;
    display: flex !important;
    flex-direction: column !important;
}

[data-testid="stVerticalBlockBorderWrapper"] {
    flex: 1 1 auto !important;
    height: 100% !important;
    min-height: 9.5rem !important;
    box-sizing: border-box !important;
    padding-bottom: 1.1rem !important;
}

[data-testid="stVerticalBlockBorderWrapper"] h4 {
    margin-bottom: 0.65rem !important;
    line-height: 1.35 !important;
}

.idle-bordered-desc {
    color: var(--text-muted) !important;
    font-size: 0.9rem !important;
    line-height: 1.55 !important;
    margin: 0 0 0.65rem 0 !important;
    padding-bottom: 0.15rem !important;
}

.decision-banner {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.25rem 1.5rem;
    margin: 0.5rem 0;
}

.decision-banner--success { border-color: var(--border-accent); }
.decision-banner--warn { border-color: rgba(245, 158, 11, 0.4); }
.decision-banner--danger { border-color: rgba(239, 68, 68, 0.4); }

.severity-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 0.5rem;
    vertical-align: middle;
}

.severity-kritik .severity-dot,
.severity-yuksek .severity-dot { background: #EF4444; }
.severity-orta .severity-dot { background: #F59E0B; }
.severity-dusuk .severity-dot { background: var(--accent); }

.pipeline-step {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0;
    font-size: 0.85rem;
    color: var(--text-muted);
}

.pipeline-step-done {
    color: var(--accent);
}

.score-legend {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-top: 0.5rem;
}

.score-value {
    color: var(--accent);
    font-size: 1.25rem;
    font-weight: 700;
}

/* Tablo (skor dökümü) */
[data-testid="stMarkdownContainer"] table {
    width: 100%;
    border-collapse: collapse;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
}

[data-testid="stMarkdownContainer"] th,
[data-testid="stMarkdownContainer"] td {
    padding: 0.65rem 1rem;
    border-bottom: 1px solid var(--border);
    color: var(--text-muted);
}

[data-testid="stMarkdownContainer"] th {
    color: var(--text-primary);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
}
</style>
"""


def _brand_logo_data_uri() -> str | None:
    """Navbar HTML img için PNG data URI."""
    if not BRAND_LOGO_PATH.is_file():
        return None
    encoded = base64.b64encode(BRAND_LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _render_landing_navbar_brand() -> None:
    """Landing sol üst — logo + marka (tek satır, kırılma yok)."""
    nav_left, _nav_mid, _nav_right = st.columns([2.2, 2.3, 1.5])
    with nav_left:
        logo_uri = _brand_logo_data_uri()
        if logo_uri:
            st.markdown(
                f"""
<div class="landing-navbar-brand">
  <img src="{logo_uri}" alt="İadeAjan" />
  <span>
    <span class="landing-navbar-name">İadeAjan</span>
    <span class="landing-navbar-tagline">KDV İade Denetim</span>
  </span>
</div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
<div class="landing-navbar-brand">
  <span class="landing-navbar-name" style="color:#00DF89;">İadeAjan</span>
</div>
                """,
                unsafe_allow_html=True,
            )


def _configure_streamlit_logo() -> None:
    """Streamlit üst/sidebar logosu (tüm sayfalar)."""
    if BRAND_LOGO_PATH.is_file():
        st.logo(
            str(BRAND_LOGO_PATH),
            icon_image=str(BRAND_LOGO_PATH),
            size="medium",
        )


def _render_brand_header(compact: bool = False, *, show_subtitle: bool = True) -> None:
    """Sidebar, login veya sunum için marka logosu + başlık."""
    logo_width = 96 if compact else 128
    if BRAND_LOGO_PATH.is_file():
        _left, center, _right = st.columns([0.15, 0.7, 0.15])
        with center:
            st.image(str(BRAND_LOGO_PATH), width=logo_width)
    pad_style = "margin:0.35rem 0 0 0;" if compact else "margin:0.5rem 0 0 0;"
    st.markdown(
        f"""
        <p class="brand-title" style="text-align:center;{pad_style}">İadeAjan</p>
        """,
        unsafe_allow_html=True,
    )
    if show_subtitle:
        st.markdown(
            '<p class="brand-sub" style="text-align:center;">KDV İade Analiz Sistemi</p>',
            unsafe_allow_html=True,
        )


def _render_agent_card(title: str, icon: str, description: str) -> None:
    """Kenarlıklı koyu ajan kartı (renkli st.info/warning/success yok)."""
    st.markdown(icon)
    st.markdown(
        f"""
        <div class="agent-card" style="margin-top:-0.35rem;">
            <p class="agent-card-title">{title}</p>
            <p class="agent-card-desc">{description}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_pipeline_step(agent: str, done: bool = True) -> None:
    """Sidebar pipeline satırı."""
    icon = AGENT_ICONS.get(agent, ":material/smart_toy:")
    check = ":material/check_circle:" if done else ""
    st.markdown(f"{icon} **{agent}** {check}")


# ─────────────────────────────────────────────────────────────
# ÇEKIRDEK — SESSION YÖNETİMİ
# ─────────────────────────────────────────────────────────────


def _init_session() -> None:
    """
    Session state'i başlatır; her sayfa yüklemesinde çalışır.
    Mevcut değerlere dokunmaz. Graph yalnızca app sayfasında derlenir.
    """
    for key, default in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _ensure_graph() -> None:
    """LangGraph'i bir kez derler (yalnızca page == app iken çağrılır)."""
    if st.session_state.graph is None:
        with st.spinner("Sistem başlatılıyor..."):
            st.session_state.graph = build_graph(interrupt=False)


def _set_sidebar_visible(show: bool) -> None:
    """Landing/login'de sidebar gizlenir."""
    display = "flex" if show else "none"
    st.markdown(
        f"""
        <style>
        section[data-testid="stSidebar"] {{
            display: {display} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _session_uploaded_paths() -> list[str]:
    """Excel/JSON tek dosya + opsiyonel çoklu belge yolları (sıra korunur)."""
    paths: list[str] = []
    invoice_path = st.session_state.get("uploaded_file_path")
    if invoice_path:
        paths.append(str(invoice_path))
    paths.extend(list(st.session_state.get("uploaded_document_paths") or []))
    return paths


def _reset_session() -> None:
    """
    Analiz state'ini sıfırlar; derlenmiş graph ve page korunur.
    """
    preserved_graph = st.session_state.graph
    preserved_page = st.session_state.get("page", "app")
    preserved_authenticated = st.session_state.get("authenticated", False)
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.graph = preserved_graph
    _init_session()
    st.session_state.page = preserved_page
    st.session_state.authenticated = preserved_authenticated


def _merge_state(update: dict[str, Any]) -> None:
    """
    Graph çıktısını session current_state ile birleştirir.
    agent_logs için extend (LangGraph reducer mantığına uygun);
    diğer alanlar override edilir.
    """
    for key, val in update.items():
        if key == "agent_logs" and isinstance(val, list):
            existing = st.session_state.current_state.get("agent_logs", [])
            st.session_state.current_state["agent_logs"] = existing + val
        else:
            st.session_state.current_state[key] = val


def _update_phase() -> None:
    """
    current_state'ten phase'i günceller.
    """
    status = st.session_state.current_state.get("analysis_status", "")
    clarification_needed = st.session_state.current_state.get(
        "clarification_needed", False
    )

    if status == "completed":
        st.session_state.phase = "done"
    elif status == "failed":
        st.session_state.phase = "error"
    elif status == "clarification_blocked":
        st.session_state.phase = "blocked"
    elif status == "clarification_waiting" or clarification_needed:
        st.session_state.phase = "clarification"
    elif status == "running":
        st.session_state.phase = "running"
    else:
        st.session_state.phase = "running"


def _extract_progress_from_logs() -> list[str]:
    """
    agent_logs satırlarındaki [AgentName] prefix'ten benzersiz ajan listesini,
    ilk görülme sırasına göre çıkarır.
    """
    logs: list[str] = st.session_state.current_state.get("agent_logs", [])
    seen: set[str] = set()
    ordered: list[str] = []
    for line in logs:
        match = _AGENT_LOG_RE.search(line)
        if match:
            agent = match.group(1)
            if agent not in seen:
                seen.add(agent)
                ordered.append(agent)
    return ordered


# ─────────────────────────────────────────────────────────────
# İKİ SEGMENTLİ INVOKE AKIŞI
# ─────────────────────────────────────────────────────────────


def _run_segment_1(initial_state: dict[str, Any]) -> None:
    """
    Segment 1: graph.stream() ile node'ları teker teker işler.

    ClarificationAgent ilk kez göründüğünde (Üretim modu — clarification_needed=True)
    stream durdurulur; kullanıcı cevap bekletilir.
    clarification_needed=False olursa (sorular yoksa) Done fazına geçilir.
    Collector hata verirse Error fazına geçilir.

    graph.stream(stream_mode="updates") her node güncellemesini ayrı chunk olarak
    verir; bu sayede Clarification'ı ilk çalışmasında yakalayıp durdurabiliriz.
    """
    st.session_state.phase = "running"
    _clear_clarification_proof_cache()
    schedule_file_cleanup = __import__(
        "app.services.proof_ledger", fromlist=["schedule_file_cleanup"]
    ).schedule_file_cleanup
    schedule_file_cleanup()
    st.session_state.current_state = {
        "proof_ledger": [],
        "verification_summary": {},
    }
    st.session_state.progress_log = []
    st.session_state.clarification_answers = {}

    try:
        for chunk in st.session_state.graph.stream(
            initial_state,
            stream_mode="updates",
        ):
            for node_name, node_data in chunk.items():
                _merge_state(node_data)

                # Clarification node ilk kez çalıştı → Üretim modu
                # clarification_needed=True ise kullanıcıya sor, döngüyü kes
                if node_name == "ClarificationAgent":
                    clar_needed = st.session_state.current_state.get(
                        "clarification_needed", False
                    )
                    if clar_needed:
                        # Döngüye girmeden stream'i burada bitiriyoruz
                        st.session_state.phase = "clarification"
                        st.session_state.progress_log = _extract_progress_from_logs()
                        return

    except Exception as exc:
        st.session_state.current_state["error_state"] = f"SEGMENT1_ERROR: {exc}"
        st.session_state.current_state["analysis_status"] = "failed"

    st.session_state.progress_log = _extract_progress_from_logs()
    _update_phase()


def _run_segment_2(answers: dict[str, Any]) -> None:
    """
    Segment 2: Cevaplar enjekte edilerek graph.stream ile devam edilir.

    graph.invoke yerine graph.stream kullanılır:
    - CollectorAgent'ın mock senaryoya düşmemesi için uploaded_files korunur.
    - Yeni clarification turu (AnalyzerAgent yeni soru ürettiyse) için stream
      ClarificationAgent'ta durdurulur; kullanıcı yeni soruyu görür.
    - Tüm turlar tamamlandığında DecisionAgent çalışıp Done fazına geçilir.
    """
    resume_state = dict(st.session_state.current_state)
    resume_state["clarification_answers"] = answers
    resume_state["clarification_needed"] = False
    resume_state["clarification_message"] = ""   # yeni tur için mesajı temizle
    # agent_logs korunur — Segment 1 zinciri kaybolmasın (Collector segment 2'de atlanır)

    # Yüklenen dosya yolunu koru — CollectorAgent mock senaryoya düşmesin
    paths = _session_uploaded_paths()
    if paths:
        resume_state["uploaded_files"] = paths
    if st.session_state.current_state.get("proof_ledger"):
        resume_state["proof_ledger"] = st.session_state.current_state["proof_ledger"]

    try:
        for chunk in st.session_state.graph.stream(
            resume_state,
            stream_mode="updates",
        ):
            for node_name, node_data in chunk.items():
                _merge_state(node_data)

                # Yeni clarification turu: AnalyzerAgent yeni sorular üretmişse
                # ClarificationAgent production modunda stream'i durdur,
                # kullanıcıya yeni soruları göster.
                if node_name == "ClarificationAgent":
                    clar_needed = st.session_state.current_state.get(
                        "clarification_needed", False
                    )
                    if clar_needed:
                        st.session_state.phase = "clarification"
                        st.session_state.progress_log = _extract_progress_from_logs()
                        return

    except Exception as exc:
        st.session_state.current_state["error_state"] = f"SEGMENT2_ERROR: {exc}"
        st.session_state.current_state["analysis_status"] = "failed"

    st.session_state.progress_log = _extract_progress_from_logs()
    _update_phase()


# ─────────────────────────────────────────────────────────────
# YÜKLEME REHBERİ & ÖN KONTROL
# ─────────────────────────────────────────────────────────────


def _render_upload_guide(*, compact: bool = False) -> None:
    """Kullanıcıya Excel/CSV yükleme kurallarını gösterir."""
    with st.expander(
        ":material/menu_book: Dosya nasıl hazırlanır?",
        expanded=not compact,
    ):
        st.markdown(
            """
<div class="upload-guide-body">

<p><strong>Desteklenen formatlar:</strong> Excel (<code>.xlsx</code>, <code>.xls</code>), CSV, JSON</p>

<p><strong>Fatura listenizde olması gereken sütunlar</strong></p>

<table>
<thead>
<tr><th>Sütun</th><th>Örnek başlık</th><th>Zorunlu</th></tr>
</thead>
<tbody>
<tr><td>Fatura no</td><td><code>Belge Kimliği</code>, <code>Fatura No</code></td><td>Evet (veya tutar)</td></tr>
<tr><td>Tarih</td><td><code>İşlem Tarihi</code></td><td>Önerilir (YYYY-MM-DD)</td></tr>
<tr><td>Evrak türü</td><td><code>Evrak Türü</code></td><td><strong>Çok önemli</strong></td></tr>
<tr><td>Tutar</td><td><code>Yekün (TRY)</code>, <code>Tutar</code></td><td>Evet (veya fatura no)</td></tr>
<tr><td>KDV oranı</td><td><code>KDV Oranı</code></td><td>İsteğe bağlı</td></tr>
<tr><td>Karşı taraf</td><td><code>Karşı Taraf Ünvanı</code></td><td>Önerilir</td></tr>
<tr><td>VKN</td><td><code>Firma VKN Numarası</code></td><td>Önerilir (10–11 rakam)</td></tr>
</tbody>
</table>

<p><strong>Evrak türü</strong> satırda şunlardan biri olmalı: <code>Alış</code>, <code>Satış</code>, <code>İhracat</code></p>

<p><strong>Analiz sırasında sorulacak belgeler</strong> (Excel’e yazılmaz, sistem sorar):</p>
<ul>
<li>İhracat iadesi → Gümrük Çıkış Beyannamesi (GÇB)</li>
<li>Tevkifat iadesi → 2 No’lu KDV Beyannamesi</li>
</ul>

<p>Yanlış veya eksik dosya yüklerseniz sistem <strong>uyarır</strong> ve mümkün olanları
<strong>otomatik düzeltmeye</strong> çalışır (tarih formatı, tutar, VKN temizliği, eksik fatura no).</p>

</div>
            """,
            unsafe_allow_html=True,
        )
        st.download_button(
            label="Örnek şablon Excel indir",
            data=get_template_excel_bytes(),
            file_name="iadeajan_fatura_sablonu.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            icon=":material/download:",
        )


def _run_upload_preflight(file_path: str) -> PreflightResult:
    """Dosyayı kontrol eder; düzeltme varsa düzeltilmiş kopyayı kaydeder."""
    result = inspect_upload(file_path, apply_repairs=True)
    st.session_state.upload_preflight = result

    if (
        result.ok
        and result.repaired_rows
        and result.fixes_applied
        and result.repaired_columns
    ):
        repaired = Path(file_path).parent / f"duzeltilmis_{Path(file_path).name}"
        if not str(repaired).lower().endswith(".xlsx"):
            repaired = repaired.with_suffix(".xlsx")
        write_repaired_excel(
            result.repaired_rows, result.repaired_columns, repaired
        )
        st.session_state.uploaded_file_path = str(repaired)
        st.session_state.uploaded_file_name = repaired.name

    return result


_UPLOAD_ISSUES_PAGE_SIZE = 100


def _render_upload_issues(issues: list[UploadIssue]) -> None:
    """Satır/sütun düzeyinde yükleme hatalarını tablo ve CSV ile gösterir."""
    if not issues:
        return

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    row_issues = [i for i in issues if i.row is not None]
    file_issues = [i for i in issues if i.row is None]

    first_row = row_issues[0].row if row_issues else None
    summary = (
        f"**{len(errors)}** hata, **{len(warnings)}** uyarı"
        + (f" — ilk satır düzeltmesi: **{first_row}**" if first_row else "")
    )
    st.markdown(summary)

    for issue in file_issues:
        if issue.severity == "error":
            st.error(issue.message_tr)
        else:
            st.warning(issue.message_tr)

    if row_issues:
        page_key = "upload_issues_page"
        total_pages = max(1, (len(row_issues) + _UPLOAD_ISSUES_PAGE_SIZE - 1) // _UPLOAD_ISSUES_PAGE_SIZE)
        page = st.session_state.get(page_key, 0)
        if page >= total_pages:
            page = 0
            st.session_state[page_key] = 0

        col_nav1, col_nav2, col_nav3 = st.columns([1, 2, 1])
        with col_nav1:
            if st.button("← Önceki", disabled=page <= 0, key="upload_issues_prev"):
                st.session_state[page_key] = max(0, page - 1)
                st.rerun()
        with col_nav2:
            st.caption(f"Satır hataları: sayfa {page + 1} / {total_pages}")
        with col_nav3:
            if st.button("Sonraki →", disabled=page >= total_pages - 1, key="upload_issues_next"):
                st.session_state[page_key] = min(total_pages - 1, page + 1)
                st.rerun()

        start = page * _UPLOAD_ISSUES_PAGE_SIZE
        chunk = row_issues[start : start + _UPLOAD_ISSUES_PAGE_SIZE]
        table_rows = [
            {
                "Satır": issue.row,
                "Sütun": issue.column or "—",
                "Harfi": issue.column_letter or "—",
                "Alan": issue.field_label_tr,
                "Mesaj": issue.message_tr,
                "Önem": "Hata" if issue.severity == "error" else "Uyarı",
            }
            for issue in chunk
        ]
        st.dataframe(table_rows, use_container_width=True, hide_index=True)

        st.download_button(
            label="Hata listesini indir (CSV)",
            data=issues_to_csv_bytes(issues),
            file_name="upload_hatalari.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        st.caption(
            "Tahmin ve uyarılar, ilgili hücre düzeltildiğinde maddenin kalktığı varsayımına dayanır."
        )


def _render_preflight_feedback(result: PreflightResult | None) -> bool:
    """
  Preflight uyarılarını gösterir.
  Döner: analiz başlatılabilir mi (bloklayıcı hata yok).
    """
    if result is None:
        return True

    if result.issues:
        _render_upload_issues(result.issues)
    else:
        for err in result.blocking_errors:
            st.error(err)
        for warn in result.warnings:
            st.warning(warn)

    for fix in result.fixes_applied:
        st.success(f":material/auto_fix_high: {fix}")

    if result.ok and result.invoice_count:
        st.caption(
            f":material/fact_check: {result.invoice_count} fatura satırı okundu."
        )

    return result.ok


# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────


def render_sidebar() -> str:
    """
    Sidebar'ı render eder. Seçilen scenario_id'yi döndürür.
    """
    with st.sidebar:
        _render_brand_header(compact=True)

        st.divider()

        st.markdown("**:material/tune: Analiz Ayarları**")
        has_invoice_file = bool(st.session_state.get("uploaded_file_path"))

        scenario_label = st.selectbox(
            "Şirket Senaryosu",
            options=list(SCENARIOS.values()),
            index=0,
            disabled=(st.session_state.phase == "running") or has_invoice_file,
        )
        scenario_id = next(
            k for k, v in SCENARIOS.items() if v == scenario_label
        )

        # ── Dosya Yükleyici ───────────────────────────────────────
        st.markdown("**:material/upload_file: Adım 1: Fatura Listesi (Zorunlu)**")
        st.caption(
            "Excel/CSV ile fatura listenizi yükleyin; yoksa demo senaryo kullanılır."
        )
        _render_upload_guide(compact=True)

        uploaded = st.file_uploader(
            "Dosya seçin",
            type=["xlsx", "xls", "csv", "json"],
            disabled=(st.session_state.phase == "running"),
            help="Fatura listesi: xlsx, xls, csv veya json",
            label_visibility="collapsed",
        )
        preflight_ok = True
        if uploaded:
            upload_dir = Path(tempfile.gettempdir()) / "iadeajan_uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            upload_path = upload_dir / uploaded.name
            upload_path.write_bytes(uploaded.getvalue())
            st.session_state.uploaded_file_name = uploaded.name
            st.session_state.uploaded_file_path = str(upload_path)

            with st.spinner("Dosya kontrol ediliyor..."):
                preflight = _run_upload_preflight(str(upload_path))
            preflight_ok = _render_preflight_feedback(preflight)

            display_name = st.session_state.uploaded_file_name or uploaded.name
            st.caption(f":material/check_circle: `{display_name}`")
        else:
            st.session_state.uploaded_file_name = None
            st.session_state.uploaded_file_path = None
            st.session_state.upload_preflight = None

        st.markdown("**:material/picture_as_pdf: Adım 2: Resmi Belgeler (Opsiyonel)**")
        st.caption("İsteğe bağlı; çoklu seçim. En fazla 10 MB / dosya.")
        docs_widget = st.file_uploader(
            "Belgeleri seçin",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            disabled=(st.session_state.phase == "running"),
            label_visibility="collapsed",
            key="iadeajan_sidebar_docs",
        )
        _max_doc_bytes = 10 * 1024 * 1024
        if docs_widget:
            upload_dir_docs = Path(tempfile.gettempdir()) / "iadeajan_uploads"
            upload_dir_docs.mkdir(parents=True, exist_ok=True)
            saved_doc_paths: list[str] = []
            for doc_file in docs_widget:
                doc_bytes = doc_file.getvalue()
                if len(doc_bytes) > _max_doc_bytes:
                    st.warning(f"`{doc_file.name}` 10 MB üzeri — atlandı.")
                    continue
                dest_doc = upload_dir_docs / f"{uuid.uuid4().hex[:12]}_{doc_file.name}"
                dest_doc.write_bytes(doc_bytes)
                saved_doc_paths.append(str(dest_doc))
            st.session_state.uploaded_document_paths = saved_doc_paths
            st.caption(f":material/check_circle: {len(saved_doc_paths)} belge hazır")
        if st.session_state.get("uploaded_document_paths"):
            if st.button("Belge yüklemelerini temizle", key="clear_doc_uploads"):
                st.session_state.uploaded_document_paths = []
                st.rerun()

        st.divider()

        # ── Analizi Başlat ────────────────────────────────────────
        start_disabled = st.session_state.phase == "running"
        if has_invoice_file and not preflight_ok:
            start_disabled = True
            st.caption(
                ":material/block: Dosyada düzeltilmesi gereken hata var; "
                "analiz başlatılamaz."
            )
        st.divider()
        if st.button(
            "Analizi Başlat",
            use_container_width=True,
            type="primary",
            disabled=start_disabled,
            icon=":material/play_arrow:",
        ):
            paths = _session_uploaded_paths()
            if paths:
                initial_state = {
                    "scenario_id": None
                    if st.session_state.get("uploaded_file_path")
                    else scenario_id,
                    "uploaded_files": paths,
                    "analysis_status": "running",
                    "agent_logs": [],
                }
                if st.session_state.get("uploaded_file_path"):
                    st.session_state.selected_scenario = None
                else:
                    st.session_state.selected_scenario = scenario_id
            else:
                initial_state = {
                    "scenario_id": scenario_id,
                    "uploaded_files": [],
                    "analysis_status": "running",
                    "agent_logs": [],
                }
                st.session_state.selected_scenario = scenario_id

            with st.spinner("Ajanlar başlatılıyor..."):
                _run_segment_1(initial_state)
            st.rerun()

        # ── Sıfırla ───────────────────────────────────────────────
        if st.session_state.phase not in ("idle", "running"):
            if st.button(
                "Sıfırla",
                use_container_width=True,
                icon=":material/restart_alt:",
            ):
                _reset_session()
                st.rerun()

        # ── Pipeline Durumu ───────────────────────────────────────
        if st.session_state.progress_log:
            st.divider()
            st.markdown("**:material/hub: Pipeline**")
            for agent in st.session_state.progress_log:
                _render_pipeline_step(agent, done=True)

        st.divider()
        if st.button(
            "Çıkış Yap",
            use_container_width=True,
            icon=":material/logout:",
            key="sidebar_logout",
        ):
            st.session_state.authenticated = False
            st.session_state.page = "landing"
            st.rerun()

    return scenario_id


# ─────────────────────────────────────────────────────────────
# FAZ RENDER FONKSİYONLARI
# ─────────────────────────────────────────────────────────────

# Landing vitrin — üst özellik şeridi
LANDING_VITRIN_CARDS: list[tuple[str, str, str]] = [
    (
        ":material/verified_user:",
        "Zero Trust Mimarisi",
        "Kanıtsız beyan reddedilir; yalnızca doğrulanmış belge kabul edilir.",
    ),
    (
        ":material/psychology:",
        "Hibrit Denetim",
        "Python determinizmi ile LLM semantiği tek motorda birleşir.",
    ),
    (
        ":material/account_balance:",
        "Tam Uyumluluk",
        "GİB ve e-Fatura API gateway ile mevzuata tam hizalı denetim.",
    ),
]

# Yatırım sunumu PDF — slayt 2 (problem istatistikleri)
PITCH_PROBLEM_CARDS: list[tuple[str, str, str]] = [
    (
        ":material/schedule:",
        "6-8 Ay Bekleme Süresi",
        "Bürokratik incelemeler nedeniyle şirketler kendi paralarına aylarca ulaşamıyor.",
    ),
    (
        ":material/table_chart:",
        "40K+ Satırlık Excel Dosyaları",
        "Sürecin uzamasının ana nedeni; devasa, karmaşık ve hatalarla dolu fatura setleri.",
    ),
    (
        ":material/percent:",
        "%0 Güven Ortamı",
        "Bankalar, bu karmaşık dosyaların doğruluğuna güvenemediği için kredi vermekten korkuyor.",
    ),
]

# Yatırım sunumu PDF — slayt 3 (kördüğüm)
PITCH_ECOSYSTEM_CARDS: list[tuple[str, str, str]] = [
    (
        ":material/store:",
        "KOBİ / İhracatçı",
        '"Devletten 5 Milyon TL iade alacağım var ama nakdim bitti. Bugün işçi '
        'maaşlarını ödeyemiyorum."',
    ),
    (
        ":material/account_balance:",
        "Banka / Faktoring",
        '"Sana bu alacağın karşılığında kredi veririm ama ya devlet o Excel dosyasında '
        'hata bulup iadeni iptal ederse? Benim param yanar."',
    ),
]

# Yatırım sunumu PDF — slayt 4 (nöro-sembolik)
PITCH_NEURO_CARDS: list[tuple[str, str, str]] = [
    (
        ":material/psychology:",
        "Anlamsal Zeka (LLM)",
        'Dağınık veriyi okur, anlamlandırır. "Tevkifat istenip tevkifatsız fatura '
        'kesilmesi" gibi komplike mantık çelişkilerini insan gibi yakalar.',
    ),
    (
        ":material/rule:",
        "Determinizm (Python)",
        "LLM'in yakaladığı veriyi Ceza Kanunnamesine sokar. GİB mevzuatına göre cezayı "
        "kesin ve hatasız keser; matematiği şansa bırakmaz.",
    ),
    (
        ":material/visibility:",
        "Açıklanabilir AI",
        "Nöro-Sembolik mimari kara kutu sorununu çözer. Hangi cezanın hangi mevzuat "
        "maddesine göre kesildiği şeffaftır.",
    ),
]

# Yatırım sunumu PDF — slayt 7 (mimari)
PITCH_ARCHITECTURE_CARDS: list[tuple[str, str, str]] = [
    (
        ":material/shield:",
        "Bounded AI (Sınırlandırılmış Zeka)",
        "Yapay zeka kendi kendine kural uyduramaz; yalnızca kodlanmış Ceza Matrisinden "
        "madde seçebilir.",
    ),
    (
        ":material/lock:",
        "Finansman Kilidi (Mandatory Lock)",
        "Hukuken zorunlu belge (ör. GÇB) eksikse LLM 100 puan verse bile banka kredisi "
        "kilitlenir.",
    ),
    (
        ":material/balance:",
        "Tavan Ceza Sistemi",
        "Tekil hatalar dosyanın tamamını yakmaz; orantılı ceza kesilir. Makro çöküş "
        "varsa dosya iptal edilir.",
    ),
]

# App idle — operasyonel süreç kartları (4 ajan)
PROCESS_CARDS: list[tuple[str, str, str]] = [
    (
        ":material/inbox:",
        "1. Collector",
        "Ham ve bozuk Excel verilerini alır, standartlaştırır. Hassas VKN "
        "verilerini KVKK uyumu için maskeler.",
    ),
    (
        ":material/analytics:",
        "2. Analyzer",
        "Çift beyinli çalışır. Python zorunlu belgeleri (GÇB) denetler; LLM "
        "anlamsal anormallikleri bulur.",
    ),
    (
        ":material/forum:",
        "3. Clarification",
        "Şüpheli durumlarda süreci dondurur. «İhracat demişsiniz ama GÇB yok?» "
        "diyerek Human-in-the-Loop sürecini işletir.",
    ),
    (
        ":material/gavel:",
        "4. Decision",
        "Tüm kanıtları toplar; Ceza Kanunnamesine göre KDV İade Güven Skoru ve "
        "finansman kararı üretir.",
    ),
]

def _render_bordered_card(icon: str, title: str, desc: str) -> None:
    """Tek bordered kart — Material ikon + başlık + açıklama."""
    with st.container(border=True):
        st.markdown(f"#### {icon} {title}")
        st.markdown(
            f'<p class="idle-bordered-desc">{desc}</p>',
            unsafe_allow_html=True,
        )


def _render_equal_card_row(
    cards: list[tuple[str, str, str]],
    *,
    columns: int | None = None,
) -> None:
    """Bordered kartlar; 4 adet için 2x2 ızgara, 3 adet için tek satır."""
    n = columns or len(cards)

    if len(cards) == 4 and n == 4:
        col1, col2 = st.columns(2)
        with col1:
            _render_bordered_card(*cards[0])
        with col2:
            _render_bordered_card(*cards[1])

        col3, col4 = st.columns(2)
        with col3:
            _render_bordered_card(*cards[2])
        with col4:
            _render_bordered_card(*cards[3])
        return

    if len(cards) == 3 and n == 3:
        col1, col2 = st.columns(2)
        with col1:
            _render_bordered_card(*cards[0])
        with col2:
            _render_bordered_card(*cards[1])
        col3, _col_spacer = st.columns(2)
        with col3:
            _render_bordered_card(*cards[2])
        return

    if len(cards) == 2 and n == 2:
        col1, col2 = st.columns(2)
        with col1:
            _render_bordered_card(*cards[0])
        with col2:
            _render_bordered_card(*cards[1])
        return

    cols = st.columns(n)
    for col, card in zip(cols, cards, strict=True):
        with col:
            _render_bordered_card(*card)


def _render_pitch_roi_comparison() -> None:
    """Landing — geleneksel süreç vs İadeAjan (Kördüğüm ile Mimari arası)."""
    st.markdown("#### :material/compare_arrows: Neden İadeAjan?")
    col_legacy, col_iadeajan = st.columns(2)
    with col_legacy:
        with st.container(border=True):
            st.markdown("##### :material/hourglass_empty: Geleneksel Süreç")
            st.markdown(
                '<p class="idle-bordered-desc" style="color:#a8a29e;margin:0;">'
                "Manuel Excel tasnifleri, YMM süreçlerinde insan hatası riski ve "
                "faktoring onayı için 30-45 günlük kör bekleyiş.</p>",
                unsafe_allow_html=True,
            )
    with col_iadeajan:
        with st.container(border=True):
            st.markdown("##### :material/bolt: İadeAjan Otonomisi")
            st.markdown(
                '<p class="idle-bordered-desc" style="color:#00DF89;margin:0;">'
                "Dosyalar saniyeler içinde kanıt defterine işlenir, API ile doğrulanır ve "
                "aynı gün finansman onayı için şeffaf Güven Raporu üretilir.</p>",
                unsafe_allow_html=True,
            )
    st.divider()


def _render_pitch_financing_section() -> None:
    """Landing — anında finansman / risk skoru (premium kart düzeni)."""
    st.markdown("#### :material/payments: Sadece Denetim Değil, Anında Finansman")
    st.markdown(
        '<p class="lead-text" style="font-size:0.95rem;margin:0 0 1.25rem 0;max-width:52rem;">'
        "Devletten KDV iadesi almak aylar sürer; KOBİ'lerin nakde bugün ihtiyacı var. "
        "İadeAjan dosyayı saniyeler içinde denetler.</p>",
        unsafe_allow_html=True,
    )

    col_story, col_score = st.columns([1.15, 1], gap="large")
    with col_story:
        with st.container(border=True):
            st.markdown("##### :material/schedule: Aylarca Beklemeye Son")
            st.markdown(
                '<p class="idle-bordered-desc" style="margin:0;">'
                "90+ (Düşük Risk) alan, eksik zorunlu belgesi olmayan dosyalar için otomatik "
                "Güven Raporu üretilir. Şirketler bu raporla faktoring firmalarına giderek aynı "
                "gün ön finansman (kredi) alabilirler.</p>",
                unsafe_allow_html=True,
            )
            st.markdown(
                """
<div class="pitch-finance-chip-row">
<span class="pitch-finance-chip">Güven Raporu</span>
<span class="pitch-finance-chip">Aynı Gün Ön Finansman</span>
<span class="pitch-finance-chip">Faktoring Uyumu</span>
</div>
                """,
                unsafe_allow_html=True,
            )
    with col_score:
        st.markdown(
            """
<div class="pitch-finance-score">
<p class="pitch-finance-score-label">Düşük Risk Skoru</p>
<p class="pitch-finance-score-value">90+</p>
<p class="pitch-finance-score-hint">Eksik zorunlu belge yok → otomatik onay hattı</p>
</div>
            """,
            unsafe_allow_html=True,
        )
    st.divider()

def _render_pitch_data_security_pledge() -> None:
    """Landing — kurumsal veri gizliliği taahhüdü (sayfa altı)."""
    st.divider()
    with st.container(border=True):
        st.markdown(
            "#### :material/lock: Enterprise-Grade Veri Gizliliği (Zero-Retention)"
        )
        st.info(
            "Ticari sırlarınız mühürlüdür. Yüklenen faturalar ve gümrük beyannameleri "
            "KVKK/GDPR standartlarında maskelenerek işlenir. Verileriniz yapay zeka (LLM) "
            "modellerinin eğitiminde KESİNLİKLE kullanılmaz; denetim bitiminde izole edilir."
        )


def _render_landing_hero() -> None:
    """Landing üst — navbar (sol logo) + merkez hero + CTA."""
    st.markdown('<div class="landing-hero">', unsafe_allow_html=True)

    _render_landing_navbar_brand()
    st.markdown("<br><br>", unsafe_allow_html=True)

    st.markdown(
        "<h1 style='text-align: center; font-size: 3.5rem; font-weight: 800; "
        "letter-spacing: -0.03em; line-height: 1.15; margin-bottom: 1rem;'>"
        "Sermayenizi <span style='color: #00DF89;'>Serbest Bırakın</span></h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='text-align: center; font-size: 1.25rem; color: #888; max-width: 750px; "
        "margin: 0 auto 2.5rem auto; line-height: 1.6;'>"
        "Aylar süren KDV iade denetimlerini ve ön onay süreçlerini Zero Trust (Sıfır Güven) "
        "mimarisi ve yapay zeka ile saniyelere indiren kurumsal risk analiz motoru.</p>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="landing-cta-wrap">', unsafe_allow_html=True)
    _cta_col1, cta_col2, _cta_col3 = st.columns([1, 1.5, 1])
    with cta_col2:
        if st.button(
            "Sisteme Giriş Yap / Demoyu Başlat →",
            type="primary",
            use_container_width=True,
            key="landing_hero_cta",
        ):
            st.session_state.page = "login"
            st.rerun()
        if st.button(
            "Analiz Merkezine Git",
            use_container_width=True,
            icon=":material/dashboard:",
            key="nav_to_app",
        ):
            st.session_state.page = "app"
            st.rerun()
        st.markdown(
            "<p style='text-align: center; font-size: 0.85rem; color: #666; margin-top: 0.8rem;'>"
            "GİB ve e-Fatura API entegrasyonu ile tam uyumlu.</p>",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_product_pitch() -> None:
    """Yatırım sunumu PDF — landing içerik (hero + slaytlar)."""
    _render_landing_hero()
    st.divider()
    _render_landing_vitrin()
    st.divider()

    # Slayt 2 — Problem
    st.markdown(
        "#### :material/report_problem: Problem: Kilitlenmiş Milyarlarca Lira"
    )
    st.markdown(
        '<p class="lead-text" style="font-size:0.95rem;margin:0 0 0.5rem 0;">'
        "Türkiye'de binlerce KOBİ ve ihracatçı, devletten alacaklı olduğu KDV "
        "iadelerini almak için aylarca bekliyor. Nakit akışı duruyor, büyüme "
        "yavaşlıyor.</p>",
        unsafe_allow_html=True,
    )
    _render_equal_card_row(PITCH_PROBLEM_CARDS, columns=3)
    st.divider()

    # Slayt 3 — Kördüğüm
    st.markdown("#### :material/sync_alt: Ekosistemdeki Kördüğüm")
    st.markdown(
        '<p class="lead-text" style="font-size:0.9rem;margin:0.5rem 0 0.25rem 0;">'
        "<strong>Sonuç:</strong> Şirket kilitlenir, banka fırsatı kaçırır, "
        "ticaret yavaşlar.</p>",
        unsafe_allow_html=True,
    )
    _render_equal_card_row(PITCH_ECOSYSTEM_CARDS, columns=2)
    st.markdown(
        '<p class="lead-text" style="font-size:0.95rem;margin:0.75rem 0 1.25rem 0;">'
        '<span style="color:var(--accent);font-weight:600;">'
        "İşte biz İadeAjan olarak, tam bu kördüğümü çözmek için yola çıktık."
        "</span></p>",
        unsafe_allow_html=True,
    )
    _render_pitch_roi_comparison()

    # Slayt 4 — Nöro-Sembolik
    st.markdown("#### :material/hub: Zeka ve Kanunun Kusursuz Birleşimi")
    _render_equal_card_row(PITCH_NEURO_CARDS, columns=3)
    st.divider()

    # Slayt 5 — Ajanlar
    st.markdown(
        "#### :material/smart_toy: Otonom Ajanlarla (LangGraph) Denetim Akışı"
    )
    _render_equal_card_row(PROCESS_CARDS)
    st.divider()

    # Slayt 7 — Mimari
    st.markdown("#### :material/security: Halüsinasyon Görmeyen Mimari")
    _render_equal_card_row(PITCH_ARCHITECTURE_CARDS, columns=3)
    st.divider()

    # Slayt 8 — Finansman
    _render_pitch_financing_section()

    # Slayt 9 — Hedef pazar
    st.markdown("#### :material/storefront: Hedef Pazar ve İş Modeli (B2B SaaS)")
    st.markdown(
        '<p class="lead-text" style="font-size:0.95rem;margin:0;">'
        "Ekosistemdeki tüm paydaşlara değer katıyoruz: Şirketler hızlı finansmana, "
        "müşavirler otonom denetime, finans kurumları ise sıfır riskli «temiz» KDV "
        "dosyalarına kavuşuyor.</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    # Slayt 10 — Talep artışı
    st.markdown("#### :material/trending_up: Nakit İhtiyacı ve Beklenen Talep Artışı")
    st.markdown(
        '<p class="lead-text" style="font-size:0.95rem;margin:0;">'
        "Ekonomik daralma ve işletme sermayesi ihtiyacının artmasıyla birlikte, "
        "şirketlerin KDV alacaklarını hızlıca nakde (ön finansman) çevirme taleplerinde "
        "eksponansiyel bir büyüme öngörüyoruz.</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    # Slayt 11 — Vizyon
    st.markdown("#### :material/flag: Vizyonumuz")
    st.markdown(
        '<p class="lead-text" style="font-size:0.95rem;margin:0;">'
        "İadeAjan olarak vizyonumuz, vergi ve denetim süreçlerini insan eli değmeyen, "
        "%100 otonom, adil ve şeffaf bir ekosisteme dönüştürmektir. Bugün KDV iade "
        "süreçlerindeki bürokrasiyi ve halüsinasyon riskini ortadan kaldırarak "
        "başladığımız bu yolculukta, gelecekteki en büyük hedefimiz: şirketlerin "
        "finansal sağlıklarını gerçek zamanlı analiz eden, devlet mevzuatlarıyla "
        "saniyeler içinde senkronize olan ve Güven Raporları ile şirketlere anında "
        "ön finansman kapılarını açan merkezi bir yapay zeka altyapısı olmaktır. "
        "Karmaşayı bitirmeye, ticarete hız katmaya geliyoruz.</p>",
        unsafe_allow_html=True,
    )
    _render_pitch_data_security_pledge()


def _render_landing_vitrin() -> None:
    """Landing üst şerit — 3 SaaS özelliği."""
    cols = st.columns(3)
    for col, card in zip(cols, LANDING_VITRIN_CARDS, strict=True):
        with col:
            _render_bordered_card(*card)


def render_landing_page() -> None:
    """Vitrin / sunum — page == landing (PDF yatırım sunumu tam metin)."""
    _set_sidebar_visible(False)
    _render_product_pitch()


def render_login_page() -> None:
    """Vitrin login — page == login."""
    _set_sidebar_visible(False)

    _left, center, _right = st.columns([1, 1.2, 1])
    with center:
        st.markdown('<div class="login-panel">', unsafe_allow_html=True)
        _render_brand_header(compact=True)
        st.markdown("### Kurumsal Giriş")
        st.caption("Demo ortamı — kimlik bilgileri doğrulanmaz.")

        st.text_input("E-posta", placeholder="ornek@sirket.com", key="login_email")
        st.text_input("Şifre", type="password", placeholder="••••••••", key="login_password")

        if st.button("Giriş Yap", type="primary", use_container_width=True, icon=":material/login:"):
            st.session_state.authenticated = True
            st.session_state.page = "app"
            st.rerun()

        if st.button("Hesap oluştur", use_container_width=True, icon=":material/person_add:"):
            st.session_state.page = "signup"
            st.rerun()

        if st.button("← Sunuma dön", use_container_width=True):
            st.session_state.page = "landing"
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


def render_signup_page() -> None:
    """Vitrin kayıt — page == signup veya app (authenticated değil)."""
    _set_sidebar_visible(False)

    _left, center, _right = st.columns([1, 1.2, 1])
    with center:
        st.markdown('<div class="login-panel">', unsafe_allow_html=True)
        _render_brand_header(compact=True)
        st.markdown("### Hesap Oluştur")
        st.caption("Demo ortamı — bilgiler doğrulanmaz; kayıt sonrası Analiz Merkezi açılır.")

        st.text_input("E-posta", placeholder="ornek@sirket.com", key="signup_email")
        st.text_input("Şifre", type="password", placeholder="••••••••", key="signup_password")
        st.text_input("Şirket ünvanı", placeholder="Örnek İhracat A.Ş.", key="signup_company")

        if st.button(
            "Hesap Oluştur",
            type="primary",
            use_container_width=True,
            icon=":material/person_add:",
        ):
            st.session_state.authenticated = True
            st.session_state.page = "app"
            st.rerun()

        if st.button(
            "Zaten hesabım var → Giriş Yap",
            use_container_width=True,
            icon=":material/login:",
        ):
            st.session_state.page = "login"
            st.rerun()

        if st.button("← Sunuma dön", use_container_width=True):
            st.session_state.page = "landing"
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


def _render_how_it_works_cards() -> None:
    """App idle — operasyonel süreç kartları (4 ajan, 2x2)."""
    st.markdown("#### :material/route: Nasıl Çalışır?")
    _render_equal_card_row(PROCESS_CARDS)


def _render_app_usage_guide() -> None:
    """Uygulama kullanım rehberi — Dosya Hazırlama ile aynı expander kalıbı."""
    with st.expander(
        ":material/play_circle: Uygulamayı nasıl kullanırsınız?",
        expanded=True,
    ):
        st.markdown(
            """
<div class="usage-guide-body">
<ol>
<li><strong>Adım 1 — Fatura listesi:</strong> Sol menüden Excel/CSV fatura listenizi
yükleyin veya demo senaryo seçin.</li>
<li><strong>Adım 2 — Belgeler (opsiyonel):</strong> GÇB veya diğer PDF/resim
belgeleri ikinci adımdan ekleyebilirsiniz.</li>
<li><strong>Analizi Başlat:</strong> Sol alttaki birincil butonla LangGraph ajan
zincirini çalıştırın.</li>
<li><strong>Ek bilgi (gerekirse):</strong> Sistem eksik belge veya şüpheli durumda
sorular sorar; kanıt yükleyin veya yanıtlayın.</li>
<li><strong>Rapor:</strong> KDV İade Analiz Raporu — skor, risk maddeleri ve
faktoring / ön finansman uygunluğunu inceleyin.</li>
</ol>
</div>
            """,
            unsafe_allow_html=True,
        )


def render_idle_phase() -> None:
    """App idle — yalnızca operasyonel karşılama (pitch yok)."""
    if st.button("← Ana Sayfaya Dön", icon=":material/home:", key="nav_to_landing"):
        st.session_state.page = "landing"
        st.rerun()

    st.markdown("## :material/dashboard: Analiz Merkezi")
    st.caption("Sol menüden dosya yükleyip analizi başlatın.")

    _render_how_it_works_cards()
    _render_app_usage_guide()

    st.markdown("#### Dosya Hazırlama")
    _render_upload_guide(compact=False)


def render_running_phase() -> None:
    """Analiz devam ederken bekleme ekranı."""
    with st.spinner("Yapay zeka ajanları çalışıyor, dosyalar inceleniyor..."):
        time.sleep(0.1)
    st.markdown(
        ":material/hourglass_empty: Analiz devam ediyor. "
        "Ajanların ilerleyişini sol panelden takip edebilirsiniz."
    )


def _clear_clarification_proof_cache() -> None:
    """Segment 1 başında kanıt doğrulama önbelleğini temizler."""
    for key in list(st.session_state.keys()):
        if key.startswith("proof_cache_"):
            del st.session_state[key]


def _append_proof_to_state(record: Any) -> None:
    """ProofRecord'u current_state proof_ledger'a ekler."""
    ledger = append_record(
        st.session_state.current_state,
        record,
    )
    st.session_state.current_state["proof_ledger"] = ledger
    purge_expired(st.session_state.current_state)


def _proof_cache_key(q_id: str) -> str:
    return f"proof_cache_{q_id}"


def _render_proof_document_question(q_id: str, q_text: str, field: str) -> None:
    """Belge eksikliği sorusu — yükleme + Gemini doğrulama; radyo yok."""
    expected = expected_type_for_field(field)
    if not expected:
        st.warning("Bu soru için kanıt türü tanımlı değil.")
        return

    cache_key = _proof_cache_key(q_id)
    prev = st.session_state.clarification_answers.get(q_id)

    if prev == "Hayır":
        st.markdown(f"**{q_text}**")
        st.info("Belge mevcut değil olarak işaretlendi.")
        return

    if prev == "Evet":
        st.markdown(f"**{q_text}**")
        cache = st.session_state.get(cache_key) or {}
        st.success("Belge yapay zeka tarafından doğrulandı!")
        evidence = cache.get("evidence")
        if evidence:
            st.caption(str(evidence))
        return

    st.markdown(f"**{q_text}**")
    st.caption(
        ":material/verified_user: Güvenlik için yalnızca yüklenen ve doğrulanan belge "
        "kabul edilir."
    )

    uploaded = st.file_uploader(
        "Kanıt belgesini yükleyin (PDF/PNG)",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        key=f"proof_upload_{q_id}",
    )

    col_no, _ = st.columns([1, 3])
    with col_no:
        if st.button(
            "Belgem Yok (Hayır)",
            key=f"proof_no_{q_id}",
            use_container_width=True,
        ):
            st.session_state.clarification_answers[q_id] = "Hayır"
            st.session_state.pop(cache_key, None)
            st.rerun()

    if uploaded is None:
        return

    sig = f"{uploaded.name}:{uploaded.size}"
    cache: dict[str, Any] = st.session_state.get(cache_key) or {}

    if cache.get("sig") == sig and cache.get("ok") is True:
        st.success("Belge yapay zeka tarafından doğrulandı!")
        st.session_state.clarification_answers[q_id] = "Evet"
        return

    if cache.get("sig") == sig and cache.get("ok") is False:
        st.error(
            "Yüklenen belge istenen türde değil veya doğrulanamadı. "
            "Lütfen doğru belgeyi yükleyin."
        )
        return

    proof_dir = Path(tempfile.gettempdir()) / "iadeajan_clarification_proofs"
    proof_dir.mkdir(parents=True, exist_ok=True)
    temp_path = proof_dir / f"{uuid.uuid4().hex[:12]}_{uploaded.name}"
    temp_path.write_bytes(uploaded.getvalue())

    ctx = dict(st.session_state.current_state or {})
    with st.spinner("Yapay zeka belgeyi doğruluyor..."):
        record, _entry = ingest_document_file(
            str(temp_path),
            expected_type=expected,
            state=ctx,
            session_id=ctx.get("company_id", "ui-session"),
            question_id=q_id,
            field=field,
        )
        record.question_id = q_id
        record.field = field

    if record.verification_status == "passed":
        _append_proof_to_state(record)
        cls = record.classification
        st.session_state[cache_key] = {
            "sig": sig,
            "ok": True,
            "type": cls.type if cls else expected,
            "evidence": cls.evidence if cls else "",
        }
        st.session_state.clarification_answers[q_id] = "Evet"
        st.success("Belge yapay zeka tarafından doğrulandı!")
        if cls and cls.evidence:
            st.caption(cls.evidence)
    else:
        st.session_state[cache_key] = {"sig": sig, "ok": False}
        st.session_state.clarification_answers.pop(q_id, None)
        st.error(
            "Yüklenen belge istenen türde değil veya doğrulanamadı. "
            "Lütfen doğru belgeyi yükleyin."
        )
        for chk in record.failed_checks:
            st.caption(f"• {chk.message or chk.code}")


def render_clarification_phase() -> None:
    """
    Clarification fazı — kullanıcıdan ek bilgi alır.
    Belge sorularında kanıt yükleme + AI doğrulama zorunludur.
    """
    state = st.session_state.current_state
    questions: list[dict[str, Any]] = state.get("clarification_questions") or []
    message: str = state.get("clarification_message") or (
        "Analizinizin tamamlanabilmesi için bazı bilgilere ihtiyaç duyulmaktadır."
    )

    st.markdown("## :material/forum: Ek Bilgi Gerekiyor")
    st.markdown(
        f'<div class="decision-banner decision-banner--warn">'
        f"<strong>İadeAjan</strong><br/>{message}</div>",
        unsafe_allow_html=True,
    )
    cls = state.get("classification_result") or {}
    detected_refund = cls.get("refund_type") or (state.get("company_profile") or {}).get("refund_type")
    if detected_refund:
        st.caption(
            f"İade türü faturalardan otomatik tespit edildi: **{detected_refund}** "
            "(manuel değiştirilemez)."
        )

    answers: dict[str, str] = {}

    if not questions:
        st.info("Tüm sorular yanıtlandı. Devam Et butonuna basın.")
    else:
        st.markdown("---")
        st.markdown("Lütfen aşağıdaki soruları yanıtlayın:")

        for q in questions:
            q_id: str = q.get("question_id") or f"q_{id(q)}"
            q_text: str = q.get("question_text") or "?"
            q_options: list[str] = q.get("options") or ["Evet", "Hayır", "Bilmiyorum"]
            field: str = q.get("field") or ""

            if is_proof_document_field(field):
                _render_proof_document_question(q_id, q_text, field)
                st.markdown("---")
                continue

            prev = st.session_state.clarification_answers.get(q_id)
            try:
                default_idx = q_options.index(prev) if prev in q_options else 0
            except (ValueError, TypeError):
                default_idx = 0

            col1, col2 = st.columns([3, 1])
            with col1:
                answer = st.radio(
                    label=q_text,
                    options=q_options,
                    index=default_idx,
                    key=f"clar_{q_id}",
                    horizontal=True,
                )
            with col2:
                if field:
                    st.caption(f"`{field}`")

            answers[q_id] = answer

    st.divider()

    if st.button(
        "Cevapları Gönder ve Devam Et",
        type="primary",
        use_container_width=True,
        icon=":material/send:",
    ):
        all_answers = {**st.session_state.clarification_answers, **answers}

        missing_proof: list[str] = []
        for q in questions:
            q_id = q.get("question_id") or ""
            field = q.get("field") or ""
            if not q_id or not is_proof_document_field(field):
                continue
            if q.get("required", True) and q_id not in all_answers:
                missing_proof.append(q.get("question_text") or q_id)

        if missing_proof:
            st.error(
                "Lütfen tüm belge soruları için kanıt yükleyin veya "
                "'Belgem Yok (Hayır)' seçin."
            )
            for label in missing_proof:
                st.caption(f"• {label}")
            return

        st.session_state.clarification_answers = all_answers
        with st.spinner("Cevaplar işleniyor, karar üretiliyor..."):
            _run_segment_2(all_answers)
        st.rerun()


def _render_score_bar(score: int) -> None:
    """Skoru progress bar olarak gösterir."""
    st.markdown(f'<p class="score-value">{score}/100</p>', unsafe_allow_html=True)
    st.progress(score / 100)
    st.markdown(
        '<p class="score-legend">'
        "< 60 Yüksek Risk &nbsp;|&nbsp; "
        "60–89 Orta Risk &nbsp;|&nbsp; "
        "≥ 90 Düşük Risk</p>",
        unsafe_allow_html=True,
    )


def _render_llm_badge(llm_used: bool, macro_skip_llm: bool = False) -> None:
    """LLM / makro kilit durum rozetini gösterir."""
    if macro_skip_llm:
        st.markdown(
            '<span style="background:#b45309;color:#fff;padding:3px 10px;'
            'border-radius:12px;font-size:0.78rem;font-weight:600;">'
            "🔒 Makro kilit — AI denetimi atlandı</span>",
            unsafe_allow_html=True,
        )
    elif llm_used:
        st.markdown(
            '<span style="background:#1e7e34;color:#fff;padding:3px 10px;'
            'border-radius:12px;font-size:0.78rem;font-weight:600;">'
            "🤖 AI Denetçi aktif</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<span style="background:#856404;color:#fff;padding:3px 10px;'
            'border-radius:12px;font-size:0.78rem;font-weight:600;">'
            "⚠️ AI Denetçi devre dışı — temel kurallar kullanıldı</span>",
            unsafe_allow_html=True,
        )


def _source_label(source: str) -> str:
    labels = {
        "llm": "🤖 AI",
        "hybrid": "⚙️ Hibrit",
        "python": "⚙️ Sistem",
        "fallback": "⚙️ Fallback",
    }
    return labels.get(source, source)


def _render_remediation_plan(report: dict[str, Any]) -> None:
    """Hedef skora ulaşmak için yapılacaklar ve tahmini skor projeksiyonu."""
    plan: dict[str, Any] = report.get("remediation_plan") or {}
    if not plan:
        score_breakdown = report.get("score_breakdown") or {}
        plan = score_breakdown.get("remediation_plan") or {}

    st.markdown("### :material/playlist_add_check: Hedef Skora Ulaşmak İçin Yapılacaklar")

    headline = str(plan.get("headline_tr") or "")
    if headline:
        st.markdown(
            f'<div class="decision-banner decision-banner--warn" style="margin-bottom:1rem;">'
            f"{headline}</div>",
            unsafe_allow_html=True,
        )

    steps: list[dict[str, Any]] = list(plan.get("steps") or [])
    if not steps:
        st.markdown(
            ":material/check_circle: Ek düzeltme gerekmiyor — mevcut skor korunur."
        )
    else:
        rows = []
        for step in steps:
            lock_label = "Evet" if step.get("is_mandatory_lock") else ""
            rows.append(
                {
                    "Öncelik": step.get("priority", ""),
                    "Kod": step.get("code", ""),
                    "Yapılacak iş": step.get("action", ""),
                    "+Puan": step.get("points_recoverable", 0),
                    "Finansman kilidi": lock_label,
                }
            )
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "+Puan": st.column_config.NumberColumn(format="+%d"),
            },
        )

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric("Şu anki skor", f"{plan.get('current_score', 0)}/100")
        with col_b:
            st.metric(
                "Tahmini skor (tümü giderilirse)",
                f"{plan.get('projected_score_if_all_resolved', 0)}/100",
            )
        with col_c:
            st.metric("Hedef (ön finansman)", f"≥{plan.get('target_score_finance', 90)}")

    st.caption(
        "Tahmin, ilgili belge/kanıt yüklendiğinde veya veri düzeltildiğinde "
        "maddelerin tamamen kalktığı varsayımına dayanır; kısmi düzeltmelerde skor "
        "daha düşük kalabilir."
    )


def _render_penalty_table(items_by_code: dict, mandatory_locks: list[str]) -> None:
    """Kanunname bazlı ceza tablosunu gösterir."""
    if not items_by_code:
        st.markdown(":material/check_circle: Ceza tespit edilmedi.")
        return

    rows = []
    for code, data in items_by_code.items():
        is_lock = code in mandatory_locks
        lock_icon = " 🔒" if is_lock else ""
        rows.append({
            "Kod": f"{code}{lock_icon}",
            "Açıklama": data.get("description", "")[:60],
            "Adet": data.get("count", 1),
            "Puan (-)": data.get("total_penalty", 0),
            "Kaynak": _source_label(data.get("source", "python")),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Puan (-)": st.column_config.NumberColumn(format="-%d"),
        },
    )

    if mandatory_locks:
        st.warning(
            f"🔒 **Finansman kilidi:** {', '.join(mandatory_locks)} — "
            "Bu maddeler çözülmeden dosya işleme alınamaz.",
            icon="🔒",
        )


def render_done_phase() -> None:
    """Analiz tamamlandığında nihai raporu gösterir (Path B)."""
    state = st.session_state.current_state
    report: dict[str, Any] = state.get("final_report") or {}

    if not report:
        st.error("final_report boş. Bir sorun oluştu.")
        if st.button(
            "Sıfırla",
            use_container_width=True,
            icon=":material/restart_alt:",
        ):
            _reset_session()
            st.rerun()
        return

    score: int = report.get("calculated_score", 0)
    risk_category: str = report.get("risk_category", "-")
    approval: str = report.get("approval_status", "-")
    summary: str = report.get("decision_summary", "-")
    risk_items: list[dict] = report.get("risk_items") or state.get("risk_items") or []
    missing_docs: list[dict] = report.get("missing_docs") or state.get("missing_docs") or []
    agent_logs: list[str] = state.get("agent_logs") or []
    fin: dict[str, Any] = report.get("finance_eligibility") or {}
    score_breakdown: dict[str, Any] = report.get("score_breakdown") or {}
    items_by_code: dict = score_breakdown.get("items_by_code") or {}
    llm_used: bool = bool(report.get("llm_used", score_breakdown.get("llm_used", False)))
    macro_skip_llm: bool = bool(
        report.get("macro_skip_llm", score_breakdown.get("macro_skip_llm", False))
    )
    llm_summary: str = str(score_breakdown.get("llm_summary") or "")
    mandatory_locks: list[str] = report.get("mandatory_lock_triggered") or []
    macro_invalid_codes = [
        c for c in mandatory_locks
        if c in ("DATA_INTEGRITY_FAILURE", "REFUND_LOGIC_IMPOSSIBLE")
    ]
    doc_lock_codes = [c for c in mandatory_locks if c not in macro_invalid_codes]

    # ── Başlık + LLM Badge ────────────────────────────────────
    st.markdown("# :material/analytics: KDV İade Analiz Raporu")
    head_col, badge_col = st.columns([4, 1])
    with head_col:
        st.caption(
            f"**{report.get('company_name', '')}** | "
            f"VKN: `{report.get('tax_number', '')}` | "
            f"İade Türü (otomatik tespit): `{report.get('refund_type', '')}`"
        )
    with badge_col:
        _render_llm_badge(llm_used, macro_skip_llm=macro_skip_llm)

    st.divider()

    if macro_invalid_codes:
        doc_note = ""
        if doc_lock_codes:
            doc_note = (
                f" Ayrıca belge/finansman kilidi: "
                f"<code>{', '.join(doc_lock_codes)}</code>."
            )
        st.markdown(
            '<div class="decision-banner decision-banner--danger">'
            "<strong>Dosya geçersiz (makro kilit).</strong> Bu veri seti ile KDV iadesi talebi "
            "anlamlı değildir. Gösterilen skor yalnızca ceza dökümü içindir. "
            f"Makro kodlar: <code>{', '.join(macro_invalid_codes)}</code>."
            f"{doc_note} "
            "Faturaları ve iade türünü GİB düzenine göre düzeltin.</div>",
            unsafe_allow_html=True,
        )

    macro_ga_text = ""
    for r in risk_items:
        code = str(r.get("code") or "")
        if code in ("GENERAL_ANOMALY_HIGH", "GENERAL_ANOMALY_MEDIUM"):
            meta = r.get("metadata") or {}
            if meta.get("macro_flag"):
                macro_ga_text = str(r.get("reason", ""))
                break
    if macro_ga_text:
        st.warning(
            f"**Makro anomali tespit edildi** — dosya geneline yayılan şüpheli pattern: "
            f"{macro_ga_text[:400]}",
            icon="⚠️",
        )

    # ── Metrik Kartları ───────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            label="Nihai Skor",
            value=f"{score}/100",
            delta=f"-{report.get('total_deduction', 0)} puan kesildi",
            delta_color="inverse",
            help="100 üzerinden hesaplanan nihai iade uygunluk skoru",
        )
    with col2:
        st.metric(
            label="Tespit Edilen Riskler",
            value=len(risk_items),
            delta=f"-{report.get('total_risk_penalty', 0)} puan",
            delta_color="inverse",
        )
    with col3:
        st.metric(
            label="Eksik Belgeler",
            value=len(missing_docs),
            delta=f"-{report.get('doc_penalty', 0)} puan",
            delta_color="inverse",
        )

    st.divider()

    # ── Karar Bandı ───────────────────────────────────────────
    approval_text = APPROVAL_LABELS.get(approval, approval)
    if risk_category == "Düşük Risk":
        banner_class = "decision-banner decision-banner--success"
    elif risk_category == "Orta Risk":
        banner_class = "decision-banner decision-banner--warn"
    else:
        banner_class = "decision-banner decision-banner--danger"

    st.markdown(
        f'<div class="{banner_class}">'
        f"<strong>{risk_category}</strong> — {approval_text}"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="lead-text" style="margin-top:1rem;">{summary}</p>',
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Sistem / AI notu ──────────────────────────────────────
    if llm_summary:
        if macro_skip_llm:
            st.markdown("### :material/lock: Sistem Notu (makro kilit)")
            st.warning(llm_summary, icon="🔒")
        else:
            st.markdown("### :material/smart_toy: AI Denetçi Yorumu")
            st.info(llm_summary, icon="🤖")

    # ── Finansman Paneli ──────────────────────────────────────
    st.markdown("### :material/payments: Faktoring / Ön Finansman Uygunluğu")
    finance_headline = str(report.get("finance_headline") or fin.get("headline") or "")
    if finance_headline:
        st.markdown(
            f'<div class="decision-banner decision-banner--danger">'
            f"<strong>{finance_headline}</strong></div>",
            unsafe_allow_html=True,
        )
    if fin.get("eligible"):
        amount = fin.get("estimated_amount", 0)
        st.markdown(
            f'<div class="decision-banner decision-banner--success">'
            f"<strong>Ön finansman için uygun.</strong> "
            f"Tahminen <strong>{amount:,.0f} ₺</strong> tutarında faktoring "
            f"başvurusu yapılabilir.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="decision-banner">'
            f"{fin.get('reason', 'Faktoring için uygun değil.')}</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    _render_remediation_plan(report)

    st.divider()

    # ── Skor Hesaplama Özeti ──────────────────────────────────
    st.markdown("### :material/calculate: Skor Hesaplama Dökümü")
    col_a, col_b = st.columns(2)
    with col_a:
        python_p = score_breakdown.get("python_penalty", report.get("python_penalty", 0))
        llm_p = score_breakdown.get("llm_penalty", report.get("llm_penalty", 0))
        doc_p = report.get("doc_penalty", 0)
        bonus = report.get("clarification_bonus", 0)
        st.markdown(
            f"""
| Kalem | Puan |
|---|---|
| Başlangıç skoru | +100 |
| Sistem (mevzuat) cezası | -{python_p} |
| AI Denetçi cezası | -{llm_p} |
| Belge eksikliği cezası | -{doc_p} |
| Clarification düzeltmesi | +{bonus} |
| **Nihai Skor** | **{score}** |
"""
        )
        matrix_ver = score_breakdown.get("matrix_version", "")
        if matrix_ver:
            st.caption(f"Ceza matris versiyonu: `{matrix_ver}`")
    with col_b:
        _render_score_bar(score)

    # ── Ceza Kanunnamesi Tablosu ───────────────────────────────
    st.markdown("### :material/gavel: Tespit Edilen Ceza Maddeleri")
    _render_penalty_table(items_by_code, mandatory_locks)

    # ── Detaylı İnceleme ──────────────────────────────────────
    with st.expander(":material/fact_check: Tüm Risk Maddeleri (ham liste)", expanded=False):
        if risk_items:
            for r in risk_items:
                severity = r.get("severity", "orta")
                sev_class = SEVERITY_CLASS.get(severity, "severity-orta")
                code_badge = (
                    f'<code style="font-size:0.72rem;background:#f0f0f0;padding:1px 5px;'
                    f'border-radius:4px;">{r.get("code") or ""}</code> '
                    if r.get("code") else ""
                )
                source_badge = _source_label(r.get("source", "python"))
                st.markdown(
                    f'<p class="{sev_class}">'
                    f'<span class="severity-dot"></span>'
                    f"{code_badge}"
                    f"<strong>{r.get('title', '?')}</strong> "
                    f"({source_badge}, -{abs(r.get('score_impact', 0))} puan) — "
                    f"{r.get('reason', '')}</p>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(":material/check_circle: Kritik risk tespit edilmedi.")

        st.divider()

        if missing_docs:
            st.markdown("**Eksik / Hatalı Belgeler:**")
            for d in missing_docs:
                lock_icon = "🔒 " if d.get("code") in mandatory_locks else ""
                st.markdown(
                    f":material/description: {lock_icon}**{d.get('doc_name', '?')}** "
                    f"(-{abs(d.get('score_impact', 0))} puan) — "
                    f"{d.get('reason', '')}"
                )
        else:
            st.markdown(":material/check_circle: Eksik belge tespit edilmedi.")

    # ── Sistem Logları ────────────────────────────────────────
    with st.expander(":material/memory: Sistem Logları (Ajan Zinciri)", expanded=False):
        st.caption("Ajanların birbirleriyle haberleşme zinciri:")
        if agent_logs:
            st.code("\n".join(agent_logs), language="text")
        else:
            st.info("Log bulunamadı.")

    # ── Yeni Analiz ───────────────────────────────────────────
    st.divider()
    if st.button(
        "Yeni Analiz Başlat",
        use_container_width=True,
        icon=":material/restart_alt:",
    ):
        _reset_session()
        st.rerun()


def render_blocked_phase() -> None:
    """Kanıt/retry limiti — skor üretilmedi."""
    state = st.session_state.current_state
    reason = state.get("block_reason", "PROOF_REQUIRED")
    report = state.get("final_report") or {}

    st.markdown("## :material/block: Analiz Tamamlanamadı")
    st.markdown(
        f'<div class="decision-banner decision-banner--warn">'
        f"<strong>Zorunlu belgeler doğrulanamadı</strong><br/>"
        f"Kod: <code>{reason}</code><br/>"
        f"Bu aşamada skor veya finansman ön değerlendirmesi üretilmedi."
        f"</div>",
        unsafe_allow_html=True,
    )

    warnings = state.get("process_warnings") or []
    if warnings:
        with st.expander("Süreç uyarıları"):
            for w in warnings:
                st.caption(f"• {w}")

    pending = report.get("pending_questions") or []
    questions = state.get("clarification_questions") or []
    if questions:
        st.markdown("**Bekleyen sorular**")
        for q in questions:
            st.caption(f"• {q.get('question_text', q.get('question_id'))}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yeniden Dene", type="primary", use_container_width=True):
            st.session_state.phase = "clarification"
            state["analysis_status"] = "clarification_waiting"
            st.rerun()
    with col2:
        if st.button("Analizi Sonlandır", use_container_width=True):
            state["analysis_status"] = "failed"
            state["failure_reason"] = "USER_CANCELLED"
            _update_phase()
            st.rerun()


def render_error_phase() -> None:
    """Hata ekranı."""
    state = st.session_state.current_state
    st.markdown("## :material/error: Analiz Hatası")
    err = state.get("error_state", "Bilinmeyen hata")
    st.markdown(
        f'<div class="decision-banner decision-banner--danger">'
        f"<code style='color:#FAFAFA;'>{err}</code></div>",
        unsafe_allow_html=True,
    )

    if st.button(
        "Tekrar Dene",
        use_container_width=True,
        icon=":material/restart_alt:",
    ):
        _reset_session()
        st.rerun()


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────


def main() -> None:
    """
    Uygulamanın ana giriş noktası.
    page: landing → login → app; app içinde phase automata.
    """
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    _configure_streamlit_logo()
    _init_session()

    page = st.session_state.page

    if page == "landing":
        render_landing_page()
    elif page == "login":
        render_login_page()
    elif page == "signup":
        render_signup_page()
    elif page == "app":
        if not st.session_state.get("authenticated"):
            _set_sidebar_visible(False)
            render_signup_page()
            return

        _set_sidebar_visible(True)
        _ensure_graph()
        render_sidebar()

        phase = st.session_state.phase

        if phase == "idle":
            render_idle_phase()
        elif phase == "running":
            render_running_phase()
        elif phase == "clarification":
            render_clarification_phase()
        elif phase == "blocked":
            render_blocked_phase()
        elif phase == "done":
            render_done_phase()
        elif phase == "error":
            render_error_phase()
        else:
            st.error(f"Bilinmeyen phase: {phase!r}")
    else:
        st.error(f"Bilinmeyen sayfa: {page!r}")


if __name__ == "__main__":
    main()
