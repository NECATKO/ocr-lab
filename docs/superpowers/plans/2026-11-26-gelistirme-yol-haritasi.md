# OCR Lab geliştirme yol haritası

Akış: CamScanner PDF'si Drive `input/` veya Telegram üzerinden gelir, Colab L4'te bf16 ile okunur, Word, aranabilir
PDF ve TXT olarak Drive'a ve Telegram'a teslim edilir. İlk taslak 2026-11-26'da yazıldı; aynı gün kodla
karşılaştırıldı, ana hatalar düzeltildi ve uçtan uca canlı denendi. Bu belge artık durumu ve kalan işleri tutar.

## Yapılanlar (2026-11-26)

| # | Sorun | Çözüm | Commit |
|---|---|---|---|
| 1 | Colab kurulum hatası `OCR-VPS-SONUC 3` yazınca izleyici işi tamamlanmış sayıyor, başlamamış PDF'leri kalıcı olarak engelliyordu. | Sonuç kodu yalnız kendi satırından okunur. 2/3 tamamlanmış sayılmaz: dosyalar kuyrukta kalır, 30 dk sonra yeniden denenir, iki ardışık hatada bildirilip bırakılır. | `3c32ec6` |
| 2 | 4 saatlik sınır yalnız yeni satır gelince kontrol ediliyordu; sessiz/kilitlenen iş GPU'yu açık tutabiliyordu. | Çıktı ayrı iş parçacığında okunur, süreyi ana süreç ölçer. Uzak oturumu durdurma komutunun da süre sınırı var. | `3c32ec6` |
| 3 | Tek bir çıktı yazılınca kaynak PDF `islenenler/`'e taşınıyordu (Word eksik olsa bile). | Kaynak yalnız docx, pdf ve txt'nin üçü yazılınca taşınır; eksik biçim bildirimde görünür. | `0b453a5` |
| 4 | `bekleyen_yukleme` sorgu hatasında 0 döndürüyor, bağlantı yükleme bitmeden kaldırılabiliyordu. | Bilinmeyen durum boş kuyruk sayılmaz; 30 dk sonunda doğrulanamazsa kullanıcı uyarılır. Bozuk `vfs/stats` yanıtı artık sonuç satırını engellemez. | `0b453a5` |
| 5 | Telegram gönderimi başarısız olursa belge bir daha gönderilmiyordu. | Teslim edilemeyen belgeler izleyici durumuna yazılır; her tur (GPU beklemesi ve boş kuyruktan önce) yalnız onları dener, en fazla 3 kez. | `c320050` |
| 6 | Kesinti, sırada bekleyen başlamamış kitapların deneme hakkını da düşürüyordu. | Colab her dosyaya başlarken `OCR-VPS-DOSYA` basar; yalnız başlamış dosyanın sayacı artar. | `3c697b5` |
| 7 | Telefon özeti yalnız "N uyarı" diyordu. | CLI raporunda `inceleme` listesi: "PDF s. 2 · kitap s. 75 · tekrar döngüsü, beklenmedik karakter (总数)". Önem sırasıyla ilk 5 sayfa. | `47049c3` |
| 8 | Modelin atladığı dipnot/satır uyarısız geçebiliyordu. | `uncovered_text`: hiçbir bloğun kapsamadığı ve metni model çıktısında olmayan anlamlı tesseract satırları için "olası eksik metin (alt bölüm)" uyarısı. Metin Word'e eklenmez. | `3db3446` |

**Canlı deneme (2026-11-26).** `tarama1.pdf`'nin 3 sayfalık kesiti Drive `input/`'a kondu. L4 bf16, yaklaşık 9 dk;
docx/pdf/txt Drive'da, kaynak arşivlendi, PDF aranabilir, Word ve PDF Telegram'a ulaştı. Hata yolları (kurulum
hatası, kesinti, Telegram arızası) yalnız yerel testlerle sınandı.

**Eksik metin uyarısının ölçümü.** Önbellekteki 46 gerçek sayfada (tarama1, tarama5; eski fp8 okumaları) yanlış
alarm 0. Dipnot blokları silinerek taklit edilen kayıplarda 33 sayfanın 32'si yakalandı; kaçan, iki tek sözcüklük
"Ay. yer." dipnotu. Örnek az ve kayıp taklit; gerçek değerlendirme seti gelince yeniden ölçülmeli.

## Bilerek yapılmayanlar

- **Aranabilir PDF'de filigran/kısmi metin katmanı birleştirme.** İlk taslak, yalnız seçilebilir "CamScanner"
  yazısı olan sayfaların metinli sayılıp OCR katmanı almadığını varsaydı. Elimizdeki iki gerçek CamScanner
  PDF'sinde (üretici `intsig.com`) hiç seçilebilir metin yok; sorun kullanıcının dışa aktarma ayarlarıyla oluşmuyor.
  Bölgesel birleştirme büyük bir iş ve metni iki kez kopyalama riski taşıyor. Başka ayarla dışa aktarılmış bir PDF'de
  sorun görülürse yeniden ele alınır (`export_pdf._has_text`, `TEXT_MIN_CHARS`).
- **Ayrıntılı iş kaydı, içerik özetiyle uzak doğrulama, `--yeniden-dene` komutu.** Tek kullanıcılı akışta yukarıdaki
  sade düzeltmeler ana veri kaybı risklerini kapattı.

## Kalan işler

1. **Gerçek değerlendirme seti (kullanıcı sayfaları seçer).** Birkaç kitaptan 20–30 CamScanner sayfası: temiz gövde,
   yoğun dipnot, cilt kenarında gölge/eğrilik, ayın/hemze. Geliştirme ve son değerlendirme yarıları ayrı; ardışık
   sayfalar aynı yarıda. Referanslar ve taramalar Git dışında. Ölçüler: atlanan gövde satırı, tamamen/yarım eksik
   dipnot, yanlış basılı sayfa numarası, uyarısız eksik sayfa, gereksiz uyarı, ayın/hemze doğruluğu.
2. **Eksik metin eşiklerini bu sette yeniden ölç.** Özellikle tek sözcüklük dipnotlar ("Ay. yer.") ve gövdeden tek
   satır atlanması şu an yakalanmıyor.
3. **Yalnız eksik biçimi üretmek.** Word yazılamazsa kullanıcı PDF'yi yeniden yüklemek zorunda; PDF ve TXT `_2`
   ekiyle ikinci kez oluşuyor. Önbellekten yalnız eksik biçimi dışa aktarmak GPU okuması gerektirmez.
4. **200 / 300 DPI kararı (Colab birimi harcar; önce kullanıcıya sorulur).** Değerlendirme setinin geliştirme
   yarısında aynı model ve bf16 ile; değişken yalnız DPI. Karar ölçütü: kurtarılan dipnotlar, yeni kayıplar, yanlış
   sayfa numaraları ve ortanca ek süre. Kazanç yalnız küçük puntolu dipnotlarda ise seçili sayfaları 300 DPI'da
   yeniden okumak ayrıca değerlendirilir.
5. **Bilinen model hatası.** `tarama1` kitap s. 75'te bf16 açgözlü kod çözme "terör" yerine "总数" yazıyor; tekrar
   cezasıyla yeniden okuma düzeltmiyor. Uyarı bildirimde sayfa numarasıyla görünüyor; kalıcı çözüm DPI/model
   deneyinin konusu.
