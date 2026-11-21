# Açık referans seti

Üç kamu malı eserden altı Latin harfli sayfa. `generate.py`, bu klasördeki referansları dizip yalnızca
görüntü içeren üç PDF üretir; PDF'lerde seçilebilir metin katmanı yoktur. PDF'ler özgün baskıların taraması
değildir. İki sayfa perspektif, gölge ve bulanıklıkla telefon çekimini taklit eder; dipnotlar ölçüm için
sonradan eklenmiştir. Bu set gerçek arşiv taramalarının veya telefon fotoğraflarının yerine geçmez.

| PDF | Kaynak | Sayfalar |
|---|---|---|
| `kasagi.pdf` | Ömer Seyfettin, *Kaşağı*, [Vikikaynak sürümü 172873](https://tr.wikisource.org/w/index.php?title=Ka%C5%9Fa%C4%9F%C4%B1&oldid=172873) | Temiz dizgi, telefon benzetimi |
| `hurriyet.pdf` | Namık Kemal, *Hürriyet Kasidesi*, [Vikikaynak sürümü 173050](https://tr.wikisource.org/w/index.php?title=H%C3%BCrriyet_Kasidesi&oldid=173050) | Temiz dizgi, küçük puntolu ayın/hemze dipnotları |
| `istiklal.pdf` | Mehmet Âkif Ersoy, *İstiklâl Marşı*, [Vikikaynak sürümü 160094](https://tr.wikisource.org/w/index.php?title=%C4%B0stikl%C3%A2l_Mar%C5%9F%C4%B1&oldid=160094) | Temiz dizgi, telefon benzetimi |

Seçili parçaların Latin harfli Vikikaynak dizgisinde satır bölümü ve kimi noktalama işaretleri
uyarlandı; sayfa numaraları özgün esere değil örnek dizgiye aittir. Dijital uyarlamalar ve bunlardan
üretilen görüntüler [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.tr) ile
paylaşılır. Atıf: tabloda belirtilen yazarlar ve Vikikaynak katkıcıları (sürüm geçmişi kaynak bağlantılarındadır).

## Çalıştırma

Pillow ana ortamda kurulu olmalı. Varsayılan font DejaVu Serif (`fonts-dejavu-core`); istenirse
`--font` ile başka bir serif TTF verilebilir. Aynı koşullarda üretilen PDF'leri karşılaştırın:
font/değişen görüntü kodlayıcısı dosya özetini değiştirebilir.

```bash
.venv/bin/python benchmark/generate.py
./ocr-lab output/benchmark-ornek/{kasagi,hurriyet,istiklal}.pdf \
  -p hepsi --referans benchmark/referans --kor-test -e dots.mocr:duzen glm-ocr tesseract
./ocr-lab rapor --html
```

`--kor-test`, GT'den türetilen kelime listesini ve kullanıcı sözlüğünü kapatır; ayın/hemze kuralları
yürürlükte kalır. Bu referansları `gt/` klasörüne ya da `ayarlar/kesme.txt`'ye kopyalamayın; kural
değişikliğini bu sayfalara bakarak ayarlarsanız artık bağımsız değerlendirme için yeni metin seçin.
Buradaki altı örnek, bir motorun genel doğruluğunu veya üretim varsayılanını tek başına belirlemez.
