# ocr-now + ocr-lab

Taranmış kitap sayfalarını (PDF ya da görüntü) **seçilebilir ve düzenlenebilir** metne çeviren yerel bir OCR hattı.
Atıf ve alıntı için yapıldı. Çıktı kitapta ne yazıyorsa onu verir. Satır kırılımları, sayfa numaraları ve
Osmanlıca transkripsiyon işaretleri (â î û, ‘ ’, ğ ı ş) korunur.

| Komut | Ne için |
|---|---|
| `./ocr-now` | Üretim: PDF girer, kitabın sayfasına benzeyen `.docx` ve/veya ham `.txt` çıkar |
| `./ocr-lab` | Benchmark: OCR motorlarını ve okuma modlarını `gt/` altındaki doğru metinlerle karşılaştırır |

İki komut aynı motor tanımlarını (`src/ocrnow/engines.py`) kullanır, yeni bir model bir kez eklenir. Her şey
yerelde çalışır, sayfalar hiçbir dış servise gönderilmez.

Değişiklik geçmişi: [CHANGELOG.md](CHANGELOG.md).

## Hızlı başlangıç

```bash
./kurulum.sh                                   # ana ortam + bütün motorlar (tekrar çalıştırmak güvenli)
./ocr-now models                               # hangi model hazır?
./ocr-now input/kitap.pdf -p 10-25 -f docx     # 10-25. sayfalar → output/belgeler/kitap_s10-25.docx
./ocr-now                                      # ya da adım adım sorsun
```

Gereksinimler:
- Linux ya da WSL2.
- NVIDIA GPU (8 GB'ta test edildi).
- Python 3.12 ve [uv](https://docs.astral.sh/uv/).
- tesseract (`sudo apt install -y tesseract-ocr tesseract-ocr-tur`).

## Nasıl çalışır

Her sayfa için şu adımlar sırayla yapılır:

1. **Ana model okur.** Varsayılan model dots.mocr'dur. Düzen modunda başlık, paragraf, dipnot ve sayfa numarasını
   konumlarıyla birlikte, ayrı bloklar hâlinde verir.
2. **tesseract aynı sayfayı okur.** Harfleri daha zayıf okur ama satır kutuları doğrudur.
3. **Satır kırılımları geri getirilir.** Ana modelin metni, tesseract satırlarına karakter düzeyinde hizalanır.
   Böylece her satır kitaptaki yerinden kırılır, satır sonu tireleri de doğru yere konur.
4. **Ayın (‘) ve hemze (’) ayrılır.** Bunu yapan kurallar ve bir kelime listesidir.
5. **Şüpheli kelimeler işaretlenir.** Ana model ile tesseract'ın farklı okuduğu kelimeler sarıya boyanır.
6. **Kitap sayfa numarası bulunur.** Önce sayfadan okunur. Okunamazsa komşu sayfalardan tahmin edilir.
7. **Sayfa yapısı önbelleğe yazılır, sonra dışa aktarılır.** Yazılan biçimler `.docx` ve `.txt`'dir.

Örnek sayfada (2800 karakter) sonuçlar şöyle:
- **Doğruluk:** dots.mocr düzen modunun normalize CER'i **%0.32**.
- **Satır yapısı:** 39 satır kırılımının **39'u** kitaptaki yerinde.

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
| `-p`, `--sayfalar` | Değerler: `hepsi` (varsayılan), `5`, `1,3,7`, `10-25`, `ilk10`; karışık da olur (`1-3,7`). Birden fazla PDF'te hepsine uygulanır. PDF'i aşan kısım kırpılır ve uyarı verilir |
| `-m`, `--model` | Kullanılacak motor (varsayılan `dots.mocr`) |
| `-o`, `--okuma` | Okuma modu: `duzen` (varsayılan; model desteklemiyorsa `duz`) ya da `duz`. Düz modda başlık, dipnot ve sayfa numarası ayrılamaz |
| `-f`, `--format` | `docx`, `txt` ya da `docx txt` |
| `--isaretle` / `--isaretleme` | Şüpheli kelimeleri sarı işaretle / işaretleme |
| `--kesme-yok` | Ayın/hemze geri getirme adımını kapatır |
| `--kalin-kalip KALIP` | Kalın yazılacak nüsha işaretinin düzenli ifadesi. Varsayılan `\[\d+[ab] [^\]]*\]`, ör. `[106b sayfanın sağ yanı]`. `''` verilirse hiçbir şey kalın yapılmaz |
| `--ilk-sayfa N` | İlk seçilen sayfanın kitaptaki numarası. Okunamayan numaralar buradan sayılır |
| `--birlestir` | Bütün girdileri tek belgede birleştirir |
| `--dpi N` | PDF görüntüleme çözünürlüğü (varsayılan 200) |

### Çıktı

**Dosyalar** `output/belgeler/` altına yazılır.
- Her PDF kendi belgesini üretir.
- Bütün sayfalar seçilmediyse ad seçimi taşır (`kitap_s11-20.docx`).
- Aynı adlı belge varsa üzerine yazılmaz, `_2`, `_3` eklenir. Elle düzeltilmiş bir belge böylece kaybolmaz.
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

**Köşeli parantezli sayfa numarası** (`[413]`) tahmin edilmiş demektir.
- Numara ya sayfadan okunamadı ya da komşularıyla çelişiyordu.
- Kendi içinde tutarlı okumalar korunur, ör. 412–415'ten sonra 418–425.
- Yalnız tek başına aykırı kalan okuma düzeltilir ve uyarılır (`s. 2: okunan 431 → [413]`).
- Hiçbiri birbirini tutmuyorsa (ör. iki sayfalık alıntıda 412 ve 431) hangisinin doğru olduğu bilinemez. Okunan
  numaralar korunur ama köşeli parantezle yazılır ve uyarılır.
- Hiç ipucu yoksa `[PDF s. 3]` yazılır.

**Sarı vurgu** iki durumda konur. Amaç, hataların bütün sayfayı okumadan bulunabilmesidir.
- Ana model ile tesseract'ın farklı okuduğu, bakılmaya değer bir kelime.
- Kuralların karar veremediği bir ayın/hemze işareti.

**`.txt`** süssüz ham metindir.
- Paragraflar tek satırdır, satır sonu tireleri birleştirilmiştir.
- İtalik/kalın vurgu yoktur: modelin `*…*` işaretleri kaldırılmıştır.
- Sayfalar arasında `--- s. 412 ---` ayracı bulunur.

**İş sonu:**
- Belge yolları, tahmin edilen sayfa numaraları ve bütün uyarılar sayfa sayfa listelenir.
- Okunamayan sayfa belgede `[Bu sayfa okunamadı]` olarak durur. Bu durumda çıkış kodu 1 olur.
- Hiçbir sayfası okunamayan belge yazılmaz.

### Önbellek

Sayfa yapısı `output/_cache/` altına sayfa başına bir kez yazılır.
- Aynı sayfa aynı model/modla yeniden istenirse (ör. önce `-f docx`, sonra `-f txt`) OCR yeniden çalışmaz.
- Bütün sayfalar önbellekteyse model sunucusu hiç açılmaz.
- `ayarlar/kesme.txt` değişirse eski sayfalar tazelenmez. İlgili `output/_cache/<kaynak>-<boyut>/` klasörünü silin.

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

Doğru metin `gt/<pdf adı>_syf<N>.txt` olarak varsa şu ölçüler hesaplanır:

| Ölçü | Ne ölçer |
|---|---|
| `cer_raw` | Ham karakter hata oranı |
| `cer_norm` | Markdown, kesme türü ve satır sonu tireleri yok sayılarak |
| `cer_body` | Başlık ve sayfa numarası hariç |
| `cer_base` | İşaretler ve büyük/küçük harf hariç, yalnız temel harfler |
| `wer` | Kelime hata oranı |
| `line_end` | docx satır sonlarının kitaptakiyle eşleşmesi |
| `apostrophe`, `apostrophe_flagged` | Ayın/hemze türü doğruluğu, sarıya düşen işaret sayısı |

Koşular `output/lab/<YYYYmmdd-HHMM>/` altına yazılır. Her koşunun içinde `results.json`, ham model çıktıları ve bir
karşılaştırma `.md`'si bulunur. `output/lab/summary.csv` bütün koşuları biriktirir.

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
ocr-now, ocr-lab, kurulum.sh   giriş noktaları (her klasörden çalışır; göreli yollar bulunduğunuz yere göredir)
src/ocrnow/                    program; bütün yollar src/ocrnow/paths.py'de
src/ocrnow/lab/                ocr-lab: ölçüler, benchmark, marimo notebook
ayarlar/kesme.txt              kullanıcının ayın/hemze kelime listesi
tests/                         testler: .venv/bin/pytest
docs/                          tasarım belgesi ve uygulama planı
input/                         taranacak PDF'ler (varsayılan girdi)
gt/                            doğru metinler: gt/<pdf>_syf<N>.txt
output/belgeler/               ocr-now belgeleri
output/_cache/  output/_pages/ önbellek, sayfa görselleri
output/lab/                    ocr-lab koşuları, summary.csv, rapor.html
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
- **fp8** (deepseek-ocr2, dots.mocr): Bu modellerin bf16 hâlleri 8 GB GPU'ya sığmıyor.
