# İadeAjan — Çalışan Algoritma ve Ceza Mahkemesi (v3.2-macro)

> **Versiyon:** `v3.2-macro` (`decision_agent.MATRIX_VERSION`)  
> **Tek puan kaynağı:** `app/schemas/penalty_codes.py` → `PENALTY_MATRIX`  
> **Mimari adı:** Path B — *LLM kanunname kodu seçer, Python puan keser*

---

## 1. Özet: Ne yaptık?

Eski yaklaşımda (Path A) LLM veya sabit severity haritaları skoru dağıtıyordu; tutarsızlık ve “çifte ceza” riski vardı.

**Path B + v3.2 makro katmanı** ile:

| Rol | Kim yapar? |
|-----|------------|
| **Kod seçimi (risk)** | Python mevzuat kuralları + (isteğe bağlı) Gemini LLM, yalnızca kanundaki kodlardan |
| **Puan kesimi** | Her zaman `compute_code_penalty(code, count)` — matristen |
| **Belge eksikliği** | Python → `missing_docs` listesi, aynı matris |
| **Dosya geçersiz mi?** | Python makro kuralları → LLM ve DQ fallback **bilinçli atlanır** |
| **Nihai skor** | `DecisionAgent`: 100 − risk cezaları − belge cezaları + clarification bonusu |

Başka hiçbir dosyada sabit “−15 puan” gibi değer **olmamalı**; hepsi `penalty_codes.py` içinde.

---

## 2. Ajan zinciri (LangGraph)

```
                    ┌─────────────────────────────────────────┐
                    │ START                                   │
                    │  • Faturalar yüklüyse → Analyzer (atla) │
                    │  • Değilse → Collector                  │
                    └─────────────────┬───────────────────────┘
                                      ▼
                    ┌─────────────────────────────────────────┐
                    │ CollectorAgent                          │
                    │  Excel/CSV upload veya mock senaryo     │
                    │  → normalize faturalar, tedarikçiler,   │
                    │    belgeler, iade türü, şirket profili  │
                    └─────────────────┬───────────────────────┘
                                      ▼
                    ┌─────────────────────────────────────────┐
                    │ AnalyzerAgent                           │
                    │  1) Python mevzuat                      │
                    │  2) Makro bütünlük (v3.2)               │
                    │  3) LLM denetçi VEYA fallback DQ        │
                    └─────────┬───────────────────┬───────────┘
                              │                   │
              clarification_needed=True           False
                              ▼                   ▼
                    ┌──────────────────┐   ┌──────────────────┐
                    │ Clarification    │   │ DecisionAgent  │
                    │ (kullanıcı soru) │   │ skor + rapor   │
                    └────────┬─────────┘   └────────┬─────────┘
                             │ cevaplar tam          │
                             └──────► Analyzer (2. tur) ─────┘
```

### Streamlit (UI) iki segment

1. **Segment 1:** Graph `interrupt_before=Clarification` ile durur → kullanıcıya sorular.
2. **Segment 2:** Cevaplar state’e enjekte edilir; `normalized_invoices` dolu olduğu için **Collector tekrar çalışmaz**; doğrudan Analyzer → Decision.

`agent_logs` state alanı `operator.add` ile birleşir; her ajan yalnızca **yeni** log satırları döndürmelidir.

### Belge envanteri (`document_inventory`) kaynakları

| Kaynak | Ne zaman | Analyzer etkisi |
|--------|----------|------------------|
| **Senaryo / canonical JSON** | `documents[]` payload | Normalize edilir (`status`, `type`) |
| **BelgeAnlama (Gemini)** | Streamlit’te PDF/resim yükleme; `Classifier.confidence ≥ 0.8` ve tip `gumruk_beyannamesi` / `ymm_tasdik_raporu` | `status=mevcut` ile eklenir |
| **Clarification** | GÇB / 2 No sorularına olumlu cevap | İlgili `type` için `mevcut` kaydı upsert |
| **Clarification kanıt yükleme** | Segment 1.5: belge sorusunda PDF/resim + `classify_document`; `confidence ≥ 0.8` ve beklenen `type` eşleşmesi | Yalnızca doğrulama sonrası `"Evet"` cevabı ve envanter upsert (radyo ile sahte onay yok) |

Excel‑only yüklemede `documents` boş kalır; GÇB/YMM için ya PDF/resim eklenmeli ya da clarification kanıtı ile beyan güncellenmelidir.

---

## 3. Skor formülü (DecisionAgent)

```
Başlangıç        = 100

risk_cezası      = Σ |risk_items[i].score_impact|
belge_cezası     = Σ |missing_docs[j].score_impact|
toplam_kesinti   = risk_cezası + belge_cezası

ham_skor         = 100 − toplam_kesinti
nihai_skor       = clamp(ham_skor + clarification_bonus, 0, 100)
```

### Risk kategorisi ve onay

| Nihai skor | Kategori | `approval_status` |
|------------|----------|-------------------|
| ≥ 90 | Düşük Risk | `approved` |
| 60–89 | Orta Risk | `conditional_approval` |
| < 60 | Yüksek Risk | `rejected` |

### Clarification bonusu (puan **geri verilir**, belge yüklenene kadar “pending”)

| Alan (`field`) | Cevap | Bonus |
|----------------|-------|-------|
| `has_customs_declarations` | Evet | +6 |
| | Bir kısmı mevcut | +3 |
| `has_2no_declaration` | Evet | +6 |
| | Bir kısmı için verdim | +3 |
| `refund_type` | ihracat / tevkifat / indirimli_oran | +2 |

**Önemli:** Bonus, eksik belge cezasını silmez; yalnızca skoru hafifçe yukarı çeker. “Evet” cevapları `pending_verification` işaretlenir; skor ≥90 olsa bile onay `conditional_approval` olabilir.

### UI dökümü (kaynak ayrımı)

- **Sistem (mevzuat) cezası:** `source ∈ {python, fallback}`
- **AI Denetçi cezası:** `source ∈ {llm, hybrid}`

Path B’de tavan/cap yok; toplam = matrisin doğrudan toplamı.

---

## 4. Puan kesme formülü (kanunname)

Her kod için:

```python
kesinti = min(count × per_unit_penalty, max_penalty)   # negatif döner: −kesinti
```

- `count` 1–100 arası sınırlı (LLM halüsinasyon koruması).
- Aynı `(code, dedupe_suffix)` çifti `seen_risk_keys` ile **iki kez eklenmez**.
- Eksik belgeler `missing_docs` üzerinden ayrı toplanır (çoğu `is_missing_doc=True`).

---

## 5. Ceza kanunnamesi — tam tablo

### 5.1 Zorunlu belgeler (Python, çoğu finansman kilidi)

| Kod | Kesinti | Tavan | Kilit? | Ne zaman? |
|-----|---------|-------|--------|-----------|
| `GCB_MISSING` | 35 | 35 | Evet | İhracat; GÇB envanterde yok/eksik |
| `NO2_DECLARATION_MISSING` | 35 | 35 | Evet | Tevkifat; 2 No’lu beyanname yok |
| `NO2_DECLARATION_UNPAID` | 15 | 15 | Hayır | 2 No var ama ödenmemiş |
| `YMM_REPORT_MISSING` | 35 | 35 | Evet | Tahmini iade > 50.000 TL, YMM raporu yok |

### 5.2 Tedarikçi (Python)

| Kod | Kesinti | Kilit? | Ne zaman? |
|-----|---------|--------|-----------|
| `SUPPLIER_SMIYB` | 25 | Hayır | `risk_level=kritik`, kara liste değil |
| `SUPPLIER_HIGH_RISK` | 10 | Hayır | `risk_level=yüksek` |
| `SUPPLIER_BLACKLIST_LOCK` | 25 | **Evet** | Kritik + kara liste |

### 5.3 Veri kalitesi (LLM veya fallback; makroda purge edilebilir)

| Kod | Birim | Tavan | Kaynak |
|-----|-------|-------|--------|
| `DQ_INVALID_DATE` | 5 | 15 | llm / fallback |
| `DQ_ZERO_AMOUNT` | 5 | 10 | llm / fallback |
| `DQ_INVALID_VKN` | 3 | 9 | llm / fallback |

### 5.4 Tutar / tutarsızlık (LLM veya hybrid)

| Kod | Kesinti | Kaynak | Not |
|-----|---------|--------|-----|
| `AMOUNT_MISMATCH_GCB` | 15 | hybrid | Fatura ↔ GÇB > %5 fark (Python ön tarama + kod) |
| `KDV_CALC_ERROR` | 15 | llm | Matrah × oran ≠ KDV |
| `DUPLICATE_INVOICE` | 10 | llm | Tekrarlayan fatura no |
| `CHRONOLOGY_ERROR` | 8 | llm | Tarih sırası bozuk |
| `KDV_RATE_MISMATCH_EXPORT` | 10 | llm | İhracatta KDV > 0 |
| `SELF_INVOICE` | 25 | llm | Alıcı = satıcı VKN |
| `FUTURE_DATED_INVOICE` | 15 | llm | Gelecek tarih |

### 5.5 Dönem / zamanaşımı / tevkifat (Python)

| Kod | Kesinti | Ne zaman? |
|-----|---------|-----------|
| `PERIOD_OUT_OF_RANGE` | 10 (count ile) | Fatura tarihi `analysis_period` dışında |
| `DEADLINE_CRITICAL` | 3 | Zamanaşımına < 180 gün |
| `DEADLINE_WARNING_IO` | 5 | İndirimli oran, 180–365 gün |
| `TEVKIFAT_MISSING_DECL` | 5/adet | Tevkifat faturasında beyan yok |

### 5.6 LLM genel anomali (kanunname dışı şüphe)

| Kod | Kesinti | Ne zaman? |
|-----|---------|-----------|
| `GENERAL_ANOMALY_HIGH` | 20 | Ciddi, kodu olmayan pattern (`macro_flag` olabilir) |
| `GENERAL_ANOMALY_MEDIUM` | 10 | Hafif şüphe |

### 5.7 Makro bütünlük (v3.2 — dosya geçersiz)

| Kod | Kesinti | Kilit? | Python tetik |
|-----|---------|--------|----------------|
| `REFUND_LOGIC_IMPOSSIBLE` | 50 | **Evet** | ≥%75 fatura tutarı ≤0 **ve** iade türü ∈ {ihracat, tevkifat, indirimli_oran} |
| `DATA_INTEGRITY_FAILURE` | 40 | **Evet** | Toplam fatura tutarı ≤ 0 (üstteki kolu tetiklenmediyse) |

**Makro sonrası:** `DQ_ZERO_AMOUNT`, `DQ_INVALID_DATE` risk listesinden **silinir** (çifte ceza yok). LLM çağrısı ve fallback DQ **çalışmaz**.

LLM, semantik `REFUND_LOGIC_IMPOSSIBLE` (ör. ihracat beyanı + tamamı yurtiçi VKN profili) için prompt’ta yönlendirilir; Python %75 kuralı önceliklidir.

---

## 6. Denetim katmanları (sıra önemli)

### Katman 0 — CollectorAgent

1. Upload: `upload_loader` + `ai_converter` (heuristic/LLM) → canonical JSON.
2. Mock: `scenario_loader` (geliştirme senaryoları).
3. Normalize: fatura, tedarikçi, belge envanteri.
4. İade türü: fatura tiplerinden heuristic (`ihracat` / `tevkifat` / …).
5. Upload’ta boş profil → tedarikçi adı, VKN, dosya adı, tarih aralığı, tutar özeti.

### Katman 1 — Python mevzuat (her zaman, API gerekmez)

Sıra (`analyzer_node` içinde):

1. **İhracat:** `GCB_MISSING`, `AMOUNT_MISMATCH_GCB` (GÇB tutarı varsa).
2. **Tevkifat:** `NO2_*`, `TEVKIFAT_MISSING_DECL`.
3. **Tedarikçi riskleri** (tüm türler).
4. **YMM** (tutar eşiği 50.000 TL).
5. **Zamanaşımı** (`analysis_period.end` gerekir).
6. **Dönem dışı faturalar.**

Bu katman `missing_docs` ve `risk_items` üretir; skorlar matristen gelir.

### Katman 2 — Makro bütünlük (v3.2)

`_apply_macro_integrity_rules` → `True` ise:

- `macro_skip_llm = True`
- LLM atlanır; özet: *"Makro kilit tetiklendi — AI denetimi atlandı."*
- Fallback DQ atlanır.

### Katman 3a — LLM denetçi (Gemini)

**Koşullar:** Makro yok, `LLM_ANOMALY_ENABLED=true`, API key var, paket yüklü.

- Structured JSON: `LLMAnalysisResult` (`summary`, `penalties[]`).
- Yalnızca `LLM_ALLOWED_CODES` (source `llm` veya `hybrid`).
- Python-only kodları raporlaması **yasak** (prompt’ta listelenir).
- Geçersiz kod → filtre, log: `[LLM Filtre]`.
- Puan yine `add_penalty` → `compute_code_penalty`.

**Ortam değişkenleri:** `GOOGLE_API_KEY` veya `GEMINI_API_KEY`.

### Katman 3b — Fallback DQ (yalnızca LLM başarısız ve makro yok)

Deterministik:

- Geçersiz tarih → `DQ_INVALID_DATE`
- Tutar ≤ 0 → `DQ_ZERO_AMOUNT`
- VKN sayısal değil → `DQ_INVALID_VKN`

`source="fallback"` → UI’da “Sistem” cezasına yazılır.

### Katman 4 — Clarification soruları

Eksik GÇB / 2 No / belirsiz iade türü → en fazla 3 soru. Cevaplar sonraki Analyzer turunda `refund_type` vb. günceller.

### Katman 5 — DecisionAgent

Toplama, kategori, finansman, `final_report`, `items_by_code` (UI ceza tablosu).

---

## 7. Nelere puan kesilmez / sistem yapmaz

| Konu | Durum |
|------|--------|
| Kanunda kodu olmayan bulgu | LLM `GENERAL_ANOMALY_*` kullanmalı; yoksa raporlanmaz |
| Python-only kodlar | LLM **kesinlikle** üretmemeli (GÇB, YMM, tedarikçi SMİYB vb.) |
| Makro kilitli dosyada satır bazlı DQ | **Kesilmez** (purge + skip) |
| Clarification “Hayır” | Bonus 0; belge cezası kalır |
| Finansman | Skor ≥90 ve tutar ≥50k yetmez; `MANDATORY_LOCK_CODES` varsa **Hayır** |
| GİB’e otomatik başvuru | Yok — yalnızca analiz/öneri |
| Belge OCR doğrulama | Upload tabular; gerçek PDF doğrulama sınırlı |

### Finansman kilidi kodları (`is_mandatory_lock=True`)

```
GCB_MISSING
NO2_DECLARATION_MISSING
YMM_REPORT_MISSING
SUPPLIER_BLACKLIST_LOCK
DATA_INTEGRITY_FAILURE
REFUND_LOGIC_IMPOSSIBLE
```

Bu kodlardan biri risk veya eksik belgede varsa → faktoring **Hayır**, GİB işleme almaz mesajı.

---

## 8. Örnek: T07 (negatif ihracat)

**Veri:** 4 ihracat faturası, tutarların tamamı ≤ 0.

| Adım | Sonuç |
|------|--------|
| Mevzuat | `GCB_MISSING` → −35 (belge) |
| Makro | %100 negatif/sıfır → `REFUND_LOGIC_IMPOSSIBLE` → −50 |
| LLM | **Atlandı** (`macro_skip_llm=True`) |
| DQ fallback | **Atlandı** |
| Skor | 100 − 50 − 35 = **15** |
| Kategori | Yüksek Risk (<60) |
| Finansman | Kilit: `REFUND_LOGIC_IMPOSSIBLE`, `GCB_MISSING` |

---

## 9. Örnek: T01 (temiz ihracat, yalnızca GÇB eksik)

| Adım | Sonuç |
|------|--------|
| Makro | Tetiklenmez (pozitif tutarlar) |
| Mevzuat | `GCB_MISSING` −35 |
| LLM | Çalışabilir (ek anomali yoksa 0) |
| Skor | ~**65** (100 − 35) |
| Kategori | Orta Risk |

---

## 10. Shadow learning

Makro ve LLM çıktıları `logs/shadow_learning.jsonl` dosyasına append-only yazılır (kalibrasyon / geriye dönük inceleme). Üretim skorunu değiştirmez.

---

## 11. Dosya haritası

| Dosya | Görev |
|-------|--------|
| `app/schemas/penalty_codes.py` | Ceza enum + matris + `compute_code_penalty` |
| `app/agents/analyzer_agent.py` | Mevzuat + makro + LLM + fallback |
| `app/agents/decision_agent.py` | Skor, kategori, finansman, rapor |
| `app/agents/collector_agent.py` | Veri toplama / normalize |
| `app/agents/clarification_agent.py` | Kullanıcı soruları |
| `app/graph/workflow.py` | LangGraph yönlendirme |
| `main.py` | Streamlit UI, segment 1/2 |
| `tests/v3_accuracy_test.py` | Senaryo regresyon testleri |
| `app/schemas/verification.py` | ProofRecord, retention, redacted view |
| `app/services/proof_ledger.py` | Kanıt defteri, TTL, audit |
| `app/services/verification/` | ingest, cross_validate, envanter güveni |

---

## 12. Proof ledger retention (özet)

- Kanıt kayıtları oturum süresince state içinde tutulur; production’da disk persist yok.
- Detaylı KVKK tablosu: `docs/GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md` §15.
- Clarification belge cevapları sunucuda `proof_ledger` ile doğrulanır (`REQUIRE_VERIFIED_PROOF=true`).

---

## 13. Veri kalitesi — VKN (mevcut vs planlanan)

| Davranış | v3.2-macro (şimdi) | v3.3+ (plan, flag) |
|----------|-------------------|-------------------|
| Format (10 hane) | `DQ_INVALID_VKN` ceza (LLM/fallback) | Aynı |
| Mod-11 checksum | **Yalnızca uyarı** (`VKN_CHECKSUM_WARN`) | Opsiyonel `DQ_INVALID_VKN_CHECKSUM` (taslak 3 puan/fatura, max 9) |
| Aktivasyon | — | `ENABLE_VKN_CHECKSUM_PENALTY=true` |

Path B skor formülü değişmez; checksum cezası yalnızca yeni `risk_items` kodu olarak eklenir (mandatory lock değil).

---

## 14. analysis_status (ürün durumları)

| Status | Skor / Decision | UI phase |
|--------|-----------------|----------|
| `completed` | Tam rapor + skor | done |
| `failed` | Skor yok | error |
| `clarification_blocked` | Skor yok (önizleme riskleri kalabilir) | blocked |
| `clarification_waiting` | — | clarification |
| `running` / `pending` | — | running / idle |

`retry_count >= max_retries` ile kanıt tamamlanamazsa → `clarification_blocked` + `PROOF_REQUIRED` (Decision atlanır).

---

## 15. Tasarım ilkeleri (ceza mahkemesi)

1. **Tek kaynak:** Puan sadece `PENALTY_MATRIX`’ten.
2. **LLM kanun seçici, hakim Python:** Model “−50 puan” yazmaz; kod + count yazar.
3. **Makro önce:** Dosya ölüyse detay AI’a gitme.
4. **Çifte ceza yok:** Makro ↔ DQ_ZERO çakışması purge ile giderilir.
5. **Deterministik çekirdek:** Belge ve mevzuat API’siz çalışır.
6. **Bounded AI:** LLM kod listesi ve count tavanı ile sınırlı; kanunname dışı → GA kodları.

---

*Son güncelleme: v3.2-macro — makro kilit, Path B ceza kanunnamesi, Streamlit segment akışı.*
