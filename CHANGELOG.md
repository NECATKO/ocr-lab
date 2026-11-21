# Değişiklik geçmişi

Bu dosyadaki bütün önemli değişiklikler burada listelenir.
Biçim [Keep a Changelog](https://keepachangelog.com/tr-TR/1.1.0/) örneğini izler, sürümler
[Semantic Versioning](https://semver.org/lang/tr/) kurallarına uyar.

## [Yayımlanmadı]

### Eklendi
- **Geliştirilmiş Linux ve Windows (WSL2) kurulumu (`kurulum.sh`, `kurulum.bat`, `kurulum.ps1`, `.gitattributes`):**
  `kurulum.sh` artık `curl`, `tar`, `tesseract` (+ Türkçe `tur` dil paketi) ve `uv` gereksinimlerini denetleyip
  eksikleri (`apt`/`dnf`/`pacman`/`zypper` ve `uv` kurucusu ile) otomatik kurar; NVIDIA GPU/VRAM ve disk alanı
  kontrolü yapar, GPU yoksa boşa yerel VLM indirmeden temel kurulumla devam eder. `--hepsi`, `--temel`,
  `--kontrol`, `--kisayol-yok` ve seçici model kurulumu desteklenir; Linux masaüstünde `.desktop` kısayolu
  oluşturulur. Windows'ta `kurulum.bat` / `kurulum.ps1` WSL2 + Ubuntu kontrol/kurulumu yapar, projeyi hızlı WSL
  dosya sistemine (`~/ocr-lab`) aktarır ve `kurulum.sh`'ı çalıştırır.
- **Profil mimarisi** (`--profil`): `kitap` (varsayılan, mevcut akış), `gunluk` (hızlı fotoğraf/pano metni),
  `ders-notu` (el yazısı/tahta/slayt → Markdown+LaTeX), `fatura` (yapısal JSON/CSV + Markdown) ve `yazma`
  (sağdan sola Arapça/Osmanlıca). Kitap dışı profillerde Tesseract satır hizalaması ve kitap sayfa no uyarısı
  otomatik kapatılır.
- **Yeni çıktı biçimleri:** Markdown (`-f md`), yapısal JSON (`-f json`), tablo/fatura CSV (`-f csv`),
  aranabilir Unicode PDF (`-f pdf`), akan A4 Word ve sağdan sola (RTL, `Amiri` kompleks betik fontlu) Word.
- **Harici OpenAI-uyumlu API motoru (`-m api`):** OpenAI, Gemini, OpenRouter, Ollama veya LM Studio uç noktaları
  `ayarlar/api.json` (git dışı, `0600`) ya da `OCR_API_BASE`/`OCR_API_MODEL`/`OCR_API_KEY` ile tanımlanır; yerel GPU
  kilidi almaz ve profile özel prompt kullanır.
- **Çoklu Kraken HTR modeli ve dil seçimi:** `--kraken-model` (`models/` altındaki `.mlmodel` dosyaları) ve `--dil`
  (Tesseract dili, ör. `tur+eng`, `ara`).
- **`ocr-gui` genel OCR deneyimi:** 5'li profil seçici, **Ctrl+V ile panodan görüntü yapıştırma**, harici API ve
  Kraken/dil ayarları, sonuç ekranında **yan yana görsel + düzenlenebilir metin önizleme**, tek tıkla kopyalama /
  düzeltilmiş metni indirme ve **Son üretilen belgeler** listesi.
- Web arayüzünde ana ayarlardan önerilen model ve okuma modu, gelişmiş ayarlardan terminaldeki tüm OCR
  motorları seçilebilir. İki liste aynı seçimi paylaşır.
- Benchmark için `--referans` ve `--kor-test` seçenekleri; kamu malı metinlerden altı görüntü sayfası üreten
  [açık referans seti](benchmark/README.md).
- `ocr-gui`: `ocr-now`'ı terminalsiz kullanmak için tarayıcıda açılan arayüz. Windows masaüstündeki
  **ocr-now-windows** klasöründen başlar; klasörde belgelerin ve taramaların kısayolları da durur. Tarama sürükleyip bırakılır, ayarlar formdan verilir, okuma sayfa sayfa ve kalan süreyle
  izlenir, iptal edilebilir. Bitince belgeler indirilir ya da Gezgin'de gösterilir; sayfa numaraları ve uyarılar
  listelenir. Yeni bağımlılık yoktur, yalnız `127.0.0.1`'den erişilir.
- `ocr-now --rapor DOSYA`: iş sonu özetini JSON olarak da yazar.

### Düzeltildi
- Türkçe büyük `İ`/`I` küçük harfe çevrilirken (`apostrophe.py`, `metrics.py`) sözlük ve kural ıskalamaları giderildi
  (`İ‘tibâr`, `HALEB’İN`, `AY’IN`).
- Ortalanmış sayfa numaraları `"orta"` olarak tanınır: Word altlığında ortalı yazılır ve sahte sol/sağ çiftlik
  kuralı öğretmez. Geniş üstlük/altlıklarda numaranın ucu kullanılır; üstlükteki sıra sayıları (`19. YÜZYIL`) ve
  bölüm/cilt/tarih etiketleri (`BÖLÜM 3`, `HİCRÎ 1324`) sayfa numarası sayılmaz.
- Küçük puntolu ardışık dipnot bloklarında Tesseract satırları artık tek bloğa atanır (`assign_lines`); satır
  yüksekliğine ölçekli pay/girinti ve kısa bloklar için `MIN_OVERLONG` eşiğiyle yanlış hizalama düşüşleri azaldı.
- Beklenmedik CJK yazı karakteri sızıntıları sayfa uyarısı üretir ve Word'de sarı işaretlenir; paralel okumada
  Tesseract alt süreçlerine `OMP_THREAD_LIMIT=1` uygulanır.
- Modelin `<sup>…</sup>` etiketleri Word'de üst simge olur ve `.txt`'de temizlenir; `15.`/`3)` biçimli ve 4 haneli
  dipnotlar desteklenir.
- Üretim OCR önbellek anahtarı dosya `ctime_ns` ve mutlak depo/ikili yolundan ayrıldı; `smart_resize` parametreleri
  motor tanımına taşındı; satır hizalama bantlı Ukkonen DP kullanır.
- `ocr-gui`: dosya yükleme 64 KiB akışla ve SHA-256 karşılaştırmasıyla atomik yapılır, PDF sayfa sayıları
  önbelleklenir, WSL kısayolunda boşluklu yollar tırnaklanır.
- Benchmark `results.json` çıktısına kural/sözlük kararıyla model geri dönüşünü ayıran `apostrophe_decided` alanı
  eklendi.
- Uzunluk sınırında kesilen OCR çıktısı Word ve TXT'de işaretlenir, raporda ayrı listelenir ve sonraki koşuda
  yeniden okunur. Kurtarılan metin korunur; belge yazıldıysa arayüz **Kısmen tamamlandı** gösterir.
- Word'e yazılamayan karakterler `�` ile değiştirilir ve ilgili sayfa uyarılır. Bir biçimin yazım hatası
  diğer biçimlerin ve iş raporunun yazılmasını engellemez.
- Eşzamanlı ocr-now koşuları aynı çıktı adını kullanmaz; bir işte Word ve TXT aynı ad ekini paylaşır.
- Görüntü ve OCR önbelleği dosya adı ve boyutu yerine SHA-256 içerik özetini kullanır; farklı taramalar
  karışmaz. Eski önbellekler olduğu gibi kalır, yeni koşular bunları kullanmaz.
- Metin bulunamayan sayfa Word ve TXT'de **Kontrol gerekli** olarak görünür ve sonraki koşuda yeniden okunur.
- Model açılışı sırasında iptal, OCR başlamadan sunucuyu kapatır ve GPU kilidini bırakır.
- Arayüz, bazı belgeler yazıldığı hâlde iş eksikse **Kısmen tamamlandı** gösterir; dışa aktarma hataları
  sonuç ekranında dosya adıyla listelenir.
- Benchmark, eşli Markdown vurgusunu normalize CER'de metin farkı saymaz; ayın/hemze sözlüğünden
  benchmark yapılan kitabın tüm referans sayfalarını çıkarır.
- Günümüz Türkçesiyle basılmış bir kitabın telefonla çekilmiş 22 sayfasında bulunan metin hataları:
  - Model, numarayla başlayan dipnotu ("15 Bk. Âyet 30.") sayfa altlığı sanabiliyordu. Dipnot metni belgeye
    girmiyor, numarası sayfa numarası okunuyordu. Numara + küçük harfli cümle olan altlık artık dipnot sayılır;
    "412 TEZKİRE", "56 / Yazar Adı" gibi altlıklar etkilenmez.
  - Başlıklardaki Markdown işaretleri (`# `, `## `) metne girmiyor.
  - Kesme işareti (ayın/hemze):
    - Rakamdan, kapanan ayraçtan ya da kısaltma noktasından sonraki ek hemze alır: `1979’da`, `(s.a.s)’in`,
      `vs.’lerin`.
    - 1-2 harfli özel adlarda ek, ünlü uyumuna uyuyorsa hemze alır: `Ay’ın`, `O’dur`. `Ba‘de`, `Ya‘ni`, `Ma‘a`
      ayınlı kalır.
    - Kelimedeki ikinci işaretin kökü bütün kelimedir: `Şa‘ban’dır`, `el-Eş‘arî’nin`.
    - Harf-i tarifin bütün biçimleri hemze alır: `bi’t-Tevbîh`, `Nûru’d-dîn` (`Sa‘d-ı` ayınlı kalır).
    - Batı dillerindeki ses düşmesi hemze alır: `l’Islam`, `d’Alembert`.
    - `-nun/-nün` eki listeye eklendi (`Tokyo’nun`).
    - Kelime listesindeki kelimenin kesmeli eki de bulunur (`Kur’an` → `Kur’an’ın`).
    - `ayarlar/kesme.txt`'ye kurallarla ayrılamayan sık kelimeler eklendi (`Kur’an`, `hey’et`, `te’rîh`…).
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
- Ham OCR önbelleği ve benchmark kaydı aynı OCR ayar kimliğini kullanır. Bilinen vLLM model revizyonu
  açılışta sabitlenir; doğrulanamayan VLM okumaları ham önbelleğe alınmaz. Eski önbellek dosyaları silinmez.
- Benchmark ortak tamamlanan sayfalarda karşılaştırır. Tam, kesik, boş ve okunamayan denemeler ayrı
  sayılır; eksik kapsamada kazanan vurgulanmaz. `summary_v3.csv` tüm denemeleri saklar.
- Benchmark, işlenmiş belge metninin CER/WER değerlerini model metninden ayrı raporlar. Yeni CSV
  `summary_v2.csv` olarak yazılır; eski ölçümler değiştirilmez.
- dots.mocr'da önbellekte olmayan sayfalar modele 8'er 8'er aynı anda gönderilir; vLLM onları toplu işler.
  tarama1'in 22 sayfası önbelleksiz 8 dk 25 sn yerine 3 dk 9 sn'de okundu. tesseract da modeli beklerken çalışır.
  - Sunucu `VLLM_BATCH_INVARIANT=1` ile açılır. Bu ayar olmadan toplu okuma metni değiştiriyordu (22 sayfanın
    ikisinde Çince karakter, birinde “ ” yerine düz tırnak). Ayarla 1'li ve 8'li okuma birebir aynı. Eski tek tek
    okumaya göre 8 sayfada 6 kelime farkı çıktı; görüntüyle karşılaştırınca 4'ünde yeni okuma doğru, 1'inde eski.
    Önbellekteki eski okumalar geçerli kalır.
  - Öteki motorlar ölçülmediği için sayfaları tek tek okur.
  - İlerleme satırları ve uyarılar yine sayfa sırasıyla gelir. İptal bekleyen sayfaları başlatmaz; model sunucusu
    10 sn içinde kapanmazsa zorla kapatılır (paralel istekler bağlantıyı açık tuttuğu için 60 sn bekleniyordu).
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
