from pathlib import Path

import pandas as pd


# Ortak test verisi — xlsx-01, csv-04 ve xls-05 için aynı içerik
KARMA_DATA = {
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

XLSX_DATASETS = {
    "test_excel_01_karma.xlsx": KARMA_DATA,
    "test_excel_02_eksik_sutun.xlsx": {
        "Belge Kimliği": ["MX-001", "MX-002", "MX-003"],
        "İşlem Tarihi": ["2025-04-02", "2025-04-07", "2025-04-16"],
        "Karşı Taraf Ünvanı": ["Vendor A", "Vendor B", "Vendor C"],
        "Yekün (TRY)": [21000, 17000, 9800],
    },
    "test_excel_03_karisik_tipler.xlsx": {
        "Belge Kimliği": ["Z-01", "Z-02", "Z-03", "Z-04"],
        "İşlem Tarihi": ["2025-03-01", "bozuk_tarih", "2025/03/19", None],
        "Evrak Türü": ["Alış", "Satış", "İhracat", 123],
        "Yekün (TRY)": [5000, "12000", None, "yanlis"],
        "Kesilen Vergi Zımbırtısı": [20, "20", "x", None],
        "Karşı Taraf Ünvanı": ["Tedarikçi 1", "Tedarikçi 2", "", "Tedarikçi 4"],
        "Firma VKN Numarası": ["1111111111", None, "abc", "9999999999"],
    },
}


def _write_xls(df: pd.DataFrame, path: Path) -> bool:
    """
    Gerçek .xls (BIFF8) formatı üretir.
    xlwt varsa kullanır; yoksa xlsxwriter ile xlsx üretip uzantıyı .xls yapar
    (xlrd>=2.0 bu dosyayı okuyamaz, test SKIP olarak işaretlenir).
    Dönüş değeri: True → gerçek xls, False → sahte (xlsx içerik, xls uzantı).
    """
    try:
        import xlwt  # type: ignore[import]
        workbook = xlwt.Workbook(encoding="utf-8")
        sheet = workbook.add_sheet("Sayfa1")
        for col_idx, col_name in enumerate(df.columns):
            sheet.write(0, col_idx, col_name)
        for row_idx, row in enumerate(df.itertuples(index=False), start=1):
            for col_idx, value in enumerate(row):
                sheet.write(row_idx, col_idx, value if value is not None else "")
        workbook.save(str(path))
        return True
    except ImportError:
        # xlwt yok — xlsx içeriği .xls uzantısıyla yaz (xlrd okuyamaz, SKIP)
        df.to_excel(path.with_suffix(".xlsx"), index=False, engine="openpyxl")
        path.with_suffix(".xlsx").rename(path)
        return False


def main() -> None:
    hedef_klasor = Path("test_excels")
    hedef_klasor.mkdir(parents=True, exist_ok=True)

    # --- XLSX dosyaları ---
    for dosya_adi, data in XLSX_DATASETS.items():
        df = pd.DataFrame(data)
        hedef_yol = hedef_klasor / dosya_adi
        df.to_excel(hedef_yol, index=False, engine="openpyxl")
        print(f"✅ {hedef_yol} oluşturuldu (xlsx)")

    # --- CSV dosyası (UTF-8 BOM — Windows Excel uyumlu) ---
    df_csv = pd.DataFrame(KARMA_DATA)
    csv_yol = hedef_klasor / "test_excel_04_karma.csv"
    df_csv.to_csv(csv_yol, index=False, encoding="utf-8-sig")
    print(f"✅ {csv_yol} oluşturuldu (csv)")

    # --- XLS dosyası ---
    df_xls = pd.DataFrame(KARMA_DATA)
    xls_yol = hedef_klasor / "test_excel_05_karma.xls"
    gercek_xls = _write_xls(df_xls, xls_yol)
    if gercek_xls:
        print(f"✅ {xls_yol} oluşturuldu (xls — gerçek BIFF8 formatı)")
    else:
        print(
            f"⚠️  {xls_yol} oluşturuldu (xlwt yok — xlsx içerik, xls uzantı; "
            "xlrd okuyamaz, test SKIP olacak)"
        )

    toplam = len(XLSX_DATASETS) + 2  # 3 xlsx + 1 csv + 1 xls
    print(f"\nToplam {toplam} adet test dosyası hazır: {hedef_klasor}/")


if __name__ == "__main__":
    main()
