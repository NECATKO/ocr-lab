# OCR Lab — proje notları

## Gerçek kullanım akışı

Kullanıcı kitap sayfalarını telefonla olabildiğince net çeker, CamScanner'da düzeltir ve çıkan PDF'yi OCR Lab'a verir. Doğrulamada ana girdi ham fotoğraf değil, kullanıcının normal ayarlarla dışa aktardığı **CamScanner PDF'sidir**.

## Bekleyen çalışma: gerçek taramalarla doğrulama

- Birkaç kitaptan 20–30 CamScanner sayfası seç: temiz gövde metni, yoğun dipnot, cilt kenarında gölge/eğrilik ve ayın/hemze içeren metin. Kural geliştirme sayfalarını son değerlendirme setinden ayrı tut.
- Görüntüyle karşılaştırarak atlanan satır/dipnotları, yanlış kitap sayfa numaralarını ve uyarısız eksik çıktıları say. Ham fotoğrafa yalnız hatanın çekim, CamScanner veya OCR kaynaklı olduğunu ayırmak gerektiğinde dön.
- `benchmark/` içindeki altı yeniden dizilmiş sayfa bu gerçek kullanım doğrulamasının yerine geçmez. Kullanıcının özel taramalarını ve telifli referans metinlerini Git'e ekleme.

## Okuma ortamı

- Okuma yalnız yerelde yapılır: ev bilgisayarında `ocr-now` / `ocr-gui` (8 GB GPU, fp8). Google Colab, bulut GPU
  ve VPS/Telegram telefon akışı 2026-11-28'de kaldırıldı; kullanıcı istemedikçe yeniden eklemeyin.
