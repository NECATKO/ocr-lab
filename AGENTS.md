# OCR Lab — proje notları

## Gerçek kullanım akışı

Kullanıcı kitap sayfalarını telefonla olabildiğince net çeker, CamScanner'da düzeltir ve çıkan PDF'yi OCR Lab'a verir. Doğrulamada ana girdi ham fotoğraf değil, kullanıcının normal ayarlarla dışa aktardığı **CamScanner PDF'sidir**.

## Bekleyen çalışma: gerçek taramalarla doğrulama

- Birkaç kitaptan 20–30 CamScanner sayfası seç: temiz gövde metni, yoğun dipnot, cilt kenarında gölge/eğrilik ve ayın/hemze içeren metin. Kural geliştirme sayfalarını son değerlendirme setinden ayrı tut.
- Görüntüyle karşılaştırarak atlanan satır/dipnotları, yanlış kitap sayfa numaralarını ve uyarısız eksik çıktıları say. Ham fotoğrafa yalnız hatanın çekim, CamScanner veya OCR kaynaklı olduğunu ayırmak gerektiğinde dön.
- `benchmark/` içindeki altı yeniden dizilmiş sayfa bu gerçek kullanım doğrulamasının yerine geçmez. Kullanıcının özel taramalarını ve telifli referans metinlerini Git'e ekleme.

## Telefon / VPS akışı

- Asıl kullanım: kullanıcı PDF'yi Drive'daki `ocr-lab/input/` klasörüne atar. Kendi VPS'indeki izleyici
  (`vps/izleyici.py`, systemd kullanıcı zamanlayıcısı) dosyayı Colab L4'te okutur. Çıktı `ocr-lab/output/`
  klasörüne yazılır ve ntfy bildirimi gelir. Okuma her zaman Colab'da bf16 ile yapılır; ev bilgisayarı bu akışa katılmaz.
- `vps/colab_is.py` Colab makinesinde çalışan bir şablondur; `colab/runner.py` içindeki `telefon_calistir`'ı
  kullanır. Bu dosyalardaki değişiklikler ancak push edildikten sonra VPS'e geçer. VPS her çalışmadan önce
  `git pull` yapar.
- VPS'te ya da Colab'da canlı deneme yapmak Colab birimi harcar. Bunun için önce kullanıcıya sorun.
