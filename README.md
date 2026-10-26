# ocr-lab

Taranmış kitap sayfalarını (PDF ya da görüntü) düzenlenebilir metne çevirir. İki komut var:

| Komut | Ne için |
|---|---|
| `./ocr-now` | Üretim: PDF girer, kitabın sayfasına benzeyen `.docx` ve/veya düz `.txt` çıkar |
| `./ocr-lab` | Benchmark: motorları ve okuma modlarını `gt/` altındaki doğru metinlerle karşılaştırır |

İkisi de aynı motor tanımlarını (`src/ocrnow/engines.py`) kullanır. Yeni bir model bir kez eklenir.
Her şey yerelde çalışır, sayfalar hiçbir dış servise gönderilmez.

Hedef makine: Linux/WSL2, NVIDIA GPU (8 GB'ta test edildi), Python 3.12, [uv](https://docs.astral.sh/uv/).

## Kurulum

```bash
./kurulum.sh                 # ana ortam + bütün motorlar (tekrar çalıştırmak güvenli, kurulu adım atlanır)
./ocr-now models             # hangi model hazır, ne kadar GPU belleği ve disk ister
./ocr-now models install     # yalnız önerilen modelleri kur (ya da: install glm-ocr, install --hepsi)
```

tesseract bir sistem paketidir. Kurulu değilse kurulum çalıştırılacak komutu gösterir
(`sudo apt install -y tesseract-ocr tesseract-ocr-tur`). Model ağırlıkları dağıtılmaz, her biri kendi kaynağından
(Hugging Face) indirilir. dots.mocr'un kendi lisans sözleşmesi vardır.

### Önerilen modeller

`./ocr-now models` çıktısından (8 GB GPU):

| Model | GPU belleği | Disk | Not |
|---|---|---|---|
| ★ **dots.mocr** | 4.9 GB | 5.7 GB | varsayılan; düzen modu başlık, paragraf, dipnot ve sayfa numarasını ayırır |
| ★ glm-ocr | 4.0 GB | 2.5 GB | hızlı alternatif |
| ★ lightonocr | 3.4 GB | 1.9 GB | hızlı alternatif; metni biraz modernleştirir |
| ★ tesseract | CPU | — | satır yapısı ve sarı işaretleme için her zaman gerekli (yoksa iki komut da baştan durur) |
| paddleocr-vl | 3.2 GB | 2.7 GB | yalnız ocr-lab'da denemek için |
| deepseek-ocr2 | 5.4 GB | 6.3 GB | yalnız ocr-lab'da denemek için |
| kraken | CPU | 3.7 GB | yalnız ocr-lab'da denemek için |

Disk sütunu vLLM motorlarının ortak ortamını (`.venv-vllm`, ~8 GB) içermez. GPU belleği tahmini asgaridir
(fp8 motorlarda vLLM logundaki yüklenen bellek + 1.5 GB). `models install` GPU'ya sığmayabilecek motor için uyarır.

## ocr-now: belge üretme

```bash
./ocr-now                                        # adım adım sorar: dosya, sayfalar, model, okuma modu, format
./ocr-now input/kitap.pdf -p 10-25 -f docx       # sormadan: 10-25. sayfalar Word'e (kitap_s10-25.docx)
./ocr-now input/kitap.pdf -p 1 -f docx txt       # ikisi birden
./ocr-now taramalar/ -f txt                      # klasördeki PDF'ler ayrı belge, görüntüler tek belge
./ocr-now a.pdf b.pdf --birlestir -f docx        # iki PDF tek belgede (a_birlesik.docx)
./ocr-now input/kitap.pdf -p ilk10 --ilk-sayfa 412 -f docx --isaretle
```

Girdi verilmezse `input/` klasörü kullanılır. Format ve sarı işaretleme verilmezse iş sonunda sorulur (girdi terminal
değilse sorulmaz: `docx`, işaretleme açık).

**Dikkat:** `-f` birden fazla değer alır. Girdileri `-f`'den önce yazın ya da araya `--` koyun:
`./ocr-now -f docx -- input/kitap.pdf`. Aksi hâlde dosya adı biçim sanılır.

| Seçenek | Anlamı |
|---|---|
| `-p`, `--sayfalar` | `hepsi` (varsayılan), `5`, `1,3,7`, `10-25`, `ilk10`, karışık: `1-3,7`. Birden fazla PDF'te hepsine uygulanır. PDF'i aşan kısım kırpılır ve uyarı verilir |
| `-m`, `--model` | motor (varsayılan `dots.mocr`) |
| `-o`, `--okuma` | okuma modu: `duzen` (varsayılan; model desteklemiyorsa `duz`) ya da `duz`. Düz modda başlık/dipnot/sayfa numarası ayrımı yapılamaz |
| `-f`, `--format` | `docx`, `txt` ya da `docx txt` (verilmezse sonda sorulur; terminal değilse `docx`) |
| `--isaretle` / `--isaretleme` | şüpheli kelimeleri sarı işaretle / işaretleme (verilmezse sonda sorulur; terminal değilse açık) |
| `--kesme-yok` | ayın/hemze (‘ ’) geri getirme adımını kapat |
| `--kalin-kalip KALIP` | kalın yazılacak nüsha işaretinin düzenli ifadesi (varsayılan `\[\d+[ab] [^\]]*\]`, ör. `[106b sayfanın sağ yanı]`); `''` kalın yapmaz; geçersiz ifade baştan reddedilir |
| `--ilk-sayfa N` | ilk seçilen sayfanın kitap sayfa numarası (okunamayan numaralar buradan sayılır) |
| `--birlestir` | bütün girdileri tek belgede birleştir |
| `--dpi N` | PDF görüntüleme çözünürlüğü (varsayılan 200) |

### Çıktı

- `output/belgeler/<kaynak adı>.docx` ve `.txt`. Her PDF kendi belgesini üretir. Bütün sayfalar seçilmediyse ad
  seçimi taşır (`-p 11-20` → `kitap_s11-20.docx`). Aynı adlı belge varsa üzerine yazılmaz: `_2`, `_3` ... eklenir
  (elle düzeltilmiş belge kaybolmaz). Bir klasördeki görüntüler dosya adı sırasıyla (`2.jpg` → `10.jpg`) tek
  belgede birleşir. Çok sayfalı TIFF'in yalnız ilk sayfası okunur (uyarı verilir).
- **`.docx`:** Her taranmış sayfa ayrı bir Word bölümüdür. Satır kırılımları ve satır sonu tireleri kitaptaki gibidir.
  Tekrar eden kitap başlığı üstte ortalı, dipnotlar metnin altında küçük puntoyla, dipnot numarası üst simge, nüsha
  işaretleri kalın. Kitabın sayfa numarası altlıkta, kitaptaki tarafta durur. Punto her sayfa için ayrı hesaplanır
  (en uzun satır sığsın diye, en çok 10.5, en az 8 pt); 8 pt'de de sığmayan satır uyarılır, Word onu kaydırabilir.
  40'tan çok satırlı bir sayfa Word'de ikinci bir sayfaya taşabilir; altlıktaki kitap sayfa numarası yine doğrudur
  (taşan sayfada da aynı numara görünür).
- **Köşeli parantezli sayfa numarası** (`[413]`): numara sayfadan okunamadı ya da komşularıyla çelişiyordu, komşu
  sayfalardan tahmin edildi. Kendi içinde tutarlı okumalar korunur (atlanan tarama sayfası, iki ayrı alıntı:
  412–415 ardından 418–425 olduğu gibi kalır); yalnız tek başına aykırı okuma düzeltilir ve uyarılır
  (`s. 2: okunan 431 → [413]`). Tahminler iş sonunda listelenir. Hiç ipucu yoksa `[PDF s. 3]` yazılır ve uyarı
  verilir.
- **Sarı vurgu:** ya ana model ile tesseract'ın farklı okuduğu (bakılmaya değer) bir kelime, ya da kuralların karar
  veremediği bir ayın/hemze işareti. Hatalar bütün sayfayı okumadan bulunabilsin diye.
- **`.txt`:** süssüz ham metin. Paragraflar tek satır, satır sonu tireleri birleştirilmiş, sayfalar arasında
  `--- s. 412 ---` ayracı.
- İş sonunda uyarılar listelenir (ör. satır yapısı kurulamayan blok, okunamayan sayfa, uzunluk sınırında kesilen
  model çıktısı: sayfa sonu eksik olabilir). Hata veren sayfa belgede `[Bu sayfa okunamadı]` yer tutucusuyla durur,
  iş sürer, eksik sayfalar sonda söylenir (çıkış kodu 1). Hiçbir sayfası okunamayan belge yazılmaz. Model sunucusu
  açılamazsa koşu tek bir hata mesajıyla durur.

### Önbellek

Sayfa yapısı `output/_cache/` altına sayfa başına bir kez yazılır. Aynı sayfa aynı model/modla tekrar istenirse
(ör. önce `-f docx`, sonra `-f txt`) OCR yeniden çalışmaz, yalnız dışa aktarma yapılır. Bütün sayfalar
önbellekteyse model sunucusu hiç açılmaz (GPU boş kalır). Kelime listesi
(`ayarlar/kesme.txt`) değiştiğinde eski sayfalar tazelenmez: ilgili `output/_cache/<kaynak>-<boyut>/` klasörünü silin.

### Ayın/hemze kelime listesi

Hiçbir motor ayın (‘) ile hemzeyi (’) güvenilir ayıramıyor. Ayrım kurallarla geri getirilir: `’l-` elizyonu, özel
addan sonra Türkçe ek, kelime başında ünlüden önce ‘ ve bir kelime listesi. Liste `gt/` dosyalarından otomatik
çıkarılır. Kendi kelimelerinizi `ayarlar/kesme.txt`'ye her satıra bir kelime olacak şekilde, doğru işaretiyle
ekleyin (ör. `mes’ele`, `bâ‘is`). Karar verilemeyen işarete ‘ yazılır ve sarı vurgulanır. Kitap başına
`--kesme-yok` ile kapatılabilir.

## ocr-lab: benchmark

```bash
./ocr-lab                                            # adım adım sorar
./ocr-lab input/tarama5.pdf -e glm-ocr                # -p verilmezse yalnız 1. sayfa
./ocr-lab input/tarama5.pdf -p 1 -e dots.mocr glm-ocr # motor yazılırsa bütün modları (dots.mocr: duzen + duz)
./ocr-lab input/tarama5.pdf -p 1-3 -e dots.mocr:duzen # yalnız bir mod
./ocr-lab rapor                                      # önceki koşuları marimo notebook'unda açar
./ocr-lab rapor --html                               # statik sayfa: output/lab/rapor.html
```

`-e` de birden fazla değer alır: PDF'leri `-e`'den önce yazın. `-p` varsayılanı `1`'dir (ocr-now'da `hepsi`). Doğru metin `gt/<pdf adı>_syf<N>.txt` varsa
ölçüler hesaplanır: ham, normalize, gövde ve harf CER, WER, satır sonu doğruluğu (`line_end`), ayın/hemze doğruluğu
(`apostrophe`) ve sarıya düşen işaret sayısı (`apostrophe_flagged`). Benchmark'ta kazanan motor/mod
`engines.DEFAULT_ENGINE` / `engines.DEFAULT_MODE` ile ocr-now'ın varsayılanı yapılır.

Her koşu `output/lab/<YYYYmmdd-HHMM>/` altına yazılır (`results.json`, motor başına ham çıktılar,
`<ad>_karsilastirma.md`). `output/lab/summary.csv` bütün koşuları biriktirir. `rapor` için `marimo` gerekir
(`kurulum.sh` kurar; elle: `VIRTUAL_ENV=.venv uv pip install -e ".[lab]"`).

## Klasörler

```
ocr-now, ocr-lab, kurulum.sh   giriş noktaları (her klasörden çalışır; göreli yollar bulunduğunuz yere göredir)
src/ocrnow/                    program; bütün yollar src/ocrnow/paths.py'de
ayarlar/kesme.txt              kullanıcının ayın/hemze kelime listesi
input/                         taranacak PDF'ler (varsayılan girdi)
gt/                            (isteğe bağlı) doğru metin: gt/tarama5_syf1.txt → ölçüler ve kelime listesi
output/belgeler/               ocr-now belgeleri (.docx, .txt)
output/_cache/                 sayfa yapısı önbelleği
output/lab/                    ocr-lab koşuları, summary.csv, rapor.html
output/_pages/                 modele giden sayfa görselleri
output/_logs/                  VLM sunucu logları: bir motor çökerse buraya bak (<motor>.server.log); gpu.lock
models/                        kraken modeli + PaddleOCR-VL GGUF bağlantıları
tools/llama.cpp/               hazır CUDA llama-server
.venv  .venv-vllm  .venv-kraken   ayrı ortamlar (vLLM ile kraken bağımlılıkları çakışıyor)
tests/                         .venv/bin/pytest
```

**`input/`, `output/` ve `gt/` asla git'e girmez** (`.gitignore`): telifli kitap metni içerirler. Testler bu
klasörlerdeki dosyaları yalnız okur, yoksa atlanır.

## Motorlar

| Motor | Nasıl çalışır | Okuma modları |
|---|---|---|
| `tesseract` | apt paketi (`tesseract-ocr-tur`) | duz |
| `kraken` | CATMuS-Print Large modeli; Türkçe'ye özgü ı/ş/ğ harfleri modelde yok | duz |
| `paddleocr-vl` | llama.cpp + GGUF, GPU | duz |
| `glm-ocr`, `lightonocr` | vLLM, bf16 | duz |
| `deepseek-ocr2` | vLLM, **fp8** (bf16 hâli 8 GB GPU'ya sığmıyor) | duz |
| `dots.mocr` | vLLM, **fp8** | duzen, duz |

Aynı anda tek GPU işi çalışır: iki koşu GPU'yu paylaşınca ikisi de çöker. Bu yüzden VLM sunucusu açıkken
`output/_logs/gpu.lock` kilidi tutulur; ikinci bir ocr-now/ocr-lab koşusu açık bir hatayla durur (kilidi tutan
süreç numarası yazılır). VLM sunucuları sırayla açılıp kapatılır. Sunucu komutları ve istemler `src/ocrnow/engines.py`'deki `ENGINES` kaydında, ortak vLLM ayarları
`VLLM_FLAGS`'te. Sunucuyu kendiniz açtıysanız (docker, ollama vs.) yalnız portu eşleyin: kayıt ona bağlanır,
kapatmaz.

## Ortama özel ayarlar (neden var?)

- `src/ocrnow/paths.py` (alt süreç ortamı) → `VLLM_USE_FLASHINFER_SAMPLER=0`: FlashInfer örnekleyici nvcc ister,
  sistemde CUDA toolkit yok.
- `kurulum.sh` (`src/ocrnow/models.py` üzerinden) vLLM 0.32'ye tek satır yama uygular: DeepSeek-OCR-2 kodlayıcı
  çekirdeği Triton 3.7'de derlenmiyor. vLLM'i elle güncellersen `./kurulum.sh`'ı tekrar çalıştır.
- `dots.mocr` → `max_pixels` 4 MP (`engines.py`): varsayılan 11 MP için bellek profili 8 GB'ı aşıyor (sayfamız
  3.9 MP, kayıp yok). Daha büyük sayfa görüntüsü küçültülerek okunur, kutular orijinal piksellere ölçeklenir.

Not: Bazı VLM'ler Markdown (`#`, `**`) üretir; bu ham CER'i biraz şişirir (normalize CER yok sayar).
