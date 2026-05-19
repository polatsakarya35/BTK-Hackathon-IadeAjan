import pandas as pd

# Bilerek bozuk, standart dışı sütun isimleri kullanıyoruz
data = {
    "Belge Kimliği": ["FAT-001", "FAT-002", "FAT-003", "FAT-004"],
    "İşlem Tarihi": ["2025-05-01", "2025-05-05", "2025-05-10", "2025-05-12"],
    "Evrak Türü": ["İhracat", "İhracat", "Satış", "Alış"],
    "Yekün (TRY)": [45000, 32000, 15000, 8000],
    "Kesilen Vergi Zımbırtısı": [0, 0, 20, 20],
    "Karşı Taraf Ünvanı": [
        "Global Tech LLC",
        "Alpha GMBH",
        "Bursa Demir Çelik A.Ş.",
        "Ankara Lojistik Ltd.",
    ],
    "Firma VKN Numarası": [None, None, "1112223334", "9998887776"],
}

df = pd.DataFrame(data)

# Excel olarak kaydet
dosya_adi = "karmaşık_fatura_listesi.xlsx"
df.to_excel(dosya_adi, index=False)

print(f"✅ {dosya_adi} başarıyla oluşturuldu! Şimdi bu dosyayı Streamlit arayüzünden yükle.")
