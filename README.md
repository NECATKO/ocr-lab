# ocr-now + ocr-lab

Taranmış kitap sayfalarını, ders notlarını (el yazısı/tahta/slayt/PDF), Arapça/Osmanlıca yazmaları, faturaları ve günlük görselleri **seçilebilir ve düzenlenebilir** metne çeviren yerel + isteğe bağlı API destekli OCR hattı.

| Komut | Ne için |
|---|---|
| `./ocr-now` | Üretim: PDF veya görsel girer; `.docx`, `.md`, `.txt`, `.json`, `.csv` veya aranabilir `.pdf` çıkar |
| `./ocr-gui` | `ocr-now`'ın tarayıcıda açılan arayüzü: profil seçici, Ctrl+V pano yapıştırma, yan yana düzenleme |
| `./ocr-lab` | Benchmark: OCR motorlarını ve okuma modlarını `gt/` altındaki doğru metinlerle karşılaştırır |

İki komut aynı motor tanımlarını (`src/ocrnow/engines.py`) kullanır, yeni bir model bir kez eklenir. Her şey
yerelde çalışır, sayfalar hiçbir dış servise gönderilmez.

Değişiklik geçmişi: [CHANGELOG.md](CHANGELOG.md).

## Hızlı başlangıç

**Linux (veya WSL2 içi):**
```bash
./kurulum.sh                                   # gereksinimleri denetler/kurar + ana ortam + önerilen motorlar
./kurulum.sh --hepsi                           # benchmark motorları dahil tüm motorlar (~30+ GB)
./kurulum.sh --temel                           # yalnızca ana ortam + tesseract (GPU'suz / harici API için)
./kurulum.sh --kontrol                         # kurulum yapmadan sistem durumunu denetler
./ocr-now models                               # hangi model hazır?
./ocr-now input/kitap.pdf -p 10-25 -f docx     # 10-25. sayfalar → output/belgeler/kitap_s10-25.docx
./ocr-now                                      # ya da adım adım sorsun
```

**Windows:**
`kurulum.bat` dosyasına çift tıklayın (veya PowerShell'de `.\kurulum.ps1` çalıştırın). Betik WSL2 + Ubuntu yoksa kurar, projeyi hızlı Linux dosya sistemine (`~/ocr-lab`) aktarır, tüm gereksinimleri kurup Windows masaüstüne **ocr-now-windows** kısayol klasörünü oluşturur. Aynı seçenekler (`kurulum.bat --hepsi`, `--temel`, `--kontrol`) Windows'ta da geçerlidir.

Gereksinimler (eksikler kurulum betiği tarafından otomatik kontrol edilip kurulur):
- **Linux** (`apt`, `dnf`, `pacman` veya `zypper`) ya da **Windows 10/11 + WSL2**.
- **NVIDIA GPU** (yerel VLM motorları için; 8 GB'ta test edildi. Windows'ta güncel Windows NVIDIA sürücüsü gerekir. GPU yoksa kurulum otomatik olarak `tesseract` + harici `api` için temel kurulumu yapar).
- `curl`, `tar`, `tesseract` + Türkçe dil paketi (`tur`), `uv` ve Python 3.12 (`kurulum.sh` eksikleri otomatik kurar).

## Nasıl çalışır

Her sayfa için şu adımlar sırayla yapılır:

1. **Ana model okur.** Varsayılan model dots.mocr'dur. Düzen modunda başlık, paragraf, dipnot ve sayfa numarasını
   konumlarıyla birlikte, ayrı bloklar hâlinde verir.
2. **tesseract aynı sayfayı okur.** Harfleri daha zayıf okur ama satır kutuları doğrudur.
3. **Satır kırılımları geri getirilir.** Ana modelin metni, tesseract satırlarına karakter düzeyinde hizalanır.
   Böylece her satır kitaptaki yerinden kırılır, satır sonu tireleri de doğru yere konur.
4. **Ayın (‘) ve hemze (’) ayrılır.** Bunu yapan kurallar ve bir kelime listesidir.
5. **Şüpheli kelimeler işaretlenir.** Ana model ile tesseract'ın farklı okuduğu kelimeler sarıya boyanır.
6. **Kitap sayfa numarası bulunur.** Sayfadan okunan numaraya güvenilir. Okunamazsa, yalnız güvenli olduğunda
   komşu sayfalardan tahmin edilir.
7. **Dışa aktarılır.** Yazılan biçimler `-f` ile seçilir (`.docx`, `.txt`, `.md`, `.json`, `.csv`, `.pdf`). Adım 1–2'nin çıktısı önbelleğe yazılır; sonraki
   adımlar her çalıştırmada yeniden yapılır (bkz. Önbellek).

dots.mocr'da adım 1–2 sayfalar arasında paralel yürür: modele en çok 8 sayfa aynı anda gönderilir, sunucu (vLLM)
onları toplu işler. 8 GB'lık bir GPU'da 22 sayfalık tarama 8 dk 25 sn yerine 3 dk 9 sn'de okundu. Sunucu
`VLLM_BATCH_INVARIANT=1` ile açılır: bir sayfa, yanında kaç sayfa okunursa okunsun aynı metni verir (bu ayar
olmadan toplu okuma metni değiştiriyordu: Çince karakter, düz tırnak). Sonraki adımlar ve ilerleme satırları yine
sayfa sırasıyla gelir. Öteki motorlar sayfaları tek tek okur.

Örnek sayfada (2800 karakter) sonuçlar şöyle:
- **Doğruluk:** dots.mocr düzen modunun normalize CER'i **%0.32**.
- **Satır yapısı:** 39 satır kırılımının **39'u** kitaptaki yerinde.

## ocr-gui: terminalsiz kullanım

Windows masaüstündeki **ocr-now-windows** klasöründe üç kısayol durur: **OCR-Now** arayüzü açar, **Belgeler** Word/metin çıktılarının, **Taramalar** eklenen taramaların klasörünü Gezgin'de açar. **OCR-Now**'a çift tıklayın. Tarayıcıda arayüz açılır, görev çubuğunda küçültülmüş
bir pencere belirir. Arayüzde sırasıyla:

1. **Tarama:** PDF'i sürükleyip bırakın ya da `input/`'taki taramalardan birini seçin. Eklenen dosya `input/`'a
   kopyalanır; aynı dosya yeniden eklenirse kopyası çıkmaz, önbellek geçerli kalır.
2. **Ayarlar:** sayfalar, ilk sayfanın kitap numarası, Word/metin, sarı işaretleme, önerilen model ve okuma modu.
   **Gelişmiş ayarlar** terminaldeki tüm OCR motorlarını, ayın/hemze ve DPI seçeneklerini gösterir.
   Model değişince yalnızca desteklediği modlar görünür. Varsayılanlar `ocr-now`'ınkilerle aynıdır.
3. **Okumayı başlat:** her sayfa bir kutucukla gösterilir, geçen ve kalan süre yazılır. Model açılışı (birkaç
   dakika) kalan süre hesabına katılmaz. Sekme kapatılsa da okuma sürer; yeniden açınca kaldığı yerden görünür.
4. **Sonuç:** belgeleri indirme ya da Windows Gezgini'nde gösterme, taramayı gösterme, sayfa numarası
   listeleri ve uyarılar. Çıktı var ama bazı sayfalar kontrol gerektiriyorsa, okunamadıysa ya da bazı biçimler
   yazılamadıysa **Kısmen tamamlandı** görünür; yazılan belgeler yine indirilebilir.

Arkada `ocr-now` komutu çalışır (`--rapor` ile iş sonu özetini JSON olarak verir); davranışı tamamen aynıdır.
İptal, Ctrl-C ile aynı yoldan model sunucusunu kapatır. Arayüz yalnız bu bilgisayardan (`127.0.0.1`) erişilebilir.
Kapatmak için sayfadaki **Kapat** düğmesi ya da görev çubuğundaki pencere; süren okuma iptal edilir.

```bash
./ocr-gui --tarayici              # elle başlatma (http://localhost:8765)
./ocr-gui --masaustu-kisayolu     # masaüstündeki ocr-now-windows klasörünü (yeniden) kurar; kurulum.sh da yapar
```

## ocr-now: belge üretme

```bash
./ocr-now                                        # adım adım: dosya, sayfalar, model, okuma modu; sonda format
./ocr-now input/kitap.pdf -p 1 -f docx txt       # ikisi birden
./ocr-now taramalar/ -f txt                      # klasördeki PDF'ler ayrı belge, görüntüler tek belge
./ocr-now a.pdf b.pdf --birlestir -f docx        # iki PDF tek belgede (a_birlesik.docx)
./ocr-now input/kitap.pdf -p ilk10 --ilk-sayfa 412 -f docx --isaretle
```

Girdi verilmezse `input/` klasörü kullanılır. Format ve sarı işaretleme verilmezse iş sonunda sorulur. Terminal
yoksa soru sorulmaz: `docx` yazılır, işaretleme açık olur.

> **Dikkat:** `-f` birden fazla değer alır. Girdileri `-f`'den önce yazın ya da araya `--` koyun:
> `./ocr-now -f docx -- input/kitap.pdf`.

| Seçenek | Anlamı |
|---|---|
| `--profil` | Belge profili: `kitap` (varsayılan), `gunluk`, `ders-notu`, `fatura`, `yazma`. Motor, mod, format ve son-işlem varsayılanlarını ayarlar |
| `-p`, `--sayfalar` | Değerler: `hepsi` (varsayılan), `5`, `1,3,7`, `10-25`, `ilk10`; karışık da olur (`1-3,7`). Birden fazla PDF'te hepsine uygulanır. PDF'i aşan kısım kırpılır ve uyarı verilir |
| `-m`, `--model` | Kullanılacak motor (varsayılan profile göre; yerel motorlar ya da harici OpenAI-uyumlu `api`) |
| `-o`, `--okuma` | Okuma modu: `duzen` ya da `duz`. Düz modda başlık, dipnot ve sayfa numarası ayrılamaz |
| `-f`, `--format` | `docx`, `txt`, `md`, `json`, `csv`, `pdf` ya da birkaçı. `pdf`, **aranabilir PDF**'tir: orijinal (CamScanner) sayfa olduğu gibi kalır, okunan metin üstüne görünmez katman olarak eklenir; her kelime tesseract'ın bulduğu kelime kutusuna oturur. Satır sonunda tireyle bölünen kelime aramada ve kopyalamada bütün gelir. Çok satırlı kopya okuyucuya göre satır satır gelir. Arapça/Osmanlıca satırlar mantıksal sırayla yazılır; Türkçe satırın içine karışan rakam ya da Arapça kelime pdfium tabanlı okuyucularda (Chrome, Android) ters kopyalanabilir |
| `--isaretle` / `--isaretleme` | Şüpheli kelimeleri sarı işaretle / işaretleme |
| `--kesme` / `--kesme-yok` | Ayın/hemze geri getirme adımını açar / kapatır |
| `--dil KOD` | Tesseract dili (ör. `tur`, `tur+eng`, `eng`, `ara`) |
| `--kraken-model DOSYA` | Kraken için `models/` içindeki `.mlmodel` dosyası (Arapça/Osmanlıca HTR ağırlıkları dahil) |
| `--kalin-kalip KALIP` | Kalın yazılacak nüsha işaretinin düzenli ifadesi. Varsayılan `\[\d+[ab] [^\]]*\]`, ör. `[106b sayfanın sağ yanı]`. `''` verilirse hiçbir şey kalın yapılmaz |
| `--ilk-sayfa N` | İlk seçilen sayfanın kitaptaki numarası. Okunamayan numaralar buradan sayılır |
| `--birlestir` | Bütün girdileri tek belgede birleştirir |
| `--dpi N` | PDF görüntüleme çözünürlüğü (varsayılan 200) |
| `--rapor DOSYA` | İş sonu özetini (belgeler, sayfa numaraları, önizleme, uyarılar) JSON olarak da yazar; `ocr-gui` kullanır |

### Çıktı

**Dosyalar** `output/belgeler/` altına yazılır.
- Her PDF kendi belgesini üretir.
- Bütün sayfalar seçilmediyse ad seçimi taşır (`kitap_s11-20.docx`).
- Aynı adlı belge varsa üzerine yazılmaz, `_2`, `_3` eklenir. Elle düzeltilmiş bir belge böylece kaybolmaz.
  Eşzamanlı `ocr-now` koşuları da ayrı ad alır; bir işin Word ve TXT çıktısı aynı eki paylaşır.
- Word veya TXT yazımı yarıda kesilirse hedefte yarım belge kalmaz. Bir biçim yazılamazsa diğerleri yine
  denenir; yazma hatası özette dosya adıyla görünür.
- Bir klasördeki görüntüler doğal sırayla (`2.jpg` → `10.jpg`) tek belgede birleşir.

**`.docx`**
- Her taranmış sayfa ayrı bir Word bölümüdür.
- Satırlar kitaptaki yerinden elle konan satır sonuyla (Shift+Enter) kırılır. Satır sonu tireleri kitaptaki gibidir.
- Kitap başlığı üstte, ortalı ve küçük puntoyla durur.
- Dipnotlar metnin altında, kısa bir çizginin ardından yer alır. Dipnot numarası üst simgedir.
- Nüsha işaretleri kalın yazılır.
- Modelin Markdown vurgusu (`*italik*`, `**kalın**`) metne yıldız olarak girmez, Word'de italik/kalın yazılır.
  Tek başına duran yıldız (`kelime*`, dipnot simgesi olabilir) olduğu gibi kalır.
- Kitabın sayfa numarası altlıkta, kitaptaki tarafta durur.
- Punto her sayfa için en uzun satır sığacak şekilde seçilir: en çok 10.5, en az 8 pt.
- Word'ün XML biçiminde gösterilemeyen karakterler `�` ile işaretlenir; iş özetinde ilgili sayfa belirtilir.

**Sayfa numarası.** Sayfadan okunan numara olduğu gibi yazılır; komşularıyla tutmaması tek başına sorun sayılmaz.
Taranmış belge kitabın farklı yerlerinden, hatta ters sırayla alınmış parçalardan oluşabilir.
**Köşeli parantezli numara** (`[413]`) tahmin edilmiş ya da kontrol edilmesi gereken numaradır:
- **Tarafla çelişen okuma.** Okumalardan kitabın kuralı öğrenilir: çift numaralar solda mı, sağda mı (en az iki
  okuma ve açık çoğunluk gerekir). Kurala aykırı okuma okunduğu gibi kalır ama köşeli parantezle yazılır ve
  uyarılır (`s. 4: okunan 416 sayfanın tarafıyla çelişiyor (sağ sayfada çift numara), kontrol edin`).
- **Koşu içinde tek aykırı okuma.** Artan (412, 413, 414) ya da azalan (412, 411, 410) tutarlı bir koşunun
  ortasındaki tek aykırı okuma koşudan düzeltilir ve uyarılır (`s. 2: okunan 431 → [413]`). Kendi içinde tutarlı
  okumalar korunur, ör. 412–415'ten sonra 418–425.
- **Okunamayan numara** yalnız şu durumlarda tahmin edilir: sayfa bir koşunun içindeyse, en az iki okumalı bir
  koşunun hemen devamıysa ya da seçimdeki tek okumadan sonra geliyorsa. Seçimin başındaki numarasız sayfalar
  (ör. bölüm başı) ilk koşudan geriye doğru sayılır; iki koşu arasındaki boşluk sonraki koşudan sayılmaz.
- Tahmin, sayfanın çiftliğiyle çelişirse yapılmaz. Çiftlik, sayfanın başlığından öğrenilir: ör. yazar adı hep çift,
  kitap adı hep tek sayfadaysa (büyük/küçük harf ve aksan farkı yok sayılır).
- Tahmin yapılamazsa `[PDF s. 3]` yazılır ve uyarılır. Gerekirse `--ilk-sayfa` ile numara verin.

Aynı numara birden fazla sayfada okunduysa her birine uyarı eklenir
(`s. 23: 285 numarası s. 12'de de okundu (aynı sayfa iki kez taranmış olabilir)`).
`--ilk-sayfa` verilirse numaralar ondan sırayla sayılır; taraf, başlık ve tekrar denetimi yapılmaz.

**Sarı vurgu** iki durumda konur. Amaç, hataların bütün sayfayı okumadan bulunabilmesidir.
- Ana model ile tesseract'ın farklı okuduğu, bakılmaya değer bir kelime.
- Kuralların karar veremediği bir ayın/hemze işareti.

**`.txt`** süssüz ham metindir.
- Paragraflar tek satırdır, satır sonu tireleri birleştirilmiştir.
- İtalik/kalın vurgu yoktur: modelin `*…*` işaretleri kaldırılmıştır.
- Sayfalar arasında `--- s. 412 ---` ayracı bulunur.

**İş sonu:**
- Belge yolları, tahmin edilen sayfa numaraları, tarafla çelişen (kontrol edilmesi gereken) okunan numaralar ve
  bütün uyarılar sayfa sayfa listelenir.
- Okunamayan sayfa belgede `[Bu sayfa okunamadı]` olarak durur. Bu durumda çıkış kodu 1 olur.
- Motor okunabilir metin vermezse sayfa silinmez: Word ve TXT çıktısında
  `[Kontrol gerekli: Bu sayfada metin bulunamadı]` yazılır. Tarama gerçekten boş da olabilir; iş özeti bu
  sayfaları ayrı listeler ve çıkış kodu 1 olur. Bu yanıt önbelleğe yazılmaz, sayfa sonraki koşuda yeniden okunur.
- Model yanıtı uzunluk sınırında kesilirse elde edilen metin korunur, ancak ilgili Word/TXT sayfasına uyarı
  eklenir. İş özeti bu sayfaları ayrı listeler (`--rapor` alanı `kesilen`); çıkış kodu 1 olur ve arayüz belge
  yazıldıysa “Kısmen tamamlandı” gösterir. Kesik okumalar önbelleğe alınmaz, eski kesik kayıtlar da kullanılmaz.
- Kesilme çoğu kez modelin aynı şeyi tekrarladığı bir döngüdür. Yerel vLLM sunucusuyla okunan sayfa kesilirse bir
  kez daha, bu kez hafif tekrar cezasıyla (`repetition_penalty 1.05`, sıcaklık yine 0) okunur. İkinci okuma
  tamamlanırsa sayfa kullanılır ve önbelleğe yazılır. Sayfaya yine de "model tekrar döngüsüne girdi; … metni
  kontrol edin" uyarısı eklenir. İkinci okuma da kesilirse ilk sonuç yukarıdaki gibi kesik sayılır. Harici API
  motorunda bu yeniden deneme yapılmaz.
- Hiçbir sayfası okunamayan belge yazılmaz.

### Önbellek

Her sayfanın OCR çıktısı (modelin ham cevabı ve tesseract'ın satırları) `output/_cache/` altına sayfa başına bir
kez yazılır.
- Kaynağın SHA-256 içerik özeti hem görüntü hem OCR önbelleğinde kullanılır. Aynı adlı ve eşit boyutlu farklı
  taramalar birbirine karışmaz; adı değişen bir kopya, içeriği aynıysa aynı önbelleği kullanır.
- Tarama OCR sırasında değiştirilirse etkilenen sayfalar hata olarak bildirilir; değişen kaynak eski anahtara yazılmaz.
- Aynı sayfa aynı OCR kimliğiyle ve PDF için aynı DPI ile yeniden istenirse (ör. önce `-f docx`, sonra
  `-f txt`) kullanılabilir bir sonuç varsa OCR yeniden çalışmaz. Kimliğe motor ve mod, model revizyonu
  veya yerel ağırlık kimliği, istem, token sınırı ve Tesseract sürümü/dili dahildir.
- Yönetilen vLLM sunucusu, yerelde bilinen Hugging Face revizyonuna sabitlenir. Harici sunucunun revizyonu
  veya yönetilen modelin revizyonu doğrulanamazsa ham OCR önbelleği okunmaz ve yazılmaz. Doğrulanmış
  bütün sayfalar önbellekteyse model sunucusu hiç açılmaz.
- Sayfa yapısı (satırlar, ayın/hemze, sarı işaretler) her çalıştırmada önbellekteki çıktıdan yeniden kurulur
  (sayfa başına ~0.3 sn). Bu yüzden `ayarlar/kesme.txt`'deki ya da programdaki değişiklikler, `--kesme-yok` dahil,
  GPU'suz ve yeniden OCR gerekmeden uygulanır.
- Kimliksiz eski OCR kayıtları silinmez ama kullanılmaz; yalnızca istenen sayfalar yeniden okunur. İçerik
  özetli eski PNG'ler yeniden kullanılabilir; ad/boyut tabanlı eski görüntü önbelleği kullanılmaz.

### Ayın/hemze kelime listesi

Hiçbir motor ayın (‘) ile hemzeyi (’) güvenilir şekilde ayıramıyor. Ayrımı sırayla şunlar yapar:
1. Kelime listesi.
2. `’l-` elizyonu.
3. Özel addan sonra gelen Türkçe ek.
4. Kelime başında, ünlüden önce gelen işaret → ‘.

Liste `gt/` dosyalarından otomatik çıkarılır. Kendi kelimelerinizi `ayarlar/kesme.txt`'ye yazın: her satıra bir
kelime, doğru işaretiyle (ör. `mes’ele`, `bâ‘is`). Karar verilemeyen işarete ‘ yazılır ve sarıya boyanır.

## ocr-lab: benchmark

```bash
./ocr-lab                                            # adım adım sorar
./ocr-lab input/tarama5.pdf -e glm-ocr               # -p verilmezse yalnız 1. sayfa
./ocr-lab input/tarama5.pdf -p 1 -e dots.mocr glm-ocr  # motorun bütün modları (dots.mocr: duzen + duz)
./ocr-lab input/tarama5.pdf -p 1-3 -e dots.mocr:duzen  # yalnız bir mod
./ocr-lab rapor                                      # koşuları marimo notebook'unda karşılaştır
./ocr-lab rapor --html                               # statik sayfa: output/lab/rapor.html
```

Özel doğru metin klasörü için `--referans KLASÖR`; sözlükleri kapatarak ölçmek için `--kor-test` kullanın.
Tekrarlanabilir altı örnek sayfa ve komutlar: [açık referans seti](benchmark/README.md).

Doğru metin `gt/<pdf adı>_syf<N>.txt` olarak varsa şu ölçüler hesaplanır:

| Ölçü | Ne ölçer |
|---|---|
| `cer_raw` | Model metninde ham karakter hata oranı (boşluklar sadeleştirilir) |
| `cer_norm` | Model metninde Markdown vurgusu, kesme türü ve satır sonu tireleri yok sayılarak |
| `cer_body` | Model metninde çalışan başlık ve sayfa numarası hariç |
| `cer_base` | Model metninde işaretler ve büyük/küçük harf hariç, yalnız temel harfler |
| `wer` | Model metninde kelime hata oranı |
| `cer_document`, `wer_document` | Word'e giden işlenmiş metinde karakter ve kelime hata oranı |
| `line_end` | İşlenmiş sayfa satırlarının son üç karakterinin referansla eşleşmesi |
| `apostrophe`, `apostrophe_flagged` | Ayın/hemze türü doğruluğu, sarıya düşen işaret sayısı |

Belge ölçümü üstlük, gövde, dipnot ve sayfadan okunan numarayı içerir; komşulardan numara tahminini ya da
Word'ün otomatik satır kaydırmasını ölçmez. Benchmark, ayın/hemze kelime listesi için ölçülen PDF'in bütün
`gt/` sayfalarını dışarıda bırakır. Kullanıcı sözlüğü (`ayarlar/kesme.txt`) etkin kalır. `gt/` adları PDF adına
bağlı olduğundan aynı adlı farklı PDF'ler tek koşuda kabul edilmez.

Koşular `output/lab/<YYYYmmdd-HHMM>/` altına yazılır. Her koşunun içinde `results.json`, ham model çıktıları ve bir
karşılaştırma `.md`'si bulunur. JSON'da OCR kimliği/ayarları, model revizyonu (bilinmiyorsa `null`), kaynak,
referans ve sözlük özetleri, DPI ve okuma durumu saklanır. `summary_v3.csv` tüm denemeleri (hatalar dahil) biriktirir;
`summary.csv` ve `summary_v2.csv` değişmez.

Rapor her skoru seçili motor/modların **ortak tamamladığı ve o ölçünün hesaplandığı sayfalarda** verir.
Belge ölçüleri aynı sözlükle üretilmiş sayfalarda eşleştirilir.
Tam, kesik, boş ve okunamayan sayfalar ayrı sayılır. Kapsama eksikse en iyi değer vurgulanmaz. Farklı ölçüm
sürümleri, kör test ile sözlüklü koşular ya da aynı motorun farklı OCR ayarları ayrı seçilmelidir.

Benchmark'ta kazanan ayar, `engines.DEFAULT_ENGINE` ve `engines.DEFAULT_MODE` değiştirilerek ocr-now'ın varsayılanı yapılır.

## Modeller

`./ocr-now models` listeyi gösterir, `./ocr-now models install` önerilenleri kurar. `install glm-ocr` tek bir modeli,
`install --hepsi` hepsini kurar. Kurulu adımlar atlanır.

| Model | GPU belleği | Disk | Okuma modları | Not |
|---|---|---|---|---|
| ★ **dots.mocr** | 4.9 GB | 5.7 GB | duzen, duz | Varsayılan. vLLM, fp8 |
| ★ glm-ocr | 4.0 GB | 2.5 GB | duz | Hızlı alternatif. vLLM, bf16 |
| ★ lightonocr | 3.4 GB | 1.9 GB | duz | Hızlı alternatif. Metni biraz modernleştirir |
| ★ tesseract | CPU | — | duz | Satır yapısı ve sarı işaretleme için her zaman gerekli |
| paddleocr-vl | 3.2 GB | 2.7 GB | duz | Yalnız ocr-lab için. llama.cpp + GGUF |
| deepseek-ocr2 | 5.4 GB | 6.3 GB | duz | Yalnız ocr-lab için. vLLM, fp8 |
| kraken | CPU | 3.7 GB | duz | Yalnız ocr-lab için. Modelde ı/ş/ğ harfleri yok |

- **Disk:** vLLM ortamı (`.venv-vllm`, yaklaşık 8 GB) bu değerlere dahil değil.
- **GPU belleği:** Tahmini asgaridir. `models install` GPU'ya sığmayabilecek modeli uyarır.
- **Lisans:** Model ağırlıkları bu depoyla dağıtılmaz, her biri kendi kaynağından (Hugging Face) indirilir.
  dots.mocr'un kendine ait bir lisans sözleşmesi vardır.

**Aynı anda tek GPU işi çalışır.** VLM sunucusu açıkken `output/_logs/gpu.lock` tutulur. İkinci bir koşu açık bir
hatayla durur. Bir motor çökerse `output/_logs/<motor>.server.log` dosyasına bakın.

## Klasörler

```
ocr-now, ocr-lab, ocr-gui,
kurulum.sh, kurulum.bat,
kurulum.ps1                    giriş noktaları ve Linux/Windows(WSL2) kurulum betikleri
src/ocrnow/                    program; bütün yollar src/ocrnow/paths.py'de
src/ocrnow/lab/                ocr-lab: ölçüler, benchmark, marimo notebook
src/ocrnow/gui/                ocr-gui: yerel sunucu, iş yürütücü, arayüz sayfası (index.html)
ayarlar/kesme.txt              kullanıcının ayın/hemze kelime listesi
tests/                         testler: .venv/bin/pytest
docs/                          tasarım belgesi ve uygulama planı
input/                         taranacak PDF'ler (varsayılan girdi)
gt/                            doğru metinler: gt/<pdf>_syf<N>.txt
output/belgeler/               ocr-now belgeleri
output/_cache/  output/_pages/ önbellek, sayfa görselleri
output/lab/                    ocr-lab koşuları, summary_v3.csv, rapor.html
output/_logs/                  sunucu logları, gpu.lock
models/  tools/                model dosyaları, llama.cpp
.venv  .venv-vllm  .venv-kraken   ayrı ortamlar (vLLM ile kraken bağımlılıkları çakışıyor)
```

**`input/`, `output/` ve `gt/` git'e girmez** (`.gitignore`), çünkü telifli kitap metni içerirler. Kitap metni
gerektiren testler bu dosyaları yalnızca okur. Dosyalar yoksa bu testler atlanır.

## Bilinen sınırlar

- **Sayfa yüksekliği:** 40'tan çok satırlı bir sayfa Word'de ikinci bir sayfaya taşabilir. Altlıktaki numara yine
  doğrudur.
- **Düz okuma modu:** Bütün sayfa tek blok olduğu için satır yapısı çoğu zaman kurulamaz ve paragraf akan metin
  olur. Uyarı verilir.
- **Çok sayfalı TIFF:** Yalnız ilk sayfası okunur. Uyarı verilir.
- **Kapsam dışı:** Çok sütunlu sayfalar, tablolar ve formüller için özel düzen yoktur.

## Ortama özel ayarlar (neden var?)

- **`VLLM_USE_FLASHINFER_SAMPLER=0`** (`src/ocrnow/paths.py`, alt süreç ortamı): FlashInfer örnekleyici nvcc ister,
  sistemde CUDA toolkit yok.
- **vLLM 0.32 yaması** (`kurulum.sh`, `src/ocrnow/models.py` üzerinden): Tek satırlık bu yama olmadan
  DeepSeek-OCR-2 kodlayıcı çekirdeği Triton 3.7'de derlenmiyor. vLLM'i elle güncellerseniz `./kurulum.sh`'ı tekrar
  çalıştırın.
- **dots.mocr `max_pixels` 4 MP** (`engines.py`): Varsayılan 11 MP için bellek profili 8 GB'ı aşıyor. Daha büyük
  sayfa görüntüsü küçültülerek okunur, kutular orijinal piksellere geri ölçeklenir.
- **fp8** (deepseek-ocr2, dots.mocr): Bu modellerin bf16 hâlleri 8 GB GPU'ya sığmıyor. 16 GB ve üzeri yerel GPU'da
  `OCR_BF16=1` fp8'i kapatır, bf16 ağırlıklar ve daha yüksek çözünürlükle okur.
