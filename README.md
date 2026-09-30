# Oyun Ekran Çevirici (İngilizce → Türkçe) - Hafif Sürüm

Oyun oynarken tek tuşla ekrandaki İngilizce yazıları Türkçeye çevirir.
Windows'un yerleşik OCR motorunu kullanır, tek dosya ve küçüktür.

- **F8:** Ekranı çevir / çeviriyi kapat
- **F9:** Programdan çık

## Kullanım
1. [Releases](../../releases) sayfasından `OyunCeviri.exe` dosyasını indir.
2. Çift tıkla (yönetici izni ister, oyunda tuşun çalışması için gerekli).
3. Oyunu **Pencereli tam ekran (Borderless)** modunda aç.

Çeviri için internet gerekir. Windows'ta İngilizce dil paketi kurulu olmalıdır
(Ayarlar → Zaman ve Dil → Dil ve bölge → İngilizce ekle).

## Kaynaktan çalıştırma
```
pip install -r requirements.txt
python oyun_ceviri.py
```
