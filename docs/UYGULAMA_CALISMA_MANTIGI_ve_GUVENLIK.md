# İadeAjan — Uygulama Çalışma Mantığı ve Güvenlik Durumu

**Tarih:** 2026-05-18  
**Versiyon:** v3.2-macro + Zero Trust / Proof Ledger katmanı  
**Hedef kitle:** Geliştirici, ürün, denetim — sistemin bugün nasıl çalıştığını uçtan uca anlamak için.

İlgili dokümanlar:

- [`IADEAJAN_ALGORITMA_v3.2.md`](IADEAJAN_ALGORITMA_v3.2.md) — ceza mahkemesi ve skor formülü
- [`GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md`](GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md) — risk envanteri (ID’li)
- [`KULLANICI_YUKLEME_REHBERI.md`](KULLANICI_YUKLEME_REHBERI.md) — Excel/CSV yükleme kuralları

---

## 1. Uygulama nedir?

**İadeAjan**, KDV iade taleplerini otomatik ön-değerlendiren bir **çok ajanlı (multi-agent) analiz sistemidir**. Kullanıcı (veya entegrasyon) fatura listesi, belgeler ve şirket bilgisi yükler; sistem:

1. Veriyi normalize eder,
2. Mevzuat kuralları ve (isteğe bağlı) yapay zekâ ile risk/eksik belge tespit eder,
3. Eksik veya belirsiz noktalar için kullanıcıya soru sorar,
4. **0–100 arası skor**, risk kategorisi, onay durumu ve **finansman ön uygunluğu** üretir.

**Teknik yığın:** Python, LangGraph (durum makineli iş akışı), Streamlit (UI), Google Gemini (sınıflandırma / LLM denetçi), Pydantic şemalar.

**Temel ürün vaadi (hedef):** “Dosyada ne yazıyor?” değil, mümkün olduğunca **“belge gerçekten var mı, doğru türde mi, bu şirkete mi ait?”** — bu hedefe doğru gidiliyor; henüz tam resmi kayıt doğrulaması yok.

---

## 2. Mimari özeti

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Streamlit UI (main.py)                          │
│  idle → running → clarification | blocked → done | error                  │
│  Segment 1: graph stream (Collector…Clarification’da dur)                 │
│  Segment 2: cevaplar + proof_ledger → graph devam                         │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    LangGraph (app/graph/workflow.py)                      │
│                                                                           │
│   START ──► CollectorAgent ──► AnalyzerAgent ──┬──► ClarificationAgent    │
│         (upload varsa Analyzer’a atla)        │         ▲  │              │
│                                               │         │  └──► (döngü) │
│                                               └──► DecisionAgent ──► END  │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
   penalty_codes.py          verification/              document_inventory
   (tek puan kaynağı)        proof_ledger               upload_loader
```

### Paylaşımlı state (`IadeAjanState`)

Tüm ajanlar aynı sözlük üzerinde çalışır. Önemli alan grupları:

| Grup | Alanlar | Açıklama |
|------|---------|----------|
| Giriş | `scenario_id`, `uploaded_files`, `company_name`, `tax_number`, `date_range` | Senaryo veya gerçek upload |
| Veri | `normalized_invoices`, `normalized_suppliers`, `document_inventory`, `company_profile` | Collector çıktısı |
| Analiz | `risk_items`, `missing_docs`, `process_warnings`, `classification_result` | Analyzer çıktısı |
| Clarification | `clarification_needed`, `clarification_questions`, `clarification_answers` | Kullanıcı döngüsü |
| **Doğrulama (yeni)** | `proof_ledger`, `verification_summary`, `block_reason`, `failure_reason` | Kanıt defteri ve özet |
| Sonuç | `final_report`, `final_score`, `analysis_status` | Decision veya hata/blocked |
| İzleme | `agent_logs` (append-only), `retry_count`, `error_state` | Log ve kontrol |

### `analysis_status` durum makinesi (ürün)

| Durum | Anlam | UI `phase` | Skor üretilir mi? |
|-------|--------|------------|------------------|
| `pending` | Henüz başlamadı | `idle` | Hayır |
| `running` | Pipeline aktif | `running` | Hayır |
| `clarification_waiting` | Kullanıcı cevabı bekleniyor | `clarification` | Hayır |
| `clarification_blocked` | Kanıt/turlar tamamlanamadı | `blocked` | **Hayır** |
| `failed` | Kurtarılamaz hata / upload | `error` | **Hayır** |
| `completed` | Decision bitti | `done` | **Evet** |

---

## 3. Ajanlar — tek tek ne yapıyor?

### 3.1 CollectorAgent

**Girdi:** `scenario_id` (mock) ve/veya `uploaded_files` (Excel, CSV, JSON, PDF).

**İş:**

1. Tabular dosyayı okur → `ai_converter` ile kanonik fatura/tedarikçi listesine çevirir (heuristic veya LLM, ilk N satır).
2. JSON canonical yüklemede `documents[]` alanını **`upload_loader._normalize_canonical`** ile işler — doğrulanmamış `mevcut` kayıtlar **`eksik`** yapılır (Zero Trust).
3. PDF/görsel belgeler için **`ingest_document_file`** pipeline’ı çalışır (sınıflandır → çıkar → çapraz doğrula → envanter).
4. Faturalardan **iade türü** sezgisi (`ihracat` / `tevkifat` / `belirsiz`).
5. `document_inventory`, `company_profile`, `date_range` üretir.

**Çıkış:** `analysis_status=running` (başarılı) veya `failed` + `failure_reason` (upload hatası, `ALLOW_MOCK_FALLBACK` kapalıyken mock’a düşmez).

**Dosya:** `app/agents/collector_agent.py`

---

### 3.2 AnalyzerAgent

**İş sırası (Path B + v3.2 makro):**

1. **Python mevzuat kuralları** — ihracat GÇB, 2 No, tevkifat, tedarikçi riski, dönem, zamanaşımı vb.
2. **Makro bütünlük** — dosya anlamsızsa LLM atlanır (`DATA_INTEGRITY_FAILURE`, `REFUND_LOGIC_IMPOSSIBLE`).
3. **LLM denetçi** veya **Python DQ fallback** — yalnızca `PENALTY_MATRIX`’teki kodlar.

**Envanter sayımı (önemli değişiklik):** `_inventory_entry_present()` artık `STRICT_INVENTORY_TRUST=true` iken yalnızca `source=verified` veya `source=scenario` (ve `verification_status=passed`) kayıtları “mevcut” sayar. Rastgele `mevcut` veya `llm_classifier` tek başına ihracat kilidini kaldırmaz.

**Clarification:** Eksik belge / belirsiz iade türü için soru listesi üretir.

**Retry / block (önemli değişiklik):** `retry_count >= max_retries` (varsayılan 5) iken artık sorular **kapatılmıyor**. Bunun yerine `analysis_status=clarification_blocked`, `block_reason=PROOF_REQUIRED` — Decision **çağrılmaz**.

**Dosya:** `app/agents/analyzer_agent.py`

---

### 3.3 ClarificationAgent

**İki mod:**

| Mod | Koşul | Davranış |
|-----|--------|----------|
| Üretim | `clarification_answers` boş | Mesaj üretir, `clarification_waiting` |
| İşleme | Cevaplar dolu | Zorunlu soruları kontrol eder |

**İşleme modunda (yeni güvenlik):**

1. `validate_clarification_answers()` — belge sorularında olumlu cevap için `proof_ledger`’da `passed` kayıt zorunlu (`REQUIRE_VERIFIED_PROOF=true`).
2. Başarısızsa: `clarification_waiting` kalır, `process_warnings`’a kanıt hatası yazılır.
3. Başarılıysa: `merge_inventory_from_questions()` — yalnızca **verified** kanıtlı belgeler envantere `mevcut` olarak girer.
4. `refund_type` cevabı ile Excel profili **çapraz kontrol** uyarısı (otomatik override yok).

**Dosya:** `app/agents/clarification_agent.py`

---

### 3.4 DecisionAgent

**Yalnızca** `analysis_status` uygun olduğunda tam skor üretir (`completed` yolu).

`failed` veya `clarification_blocked` iken **erken çıkış:** `final_report` özet (skor yok veya `calculated_score=null`), finansman `eligible=false`.

**Skor formülü (değişmedi):**

```
nihai_skor = clamp(100 − Σ|risk cezaları| − Σ|belge cezaları| + clarification_bonus, 0, 100)
```

**Clarification bonusu:** GÇB/2 No “Evet” için +6/+3 — ancak `proof_ledger`’da ilgili alan `passed` ise `pending_verification` **üretilmez** (yeni hizalama).

**Finansman kilidi:** `MANDATORY_LOCK_CODES` içindeki kodlar (ör. `GCB_MISSING`) varsa finansman **kesinlikle kapalı** — skor yüksek olsa bile.

**Dosya:** `app/agents/decision_agent.py`

---

## 4. Doğrulama katmanı (Zero Trust) — yeni eklenenler

Bu katman, güvenlik planı (PR-SEC-01 … PR-SEC-10) ile eklendi. **Skor formülü ve `PENALTY_MATRIX` puanları değiştirilmedi**; verinin sisteme nasıl girdiği sıkılaştırıldı.

### 4.1 Bileşenler

| Bileşen | Dosya | Görev |
|---------|-------|--------|
| Şemalar | `app/schemas/verification.py` | `ProofRecord`, `RetentionPolicy`, `DocumentExtraction`, `VerificationResult` |
| Kanıt defteri | `app/services/proof_ledger.py` | TTL, `validate_clarification_answers`, audit JSONL |
| Birleşik pipeline | `app/services/verification/document_verification.py` | `ingest_document_file()` |
| Çapraz doğrulama | `app/services/verification/cross_validate.py` | VKN sahiplik, dönem, checksum uyarısı |
| Çıkarma | `app/services/verification/document_extraction.py` | PDF alanları (LLM veya mock) |
| Envanter politikası | `app/services/verification/inventory_trust_policy.py` | Hangi kaynak “sayılır” |
| Güvenlik yardımcı | `app/core/security.py` | API anahtarı, rate limit, oturum temizliği |

### 4.2 `ingest_document_file` akışı

```
PDF yükleme
    → classify_document (tip + confidence)
    → extract_document_fields (VKN, tarih, tutar, beyan no…)
    → cross_validate_document (şirket VKN, dönem, checksum uyarısı)
    → verification_status: passed | failed
    → (passed ise) envanter entry: source=verified
```

**Kanıt alanları (clarification):** `has_customs_declarations`, `has_ymm_contract`, `has_2no_declaration` → beklenen belge tipleri eşlemesi `clarification_proof.py` içinde.

### 4.3 Proof ledger

Her yüklenen kanıt için state’te bir `ProofRecord`:

- `file_sha256`, `question_id`, `verification_status`, `expires_at`
- Ham PDF state’te **tutulmaz**; geçici dosya + hash
- Log için `ProofRecordRedactedView` (VKN maskeli)

**KVKK / saklama:** Ortama göre TTL — ayrıntı `GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md` §15.

### 4.4 Streamlit entegrasyonu

- Clarification belge sorusu: `ingest_document_file` + `proof_ledger` güncelleme
- Segment 2: `proof_ledger` resume state’e kopyalanır
- Yeni ekran: `phase=blocked` — “Analiz tamamlanamadı”, yeniden dene / sonlandır

---

## 5. Skor ve ceza sistemi (Path B — değişmedi)

Tek kaynak: **`app/schemas/penalty_codes.py` → `PENALTY_MATRIX`**

Örnek zorunlu belge cezaları (finansman kilidi):

| Kod | Puan (max) | Mandatory lock |
|-----|------------|----------------|
| `GCB_MISSING` | 35 | Evet |
| `NO2_DECLARATION_MISSING` | 35 | Evet |
| `YMM_REPORT_MISSING` | … | Evet |

**Clarification bonusu** cezayı silmez; skoru hafif yukarı çeker.

**Risk kategorisi:** ≥90 Düşük, 60–89 Orta, &lt;60 Yüksek.

---

## 6. Önce vs sonra — ne değişti, daha mı iyi?

### 6.1 Önceki durum (kısa)

| Alan | Eski davranış | Risk |
|------|----------------|------|
| Clarification GÇB | Radyo “Evet” yeterli | Sahte onay |
| Envanter | `clarification` kaynağı = mevcut sayılırdı | State/API bypass |
| Canonical JSON | `documents: [{status: mevcut}]` | Clarification atlanır |
| Retry limiti | 5 tur sonra sorular kapanır, Decision çalışır | Kanıtsız skor |
| Upload fail | Sessiz mock senaryo | Yanlış veri analizi |
| Belge | Yalnızca tip + confidence | Başka firmanın PDF’i |
| Collector PDF | Sadece sınıflandırıcı | Metadata yok |

### 6.2 Şimdiki durum (iyileşmeler)

| ID (envanter) | Konu | Durum |
|---------------|------|--------|
| K-01 | Canonical sahte `mevcut` | **Kısmen çözüldü** — `normalize_canonical_document` → `eksik` |
| K-03 | Segment 2 cevap manipülasyonu | **Kısmen çözüldü** — `validate_clarification_answers` + `proof_ledger` |
| K-04 | Mock fallback | **Kısmen çözüldü** — `ALLOW_MOCK_FALLBACK` kapalıyken `failed` |
| C-03 | Retry bypass | **Çözüldü** — `clarification_blocked`, skor yok |
| E-01 | 2 No envanter | **Kısmen çözüldü** — classifier + ingest listesine eklendi |
| B-01 | VKN sahiplik | **Kısmen çözüldü** — `OWNERSHIP_VKN_MATCH` (cross_validate) |
| B-02 | Dönem | **Kısmen çözüldü** — `PERIOD_MISMATCH` (tolerans günü env ile) |
| E-02 | Clarification metadata | **Kısmen çözüldü** — verified merge extraction alanları yazar |
| — | Ürün netliği | **İyileşti** — `failed` / `blocked` / `error` ayrımı |
| — | Test | **İyileşti** — unit + attack + **E2E** (`test_e2e_verification_flow.py`) |

### 6.3 Genel değerlendirme: Daha mı iyi?

**Evet, güvenlik ve ürün tutarlılığı açısından belirgin şekilde daha iyi:**

- “Tek tıkla Evet” ile GÇB kapatma **prod’da kapatıldı** (kanıt zorunlu).
- Arka kapı JSON ve sahte resume **testlerle kanıtlandı**, birçok yol **kodda kapatıldı**.
- Kullanıcı hata durumlarını (**blocked**, **failed**) görebiliyor; sessiz yanlış skor riski azaldı.

**Ama:** Sistem hâlâ **tam otomatik ihracat doğrulama motoru değil** — aşağıdaki açık konular geçerli.

---

## 7. Hâlâ açık olan sıkıntılar ve sınırlar

Ayrıntılı ID listesi: [`GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md`](GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md)

### 7.1 Kritik / yüksek (henüz tam çözülmedi)

| Konu | Açıklama | ID |
|------|----------|-----|
| Resmi kayıt yok | GİB/gümrük API ile beyanın gerçekten var olduğu doğrulanmıyor | B-04, V-01+ |
| İhracat = Excel satırı | `is_export` bayrağı kullanıcı/Excel kontrolünde; fiziksel ihracat kanıtı yok | F-01 |
| Tutar eşleşmesi zayıf | `AMOUNT_MISMATCH_GCB` için envanterde tutar/beyan no çoğu yüklemede hâlâ boş kalabilir | F-05, B-03 |
| LLM belge çıkarma | `MOCK_DOCUMENT_EXTRACTION` veya Gemini — prod kalitesi veriye bağlı | B-05 |
| Dosya adı sezgisi | API kapalıyken `STRICT_PROOF` ile clarification’da reddedilir; sidebar yolu farklı politika | B-06 |
| Sidebar vs clarification | İki giriş kanalı; politikalar `ingest` ile hizalandı ama tam parity değil | K-02 |
| Auth / çok kullanıcı | Tek oturum Streamlit; API anahtarı opsiyonel (`IADEAJAN_API_KEY`) | O-01 |
| Test bypass | `v3_accuracy_test`, `workflow.__main__` hâlâ önceden cevap enjekte edebilir | K-05 |

### 7.2 Orta / ürün borcu

| Konu | Açıklama |
|------|----------|
| `refund_type` hâlâ radyo | Kanıt yok; yalnızca çapraz uyarı | C-01 |
| VKN checksum | Yalnızca **uyarı** (`VKN_CHECKSUM_WARN`); ceza yok (v3.3 planı) |
| Döviz | Farklı para biriminde tutar karşılaştırması atlanır / uyarı | F-06 |
| Finansman vs skor | Skor 88 (Orta) iken registry “finance_eligible” beklentisi karışabilir |
| `skipped_questions` | State’te tanımlı, kullanılmıyor | C-04 |

### 7.3 Bilinçli olarak yapılmayanlar (v3.2 kapsam dışı)

- Yeni ceza kodu veya skor formülü değişikliği (`FRAUD_VKN_MISMATCH` gibi kodlar **eklenmedi**).
- GİB entegrasyonu, blockchain, e-imza doğrulama.
- Tam PDF OCR kalitesi garantisi (Gemini’ye bağlı).

---

## 8. Veri giriş kanalları — güven seviyeleri

| Kanal | Güven seviyesi | Envantere nasıl girer |
|-------|----------------|----------------------|
| Mock senaryo (`celik_as_high`) | `source=scenario` | Test/demo; Analyzer sayar |
| Canonical JSON + doğrulanmış belge | `verified` (ingest sonrası) | Analyzer sayar |
| Canonical JSON + `user_assertion` | **Sayılmaz** → `eksik` | GÇB cezası devam |
| Clarification + `proof_ledger passed` | `verified` | Merge sonrası sayılır |
| Clarification sadece “Evet” (kanıt yok) | **Girmez** | Gate reddeder |
| Sidebar PDF (ingest passed) | `verified` | Collector merge |
| Sidebar PDF (ingest failed) | `llm_classifier` / unverified | STRICT modda sayılmaz |

---

## 9. Streamlit kullanıcı yolculuğu

### 9.1 Tipik başarılı akış

1. Kullanıcı Excel veya senaryo seçer → **Segment 1** başlar.
2. Collector + Analyzer çalışır → GÇB eksik → **clarification** ekranı.
3. Kullanıcı GÇB PDF yükler → `ingest_document_file` → ledger `passed`.
4. “Cevapları gönder” → **Segment 2** → Clarification merge → Analyzer 2. tur → Decision.
5. **done** ekranı: skor, ceza tablosu, finansman uygunluğu.

### 9.2 Bloklanmış akış

- Kanıt 5 turda tamamlanamaz → **blocked** ekranı.
- Skor üretilmez; risk önizlemesi state’te kalabilir.
- Kullanıcı “Yeniden dene” veya “Analizi sonlandır”.

### 9.3 Hata akışı

- Upload başarısız + fallback kapalı → **error**, `failure_reason=UPLOAD_FAILED`.
- Graph exception → **error**, `error_state` mesajı.

---

## 10. Ortam değişkenleri (operasyon)

| Değişken | Varsayılan (öneri) | Etki |
|----------|-------------------|------|
| `STRICT_PROOF` | `true` | Heuristic sınıflandırma kanıt sayılmaz |
| `REQUIRE_VERIFIED_PROOF` | `true` | Clarification “Evet” için ledger `passed` |
| `STRICT_INVENTORY_TRUST` | `true` | Yalnızca verified/scenario envanter |
| `ALLOW_MOCK_FALLBACK` | `false` (prod) | Upload fail → mock senaryo |
| `VERIFICATION_ENV` | `production` / `local` / `test` | TTL süreleri |
| `VERIFICATION_AUDIT_ENABLED` | `true` | `logs/verification_audit.jsonl` |
| `MOCK_DOCUMENT_EXTRACTION` | `false` (prod) | Testte `true` |
| `DOCUMENT_CLASSIFIER_ENABLED` | `true` | `false` → dosya adı sezgisi |
| `GOOGLE_API_KEY` | — | LLM/sınıflandırma |
| `MAX_LLM_TABULAR_ROWS` | `5` | Excel→LLM örnek satır sayısı |

---

## 11. Test kapsamı

| Dosya | Ne test eder |
|-------|----------------|
| `tests/test_proof_ledger.py` | TTL, validate_answers, maskeleme |
| `tests/test_verification_cross_validate.py` | VKN sahiplik, checksum uyarısı |
| `tests/test_inventory_trust_policy.py` | Canonical bypass, verified sayımı |
| `tests/test_analysis_status_machine.py` | blocked/failed → skor yok |
| `tests/test_security_attack_suite.py` | S1–S2 saldırı özetleri |
| **`tests/test_e2e_verification_flow.py`** | **3 gerçek dünya senaryosu (LangGraph zinciri)** |
| `tests/test_clarification_proof.py` | Alan–tip eşlemesi |
| `tests/test_excel_pipeline.py` | Upload formatları |
| `tests/v3_accuracy_test.py` | Senaryo skor regresyonu (ayrı harness) |

**E2E sonuç (son koşum):** 3/3 PASSED (~0,4 sn, LLM kapalı fixture ile).

```bash
.venv/bin/python -m pytest tests/test_e2e_verification_flow.py -v
```

---

## 12. Olgunluk modeli (özet)

| Seviye | Açıklama | Bugün |
|--------|----------|--------|
| L0 | Kullanıcı beyanı | Kısmen kapatıldı |
| L1 | Belge tipi (AI sınıflandırma) | Var |
| L2 | Kanıt dosyası + ledger | **Var (yeni)** |
| L3 | Yapılandırılmış çıkarma + çapraz kontrol | **Kısmen (yeni)** |
| L4 | Resmi kayıt / API | Yok |
| L5 | Tam ihracat fiziksel doğrulama | Yok |

---

## 13. Sonuç ve önerilen sonraki adımlar

### Bugünkü durum cümlesi

> İadeAjan, Path B ceza mahkemesi ile tutarlı skor üretir; **Proof Ledger ve Zero Trust politikaları** ile belge beyanlarını önemli ölçüde sıkılaştırdı. Sistem **“belge var ve doğrulama katmanından geçti”** seviyesinde güvenlidir; **“devlet kayıtlarıyla doğrulandı”** seviyesinde değildir.

### Öncelikli iyileştirmeler (ürün + teknik)

1. **Prod’da** `ALLOW_MOCK_FALLBACK=false`, `REQUIRE_VERIFIED_PROOF=true`, `STRICT_*=true` zorunlu tutmak.
2. `AMOUNT_MISMATCH_GCB` için Excel’e GÇB no/tutar sütunları ve extraction kalitesini artırmak.
3. GİB/gümrük veya en azından **manuel inceleme kuyruğu** için `verification_summary` UI’da net göstermek.
4. `GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md` maddelerine **ÇÖZÜLDÜ** işaretleri güncellemek (bu dokümanla uyumlu).
5. İsteğe bağlı: `FRAUD_VKN_MISMATCH` v3.3 — yalnızca flag ile, Path B onayı sonrası.

---

## 14. Revizyon geçmişi

| Tarih | Değişiklik |
|-------|------------|
| 2026-05-18 | İlk sürüm: çalışma mantığı + güvenlik öncesi/sonrası + açık konular + E2E referansı |

---

*Bu belge canlı tutulmalıdır; büyük mimari değişikliklerde `IADEAJAN_ALGORITMA_v3.2.md` ve `GUVENLIK_VE_DOGRULAMA_BOSLUKLARI.md` ile birlikte güncellenir.*
