# ocr-lab

PDF'leri 7 OCR motoruyla test eder ya da seçilen motorla tam çevirir.

```bash
./kurulum.sh        # her şeyi kurar (tekrar çalıştırmak güvenli)
./run.sh            # mod, PDF, model ve sayfaları sorar
./run.sh --durum    # hangi motor hazır?
```

| Mod | Ne yapar | Çıktı |
|---|---|---|
| **Test** | Seçili modeller seçili sayfaları tarar (varsayılan: hepsi, sayfa 1) | `output/tarama5_karsilastirma_syf1.md` — tüm modeller tek dosyada, süre/karakter/CER tablosu + metinler |
| **Normal** | Tek model PDF'i çevirir (varsayılan: tüm sayfalar) | `output/<model>/tarama5_<model>.md` — tek belge, sayfalar `<!-- sayfa N -->` ile ayrılır |

Sormadan çalıştırmak için: `./run.sh -e tesseract glm-ocr -p 1-3` (test), `./run.sh -m normal -e glm-ocr` (çeviri).

Aynı anda tek `./run.sh` çalıştır — iki koşu GPU'yu paylaşınca ikisi de çöker.

## Klasörler

```
run.sh, bench.py, kurulum.sh   program
input/                   taranacak PDF'ler
gt/                      (isteğe bağlı) doğru metin: gt/tarama5_syf1.txt → CER hesaplanır
output/*_karsilastirma_*.md  test modunun karşılaştırma dosyaları
output/<motor>/          sayfa sayfa sonuçlar + normal modun tam belgesi
output/summary.csv       motor, pdf, sayfa, süre, karakter, CER (koşular birleşir)
output/_pages/           modele giden sayfa görselleri
output/_logs/            VLM sunucu logları — bir motor çökerse buraya bak
output/_eski/            önceki denemeler (gerekmiyorsa silinebilir)
models/                  kraken modeli + PaddleOCR-VL GGUF bağlantıları
tools/llama.cpp/         hazır CUDA llama-server
.venv  .venv-vllm  .venv-kraken   ayrı ortamlar (vLLM ile kraken bağımlılıkları çakışıyor)
```

## Motorlar

| Motor | Nasıl çalışır |
|---|---|
| `tesseract` | apt paketi (`tesseract-ocr-tur`) |
| `kraken` | CATMuS-Print Large modeli — Türkçe'ye özgü ı/ş/ğ harfleri modelde yok |
| `paddleocr-vl` | llama.cpp + GGUF, GPU |
| `glm-ocr`, `lightonocr` | vLLM, bf16 |
| `deepseek-ocr2`, `dots.mocr` | vLLM, **fp8** (bf16 hâlleri 8 GB GPU'ya sığmıyor) |

VLM sunucuları sırayla açılıp kapatılır. Komutlar ve istemler `bench.py` başındaki `VLM` sözlüğünde, ortak vLLM
ayarları `VLLM_FLAGS`'te.

## Ortama özel ayarlar (neden var?)

- `run.sh` → `VLLM_USE_FLASHINFER_SAMPLER=0`: FlashInfer örnekleyici nvcc ister, sistemde CUDA toolkit yok.
- `kurulum.sh` vLLM 0.32'ye tek satır yama uygular: DeepSeek-OCR-2 kodlayıcı çekirdeği Triton 3.7'de derlenmiyor.
  vLLM'i elle güncellersen `./kurulum.sh`'ı tekrar çalıştır.
- `dots.mocr` → `max_pixels` 4 MP: varsayılan 11 MP için bellek profili 8 GB'ı aşıyor (sayfamız 3.9 MP, kayıp yok).

Not: Bazı VLM'ler Markdown (`#`, `**`) üretir; bu CER'i biraz şişirir.
