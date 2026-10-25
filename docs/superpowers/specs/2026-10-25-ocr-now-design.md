# ocr-now + ocr-lab — tasarım

Tarih: 2026-10-25 · Durum: onay bekliyor

## 1. Amaç ve neden

Kullanıcı, akademik proje yürüten bir hocaya yardım ediyor. Ona sık sık kitapların **taranmış bölümleri** (PDF) geliyor.
Bu bölümleri **en yüksek doğrulukta, düzenlenebilir metne** çevirip hocanın istediği formatta geri göndermesi gerekiyor.
Hoca metni makalesinde **atıf ve alıntı** için kullanıyor. Bu yüzden metnin iki özelliği şart:

- **Seçilebilir:** kopyala-yapıştır ve arama çalışmalı.
- **Düzenlenebilir:** OCR'ın kaçırdığı hataları hoca kendisi düzeltebilmeli.

Bugünkü `ocr-lab` bir **karşılaştırma tezgâhı**: `bench.py` 7 OCR motorunu çalıştırıyor, sonuçları Markdown'a ve
`summary.csv`'ye yazıyor. Bu proje onu iki parçaya ayırıyor:

- **ocr-now:** Günlük kullanılan üretim aracı. PDF girer, hocaya gidecek `.docx` / `.txt` çıkar.
- **ocr-lab:** Benchmark aracı. Yeni ve daha iyi OCR modelleri çıktıkça onları `gt/` altındaki doğru metinlerle
  karşılaştırır. Kazanan model ocr-now'da varsayılan olur.

Kullanıcı ileride bu temeli **fatura okuyup Excel'e aktarma** gibi otomasyonlarda da kullanmak ve projeyi
**yayınlamak** istiyor.

### Bugüne kadarki bulgular (2026-10-25, tarama5.pdf sayfa 1, elle yazılmış doğru metinle)

| Model | Süre | Normalize CER | WER | Not |
|---|---|---|---|---|
| dots.mocr — düzen modu | 26 sn | **0.32%** | **1.5%** | Başlık, paragraf, dipnot ve sayfa numarasını blok blok, konumlarıyla veriyor |
| dots.mocr — düz metin | 23 sn | 1.20% | 2.2% | Başlığı ve sayfa numarasını atıyor |
| lightonocr | 7 sn | 1.35% | 6.6% | Metni modernleştiriyor (`oldı→oldu`) |
| glm-ocr | 9 sn | 1.42% | 8.1% | Harfleri doğru, işaret kaybı var (ı→i, ş→s) |
| tesseract | 1 sn | 4.04% | 20.4% | î/û kaybı. **Ama satır yapısı ve konumları doğru** |
| deepseek-ocr2, paddleocr-vl, kraken | — | 5–9% | 24–46% | Bu belge türüne uygun değil |

Ayrıntılar: `output/tarama5_dogruluk_syf1.md`. Doğru metin: `gt/tarama5_syf1.txt`.

**Karma yöntem denemesi (spike):** dots.mocr düzen modunun metni, tesseract'ın satır kutularına karakter düzeyinde
hizalandı. Sayfa 1'in 39 satırının **39'u da** kitaptaki yerinden kırıldı ve satır sonu tireleri doğru yerlerde çıktı.
Bu deneme kullanıcıya `.docx` olarak gösterildi ve onaylandı. Deneme betiği (atılacak kod, yalnızca referans):
`docs/superpowers/specs/2026-10-25-ocr-now-ekler/spike_docx.py` (verisi: `output/_spike/`, çalıştırma: `python spike_docx.py output/_spike`).
Denemede öğrenilenler §6.3'te.

## 2. Hedefler

1. **En yüksek doğruluk:** Harfler, benchmark'ta en iyi çıkan modelden gelir (bugün dots.mocr düzen modu).
   Model metni "düzeltmemeli" ya da modernleştirmemeli. Osmanlıca transkripsiyon işaretleri (â î û, ‘ ’, ğ ı ş)
   olduğu gibi korunmalı.
   - **Ayın (‘) ve hemze (’) ayrımı geri getirilir (kullanıcının kararı).** Hiçbir motor bu ayrımı güvenilir
     yapamıyor: dots.mocr, glm-ocr ve tesseract hepsini düz `'` yapıyor. Ayrım yapan lightonocr ve paddleocr-vl de
     sayfa 1'de türü 31'de yalnızca 19-20 kez doğru seçiyor (hepsine ‘ demek 21/31 verirdi). Bu yüzden ayrım
     **kurallarla** geri getirilir. Kuralın karar veremediği işaretler sarı işaretlemeye (Hedef 3) dahil edilir.
     Ayrıntılar §6.4'te.
2. **Hocaya giden `.docx` kitabın sayfasına benzemeli (kullanıcının seçimi, "A seçeneği"):**
   - Her taranmış sayfa ayrı bir Word sayfası ve **ayrı bir Word bölümü (section)** olur.
   - **Kitabın orijinal sayfa numarası** o bölümün **altlığına (footer)** yazılır. Gövdeye yazılmaz, sayfadan taşmaz.
     Kitapta numara solda ise solda, sağda ise sağda durur. Word'ün kendi sayfa numarası kullanılmaz.
   - **Orijinal satır kırılımları ve satır sonu tireleri birebir korunur.** Satırlar elle konan satır sonuyla
     (Shift+Enter) kırılır. Paragraf bir Word paragrafıdır. Metin sola dayalıdır, iki yana yaslanmaz.
   - Tekrar eden kitap başlığı (Page-header) üstte, ortalı ve küçük punto durur.
   - Paragraf girintileri korunur.
   - Dipnotlar sayfanın gövdesinde, metnin altında, kısa bir çizgiyle ayrılmış küçük puntolu metin olarak durur.
     Metindeki dipnot numarası üst simge (superscript) olur.
   - Yazma nüsha işaretleri (`[106b sayfanın sağ yanı]`) kalın yazılır, satır sonunda bölünmüş olsalar bile.
3. **Belirsiz kelimeleri işaretleme (seçenek, varsayılan açık):** Ana model ile tesseract'ın farklı okuduğu kelimeler
   `.docx`'te **sarı vurgu** ile işaretlenir. Hoca hataları bütün sayfayı okumadan bulabilsin diye. Sayfa 1'de
   dots.mocr'un 6 kelime hatasından en az 4'ünde tesseract farklı okumuştu.
4. **`.txt` = ham metin:** Süssüz metin. Paragraflar tek satır, satır sonu tireleri birleştirilmiş, işaretleme yok.
   Sayfalar arasında sade bir ayraç bulunur (ör. `--- s. 412 ---`). Arama ve başka araçlara vermek için kullanılır.
5. **Kontrol seçenekleri:**
   - Girdi: tek dosya, birden fazla dosya ya da klasör. PDF dışında tek sayfalık görüntüler de (PNG/JPG/TIFF) kabul edilir.
   - PDF sayfaları: hepsi, tek sayfa (`5`), liste (`1,3,7`), aralık (`10-25`), ilk N sayfa. Birden fazla PDF'te
     komut satırından verilen seçim hepsine uygulanır. Etkileşimli modda dosya başına sorulabilir.
   - Model seçimi ve modelin desteklediği **okuma modu** seçimi (ör. dots.mocr: düzen / düz metin).
     Varsayılanlar benchmark'ta en iyi çıkan ayarlardır.
   - İki kullanım şekli korunur: seçeneksiz başlatılınca adım adım soru sorar, komut satırı seçenekleriyle soru sormadan çalışır.
   - **Çıktı gruplama (kullanıcının onayladığı öneri):** Her PDF kendi belgesini üretir. Bir klasördeki görüntü
     dosyaları dosya adı sırasıyla (doğal sıralama: `2.jpg` → `10.jpg`) **tek bir belgede** birleşir. İsteğe bağlı
     bir seçenekle birden fazla PDF tek belgede, verilen sırayla birleştirilebilir.
   - İlk sayfanın kitap sayfa numarası isteğe bağlı olarak verilebilir (bkz. §6.5, sayfa numarası geri dönüşü).
6. **İş bitince format sorulur:** `.docx`, `.txt` ya da ikisi. Sarı işaretleme açık mı diye de sorulur.
   Her ikisi komut satırından önceden verilebilir, o zaman soru sorulmaz.
7. **Önerilen modeller ve tek komutla kurulum:** `ocr-now` önerilen modelleri gereken GPU belleği ve disk boyutuyla
   listeler. Tek komut hepsini indirip kurar. Kurulum makinedeki GPU belleğine bakar: sığmayan modelde uyarır ya da
   bilinen düşük bellekli varyantı (fp8) seçer.
8. **ocr-lab benchmark aracı olarak yaşamaya devam eder:** Aynı motor listesini kullanır. Sonuçları her çalışmada
   dosyaya yazar. Analiz **marimo** notebook'unda yapılır.
9. **Arayüz yalnızca Türkçe** (kullanıcının kararı, şimdilik).

## 3. Kapsam dışı (şimdilik)

- Fatura okuma, Excel'e aktarma ve başka otomasyonlar. Mimari bunlara kapı açık bırakır: yeni bir dışa aktarıcı ya
  da yeni bir işlem adımı eklemek mevcut kodu değiştirmeden mümkün olmalı. Ama bu projede yapılmaz.
- İngilizce ya da çok dilli arayüz.
- Gerçek Word dipnotu (footnotes.xml) ve iki yana yaslı metin.
- Aranabilir PDF çıktısı. Dışa aktarıcı yapısı izin vermeli, ama bu projede yapılmaz.
- Çok sütunlu sayfalar, tablolar ve formüller için özel düzen. Gelirlerse §6.5'teki geri dönüş davranışı uygulanır.
- Grafik arayüz (GUI) ya da web arayüzü. Araç komut satırı programıdır.

## 4. Anti-hedefler (çalışsa bile başarısızlık sayılır)

- **Metni sessizce değiştirmek:** modernleştirme (`buldı→buldu`), işaret silme, kelime "düzeltme". Hoca alıntı yapıyor.
  Çıktı, kitapta ne yazıyorsa onu vermeli.
- **Sayfa numarasını ya da sayfa sınırını kaybetmek.** Atıf bunlara dayanıyor.
- **Satır yapısı bozulduğunda bunu sessizce geçmek.** Hizalama güvenilir değilse kullanıcı uyarılmalı (§6.3).
- **Motor kodunu iki programa kopyalamak.** ocr-now ve ocr-lab aynı motor tanımlarını paylaşmalı. Yeni bir model bir
  kez eklenir.
- **Kitap metnini yayınlanan depoya koymak.** `gt/`, `input/` ve `output/` telifli kitap metni içerir.

## 5. Kısıtlar

- **Donanım:** Tek bir 8 GB GPU, WSL2. Sistemde CUDA toolkit (nvcc) yok.
  - deepseek-ocr2 ve dots.mocr yalnızca fp8 ile sığıyor.
  - dots.mocr için `max_pixels` 4 MP.
  - `VLLM_USE_FLASHINFER_SAMPLER=0` gerekli.
  - vLLM 0.32'ye DeepSeek için bir satırlık yama uygulanıyor.
  - Bunların hepsi bugün `bench.py`, `run.sh` ve `kurulum.sh` içinde, korunmalı.
- **Aynı anda tek GPU işi:** İki koşu GPU'yu paylaşırsa ikisi de çöker. VLM sunucuları sırayla açılıp kapatılır.
- **Ayrı Python ortamları:** vLLM ile kraken'in bağımlılıkları çakışıyor. Bugün `.venv` (ana, hafif), `.venv-vllm` ve
  `.venv-kraken` ayrı. Ana program VLM'lerle OpenAI uyumlu HTTP üzerinden konuşuyor. Bu yapı korunur.
- **tesseract** bir apt paketi (`tesseract-ocr-tur`). Kurulum komutu bunu kendisi kuramıyorsa kullanıcıya
  çalıştıracağı komutu göstermeli.
- **Model lisansları:** Model ağırlıkları dağıtılmaz, her biri kendi kaynağından (Hugging Face) indirilir.
  dots.mocr'un kendine ait bir lisans sözleşmesi var.
- Yerel çalışır. Sayfalar hiçbir dış servise gönderilmez.
- **Yayınlama biçimi (kullanıcının kararı, şimdilik):** Depo klonlanır ve bugünkü gibi o klasörden çalıştırılır.
  Hedef makine Linux/WSL2 ve NVIDIA GPU. `pip install` ile her yere kurulan paket şimdilik hedef değil. Ama bütün
  yollar (ortamlar, `tools/`, `models/`, `input/`, `output/`, `gt/`, önbellek) **tek bir yerden** belirlenmeli.
  Böylece ileride kurulabilir pakete geçmek büyük bir yeniden yazım gerektirmez.

## 6. Kararlaştırılan yapı

### 6.1 Tek proje, iki komut

Tek bir Python paketi, iki komut (kullanıcı bu öneriyi onayladı):

```
ocr-now  [dosyalar…] [seçenekler]   → üretim
ocr-now  models [install]           → önerilen modeller / kurulum
ocr-lab  …                          → benchmark ve analiz
```

Ortak çekirdek: motor tanımları (komut, port, istem, desteklenen modlar, bellek ihtiyacı), sunucu açma/kapama,
sayfa görüntüleme, sayfa yapısı ve dışa aktarıcılar. Benchmark'ın ağır bağımlılıkları (marimo, grafik
kütüphaneleri) isteğe bağlı bir ek paket olarak kurulur.

### 6.2 İşlem hattı

```
PDF/görüntü ─► sayfa PNG ─► ana model (ör. dots.mocr düzen modu) ─► bloklar: tür + konum + metin
                         └► tesseract (TSV: satır kutuları + kelimeler) ───────────┐
                                                                                   ▼
                              sayfa yapısı (JSON, sayfa başına bir kez kaydedilir) ◄─ hizalama
                                                    │
                                ┌───────────────────┼───────────────────┐
                                ▼                   ▼                   ▼
                              .docx               .txt          (ileride: .xlsx, PDF…)
```

- **Sayfa yapısı** modelden bağımsız, ara bir biçimdir. Bloklar (tür: Page-header / Text / Footnote / Page-footer /
  Title / Picture / Table …, konum, metin), bloğun satırları (her satırın metni ve tire bilgisi) ve kelime düzeyinde
  belirsizlik işaretlerini içerir. Dışa aktarıcılar yalnızca bunu okur.
- **Sayfa yapısı önbelleğe yazılır.** Kullanıcı sonradan başka bir format isterse OCR yeniden çalışmaz, yalnızca
  dışa aktarma yapılır. (Benim kararım.)
- Düzen modu olmayan bir model seçilirse, sayfa tek bir Text bloğu sayılır. Satır yapısı yine tesseract'tan gelir.
  Başlık, dipnot ve sayfa numarası ayrımı yapılamaz ve kullanıcıya bu söylenir.

### 6.3 Satır yapısı: hizalama (spike'tan öğrenilenler)

- Ana modelin her Text bloğuna, merkezi o bloğun kutusunun içine düşen tesseract satırları atanır.
- tesseract satırları birleştirilir. Satır sonu tiresi olan satırlarda tire kaldırılıp kelime birleştirilir.
  Ana modelin blok metniyle karakter düzeyinde Levenshtein hizalaması yapılır. tesseract'ın her satır sonu konumu,
  ana model metnindeki karşılığına taşınır.
- **Tire kuralı:** tesseract satır sonu tiresini bazen `.` ya da `,` olarak okuyor. Bir satır sonunun tire sayılması
  için, ana model metninde o konumun **kelime ortası** olması gerekir (yakınında boşluk olmamalı). Ana model
  birleşik kelimenin tiresini korumuşsa (`senâ-kâr`) tireden sonra kırılır. Korumamışsa (`göstermediler`)
  tire eklenip kırılır.
- Tire olmayan satır sonu, hizalanan konuma en yakın boşluğa konur.
- Paragraf girintisi: bloğun ilk tesseract satırı, blok kutusundan belirgin şekilde içerideyse paragraf girintilidir.
- **Güven denetimi gerekli (spike'ta yoktu):** Satır sayısı tutmuyorsa, hizalama maliyeti yüksekse ya da bir blokta
  hiç tesseract satırı yoksa o blok için satır kırılımı yapılmaz (akan paragraf). Kullanıcıya hangi sayfa ve
  blokta olduğu söylenir. Eşik değerleri uygulayıcıya bırakılmıştır.
- Spike tek sayfada test edildi. Bölüm uzunluğundaki belgelerde sağlamlığı ocr-lab'da ölçülmelidir (§6.6).

### 6.4 Biçim ayrıntıları

- **Dipnot numarası:** Footnote bloğunun başındaki sayı (ör. `724`), gövde metninde bir harften hemen sonra
  geliyorsa (`taştında724`) üst simge yapılır.
- **Kalın nüsha işaretleri:** Ana model kalın yazıyı bildirmiyor. Bu yüzden işaretler bir kalıpla bulunur. Bugünkü
  kalıp: `[\d+[ab] …]`, satır sonunda bölünmüş hâli de dahil. Kalıp kitaba özel olduğu için **ayarlanabilir ve
  kapatılabilir** olmalıdır. (Kalıbın ayarlanabilir olması benim kararım.)
- **Sarı işaretleme:** Kelime düzeyinde ana model ile tesseract karşılaştırılır. Normalizasyonda kesme işaretleri
  (’ ‘ ' ʿ) eşit sayılır. Farklı okunan kelimeler sarı vurgulanır. tesseract'ın sistematik î/û kaybı yüzünden çok
  sayıda yanlış alarm çıkabilir. Bunun nasıl azaltılacağı uygulayıcıya bırakılmıştır, ama hedef, işaretlenen
  kelimelerin çoğunun gerçekten bakılmaya değer olmasıdır.
- **Ayın/hemze geri getirme:** Her `'` (ve benzeri ’ ‘ ` ´ ʿ ʾ) için sırayla:
  1. Arapça harf-i tarif elizyonu: işaretten sonra `l-` geliyorsa (`Bi’l-`, `beyne’l-`, `reisü’l-`) → **’**.
  2. Türkçe ek ayıracı: büyük harfle başlayan bir kelimeden sonra Türkçe ek geliyorsa (`Haleb’de`, `Efrenc’e`) → **’**.
  3. Kelime başında, ünlüden önce (`‘illet`, `‘arz`, `‘alîl`, `‘amel`) → **‘**.
  4. Bir **kelime listesi** sorulur. Liste `gt/` dosyalarından otomatik çıkarılır (ör. `cür’et`, `mes’ele`, `sü’âl`,
     `füc’eten` → ’; `bâ‘is`, `mâni‘`, `merci‘`, `ya‘nî`, `mu‘tâd`, `ba‘z` → ‘). Kullanıcı listeye elle de ekleyebilir.
  5. Hiçbiri karar veremezse ‘ yazılır (sayfa 1'de ayın daha sık) ve işaret **sarı** vurgulanır.

  Kurallar ve sıraları bu sayfadaki örneklerden çıkarılmış bir başlangıçtır (benim önerim). Doğrulukları ocr-lab'da
  ölçülür (§6.6). Kural adımı hem `.docx`'e hem `.txt`'ye uygulanır. Kitap başına kapatılabilir.
- **Sayfa ölçüleri ve yazı tipi:** Satırlar Word'de kendiliğinden kaymamalı. En uzun satır sayfa genişliğine sığmalı.
  Spike'ta 17×24 cm sayfa, 1.6 cm kenar boşluğu ve Times New Roman 10.5 pt yeterli oldu.

### 6.5 Geri dönüş davranışları

- Picture bloğu: metne `[Görsel]` yer tutucusu konur. Table bloğu: modelin verdiği metin düz paragraf olarak konur.
  (Benim kararım, kapsam dışı konular için en sade davranış.)
- **Sayfa numarası geri dönüşü:** Kitabın sayfa numarası Page-footer'dan (ya da numarayı içeren Page-header'dan)
  okunur. Okunamazsa (model kaçırmış, bölüm başı sayfası numarasız, roma rakamı vb.) komşu sayfaların numaralarından
  sırayla tahmin edilir. Okunan numaralar sırayla çelişiyorsa da (ör. 412, 431, 414) sıra esas alınır. Tahmin edilen
  ya da düzeltilen numara altlıkta işaretli durur (ör. `[413]`) ve iş sonunda kullanıcıya listelenir. Kullanıcı ilk
  sayfanın numarasını komut satırından verirse tahmin bu numaradan başlar. Hiçbir ipucu yoksa PDF sayfa sırası
  açıkça etiketlenerek kullanılır (ör. `[PDF s. 3]`) ve uyarı verilir. (Benim kararım.)
- Bir sayfada ana model hata verirse veya çökerse: o sayfa için hata mesajı yazılır, iş devam eder, sonunda
  hangi sayfaların eksik kaldığı listelenir. (Bugünkü `bench.py` motor düzeyinde aynısını yapıyor.)

### 6.6 ocr-lab (benchmark)

- Bugünkü test modunun işlevi korunur: seçili motorlar seçili sayfaları okur, süre ve karakter sayısı ölçülür,
  `gt/<pdf>_syf<N>.txt` varsa CER hesaplanır.
- Ölçüler, bugünkü elle yapılan analizde kullanılanlarla aynı olur:
  ham CER, normalize CER (Markdown ve kesme işareti farkları ile satır sonu tireleri yok sayılır),
  gövde CER (başlık ve sayfa numarası hariç), harf CER (işaretler ve büyük/küçük harf hariç) ve WER.
  Referans uygulama: `docs/superpowers/specs/2026-10-25-ocr-now-ekler/cmp.py`.
- **Yeni ölçü: satır sonu doğruluğu.** docx hattı için, satır sonlarının kitaptakiyle ne kadar eşleştiği ölçülür.
- **Yeni ölçü: ayın/hemze doğruluğu.** Normalize CER kesme işaretlerini eşit saydığı için bu kaybı gizliyordu.
  Kural adımından sonra her işaretin türünün gt ile ne kadar eşleştiği ve kaç tanesinin sarıya düştüğü ayrıca ölçülür.
- **Motor + mod birlikte test edilir** (ör. `dots.mocr/düzen`, `dots.mocr/düz`), çünkü aynı modelin modları farklı sonuç veriyor.
- Her çalışmanın sonuçları (ölçüler, model çıktıları, kelime hataları) makinenin okuyabileceği dosyalara yazılır.
  Önceki çalışmalar korunur.
- **marimo notebook:** Sonuçları okur. Model/mod seçip ölçüleri karşılaştırma, hatalı kelimeleri yan yana görme ve
  sayfa görüntüsünü yanında açma imkânı verir. Paylaşılacaksa statik HTML'e çevrilir.
- Benchmark'ta kazanan motor/mod, ocr-now'ın varsayılanı olarak tek bir ayarla belirlenir.

### 6.7 Önerilen modeller

Başlangıç listesi bugünkü bulgulara göre (benim önerim):

- **dots.mocr** (düzen modu, varsayılan)
- **glm-ocr** ve **lightonocr** (hızlı alternatifler)
- **tesseract** (satır yapısı ve sarı işaretleme için her zaman gerekli)

deepseek-ocr2, paddleocr-vl ve kraken ocr-lab'da test edilebilir kalır ama önerilen listede yer almaz. Her model için
gereken GPU belleği ve disk boyutu listede yazar. Değerler uygulayıcı tarafından ölçülerek doldurulur.

### 6.8 Klasörler ve depo

- Program kodu ve **proje belgeleri (`docs/`)**, kullanıcı verisinden (`input/`, `output/`, `gt/`) tamamen ayrıdır
  (kullanıcının kararı).
- Proje git deposu olarak başlatılır. `.gitignore` şunları dışarıda bırakır: `input/`, `output/`, `gt/`, `.venv*`,
  `tools/`, `models/`, `.superpowers/`. (Benim kararım. Yayınlama hedefi ve telifli metin nedeniyle.)
- Bugünkü `bench.py`, `run.sh` ve `kurulum.sh`'in işlevi yeni yapıya taşınır. Ayrı ortamlar, sunucu ayarları ve
  vLLM yaması kaybolmaz.

## 7. Uygulayıcıya bırakılanlar

- Paket ve modül yapısı, dosya adları, sayfa yapısı JSON'unun tam şeması.
- Komut satırı seçeneklerinin adları (Türkçe arayüzle uyumlu olmalı).
- Çıktı dosyalarının tam yolu ve adı (kaynak dosyanın adını taşımalı).
- Hizalama güven eşikleri ve sarı işaretlemede yanlış alarm azaltma yöntemi.
- marimo notebook'unun ekran düzeni.
- Önerilen modellerin bellek ve disk değerlerinin ölçülmesi.
- Test stratejisi. `gt/tarama5_syf1.txt` ve sayfa 1 çıktıları ilk test verisi olarak kullanılabilir.

## 8. Benim verdiğim kararlar (kullanıcı kontrol etmeli)

1. Sarı işaretleme varsayılan olarak **açık**.
2. Sayfa yapısı önbelleğe yazılır. Başka format istenince OCR tekrarlanmaz.
3. Kalın nüsha işareti kalıbı ayarlanabilir ve kapatılabilir.
4. Önerilen modeller: dots.mocr (varsayılan), glm-ocr, lightonocr, tesseract.
5. Picture → `[Görsel]`, Table → düz paragraf.
6. Git deposu ve `.gitignore` ile kitap metninin depodan dışarıda tutulması.
7. Benchmark bağımlılıkları isteğe bağlı ek paket.
8. Sayfa numarası okunamazsa sırayla tahmin, işaretli gösterim ve isteğe bağlı başlangıç numarası (§6.5).
9. Ayın/hemze kurallarının içeriği ve sırası, karar verilemeyince ‘ yazılıp sarıya düşürülmesi (§6.4).
   Kullanıcının kararı yalnızca "kurallarla geri getir, belirsizi işaretle" kısmı.
10. Görüntü klasöründe doğal dosya adı sıralaması.
