# İadeAjan — Dosya Yükleme Rehberi

Bu rehber, KDV iade analizi için **fatura listenizi** nasıl hazırlamanız gerektiğini açıklar.

## Desteklenen dosya türleri

- Excel: `.xlsx`, `.xls`
- Virgülle ayrılmış: `.csv`
- Gelişmiş kullanıcılar için: `.json` (kanonik format)

## Excel’de olması gereken sütunlar

| Alan | Örnek sütun adı | Zorunlu |
|------|-----------------|---------|
| Fatura numarası | `Belge Kimliği`, `Fatura No` | Evet* |
| İşlem tarihi | `İşlem Tarihi`, `Fatura Tarihi` | Önerilir |
| Evrak türü | `Evrak Türü`, `Alış/Satış` | **Çok önemli** |
| Tutar (TRY) | `Yekün (TRY)`, `Tutar`, `Matrah` | Evet* |
| KDV oranı | `KDV Oranı`, `Kesilen Vergi Zımbırtısı` | İsteğe bağlı |
| Karşı taraf ünvanı | `Karşı Taraf Ünvanı`, `Tedarikçi` | Önerilir |
| VKN / TCKN | `Firma VKN Numarası`, `VKN` | Önerilir |

\* En az **fatura numarası** veya **tutar** sütunlarından biri olmalıdır.

### Evrak türü değerleri

Her satırda şunlardan biri yazın:

- `Alış`
- `Satış`
- `İhracat` (büyük **İ** ile yazılabilir)

### Tarih ve tutar formatı

- Tarih tercihen: `2025-05-01` (YYYY-MM-DD)
- `01.05.2025` veya `01/05/2025` yüklerseniz sistem mümkünse **otomatik çevirir**
- Tutar sayı olmalı; `12.500,50` gibi Türk formatı da düzeltilebilir
- VKN yalnızca rakam olmalı (10 veya 11 hane)

## Web sitesinde ayrıca sorulacak belgeler

Bunlar Excel’e yazılmaz; analiz sırasında sistem sorar:

| İade türü | Gerekli belge |
|-----------|----------------|
| İhracat | Gümrük Çıkış Beyannamesi (GÇB) |
| Tevkifat | 2 No’lu KDV Beyannamesi |

Eksik belgeler skoru düşürür ve onay sürecini etkiler.

## Sistem ne yapar?

1. Dosyanızı okur ve sütunları tanır.
2. Mümkün olan hataları **otomatik düzeltir** (tarih, tutar, VKN, eksik fatura no).
3. Düzeltilemeyen sorunlar için **uyarı** gösterir.
4. Kritik hata varsa analizi başlatmanızı engeller.

## Örnek şablon

Web arayüzündeki **“Örnek şablon Excel indir”** düğmesini kullanın veya `assets/templates/iadeajan_fatura_sablonu.xlsx` dosyasını kopyalayın.

## Satır ve sütun düzeyinde hata raporu

Yükleme sonrası sistem, sorunlu hücreleri **Excel satır numarası**, **sütun adı** ve (mümkünse) **sütun harfi** (A, B, C…) ile listeler. Örnek:

> Satır 1847, sütun «Evrak Türü» (C): değer tanınmadı — Alış, Satış veya İhracat yazın.

Tüm satır uyarılarını CSV olarak indirebilirsiniz. Varsayılan üst sınır: **50.000 veri satırı** (`MAX_UPLOAD_ROWS` ortam değişkeni ile değiştirilebilir).

## Sık hatalar

| Hata | Sonuç |
|------|--------|
| Evrak türü sütunu yok | Faturalar “belirsiz” sayılır |
| Bozuk tarih | Veri kalitesi cezası |
| Sıfır veya metin tutar | Ceza veya red riski |
| Geçersiz VKN | Ceza |
| İhracat ama GÇB yok | Belge cezası (−35 puan bandı) |
