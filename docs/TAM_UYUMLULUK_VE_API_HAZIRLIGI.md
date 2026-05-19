# Tam Uyumluluk ve API Hazırlığı

Bu belge, BTK / yatırımcı ve jüri sunumları için **Tam Uyumluluk** katmanını özetler: belge zinciri (Zero Trust) + resmi GİB/Gümrük ve e-Fatura **tak-çalıştır** API soketi.

---

## 1. Özet

**Tam uyumluluk** = yüklenen belgelerin iç tutarlılığı (çapraz doğrulama, sahiplik, envanter) **ve** mümkün olduğunda GİB kayıtlarına karşı resmi doğrulama.

- **Demo / geliştirme:** `ENABLE_REAL_GIB_API=false` → deterministik mock (jüri senaryoları tekrarlanabilir).
- **Üretim:** `ENABLE_REAL_GIB_API=true` + `GIB_API_URL` / `GIB_EFATURA_API_URL` → canlı HTTP (sözleşme onaylandığında endpoint gövdeleri tamamlanır).

Path B (ceza matrisi, `add_penalty`, DecisionAgent) ve Zero Trust (`proof_ledger`, `STRICT_INVENTORY_TRUST`) **değiştirilmeden** genişletildi; yalnızca iki yeni **zorunlu kilit** ceza kodu eklendi.

---

## 2. Tak-çalıştır entegrasyon

| Değişken | Varsayılan | Anlam |
|----------|------------|--------|
| `ENABLE_REAL_GIB_API` | `false` | `false` → mock; `true` → canlı |
| `GIB_API_URL` | — | GÇB / gümrük doğrulama tabanı |
| `GIB_EFATURA_API_URL` | — | e-Fatura (yoksa `GIB_API_URL` kullanılır) |
| `GIB_API_TIMEOUT_SEC` | `15` | HTTP timeout |

**Tek giriş noktası:** `app/services/integrations/gib_api_client.py`

- `verify_gcb_with_gib(vkn, declaration_no, amount)`
- `verify_efatura_with_gib(vkn, invoice_uuid, issue_date, amount)`

Dönüş: `GibApiVerificationResult` (`verified`, `source`, `message`, `rejection_code`).

Geriye uyumluluk: `app/services/verification/gib_api_mock.py` → `verify_with_gib` gateway’e delegasyon (`ENABLE_GIB_MOCK` artık zorunlu değil).

---

## 3. GÇB akışı (Gümrük çıkış beyannamesi)

1. Belge ingest → `cross_validate_document` (`app/services/verification/cross_validate.py`).
2. Sahiplik (`OWNERSHIP_VKN_MATCH`) ve beyan no dolu ise → `verify_gcb_with_gib`.
3. **Geçti:** uyarı `GIB_API_VERIFIED`.
4. **Red:** hata `GIB_API_GCB_REJECTED` → doğrulama `failed`; kanıt defterine yazılır.
5. **Skor:** Analyzer `_apply_gcb_api_penalty_from_ledger` → `PenaltyCode.GIB_API_GCB_REJECTED` (−50, zorunlu kilit).

**Jüri demo anahtarı:** beyan no `SAHTE123` veya VKN `9999999999` → mock red.

---

## 4. e-Fatura akışı

Analyzer Katman 1, `_apply_period_rules` sonrası:

- `_apply_efatura_api_rules` — her normalize fatura için `verify_efatura_with_gib`.
- En az bir red → tek `add_penalty(GIB_API_EFATURA_REJECTED)` (−40, zorunlu kilit).

**Demo anahtarları:** fatura `id` içinde `SAHTE` / `IPTAL`, veya VKN `9999999999`.

Kod: `app/agents/analyzer_agent.py` — `_apply_efatura_api_rules`.

---

## 5. Path B ve Zero Trust

- Yeni kodlar `MANDATORY_LOCK_CODES` içinde (`is_mandatory_lock=True`).
- DecisionAgent Path B davranışı aynı; ek kodlar matrise `source=python` ile girer.
- Envanter: başarısız GÇB ingest → envantere girmez; API red ayrıca ceza olarak yansır.

---

## 6. Sahte uyumlu set senaryosu

| Tetikleyici | Katman | Sonuç |
|-------------|--------|--------|
| `declaration_no=SAHTE123` | cross_validate | `failed`, `GIB_API_GCB_REJECTED` |
| `id=SAHTE-INV` | Analyzer e-Fatura API | `GIB_API_EFATURA_REJECTED` |
| E2E ownership test VKN | `8888888888` | Ownership fail; GİB mock ile çakışmaz (`9999999999` API red kuralı ayrı) |

---

## 7. Yol haritası (canlı)

1. GİB resmi API sözleşmesi ve test ortamı URL’leri.
2. `gib_api_client._live_verify_gcb` / `_live_verify_efatura` — path ve payload (şu an URL dolu iken `NotImplementedError` + TODO).
3. Hukuk / KVKK veri işleme sözleşmesi.
4. `registry_status_summary()` — UI’da `tam_uyumluluk: api_bekleniyor | tamam`.

---

## 8. Kod referansları

| Bileşen | Dosya |
|---------|--------|
| API Gateway | `app/services/integrations/gib_api_client.py` |
| Ceza kodları | `app/schemas/penalty_codes.py` — `GIB_API_GCB_REJECTED`, `GIB_API_EFATURA_REJECTED` |
| GÇB çapraz doğrulama | `app/services/verification/cross_validate.py` |
| Kanıt defteri bayrakları | `app/services/verification/document_verification.py` — `gib_api_flags`, `verification_summary` |
| Analyzer | `app/agents/analyzer_agent.py` |
| Registry factory | `app/services/verification/registry/factory.py` |
| Unit testler | `tests/test_gib_api_client.py`, `tests/test_verification_cross_validate.py`, `tests/test_analyzer_efatura_api.py` |

---

*Son güncelleme: Tam Uyumluluk API Gateway entegrasyonu ile uyumlu.*
