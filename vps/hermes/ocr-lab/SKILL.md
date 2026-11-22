---
name: ocr-lab
description: "Use when the user sends a scanned PDF or page photo to be OCR'd (ocr, okut, metne çevir, Word'e çevir, CamScanner)."
version: 1.0.0
author: ocr-lab
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [OCR, PDF, Drive, Colab, Turkish]
---

# OCR Lab: taranmış PDF'yi okut

Bu VPS'te OCR Lab izleyicisi çalışır (`~/ocr-lab/vps/izleyici.py`, systemd kullanıcı zamanlayıcısı
`ocr-vps.timer`). Drive'daki `gdrive:ocr-lab/input/` klasörüne gelen PDF'leri Colab L4 GPU'da okur, Word ve
metin dosyasını `output/` klasörüne yazar. Başlangıç ve bitiş bildirimlerini ve Word dosyasını Telegram'a
kendisi gönderir. Senin işin yalnız dosyayı Drive'a koymak.

## Ne zaman

- Kullanıcı bir PDF ya da sayfa fotoğrafı gönderip okutmak, metne ya da Word'e çevirmek istiyor.
- Kullanıcı açıklama yazmadan yalnız bir PDF gönderdiyse kısaca sor: "OCR'a göndereyim mi?" Kitap taraması
  (CamScanner PDF'si) olduğu belliyse sormadan gönderebilirsin.

## Nasıl

Gelen dosyanın yerel yolunu (Hermes önbelleğindeki `doc_..._ad.pdf`) betiğe ver:

```bash
python3 ~/ocr-lab/vps/ocr_ekle.py "/root/.hermes/cache/documents/doc_xxxxxxxxxxxx_kitap.pdf"
```

Birden çok dosya tek komutla verilebilir. Betik `doc_..._` önekini atar, aynı ad Drive'da varsa `_2` ekler,
dosyayı yükler ve izleyiciyi hemen başlatır. Çıktısını kullanıcıya kısaca aktar.

## Yapma

- OCR'ı kendin yapmaya çalışma (vision, tesseract vb.). Okuma yalnız Colab'da bf16 ile yapılır.
- Colab'ı kendin başlatma (`colab run`), izleyici durumunu ya da `~/.local/state/ocr-lab` dosyalarını değiştirme.
  Bunlar Colab birimi harcar ya da izleyiciyi bozar.
- Sonucu bekleyip yoklama. Sonuç ve Word dosyası iş bitince Telegram'a kendiliğinden gelir (birkaç dakika ile
  bir saat arası, sayfa sayısına göre).
- Telegram botları 20 MB'tan büyük dosyaları indiremez. Dosya sana ulaşmadıysa kullanıcıya PDF'yi doğrudan
  Drive'daki `ocr-lab/input/` klasörüne atmasını söyle; gerisi aynı şekilde işler.
