# İadeAjan — Güvenlik ve Doğrulama Boşlukları Envanteri

**Tarih:** 2026-05-18  
**Kapsam:** Path B mimarisi (Collector → Analyzer → Clarification → Decision), belge sınıflandırma, kanıta dayalı clarification UX, finansman kilidi.  
**Amaç:** Kod incelemesi ve mimari analiz sonucu tespit edilen risklerin tek referans dokümanı. Bu liste `PENALTY_MATRIX` değerlerini değiştirmez; ürün ve güvenlik borcunu kayıt altına alır.

---

## Özet

| Önem | Adet (yaklaşık) |
|------|-----------------|
| Kritik | 18 |
| Yüksek | 22 |
| Orta | 16 |
| Düşük / teknik borç | 10 |

**Doğrulama odaklı ek analiz:** Aşağıdaki [Bölüm 11](#11-derinlemesine-doğrulama-analizi-ihracat-gerçekliği-ve-belge-otantikliği) özellikle *“kullanıcının attığı şey gerçek mi — cidden satmış mı?”* sorusunu kod satırı düzeyinde açar.

**Ana tema:** Sistem çoğu yerde **“belge var mı / tip doğru mu / Excel’de ne yazıyor?”** seviyesinde çalışıyor. **“Gerçekten ihracat oldu mu, belge bu şirkete mi ait, güncel mi, tutarlar resmi kayıtlarla uyuyor mu?”** sorularının çoğu **otomatik doğrulanmıyor**.

Kanıta dayalı clarification UX (Streamlit) önceki “tek tıkla Evet” açığını **kısmen** kapattı; ancak **paralel giriş kanalları** ve **agent katmanında yeniden doğrulama olmaması** riski sürdürüyor.

---

## 1. Belge yapay zekâsı — ne yapıyor, ne yapmıyor

**İlgili dosyalar:** `app/services/document_classifier.py`, `app/services/clarification_proof.py`, `main.py` (`_render_proof_document_question`)

### Yapılanlar

- PDF/görsel → `DocumentClassification` (`type`, `confidence`, `evidence`)
- Clarification’da belge sorularında radyo yerine yükleme + `verify_classification` (tip eşleşmesi + `confidence ≥ 0.8`)
- Sidebar’da çoklu belge → Collector’da `_merge_llm_documents_into_inventory`

### Yapılmayanlar (kritik)

| ID | Sorun | Etki |
|----|--------|------|
| B-01 | Belgenin **şirket VKN/unvan** ile eşleşmesi kontrol edilmiyor | Başka firmaya ait GÇB yüklenebilir |
| B-02 | Belge **tarihi / dönemi** analiz dönemiyle karşılaştırılmıyor | Eski veya yanlış dönem beyanı “mevcut” sayılır |
| B-03 | **Beyan no, tutar, mal kodu** PDF’ten yapılandırılmış çıkarılmıyor | `AMOUNT_MISMATCH_GCB` beslenemez |
| B-04 | GİB / gümrük **dış kaynak doğrulama** yok | Görsel benzerlik ≠ resmi kayıt |
| B-05 | Sahte PDF / başkasının belgesi → model “GÇB” diyebilir | Tür doğrulaması ≠ içerik/sahiplik doğrulaması |
| B-06 | API kapalıyken **dosya adı sezgisi** (`gcb.pdf` → 0.85 güven) | `DOCUMENT_CLASSIFIER_ENABLED=false` veya hata → zayıf geçiş |
| B-07 | Geniş `except Exception` → sezgiye düşme, kullanıcıya net uyarı yok | Sessiz güven kaybı |

---

## 2. Kanıt UX sonrası kalan bypass yolları

**Kritik:** UI kapısı varken state başka yollardan doldurulabiliyor.

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| K-01 | **Kanonik JSON** yüklemede `documents[]` doğrudan `status: mevcut` verilebilir | `upload_loader._normalize_canonical`, Analyzer envanter kuralları | Clarification/kanıt hiç açılmadan GÇB cezası kalkabilir |
| K-02 | **Sidebar belge** yüklemesi aynı zayıf sınıflandırıcı; clarification’daki `expected_type` eşlemesi yok | `collector_agent._merge_llm_documents_into_inventory` | Yanlış PDF envantere “mevcut” düşer |
| K-03 | Segment 2’de `clarification_answers` istemciden gelir; **sunucu tarafı kanıt re-doğrulaması yok** | `main.py` `_run_segment_2`, `clarification_agent` | State manipülasyonu (API/otomasyon) |
| K-04 | Upload başarısız → **mock senaryoya sessiz fallback** | `collector_node` | Kullanıcı kendi dosyasını sanırken başka veri analiz edilir |
| K-05 | Test/script’lerde doğrudan `"Evet"` / dolu envanter | `tests/test_excel_pipeline.py`, `workflow.__main__`, `v3_accuracy_test.PRESET_ANSWERS` | Prod güven modeli ile test modeli uyumsuz |

---

## 3. Excel / fatura verisine kör güven

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| F-01 | **İhracat = Excel’de Tip “İhracat”** | `collector_agent._normalize_invoice`, `ai_converter` | Gerçek ihracat doğrulanmaz |
| F-02 | `has_customs_declaration` fatura satırında; upload şemasında zayıf / yok | Collector normalize | Kullanıcı bayrağı manipüle edilebilir |
| F-03 | VKN yalnızca **format** (`DQ_INVALID_VKN`); GİB doğrulama yok | `analyzer_agent` fallback DQ | Sahte VKN geçebilir |
| F-04 | Tedarikçi `risk_level` veri setinden; **kara liste API** yok | `_apply_supplier_risk_rules` | Excel’de “düşük” yazılabilir |
| F-05 | `AMOUNT_MISMATCH_GCB` kuralı var ama `declaration_no` / `amount` çoğu yüklemede **boş** | `_apply_amount_mismatch_rules` | Kural pratikte sık çalışmaz |
| F-06 | **Döviz:** `currency` alanı var; GÇB–fatura tutarı karşılaştırmasında kur çevirimi görünmüyor | Faturalar + envanter | USD/EUR–TRY yanlış veya atlanır |

---

## 4. Clarification ve döngü mantığı

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| C-01 | `q_refund_type_001` hâlâ **radyo**; kanıt yok | `analyzer_agent._build_clarification_questions`, `main.py` | Kullanıcı `ihracat` seçerek GÇB kurallarını tetikler |
| C-02 | `has_ymm_contract` UI’da hazır; Analyzer **soru üretmiyor** | `clarification_proof`, `analyzer_agent` | Tutarsız ürün yüzeyi |
| C-03 | **`retry_count >= max_retries` (varsayılan 5)** → `clarification_needed = False`, sorular silinir | `analyzer_agent` ~1207–1214 | Belge kanıtı verilmeden Decision’a geçiş (**kritik bypass**) |
| C-04 | `skipped_questions` state’te tanımlı, **kullanılmıyor** | `app/core/state.py` | Ölü sözleşme |
| C-05 | Kısmi cevaplar (`Bir kısmı mevcut`) UI’dan kalktı; `CLARIFICATION_SCORE_ADJUSTMENTS` hâlâ tanımlı | `decision_agent` | Kod/ürün uyumsuzluğu |

---

## 5. Envanter ve Collector tutarsızlıkları

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| E-01 | `classification_to_inventory_entry` yalnızca `gumruk_beyannamesi` + `ymm_tasdik_raporu`; **2 No Collector LLM yolunda envantere eklenmiyor** | `document_inventory.py` | Sidebar 2 No PDF ≠ clarification kanıt yolu |
| E-02 | Clarification `entry_from_clarification` → `mevcut` kaydı; **PDF metadata (no, tutar, date) yok** | `document_inventory.py`, `clarification_agent` | Analyzer yalnızca tip+status görür |
| E-03 | Kanıt PDF temp dizine yazılır; **Segment 2 state’ine yapılandırılmış taşınmaz** | `main.py` | İkinci analiz belge içeriğini bilmez |
| E-04 | `is_paid` (2 No ödeme) kuralı var; kanıt envanteri **`is_paid` yazmıyor** | `_apply_tevkifat_rules` | Ödenmemiş beyan atlanabilir |

---

## 6. Skor, onay ve finansman

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| S-01 | Clarification `"Evet"` → **+6** + `pending_verification`; kanıt başarılı olsa bile mesaj “belgeleri yükleyin” olabilir | `decision_agent._compute_clarification_bonus` | Yanıltıcı onay metni |
| S-02 | Bonus, eksik belge cezasını **silmez** (dokümantasyon doğru); kullanıcı skor artışını “onay” sanabilir | `IADEAJAN_ALGORITMA_v3.2.md`, Decision | UX riski |
| S-03 | Finansman: `Düşük Risk` + tutar ≥ 50k + `MANDATORY_LOCK_CODES` yok; **belge kalitesi/kanıt kanıtı kriter değil** | `decision_agent._check_finance_eligibility` | Yüksek skor + zayıf belge → finansman açılabilir (kilit kodları kalkınca) |
| S-04 | `estimated_refund_amount` Excel KDV toplamından türetilebilir | `collector_agent._enrich_company_profile_from_upload` | YMM eşiği manipülasyonu |
| S-05 | `has_ymm_contract`, `has_previous_refund` JSON profilde; **doğrulanmıyor** | `company_profile`, Analyzer çıktısı | Profil beyanı |

---

## 7. LLM katmanı (Analyzer denetçi)

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| L-01 | LLM fatura özetinden kanunname kodu seçer; **halüsinasyon / aşırı ceza** mümkün (Path B: cap yok) | `_detect_anomalies_with_llm` | Deterministik olmayan risk |
| L-02 | `LLM_ANOMALY_ENABLED=false` → zayıf fallback DQ | `analyzer_agent` | API kapalı = zayıf denetim |
| L-03 | Makro Python kuralları (`REFUND_LOGIC_IMPOSSIBLE`, `DATA_INTEGRITY_FAILURE`) iyi; LLM ile **çift sinyal** karışabilir | `_apply_macro_integrity_rules` | Yorumlama zorluğu |
| L-04 | Shadow learning yalnızca **JSONL log**; kararı bloklamaz | `shadow_learning.py` | İnsan incelemesi pipeline dışı |

---

## 8. Tabular yükleme ve AI dönüşüm

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| T-01 | Heuristic yetersizse **`_llm_convert`**: ilk 5 satırdan tüm payload | `ai_converter.py` | Tüm dosya LLM çıktısına güvenebilir |
| T-02 | LLM `IngestionPayload` üretir; `documents` genelde **[]** | `ai_converter` | Belge envanteri boş kalır veya hayali faturalar |
| T-03 | Preflight **otomatik düzeltme** (tarih/tutar) | `upload_preflight.repair_tabular` | Şüpheli veri “temiz” görünür |

---

## 9. Operasyonel ve ürün mimarisi

| ID | Sorun | Konum | Etki |
|----|--------|-------|------|
| O-01 | Streamlit’te **kimlik doğrulama / rol yok** | `main.py` | Herkes analiz çalıştırabilir |
| O-02 | Kanıt PDF’leri temp’te; **silme, şifreleme, audit trail** yok | `iadeajan_clarification_proofs` | KVKK / delil zinciri |
| O-03 | Gemini çağrılarında **kullanıcı başına kota / abuse limiti** yok | Tüm LLM servisleri | Maliyet / kötüye kullanım |
| O-04 | State’te **RAG, Supervisor, Critic, sub-graph** tanımlı; graph’ta **yok** | `app/core/state.py` vs `workflow.py` | Yanlış mimari beklentisi |
| O-05 | Kritik tedarikçi `force_clarification` açılır; sorular yine GÇB/2 No — **SMİYB özel kanıtı yok** | `analyzer_agent` | Risk–soru uyumsuzluğu |

---

## 10. “Gerçekten sattı mı?” — sistem cevap tablosu

| Soru | Sistem cevabı |
|------|----------------|
| Dışarıya gerçekten satış/ihracat oldu mu? | **Hayır** — Excel `is_export` / tip + (isteğe bağlı) GÇB PDF sınıfı |
| Belge bu şirkete mi ait? | **Hayır** |
| Belge güncel / doğru dönem mi? | **Hayır** (fatura tarihi kuralları ayrı) |
| GÇB tutarı faturayla uyuyor mu? | **Nadiren** — veri alanları çoğu zaman boş |
| Sahte veya başkasının PDF’i? | **Kısmen** — tür sınıflandırması only |
| Finansman = belge kalitesi? | **Hayır** |
| JSON ile kanıt atlanır mı? | **Evet** |
| Backend kanıtı tekrar doğrular mı? | **Hayır** |
| 5 tur sonra sorular kapanır mı? | **Evet** (`retry_count` limiti) |

---

## 11. Derinlemesine doğrulama analizi (ihracat gerçekliği ve belge otantikliği)

Bu bölüm, vergi müfettişi / finansman risk perspektifinden sorulması gereken soruları **mevzuat beklentisi** ile **kodda fiilen yapılan kontrol** arasında eşleştirir.

### 11.1 Doğrulama olgunluk modeli (sistem nerede duruyor?)

| Seviye | Tanım | İadeAjan bugün |
|--------|--------|----------------|
| **L0** | Kullanıcı beyanı (radio / Excel hücresi) | Evet — `Tip=İhracat`, clarification öncesi `"Evet"` |
| **L1** | Dosya varlığı (PDF yüklendi mi) | Kısmen — envanter `status=mevcut` |
| **L2** | Belge **türü** (GÇB görünüyor mu) | Evet — `classify_document` + `verify_classification` |
| **L3** | Belge **içeriği** (VKN, tarih, beyan no, tutar OCR) | **Hayır** — structured extraction yok |
| **L4** | **Çapraz kayıt** (fatura ↔ GÇB ↔ mükellef ↔ dönem) | **Hayır** — alanlar çoğu boş veya kullanılmıyor |
| **L5** | **Resmi kayıt** (GİB / gümrük / e-Fatura) | **Hayır** — entegrasyon yok |

**Sonuç:** Üretim akışı fiilen **L0–L2** aralığında. Finansal karar için gereken **L3–L5** eksik. Kullanıcı “cidden yurtdışına satmış mı?” sorusuna sistem **doğrudan cevap veremez**; yalnızca “Excel’de ihracat yazıyor ve (belki) GÇB benzeri bir PDF var” der.

---

### 11.2 Uçtan uca akış: kullanıcı “ihracat iadesi” iddia ettiğinde

```text
[Excel/JSON]  Tip sütunu / is_export
      │
      ▼
Collector._normalize_invoice  →  is_export = raw.is_export OR type=="ihracat"
      │
      ▼
_detect_refund_type  →  export_count > 0  ⇒  refund_type = "ihracat"
      │
      ▼
Analyzer._apply_export_rules
      │   has_ihracat_invoice?  (Excel bayrağı)
      │   gumruk_confirmed?      (envanterde type=gumruk_beyannamesi, status≠eksik)
      │   → yoksa GCB_MISSING (−35, finansman kilidi)
      │
      ▼
Clarification (GÇB sorusu)  →  kanıt PDF + tip doğrulama  →  "Evet" + envanter upsert
      │
      ▼
Analyzer (2. tur)  →  gumruk_confirmed=true  →  GCB_MISSING kalkar
      │
      ▼
Decision  →  skor + finansman (MANDATORY_LOCK yoksa açılabilir)
```

**Hiçbir adımda şunlar kontrol edilmez:**

- Malın fiziksel olarak Türkiye’den çıkıp çıkmadığı  
- GÇB’deki ihracatçı VKN’nin `company_profile.tax_number` ile aynı olması  
- GÇB tarihinin fatura tarihi ve `analysis_period` ile uyumu  
- GÇB kalem tutarlarının toplamının fatura matrahı/KDV iadesi ile tutarlılığı (alanlar dolu değilse)  
- e-Fatura / ihracat istisna belgesi / konsinye / triangulation senaryoları  

---

### 11.3 Soru bazlı detay matrisi

#### A) “Gerçekten dışarıya (ihracat) satmış mı?”

| Alt soru | Mevzuat / iş beklentisi | Kodda ne var? | Boşluk ID |
|----------|-------------------------|---------------|-----------|
| Satış yurt dışı alıcıya mı? | GÇB, konşimento, ihracat faturası, döviz geliri | Yok | **V-01** |
| İhracat kayıtlı gümrük çıkışı var mı? | GÇB zorunlu (ihracat iadesi) | Envanterde `gumruk_beyannamesi` **tipi** | **V-02** (tip ≠ çıkış) |
| Fatura ihracat istisnalı mı (KDV %0)? | Fatura + muhasebe | `kdv_amount`/`kdv_rate` LLM/DQ; Excel’de ihracat satırında 0 KDV sezgisi var (`ai_converter`) ama **zorunlu değil** | **V-03** |
| Alıcı yurt dışı mı (VKN/ünvan)? | Fatura alanı | `supplier_id` / VKN maskeli LLM; **ülke kodu / yurt dışı bayrağı yok** | **V-04** |
| Sahte ihracat (iç piyasa faturaları + ihracat etiketi) | Makro profil | `REFUND_LOGIC_IMPOSSIBLE` (LLM: yurtiçi VKN profili); Python: %75 negatif tutar | **V-05** (kısmi, atlanabilir) |
| `has_customs_declaration` fatura satırı | Satır bazlı GÇB eşleşmesi | Mock JSON’da var; **`_apply_export_rules` bu alanı okumuyor** | **V-06** |

**Kod referansı — ihracat sayılması:**

```python
# collector_agent._normalize_invoice
is_export = bool(raw.get("is_export", False)) or (invoice_type == "ihracat")
```

```python
# analyzer_agent._apply_export_rules
has_ihracat_invoice = any(
    inv.get("is_export") or inv.get("type") == "ihracat"
    for inv in normalized_invoices
)
gumruk_confirmed = any(
    d.get("type") == "gumruk_beyannamesi" and _inventory_entry_present(d)
    for d in document_inventory
)
```

Yani **“satmış mı?” ≈ “Excel’de ihracat işaretli + (isteğe bağlı) GÇB PDF sınıfı doğru”.**

---

#### B) “Attığı belge gerçek / ona mı ait?”

| Alt soru | Beklenti | Kod | Boşluk ID |
|----------|----------|-----|-----------|
| PDF gerçek GÇB mi (şablon/sahte)? | İçerik + imza + referans | Yalnızca görsel sınıflandırma | **V-07** |
| GÇB’deki ihracatçı VKN = mükellef? | `tax_number` eşleşmesi | `company_profile.tax_number` var; belgeden **okunmuyor** | **V-08** |
| GÇB’deki unvan = `company_name`? | Metin eşleşmesi | Yok | **V-09** |
| Başka firmanın GÇB’si yüklendi mi? | VKN çapraz kontrol | Yok | **V-10** |
| Eski/iptal beyan mı? | Beyan no + tarih + durum | Yok | **V-11** |
| Kanıt dosyası Segment 2’de tekrar doğrulanıyor mu? | Sunucu gate | Hayır — UI `session_state` güvenir | **V-12** |

**Kanıt UX’in sınırı** (`clarification_proof.verify_classification`):

- `result.type == expected_type` (ör. `gumruk_beyannamesi`)  
- `result.confidence >= 0.8`  

**Sahiplik, tarih, beyan numarası bu fonksiyonda yok.**

---

#### C) “Belge güncel mi / doğru dönem mi?”

| Alt soru | Beklenti | Kod | Boşluk ID |
|----------|----------|-----|-----------|
| GÇB tarihi ∈ `analysis_period`? | Dönem uyumu | `PERIOD_OUT_OF_RANGE` **yalnızca fatura tarihi** | **V-13** |
| GÇB, fatura tarihinden önce/sonra mantıklı mı? | Kronoloji | Yok | **V-14** |
| İade dönemi ile beyan dönemi | KDV beyanı uyumu | Yok | **V-15** |
| Clarification kanıtı sonrası belge tarihi kaydı | Envanter `declaration_date` | `entry_from_clarification` tarih yazmıyor | **V-16** |

---

#### D) “Fatura ile GÇB tutarlı mı?” (gerçek satış hacmi)

Kural **kodda yazılı** ama veri zinciri çoğu zaman **ölü**:

```python
# analyzer_agent._apply_amount_mismatch_rules
# GÇB tarafı: document_inventory → declaration_no + amount
# Fatura tarafı: customs_declaration_amount veya customs_declaration_no ile eşleme
# Eşik: %5 (AMOUNT_MISMATCH_THRESHOLD)
```

| Gereken alan | Excel upload (heuristic) | Mock JSON | Kanıt sonrası envanter |
|--------------|--------------------------|-----------|-------------------------|
| `declaration_no` (envanter) | Yok | Nadiren | Yok |
| `amount` (envanter GÇB) | Yok | Nadiren | Yok |
| `customs_declaration_no` (fatura) | Yok | Bazı mock’larda yok | Yok |
| `customs_declaration_amount` (fatura) | Yok | Yok | Yok |
| `currency` + kur çevrimi | `currency` var, kural kullanmıyor | Var | Yok |

**Pratik sonuç:** `AMOUNT_MISMATCH_GCB` **implemente** ama upload yolunda **neredeyse hiç tetiklenmez** → kullanıcı yüksek tutarlı sahte ihracat Excel’i + düşük/yanlış GÇB PDF ile tutarsızlık **yakalanmayabilir**. (**V-17**)

Ayrıca karşılaştırma mesajında tutarlar `₺` formatında gösteriliyor; fatura `USD`/`EUR` ise **yanlış birim** riski (**V-18**).

---

#### E) “İade türü doğru mu?” (ihracat mı tevkifat mı)

| Kontrol | Kod | Boşluk |
|---------|-----|--------|
| Otomatik tespit | `_detect_refund_type`: `export_count` / `tevkifat_count` | Excel bayrağı |
| Kullanıcı override | `q_refund_type_001` radyo, **kanıt yok** | **V-19** |
| Karışık dosya | `belirsiz` + clarification | Kısmi |

Kullanıcı ihracat seçip GÇB kanıtı yükleyebilir; tevkifat belgeleri hiç istenmeyebilir.

---

#### F) Tevkifat / 2 No (paralel doğrulama story)

| Soru | Kod | Boşluk |
|------|-----|--------|
| 2 No beyanı var mı? | Envanter `2no_kdv_beyannamesi` | Collector LLM envantere 2 No **eklemiyor** (E-01) |
| Beyan ödendi mi? | `doc.get("is_paid")` | Kanıt envanteri `is_paid` set etmiyor (E-04) |
| Tevkifat faturaları beyanla uyumlu mu? | `TEVKIFAT_MISSING_DECL` satır bayrağı | Excel `has_tevkifat_declaration` — doğrulanmıyor |

---

### 11.4 Sahte senaryolar (saldırı / hata modeli)

Aşağıdakiler bugün **yüksek veya orta olasılıkla** sistemden geçebilir veya skoru yapay olarak iyileştirebilir:

| # | Senaryo | Neden geçer? |
|---|---------|----------------|
| S1 | Excel’de 50 satır `Tip=İhracat`, gerçekte iç satış | L0 — `is_export` türetilir |
| S2 | İnternetten indirilmiş örnek GÇB PDF, başka firma | L2 — tip eşleşir, VKN kontrolü yok (V-08) |
| S3 | `gcb.pdf` dosya adı, içerik fatura görseli | Sezgi veya zayıf model (B-06) |
| S4 | Kanonik JSON: `"documents":[{"type":"gumruk_beyannamesi","status":"mevcut"}]` | K-01, clarification atlanır |
| S5 | Kanıt yüklemeden 5 Analyzer turu | C-03 — sorular kapanır |
| S6 | Segment 2’ye `clarification_answers: {q_gumruk_001: Evet}` enjekte | K-03 |
| S7 | USD faturalar + TRY GÇB, tutarlar tesadüfen yakın | V-18 veya kural hiç çalışmaz (V-17) |
| S8 | Yüksek KDV toplamı Excel → YMM eşiği; sahte YMM PDF | `estimated_refund_amount` + L2 YMM tipi |
| S9 | LLM tabular: 5 satırdan uydurulmuş 200 fatura | T-01 |
| S10 | Sidebar + clarification farklı güven: sidebar’da yanlış tip | K-02, E-01 |

---

### 11.5 “Cidden satmış mı?” — hukuki/operasyonel vs yazılım cevabı

| Paydaş sorusu | İdeal kanıt zinciri | İadeAjan cevabı |
|---------------|---------------------|-----------------|
| Mal çıktı mı? | GÇB + çıkış gümrüğü + (opsiyonel) navlun | **İndirgemez** — PDF sınıfı |
| Alıcı gerçek mi? | Fatura + INCOTERM + ödeme | **Yok** |
| Tutar doğru mu? | Fatura ↔ GÇB ↔ banka | **Nadiren** (alan yok) |
| İade hakkı doğdu mu? | KDV mevzuatı + defter | Kısmen — ceza matrisi |
| Finansman verilebilir mi? | Yukarıdakiler + skor | Skor + kilit kodları; **kanıt kalitesi değil** |

**Önemli:** Sistem bir **ön inceleme / triyaj** aracı olarak değerli; **nihai “satış doğrulandı”** iddiasını kod bugün üretmemeli. UI metinleri bunu açık söylemiyorsa **ürün riski (V-20)** oluşur.

---

### 11.6 Eksik veri sözleşmesi (doğrulama için gerekli alanlar)

Aşağıdaki alanlar `document_inventory` / fatura normalize şemasında **tanımlı değil veya doldurulmuyor**:

**GÇB / belge (envanter):**

- `declaration_no`, `declaration_date`, `exporter_vkn`, `exporter_name`  
- `total_fob`, `currency`, `line_items[]`, `customs_office`  
- `verification_source` (`llm_extraction` | `gib_api` | `manual`)  
- `proof_file_hash`, `verified_at`  

**Fatura (normalize):**

- `customs_declaration_no`, `customs_declaration_amount` (kuralda var, upload’da yok)  
- `buyer_country`, `incoterm`, `is_export` upload heuristic’te type’tan türetiliyor only  
- `export_document_ref`  

**Şirket:**

- `tax_number` doğrulanmış mı bayrağı  
- `analysis_period` belge tarihleriyle kilitli mi  

Bu alanlar olmadan **L3–L4 doğrulama kodlanamaz**.

---

### 11.7 Önerilen hedef mimari (doğrulama katmanı)

```text
                    ┌─────────────────────┐
  PDF/PNG ─────────►│ DocumentClassifier  │──► type + confidence
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ DocumentExtractor   │──► VKN, tarih, no, tutar, PB
                    └──────────┬──────────┘
                               │
         company_profile ◄─────┼─────► CrossValidator
         normalized_invoices ◄─┘       (sahiplik, dönem, tutar, kur)
                               │
                    ┌──────────▼──────────┐
                    │ verification_result │──► passed | failed | manual_review
                    └──────────┬──────────┘
                               │
              clarification_answers["Evet"] yalnızca passed ise
              document_inventory structured + source=verified
```

**Kurallar (örnek):**

1. `extracted.exporter_vkn == state.tax_number` → zorunlu (ihracat).  
2. `declaration_date` ∈ `[analysis_period.start, analysis_period.end]` (tolerans ±N gün).  
3. En az bir ihracat faturası `customs_declaration_no` ile envanter beyan no eşleşmeli.  
4. `abs(fatura_try - gcb_try) / gcb_try <= 0.05` (kur tablosu ile).  
5. `passed` olmadan `GCB_MISSING` kalkmamalı; `pending_verification` ile `approved` birlikte olmamalı.

---

### 11.8 Doğrulama boşlukları — konsolide ID listesi (V serisi)

| ID | Özet |
|----|------|
| V-01 | Fiziksel ihracat / yurt dışı alıcı doğrulanmıyor |
| V-02 | GÇB **çıkışı** değil, belge **sınıfı** kontrol ediliyor |
| V-03 | İhracat KDV %0 zorunlu değil |
| V-04 | Alıcı ülke / yurt dışı profil yok |
| V-05 | Sahte ihracat profili yalnızca kısmi makro/LLM |
| V-06 | `has_customs_declaration` fatura alanı kullanılmıyor |
| V-07–V-12 | Otantiklik, sahiplik, replay — B ve K maddeleri ile örtüşür |
| V-13–V-16 | Güncellik / dönem — belge tarihi yok |
| V-17–V-18 | Tutar eşlemesi ölü kod / döviz |
| V-19 | İade türü radyo ile manipülasyon |
| V-20 | UI “doğrulandı” algısı — extraction yokken |

---

## 12. Önerilen çözüm yönleri (öncelik)

### P0 — Kritik (finansal güven)

1. **DocumentExtraction** şeması: VKN, unvan, beyan no, tarih, tutar, para birimi.
2. **Şirket/dönem/fatura çapraz doğrulama** (Analyzer’da zorunlu).
3. **Sunucu tarafı kanıt gate:** `proof_verified`, dosya hash, Segment 2’de `classify_document` + extraction tekrar.
4. **JSON/canonical:** `documents[].status=mevcut` yalnızca `source=verified` + extraction geçerliyse.
5. **`retry_count` limiti:** Soruları kapatmak yerine `rejected` / bloklu Decision veya insan eskalasyonu.

### P1 — Yüksek

6. Tek belge pipeline (sidebar + clarification + Collector).
7. Clarification bonus / `pending_verification` ile kanıt başarısını hizala.
8. `AMOUNT_MISMATCH` için envanterden yapılandırılmış alan zorunluluğu veya kuralı devre dışı bırak + UI uyarısı.
9. Collector’a `2no_kdv_beyannamesi` envanter desteği (tutarlılık).
10. Tabular LLM dönüşümünde tam dosya veya satır satır heuristic zorunluluğu; LLM yalnızca mapping.

### P2 — Orta

11. Auth, audit log, temp dosya temizliği.
12. `DOCUMENT_CLASSIFIER_ENABLED=false` iken kullanıcı uyarısı; dosya adı sezgisini clarification’da kapat veya düşük güven.
13. Döviz normalizasyonu (TCMB kuru veya kullanıcı beyanı).
14. State sözleşmesini gerçek graph ile hizala (ölü alanları kaldır veya uygula).

### P3 — Uzun vadeli

15. GİB / gümrük / e-Fatura entegrasyonu (ürün ve hukuk kararı).
16. RAG / Supervisor / Critic — ya implement et ya dokümantasyondan çıkar.

---

## 13. İlgili dosya indeksi

| Alan | Dosyalar |
|------|----------|
| Belge sınıflandırma | `app/services/document_classifier.py` |
| Kanıt doğrulama | `app/services/clarification_proof.py`, `main.py` |
| Envanter | `app/services/document_inventory.py` |
| Collector | `app/agents/collector_agent.py` |
| Analyzer | `app/agents/analyzer_agent.py` |
| Clarification | `app/agents/clarification_agent.py` |
| Decision / finansman | `app/agents/decision_agent.py` |
| Ceza matrisi | `app/schemas/penalty_codes.py` |
| Upload | `app/services/upload_loader.py`, `upload_preflight.py`, `ai_converter.py` |
| Graph | `app/graph/workflow.py` |
| Algoritma dokümanı | `docs/IADEAJAN_ALGORITMA_v3.2.md` |

---

## 14. Revizyon geçmişi

| Tarih | Not |
|-------|-----|
| 2026-05-18 | İlk envanter: belge kanıt UX sonrası kod incelemesi + mimari analiz |
| 2026-05-18 | Bölüm 11 eklendi: ihracat gerçekliği, belge otantikliği, tutar eşlemesi, saldırı senaryoları (V-01–V-20) |
| 2026-05-18 | Bölüm 15: Proof ledger KVKK / saklama / maskeleme (PR-SEC-01) |

---

## 15. Veri saklama, maskeleme ve KVKK uyumu (Proof ledger)

### 15.1 İşlenen veri kategorileri

| Kategori | Örnek | Amaç |
|----------|--------|------|
| Belge görüntüsü / PDF | Yüklenen GÇB, YMM, 2 No | Tip ve alan doğrulama |
| Vergi kimlik no (VKN) | Çıkarım + şirket profili | Sahiplik çapraz kontrolü |
| Tutar / tarih / beyan no | Extraction alanları | Tutar/dönem uyumu (ileri faz) |

Ham PDF içeriği **kalıcı arşivlenmez**; yalnızca geçici disk yolu ve SHA-256 özeti tutulur.

### 15.2 Saklama süreleri (ortam)

| Ortam | Ledger TTL | Dosya TTL | Audit log |
|-------|------------|-----------|-----------|
| production | 24 saat (oturum) | 1 saat | 90 gün (redacted JSONL) |
| staging | 48 saat | 6 saat | 30 gün |
| test / CI | 2 saat | 30 dk | 7 gün |
| local | 8 saat | 2 saat | kapalı |

Ortam değişkeni: `VERIFICATION_ENV` veya `APP_ENV`. Audit: `VERIFICATION_AUDIT_ENABLED=true`.

### 15.3 Maskeleme (`ProofRecordRedactedView`)

- VKN: ilk 2 + son 2 hane (`12******90`)
- Dosya adı: `document.pdf` (uzantı korunur)
- SHA-256: yalnızca ilk 8 hex
- Unvan: audit satırına dahil edilmez

### 15.4 Silme ve kullanıcı hakları

- Streamlit oturumu / Segment 1 başında `proof_ledger` ve temp dosyalar silinir.
- Production’da proof ledger diske yazılmaz (`persist_ledger_to_disk=false`).
- Test artifact: yalnızca `ALLOW_TEST_ARTIFACTS=1` iken `tests/artifacts/proof_ledger/`.

### 15.5 Alt işleyen (Gemini)

Belge sınıflandırma ve çıkarma için Google Gemini API kullanılır (`GOOGLE_API_KEY`). Kurumsal DPA ve veri işleme sözleşmesi operasyonel süreç kapsamındadır.

### 15.6 O-02 (audit trail)

Yapılandırılmış doğrulama sonuçları `logs/verification_audit.jsonl` dosyasına **yalnızca maskelenmiş** alanlarla eklenir.

---

*Bu belge canlı bir güvenlik/teknik borç listesidir. Maddeler giderildikçe ilgili ID satırına “ÇÖZÜLDÜ” ve PR referansı eklenmelidir.*
