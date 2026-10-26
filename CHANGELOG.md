# Değişiklik geçmişi

Bu dosyadaki bütün önemli değişiklikler burada listelenir.
Biçim [Keep a Changelog](https://keepachangelog.com/tr-TR/1.1.0/) örneğini izler, sürümler
[Semantic Versioning](https://semver.org/lang/tr/) kurallarına uyar.

## [Yayımlanmadı]

### Düzeltildi
- Kitap sayfa numarası (gerçek bir 24 sayfalık, kitabın farklı yerlerinden ters sırayla taranmış belgeyle
  denendi):
  - Sayfadan okunan numaraya güvenilir; komşularıyla tutmadığı için köşeli paranteze alınmaz.
  - Okumalardan kitabın taraf–çiftlik kuralı (çift numara solda ya da sağda) öğrenilir. Kurala aykırı okuma
    okunduğu gibi kalır ama köşeli parantezle yazılır ve "sayfanın tarafıyla çelişiyor" uyarısı alır.
  - Azalan koşular (412, 411, 410) da tanınır; koşu içindeki tek aykırı okuma iki yönde de düzeltilir.
  - Okunamayan numara yalnız sayfa bir koşunun içindeyse ya da en az iki okumalı bir koşunun hemen devamıysa
    tahmin edilir. Tahmin, sayfanın başlığından öğrenilen çiftlikle (ör. yazar adı hep çift sayfada) çelişirse
    yapılmaz, `[PDF s. N]` yazılır. Seçimin başındaki numarasız sayfalar ilk koşudan geriye doğru sayılır; iki
    koşu arasındaki boşluk sonraki koşudan sayılmaz.
  - Koşu içinde ters yöndeki komşuyla tesadüfen eşleşen yanlış okuma (412, 413, 412, 415) da düzeltilir.
  - İş sonunda tarafla çelişen okunan numaralar, tahmin edilenlerden ayrı listelenir.
  - Aynı numara birden fazla sayfada okunduysa her birine "aynı sayfa iki kez taranmış olabilir" uyarısı eklenir.
  - `--ilk-sayfa` verildiğinde davranış değişmedi.
- Günümüz Türkçesi sayfalarında özel addan sonra gelen y'li ve yaygın ekler (`Kahire’ye`, `İstanbul’daki`,
  `Ankara’dır`, `Türkiye’nin`, `Osmanlı’lar`) artık hemze (’) alıyor; önceden ‘ yazılıp sarıya boyanıyordu.
  Ayınlı özel adlar (`Ka‘be`, `Sa‘d`) etkilenmez.
- Modelin italik/kalın için kullandığı Markdown yıldızları (`*a.g.e.*`, `**Sâniyen:**`) artık belgeye sızmıyor:
  `.docx`'te italik/kalın yazılıyor, `.txt`'de düz metin oluyor. Tek başına duran yıldıza dokunulmuyor.

### Değişti
- Önbellek artık işlenmiş sayfayı değil, OCR çıktısını (ham model cevabı, tesseract TSV, görüntü boyutu) saklıyor.
  Sayfa yapısı her çalıştırmada bundan yeniden kuruluyor; kod, `ayarlar/kesme.txt` ve `--kesme-yok` değişiklikleri
  önbellekteki sayfalara GPU'suz uygulanıyor. `.kesmesiz` önbellek dosyaları artık kullanılmıyor. Eski önbellek
  dosyaları tanınmıyor: o sayfalar bir kez yeniden okunuyor.

## [0.1.0] - 2026-10-26

İlk sürüm. Tek dosyalık `bench.py` karşılaştırma tezgâhı, ortak bir çekirdeği (`src/ocrnow/`) paylaşan iki komuta
dönüştürüldü.

### Eklendi

**`ocr-now` belge üretme komutu**
- Komut iki şekilde kullanılır: seçeneksiz başlatılınca adım adım sorar, seçeneklerle soru sormadan çalışır.
  Format ve sarı işaretleme iş bitince sorulur.
- Girdi tek dosya, birden fazla dosya ya da klasör olabilir. PDF dışında PNG, JPG ve TIFF de kabul edilir.
- Sayfa seçimi şu biçimleri alır: `hepsi`, `5`, `1,3,7`, `10-25`, `ilk10`. PDF'i aşan aralık kırpılır ve uyarı
  verilir. Açılamayan PDF için Türkçe bir hata mesajı gösterilir.
- Gruplama:
  - Her PDF ayrı bir belge olur.
  - Bir klasördeki görüntüler doğal sırayla tek belgede birleşir.
  - `--birlestir` bütün girdileri tek belgede toplar.
- Telefonla çekilmiş fotoğraflardaki yön bilgisi (EXIF) uygulanır. 16 bitlik gri görüntüler doğru okunur.

**Kitap sayfası görünümünde `.docx` çıktısı**
- Her taranmış sayfa ayrı bir Word bölümüdür. Kitabın sayfa numarası o bölümün altlığında, kitaptaki tarafta durur.
- Satır kırılımları geri getirilir. dots.mocr'un metni tesseract satır kutularına karakter düzeyinde hizalanır ve
  satır sonu tireleri kitaptaki yerlerine konur.
- Kitap başlığı ortalı ve küçük puntoyla durur. Dipnotlar çizgi altında, numaraları üst simgedir. Paragraf
  girintileri korunur.
- Nüsha işaretleri (`[106b …]`) kalın yazılır, satır sonunda bölünmüş olsalar bile. Kalıp `--kalin-kalip` ile
  değiştirilebilir ya da kapatılabilir.
- Punto her sayfa için en uzun satır sığacak şekilde seçilir (10.5–8 pt).

**Ham `.txt` çıktısı**
- Paragraflar tek satırdır, satır sonu tireleri birleştirilmiştir.
- Sayfalar arasında `--- s. 412 ---` ayracı bulunur.

**Sarı işaretleme**
- dots.mocr ile tesseract'ın farklı okuduğu kelimeler sarıya boyanır. tesseract'ın sistematik î/û kaybı yanlış alarm
  sayılmaz. Örnek sayfada kelimelerin %7'si işaretlendi ve bilinen 5 model hatasının 5'i de yakalandı.
- İşaretleme `--isaretle` / `--isaretleme` ile açılıp kapatılabilir.

**Ayın/hemze (‘ ’) geri getirme**
- Ayrımı kurallar ve bir kelime listesi yapar. Liste `gt/` dosyalarından çıkarılır ve `ayarlar/kesme.txt` ile
  genişletilebilir.
- Karar verilemeyen işaret sarıya boyanır. Adım `--kesme-yok` ile kapatılabilir.

**Kitap sayfa numarası**
- Numara altlıktan ya da başlıktan okunur. Roma rakamları da kabul edilir.
- Okunamayan ya da aykırı numara komşu sayfalardan, PDF sayfa sırasına göre tahmin edilir ve `[413]` biçiminde
  işaretlenir.
- `--ilk-sayfa` ile numaralar verilen değerden başlatılabilir.

**Önbellek**
- Sayfa yapısı `output/_cache/` altına yazılır. Başka bir format istendiğinde OCR yeniden çalışmaz.
- Bütün sayfalar önbellekteyse model sunucusu hiç açılmaz.

**`ocr-now models`**
- Önerilen modelleri GPU belleği ve disk ihtiyacıyla listeler. ★ ile işaretli olanlar önerilenlerdir.
- `models install [--hepsi | ad…]` tek komutla kurar. Kurulu adımlar atlanır. GPU'ya sığmayabilecek model için
  uyarı verilir.

**`ocr-lab` benchmark komutu**
- Motor ve okuma modu birlikte test edilir (ör. `dots.mocr:duzen`, `dots.mocr:duz`).
- Şu ölçüler hesaplanır: ham, normalize, gövde ve harf CER, WER. Yeni ölçüler: satır sonu doğruluğu, ayın/hemze
  doğruluğu, sarıya düşen işaret sayısı.
- Her koşu `output/lab/<tarih>/results.json` olarak saklanır, `summary.csv` bütün koşuları biriktirir.
- `ocr-lab rapor [--html]` marimo analiz notebook'unu açar. Notebook'ta ölçü tablosu, süre–CER grafiği, kelime
  hataları ve sayfa görüntüsü yer alır.

**Uyarılar ve güvenlik**
- Model çıktısı kesik ya da bozuk JSON olduğunda okunabilen bloklar kurtarılır ve metin kaybolmaz.
- Uzunluk sınırında kesilen model çıktısı uyarılır.
- Satır yapısı kurulamayan blok akan paragrafa düşer. Uyarı sayfa ve bloğu belirtir.
- Okunamayan sayfa belgede `[Bu sayfa okunamadı]` olarak durur. Hiçbir sayfası okunamayan belge yazılmaz.
- Çıktı adı sayfa seçimini taşır (`kitap_s11-20.docx`). Var olan belgenin üzerine yazılmaz.
- GPU kilidi (`output/_logs/gpu.lock`): aynı anda iki GPU işi açılamaz.
- tesseract kurulu değilse iki komut da baştan durur.

**Altyapı**
- Paket yapısı `src/ocrnow/` altında. Bütün yollar `paths.py`'den gelir ve `OCRNOW_ROOT` ile değiştirilebilir.
- pytest takımı 396 testten oluşuyor. Kitap metni gerektiren testler, veri yoksa atlanır.
- Tasarım belgesi ve uygulama planı `docs/` altında.

### Değişti
- Kurulum: `kurulum.sh` artık yalnız ana ortamı kurar, gerisini `ocr-now models install --hepsi`'ye devreder.
  Sürümler ve vLLM yaması aynı kaldı.
- Sunucu komutları ve istemler `bench.py`'deki `VLM` sözlüğünden `src/ocrnow/engines.py`'deki `ENGINES` kaydına
  taşındı.
- Sunucu komutları artık boşluk içeren klasör yollarında da çalışıyor.
- Sunucu açılırken Ctrl-C'ye basılırsa model GPU'da açık kalmıyor.
- `ocr-now` ve `ocr-lab` sarmalayıcıları her klasörden çalışır. Göreli dosya yolları, komutun çalıştırıldığı
  klasöre göre çözülür.

### Kaldırıldı
- `bench.py` ve `run.sh`. Yerlerini `ocr-lab` ve `ocr-now` aldı.
