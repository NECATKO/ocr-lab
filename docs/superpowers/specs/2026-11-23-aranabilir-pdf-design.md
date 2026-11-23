# Aranabilir PDF — tasarım

Tarih: 2026-11-23

## Amaç ve neden

Kullanıcı kitap sayfasını CamScanner PDF'si olarak veriyor. Çıkan PDF'den **metin seçip kopyalamak**
istiyor: sayfaya bakarken bir pasajı seçip başka yere yapıştırmak. PDF'yi hem **telefonda** (Drive ya da
Telegram'dan gelen PDF) hem de **bilgisayarda** (Windows'ta tarayıcı ya da PDF okuyucu) açacak.

Yapıştırılan metin **alıntıya hazır** olmalı. Satır sonunda tireyle bölünmüş kelime bütün gelmeli ("senâ-/kâr"
→ "senâkâr"). Mümkünse satırlar tek paragraf olarak birleşmeli.

## Bugünkü durum

- Aranabilir PDF `ocr-now -f pdf` ve ocr-gui'deki "Aranabilir PDF" kutusuyla zaten var
  (`src/ocrnow/export_pdf.py`). Elle yazılmış bir PDF yazıcısı. Sayfayı 200 DPI JPEG olarak yeniden
  sıkıştırıyor. Üstüne görünmez bir Type0/Identity-H katmanı koyuyor; ToUnicode var, gömülü font yok.
- Sorunlar:
  1. Her blok, blok kutusunun alt kenarına **tek uzun satır** olarak yazılıyor ve sayfa dışına taşıyor.
     Arama kelimeyi buluyor ama vurgu ve seçim görüntüyle örtüşmüyor.
  2. Görüntü yeniden sıkıştırılıyor: kalite kaybı var, dosya da büyüyor.
  3. RTL satırlar karakter karakter ters çevriliyor. Kopyalanan Arapça metin büyük olasılıkla ters çıkıyor.
  4. `write_pdf`, `--dpi`'ı yok sayıp her zaman varsayılan DPI ile işliyor. Okuma başka bir DPI ile yapıldıysa
     katman kayıyor.
- Telefon/VPS akışında PDF yok: `colab/runner.py` `formatlar="docx txt"` ile çağrılıyor.

## Hedefler

- G1. Girdi PDF ise çıktı sayfası **orijinal PDF sayfasının kendisi**. İçerik akışları, görüntüleri ve
  boyutu değişmiyor. Üstüne yalnız görünmez metin katmanı ekleniyor.
- G2. Görünmez metin **satır satır** görüntüdeki satırın üstüne oturuyor. Her satırın sol kenarı, taban
  çizgisi ve genişliği tesseract satır kutusuyla örtüşüyor. Genişlik yatay ölçekle (`Tz`) tutturuluyor.
  Seçim vurgusu ve arama vurgusu gerçek satırın üstünde çıkıyor.
- G3. **Tiresiz birleştirme.** Satır sonunda bölünen kelime (`Line.hyphen == True`, yani metinde birleşik
  olan kelime) katmanda ilk satıra **bütün olarak** yazılıyor; ikinci satır kelimenin geri kalanını
  atlayıp ondan sonra başlıyor. Kitapta zaten var olan tire ("Defter-dâr") olduğu gibi kalıyor.
- G4. Arapça/Osmanlıca satırlar mantıksal sırada yazılıyor ve doğru sırayla kopyalanıyor (bkz. Doğrulama).
- G5. Telefon/VPS akışı Word'ün yanında PDF de üretiyor. PDF Drive'daki `output/` klasörüne yazılıyor ve
  Telegram'a gönderiliyor.
- G6. `-f pdf` ve ocr-gui aynı yeni yazıcıyı kullanıyor.

## Hedef olmayanlar

- PDF/A uyumluluğu, PDF içinde sarı belirsizlik işaretleri, yer imleri ve içindekiler.
- Görüntü iyileştirme, yeniden kırpma ya da sıkıştırma.
- OCR'ın kendisini değiştirmek.

## Asla olmaması gerekenler

- Orijinal sayfanın görünümünün değişmesi: kalite kaybı, kırpılma ya da dönme.
- Görünmez metnin görünür hâle gelmesi.
- Yazıcının OCR'ı ya da Word çıktısını bozması. PDF yazılamazsa öbür biçimler yine yazılır ve uyarı verilir.
  Bunu bugün `_write_job` zaten yapıyor; aynı davranış korunacak.
- Telefon akışında PDF'nin sessizce eksik kalması. Yazılamadıysa bildirimde uyarı olarak görünür.

## Kısıtlar

- Yeni bağımlılık: `pikepdf`. Projenin ana bağımlılıklarına eklenecek. Colab kurulumu `-e` ile proje
  bağımlılıklarını kurduğu için ayrıca bir adım gerekmiyor. VPS'e kurulmuyor; VPS OCR yapmıyor, yalnız
  dosyaları taşıyor.
- Colab ya da VPS'te canlı deneme Colab birimi harcar. Yapılmadan önce kullanıcıya sorulacak (AGENTS.md).
- Kullanıcının özel taramaları ve telifli metinler Git'e eklenmeyecek. Testler sentetik sayfalarla yazılacak.

## Yollar (seçilen: A)

- **A. Orijinal PDF'nin üstüne katman eklemek (pikepdf). SEÇİLDİ.** Sayfa bayt bayt aynı kalır. Yalnız
  yeni bir içerik akışı ve font kaynağı eklenir. Bedeli: bir bağımlılık eklemek ve sayfa geometrisini
  (`/Rotate`, CropBox/MediaBox başlangıcı) doğru çevirmek.
- B. Bugünkü yazıcıyı düzeltmek. Bağımlılık gerekmiyor, ama görüntü yeniden sıkıştırılıyor; kullanıcının
  amacına ters. Elendi.
- C. OCRmyPDF. Oturmuş bir araç, ama ağır bir bağımlılık getiriyor (Ghostscript). Tiresiz birleştirme ve
  satır başına kendi metnimizi yazma üzerinde denetimimiz azalıyor. Elendi.

## Tasarım

### Çıktı belgesini kurmak

- Çıktı, işteki `Page` listesinin sırasını izler. `--birlestir`, sayfa seçimi ve birden fazla girdi bugünkü
  gibi çalışır.
- **PDF kaynaklı sayfa:** Kaynak PDF'nin `page.index` numaralı sayfası pikepdf ile yeni belgeye kopyalanır.
  Üstüne katman eklenir.
- **Görsel kaynaklı sayfa** (PNG/JPG/TIFF…): Görselden yeni bir sayfa kurulur. Görüntü mümkünse yeniden
  kodlanmadan gömülür; JPEG için DCTDecode kullanılabilir. Gerekirse kayıpsız (Flate) gömülür. Sayfa boyutu
  görselin DPI bilgisinden alınır, bilgi yoksa varsayılan DPI kullanılır.
- **Okunamamış sayfa** (`page.failed`): Orijinal sayfa yine konur, katman eklenmez, uyarı verilir. Kaynak
  dosya artık yoksa bugünkü gibi boş bir yer tutucu sayfa konur.
- **Kaynakta zaten metin varsa** (ör. CamScanner'ın kendi OCR'ı açıkken dışa aktarılmış sayfa): O sayfaya
  kendi katmanımız eklenmez ve uyarı verilir. Kopyada metin iki kez gelmesin diye böyle yapılır. Eski metni
  içerik akışından silmek G1'e aykırı olur. *Yapanın kararı:* Kullanıcının olağan CamScanner çıktısında
  (`input/tarama1.pdf`, üretici intsig.com) metin katmanı yok. Bu yüzden bu yol nadir bir durum için uyarıdır.
  Metin varlığı pdfium ile sayfadan metin çıkarılarak denetlenir; birkaç karakterlik eşik kullanılır.

### Koordinatlar

- `Page.width/height`, okunan görüntünün piksel boyutu. Ölçek, sayfanın görünen boyutundan (CropBox, dönme
  sonrası) hesaplanır: `pt_per_px = görünen_genişlik_pt / page.width`. Böylece okuma hangi DPI ile
  yapılmış olursa olsun katman oturur (sorun 4 kendiliğinden kalkar).
- Okuma görüntüsü pdfium'un dönmüş ve kırpılmış görüntüsü. Katman, sayfanın kendi koordinat sistemine geri
  çevrilir: `/Rotate` 0/90/180/270 ve CropBox başlangıcı hesaba katılır.

### Satır yerleşimi

- Her metin bloğunda `block.lines` (karakter aralıkları), tesseract satır kutularıyla eşleşir. Bu eşleşme
  `apply_line_structure`/`rebreak`'te kuruluyor. Yazıcı, her satırın kutusuna erişebilmeli. Öneri:
  `Line`'a isteğe bağlı bir `box` alanı eklemek. Bu alan JSON'a da girer. Önbellek yalnız ham OCR'ı
  tuttuğu için önbellek bozulmaz. Yöntem yapıcıya bırakılmıştır.
- Satır metni, `block.text[line.start:line.end]` aralığıdır. G3 için bu aralık şöyle düzeltilir: hyphen=True
  olan satır, kelimenin sonuna kadar uzatılır. Sonraki satır o kelimenin bitişinden ve boşluklardan sonra
  başlar.
- Satır, kutunun sol-alt köşesine taban çizgisiyle yerleşir. Font boyu kutu yüksekliğinden gelir. Genişlik
  `Tz` (yatay ölçek) ile kutu genişliğine eşitlenir. Kelimeler arasında boşluk karakteri bulunur; kopyada
  kelimeler bitişmez.
- Satır yapısı kurulamamış bloklarda (tek `Line(0, len)`, ya da tesseract kapalı/yok) metin blok kutusunun
  içine eşit satırlara bölünür; kutu da yoksa sayfanın sol üstüne konur. Görsel örtüşme zayıf olur ama
  metin aranabilir ve kopyalanabilir kalır.
- Hangi bloklar katmana girer: metin taşıyan bütün bloklar. Başlık, gövde, dipnot, sayfa numarası ve üst
  bilgi kitaptaki yerinde durur. Seçim görüntüye göre yapıldığı için okuma sırası sorun olmaz. `Picture`
  blokları girmez.
- Ayın/hemze düzeltmesi ve model metni neyse katmana o girer, yani Word'deki metnin aynısı. Biçim işaretleri
  (italik/kalın) katmanda yok sayılır.

### Font

- Görünmez metin (`3 Tr`). Kullanılan font, ToUnicode haritalı ve glifsiz bir Type0/Identity-H font: bugünkü
  "HiddenUnicode" ya da tesseract/OCRmyPDF'teki GlyphLessFont benzeri. Her glif genişliği sabittir.
  Seçimin her karakterde doğru yeri kaplaması için `Tz` bu sabit genişliğe göre hesaplanır.
- Okuyucular gömülü font dosyası olmayan fontta sorun çıkarırsa (pdf.js uyarısı, Android'de seçilememe) küçük
  bir glifsiz TrueType gömülür. Hangisinin gerektiği doğrulamada ortaya çıkar (bkz. Doğrulama).

### Paragrafı tek parça kopyalamak (deneysel)

- Birden fazla satırı seçip kopyalayınca okuyucular (pdfium/Chrome/Android, pdf.js, Acrobat) genelde satır
  sonuna satır kırılımı koyar. Bunu doğrudan denetleyemeyiz.
- Denenecek: her paragrafı `/Span << /ActualText (...) >> BDC … EMC` ile sarmak. Acrobat bunu dikkate alıyor,
  öbürleri belirsiz.
- **Kural:** ActualText yalnız seçimi ve aramayı hiçbir hedef okuyucuda bozmuyorsa açık kalır. Bozuyorsa
  kapatılır, satır satır kopya kabul edilir. Sonuç kullanıcıya açıkça söylenir. Tiresiz birleştirme (G3) her
  durumda geçerlidir.
- *Yapanın kararı:* İlk sürümde ActualText **kapalı** gelir. Yazıcıdaki bir parametreyle açılabilir; CLI
  bayrağı yoktur. Acrobat'ta paragrafın yarısı seçilince bile bütün paragraf kopyalanabiliyor. Bu, pasaj
  seçme amacını bozabilir. Kullanıcı telefonda ve Windows'ta elle denedikten sonra açılıp açılmayacağına
  karar verilir. Push her zaman olduğu gibi kullanıcı onayıyla yapılır.

### RTL (Arapça/Osmanlıca)

- Satır metni mantıksal sırada yazılır, bugünkü `[::-1]` kaldırılır. Okuyucuların çıkarımı geometriye göre
  sıraladığı için yön; glif konumu, `Tz` ya da gerekirse satır için ActualText ile ayarlanır. Doğru yöntem
  pdfium ve pdf.js çıkarımıyla ölçülerek seçilir. Ana kullanım Türkçe Latin yazılı kitaplar; RTL ikincil.

### Telefon/VPS akışı

- `colab/runner.py` telefon işinde `formatlar`'a `pdf` eklenir. Belge Drive `output/`'a yazılır.
- `vps/izleyici.py`'deki `gonderilecek_belgeler` PDF'yi de Telegram'a gönderir. `.txt` dışında öbür biçimler
  zaten gönderiliyor; bunun testle doğrulanması yeterli.
- PDF yazılamazsa iş "kısmen tamamlandı" olur, ntfy/Telegram bildiriminde uyarı görünür. *Yapanın
  kararı:* Girdi bugünkü gibi `islenenler/`'e taşınır, uyarı yeterlidir. PDF yazıcısı OCR'a bağlı değildir;
  hata deterministiktir ve yeniden denemek düzeltmez. Gerekirse kullanıcı bilgisayarda `-f pdf` ile
  önbellekten yeniden yazdırır.
- *Yapanın kararı:* Telegram'a gönderilemeyecek kadar büyük PDF (Hermes/Bot API sınırı, ~50 MB) gönderilmez.
  Telegram mesajına "PDF büyük, Drive'daki output/ klasöründe" notu düşülür. Bu, işi "kısmen" yapmaz;
  belge Drive'da yazılmıştır.
- README ve `vps/hermes/ocr-lab/SKILL.md`'deki "Word gelir" anlatımı "Word ve aranabilir PDF gelir" olarak
  güncellenir.

## Doğrulama

- Otomatik testler pypdfium2 ile, sentetik sayfalarla yapılır:
  - Kaynak sayfanın içerik ve görüntü baytları çıktıda değişmeden duruyor.
  - Çıkarılan metin beklenen metne eşit. Tireli kelime bütün çıkıyor, ikinci satırdaki parçası tekrar etmiyor.
  - Her satırın karakter kutuları tesseract satır kutusunun içinde, küçük bir pay ile.
  - `/Rotate` 90 ve CropBox kaydırmalı sayfada da kutular doğru yerde.
  - DPI 300 ile okunmuş sayfada da kutular doğru yerde.
  - Görsel girdiden PDF kuruluyor.
  - Okunamamış sayfa katmansız konuyor ve uyarı veriliyor.
  - Arapça satır doğru sırayla çıkıyor.
- Elle kontrol (kullanıcı): Gerçek bir CamScanner PDF'si yerelde okutulur. Çıkan PDF telefonda (Drive ve
  Telegram önizlemesi) ve Windows'ta (Edge/Chrome) açılıp üç şey denenir: arama vurgusu, satır seçimi ve çok
  satırlı kopya. Bu, ActualText ve font kararlarını verir. Colab'da deneme yapılacaksa önce sorulur.

## Yapıcıya bırakılanlar

- Satır kutusunu yazıcıya ulaştırma yöntemi (`Line.box` önerisi).
- Glifsiz fontun tam yapısı. Bugünkü yapı yeniden kullanılabilir ya da küçük bir TrueType gömülebilir.
- Görsel girdilerin gömülme biçimi.
- Modül yapısı. Mevcut `export_pdf.py` yeniden yazılır, eski elle yazılmış PDF kodu kaldırılır.

## Ek (2026-11-23): Kelime düzeyinde yerleşim

**Neden:** Kullanıcı Windows'ta deneme yaptı. Satır kutusu kurulamayan bloklarda (gerçek taramada 188 bloğun 25'i)
arama vurgusu kelimenin bir satır altına kaydı. Bu bloklarda tesseract bir satırı kaçırıyor ya da kenardaki süs
okumayı bozuyor; metin bloğa yaklaşık yayıldığı için kelimeler satır içinde kayıyordu. Kopyalama ve tireli kelime
birleştirme doğru çalıştı. Kullanıcı satırı esnetmek yerine kelime kelime yerleştirmeyi seçti.

**Karar:**
- Ana modelin her kelimesi, tesseract TSV'deki karşılık gelen kelime kutusuna (level 5) konur.
- Eşleşme, blok metni ile bloğa atanmış tesseract kelimeleri arasında karakter düzeyinde Levenshtein hizalamasıyla
  (`align._align_map`) bulunur.
- Bu yerleşim, kutusu olan ve olmayan bütün metin bloklarında kullanılır. Word çıktısı değişmez.
- Tireyle bölünmüş kelime bütün olarak yalnız ilk satırdaki kutuya yazılır.
- Eşleşmeyen kelimeler komşu kelimelerin arasına yerleştirilir. Tesseract bütün bir satırı kaçırdıysa, iki satırın
  arasına sentetik bir satır kurulur.
- Hiç tesseract kelimesi olmayan bloklar eski yayma yöntemiyle yerleştirilir.
- Kopyada kelimeler arası boşluk korunur: her kelimenin sonuna bir boşluk eklenir ve kutusu sonraki kelimeye kadar
  uzatılır.
