# Aranabilir PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `-f pdf` çıktısını, orijinal CamScanner sayfasının üstüne satır satır oturan, tiresiz kopyalanan görünmez
metin katmanı olarak yeniden yazmak ve telefon/VPS akışında Word'ün yanında PDF de üretip Telegram'a göndermek.

**Architecture:** Satırların piksel kutuları hizalama adımında `Line.box`'a yazılır. Saf bir modül
(`pdf_textlayer.py`), `Page`'den yerleştirilecek satırları (metin + piksel kutusu) üretir. `export_pdf.py`, pikepdf
ile kaynak PDF sayfasını değiştirmeden kopyalar ya da görselden sayfa kurar. Sonra görünmez Type0 fontla satırları
ekleyen ayrı bir içerik akışı ekler. Colab telefon işi `pdf`'yi de ister, VPS de onu Telegram'a gönderir.

**Tech Stack:** Python 3.12, pikepdf (yeni), pypdfium2 (testlerde çıkarım ve kutu ölçümü), Pillow, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-aranabilir-pdf-design.md`

## Global Constraints

- Kaynak PDF sayfasının mevcut içerik akışları ve görüntüleri **bayt bayt değişmez**. Katman yalnız `/Contents`
  dizisine eklenen yeni akışlarla gelir: başa `q`, sona `Q` + katman.
- Görünmez metin: `3 Tr`. Kitapta görünen hiçbir şey eklenmez.
- `Line.hyphen == True` olan satırın sonundaki kelime katmanda bütün yazılır; sonraki satır o kelimenin kalanını atlar.
- RTL metin mantıksal sırada yazılır (`[::-1]` yok).
- ActualText varsayılanı **kapalı** (`actual_text=False`). CLI bayrağı yok.
- Kaynakta zaten metin varsa o sayfaya katman eklenmez + uyarı.
- PDF yazılamazsa öbür biçimler yine yazılır (`cli._write_job` bugün böyle; bozulmamalı).
- Telegram sınırı: `50 * 1024 * 1024` bayt üstü PDF gönderilmez, Drive'da olduğu yazılır.
- Testler sentetik sayfalarla yazılır; kullanıcı taramaları ya da telifli metin Git'e girmez.
- Colab/VPS'te canlı deneme yapılmaz (birim harcar).
- Tüm testler: `.venv/bin/python -m pytest -q` (bugün 727 test geçiyor).

## Review Focus

- Okuma `--dpi 300` ile yapılmışken katman kaymamalı; ölçek sayfa boyutundan gelir, DPI'dan değil → Task 3 testi `test_pdf_layer_scales_from_page_size_not_dpi`.
- `/Rotate 90` ve kaydırılmış CropBox'lı CamScanner sayfası → Task 3 testi `test_pdf_layer_on_rotated_page`.
- Aynı işte iki farklı PDF'den sayfa (`--birlestir`) ve görsel girdiler karışık → Task 3 testi `test_pdf_mixes_two_pdfs_and_image`.
- İş bittiğinde kaynak dosya artık yok (önbellekten yeniden yazdırma) → Task 3 testi `test_pdf_missing_source_gets_placeholder_with_warning`.
- Kelime ikinci satırın tamamını kaplıyorsa (çok kısa satır) satır boş kalmalı, metin yinelenmemeli → Task 2 testi `test_hyphen_word_spanning_whole_next_line`.

---

### Task 1: Satır kutusunu `Line`'a yazmak

**Files:**
- Modify: `src/ocrnow/layout.py` (`Line` dataclass, `page_from_json`)
- Modify: `src/ocrnow/align.py` (`apply_line_structure`)
- Test: `tests/test_align.py`, `tests/test_layout.py`

**Interfaces:**
- Produces: `Line.box: tuple[int, int, int, int] | None = None`. Piksel cinsinden `(x1, y1, x2, y2)`, sayfanın
  okunan görüntüsünde, y aşağı doğru artar. Satır yapısı kurulduğunda `block.lines[i].box == tlines[i].box`.
  Kurulamadığında tek `Line(0, len, False)` satırının `box`'ı `None` olur.

- [ ] **Step 1: Failing tests yaz.**
  - `test_apply_line_structure_sets_line_boxes`: iki `TessLine`'lı bir blokta (ör. kutular `(10,10,300,30)`,
    `(10,40,200,60)`) `apply_line_structure` sonrası `[l.box for l in block.lines] == [(10,10,300,30),(10,40,200,60)]`.
  - `test_apply_line_structure_fallback_has_no_box`: `tlines=[]` → `block.lines == [Line(0, len(text), False)]`
    ve `block.lines[0].box is None`.
  - `test_page_json_roundtrip_keeps_line_box`: `page_from_json(page_to_json(p))` sonrası `box` yine tuple ve eşit.
    Eski JSON'da (`box` anahtarı yok) da yüklenmeli → `box is None`.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q tests/test_align.py tests/test_layout.py` → yeni testler FAIL.
- [ ] **Step 3:** `Line`'a `box` alanını ekle (varsayılan `None`, sona). `apply_line_structure` başarılı dalda
  her satıra `tlines[i].box` atasın; `rebreak` `len(tlines)` satır döndürür. `page_from_json` listeyi tuple'a
  çevirsin.
- [ ] **Step 4:** Tüm testler: `.venv/bin/python -m pytest -q` → hepsi PASS. Var olan Line eşitlik testleri
  `box` yüzünden kırılırsa beklenen değeri düzelt; davranışı değiştirme.
- [ ] **Step 5:** Commit: `feat(pdf): satır kutusu Line.box'a yazılıyor`

### Task 2: Katman satırlarını üreten saf modül

**Files:**
- Create: `src/ocrnow/pdf_textlayer.py`
- Test: `tests/test_pdf_textlayer.py`

**Interfaces:**
- Consumes: `Line.box` (Task 1), `export_txt.plain_text`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class LayerLine:
      text: str
      box: tuple[float, float, float, float]   # piksel (x1, y1, x2, y2), y aşağı
      block: int                               # sayfadaki blok sırası (ActualText gruplaması için)

  def layer_lines(page: Page) -> list[LayerLine]
  ```

Kurallar:
- `page.failed` → `[]`. `Picture` blokları ve boş metinli bloklar atlanır.
- Bütün satırları kutulu blok: satır metni `block.text[start:end]`. Tiresiz birleştirme şöyle yapılır:
  hyphen=True satırın sonu, `block.text` içinde sonraki boşluğa (yoksa metin sonuna) uzatılır. Sonraki satırın
  başı `max(next.start, kelime_sonu)` olur ve baştaki boşluklar atılır. Boş kalan satır üretilmez. Metin `.strip()`
  edilir.
- Kutusuz blok (fallback): metin `plain_text(block)`. Satır yüksekliği `lh` = sayfadaki kutulu satırların
  yükseklik medyanı; hiç yoksa `page.height / 50`. Satır sayısı
  `k = max(1, ceil(len(metin) * 0.5 * lh / genişlik))`; bir de bloğa sığacak kadarla sınırlanır:
  `max(1, floor(yükseklik / lh))`. Metin kelime sınırlarından yaklaşık eşit uzunlukta `k` satıra bölünür. Satırlar
  blok kutusunun üstünden başlayıp `lh` aralıkla dizilir. Blok kutusu yoksa kutu olarak sayfanın %5 kenar
  paylı alanı kullanılır.

- [ ] **Step 1: Failing tests yaz** (`Page`/`Block`/`Line` elle kurulur):
  - `test_lines_follow_boxes`: iki kutulu satır → `[(t.text, t.box) for t in out] == [("bu senâ…", box1), …]`.
  - `test_hyphenated_word_kept_whole_on_first_line`: `text="bu senâkâr Vilâyet-i Haleb"`,
    `lines=[Line(0,7,True,(0,0,100,20)), Line(7,26,False,(0,30,100,50))]` → metinler
    `["bu senâkâr", "Vilâyet-i Haleb"]`.
  - `test_existing_hyphen_not_joined`: `text="Defter-dâr oldu"`, `lines=[Line(0,7,False,b1), Line(7,15,False,b2)]`
    → `["Defter-", "dâr oldu"]`.
  - `test_hyphen_word_spanning_whole_next_line`: `text="ab cdefgh ij"`,
    `lines=[Line(0,5,True,b1), Line(5,9,False,b2), Line(9,12,False,b3)]` → `["ab cdefgh", "ij"]`
    (ikinci satır boş kalır, üretilmez).
  - `test_rtl_kept_in_logical_order`: `"بسم الله"` → `out[0].text == "بسم الله"`.
  - `test_failed_page_has_no_layer`: `page.failed=True` → `[]`.
  - `test_unboxed_block_spread_inside_bbox`: kutusuz, 200 karakterlik metinli blok `(100,100,500,400)`. Sayfada
    yüksekliği 20 olan bir kutulu satır var. Beklenen: birden fazla satır çıkar, kutuların hepsi blok kutusu
    içinde kalır, `" ".join(texts) == metin`.
  - `test_picture_block_skipped`.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q tests/test_pdf_textlayer.py` → FAIL (modül yok).
- [ ] **Step 3:** `layer_lines`'ı yukarıdaki kurallarla yaz.
- [ ] **Step 4:** Aynı komut → PASS.
- [ ] **Step 5:** Commit: `feat(pdf): katman satırları (tiresiz birleştirme, kutusuz blok yerleşimi)`

### Task 3: pikepdf ile orijinal sayfaya katman yazan `write_pdf`

**Files:**
- Modify: `pyproject.toml` (`dependencies`'e `"pikepdf"`), `uv.lock` (`uv lock`), `.venv` (`uv sync`)
- Rewrite: `src/ocrnow/export_pdf.py`
- Modify: `src/ocrnow/cli.py:29` (exporter'a uyarı listesini ver)
- Test: `tests/test_pdf_and_gui_qol.py` (eski PDF testini yenisiyle değiştir) ya da yeni `tests/test_export_pdf.py`

**Interfaces:**
- Consumes: `pdf_textlayer.layer_lines(page) -> list[LayerLine]` (Task 2).
- Produces: `write_pdf(pages: list[Page], out: Path, warnings: list[str] | None = None, *, actual_text: bool = False) -> Path`.
  `cli.EXPORTERS["pdf"]` bunu `warnings=settings["warnings"]` ile çağırır.

Davranış:
- Sayfalar `pages` sırasıyla eklenir. Kaynak `.pdf` ise o PDF pikepdf ile açılır; aynı kaynak tek kez açılır ve
  kaydetmeye dek açık tutulur. Sayfa `out_pdf.pages.append(src.pages[page.index - 1])` ile eklenir.
- Görsel kaynak: JPEG ise baytlar yeniden kodlanmadan `/DCTDecode` ile gömülür. Öbürleri RGB/L'ye çevrilip
  kayıpsız gömülür (Flate). Sayfa boyutu `px * 72 / dpi`; dpi görselin `info["dpi"]` bilgisinden gelir, yoksa
  `pages.DEFAULT_DPI`.
- Kaynak dosya yoksa: `page.width × page.height` px'lik beyaz sayfa (`DEFAULT_DPI` ile) + katman + uyarı
  `"pdf: {kaynak adı} bulunamadı; s. {index} boş sayfa üstüne yazıldı"`.
- Kaynak PDF sayfasında zaten metin varsa katman eklenmez. Denetim pdfium ile yapılır: boşluk dışı karakter
  sayısı `>= 10` ise metin var sayılır. Uyarı: `"pdf: {kaynak adı} s. {index} zaten metin katmanı içeriyor;
  aranabilir katman eklenmedi"`.
- Ölçek: sayfanın görünen boyutu (CropBox, yoksa MediaBox; `/Rotate` 90/270'te en ve boy yer değiştirir)
  `Wv × Hv` pt. `sx = Wv / page.width`, `sy = Hv / page.height`. Satır kutusu görünen alana şöyle çevrilir
  (y yukarı): `u1 = x1*sx`, `u2 = x2*sx`, `yb = Hv - y2*sy`, `yt = Hv - y1*sy`.
- Görünen alandan sayfa uzayına geçiş, katmanın başına yazılan tek bir `cm` ile yapılır. CropBox `[cx0 cy0 cx1 cy1]`,
  `w = cx1-cx0`, `h = cy1-cy0`:

  | `/Rotate` | `cm` (a b c d e f) |
  |---|---|
  | 0 | `1 0 0 1 cx0 cy0` |
  | 90 | `0 1 -1 0 cx0+w cy0` |
  | 180 | `-1 0 0 -1 cx1 cy1` |
  | 270 | `0 -1 1 0 cx0 cy1` |

  (`/Rotate` 360'a göre normalize edilir; negatif değer de olabilir.)
- Satır yazımı: font boyu `fs = (yt - yb) / 1.1`, taban çizgisi `yb + 0.2*fs`. Glif genişliği sabit `DW 500`
  (yarım em). Yatay ölçek `Tz = 100 * (u2-u1) / (len(text) * 0.5 * fs)`. Komut sırası:
  `BT 3 Tr /F0 fs Tf Tz 1 0 0 1 u1 taban Tm <hex> Tj ET` (bir BT içinde de olabilir).
- Font: bugünkü glifsiz Type0/Identity-H + CIDFontType2 + ToUnicode CMap yapısı korunur. CID ataması belge
  geneline tek haritadır. Font ve CMap tek nesnedir ve her sayfanın `/Resources /Font`'una, mevcut font
  adlarıyla çakışmayan bir adla (ör. `/OcrNowF0`) eklenir.
- İçerik: `/Contents`'in başına `q`, sonuna `Q` + `cm` + katman akışı eklenir (pikepdf `Page.contents_add`).
  Mevcut akışlar birleştirilmez.
- `actual_text=True`: aynı `block` değerli satırlar `/Span << /ActualText <FEFF…> >> BDC … EMC` ile sarılır.
  ActualText metni, blok satır metinlerinin boşlukla birleşimidir.
- Atomik yazım bugünkü gibi `paths.atomic_output(out)` ile yapılır.

- [ ] **Step 1:** `uv add pikepdf` (ya da `pyproject.toml` + `uv lock` + `uv sync`). Ardından
  `.venv/bin/python -c "import pikepdf"` OK.
- [ ] **Step 2: Failing tests yaz.** Fikstür: Pillow ile gri desenli bir görselden tek sayfalık PDF üret
  (`img.save(path, "PDF", resolution=…)`). `MediaBox [0 0 600 800]` olsun; bunun için 1200×1600 px görseli
  144 dpi ile kaydet. `Page(width=1200, height=1600)`; bir satır kutusu `(100,200,700,240)` px, metni
  `"İstanbul şehri ğşçıöü"`. Testler:
  - `test_pdf_keeps_original_page_bytes`: Kaynağın ilk içerik akışının `read_raw_bytes()` değeri ve görüntü
    XObject'inin ham baytları çıktıda aynen bulunur. Sayfanın MediaBox'ı da aynı kalır.
  - `test_pdf_text_extracts_and_sits_on_line`: pdfium çıkarımı `"İstanbul şehri ğşçıöü"` içerir. Her karakter
    kutusu (`textpage.get_charbox(i)`) `x ∈ [48, 352]`, `y ∈ [676, 702]` aralığındadır. Son karakterin sağ
    kenarı `>= 340`'tır.
  - `test_pdf_layer_scales_from_page_size_not_dpi`: Aynı kaynak. `Page(width=1800, height=2400)` ve kutu
    `(150,300,1050,360)` ile de aynı aralıklar tutar.
  - `test_pdf_layer_on_rotated_page`: Fikstürde `/Rotate 90` ve `CropBox [10 20 610 820]` var; görünen alan
    800×600 pt. `Page(width=1600, height=1200)`, kutu `(100,200,700,240)` → görünende u 50..350, v 100..120.
    Sayfa uzayında beklenen: `X ≈ cx0 + v` yani `110..130`, `Y ≈ cy0 + u` yani `70..370`. Karakter kutuları
    `x ∈ [108,132]`, `y ∈ [68,372]` içindedir.
  - `test_pdf_hyphen_and_rtl_extract`: tireli iki satır `"senâkâr"` bütün çıkar, `"kâr"` tek başına
    tekrarlanmaz. Arapça satır `"بسم الله"` içerir.
  - `test_pdf_skips_layer_when_source_has_text`: Kaynağa pikepdf ile görünür metin içeren bir sayfa yazılır
    (≥10 karakter, Helvetica). Çıktıda o sayfanın çıkarımı kaynağınkine eşittir ve `warnings`'te
    `"zaten metin katmanı"` geçer.
  - `test_pdf_mixes_two_pdfs_and_image`: iki ayrı fikstür PDF'si + bir JPEG görsel → 3 sayfa, doğru sırada. JPEG
    sayfasında `/DCTDecode` filtreli görüntünün baytları kaynak JPEG'e eşittir.
  - `test_pdf_missing_source_gets_placeholder_with_warning`: `source` yok → 1 sayfa, metin çıkar, uyarıda
    `"bulunamadı"` geçer.
  - `test_pdf_failed_page_has_no_text`: `failed=True` → sayfa var, çıkarım boş.
  - `test_pdf_actual_text_flag`: `actual_text=True` ile içerik akışında `/ActualText` bulunur. Varsayılanda bulunmaz.
  - `test_cli_pdf_exporter_passes_warnings`: `cli.EXPORTERS["pdf"](pages, out, {"warnings": w, …})` eksik
    kaynakta `w`'ye uyarı ekler.
- [ ] **Step 3:** `.venv/bin/python -m pytest -q tests/test_export_pdf.py` → FAIL.
- [ ] **Step 4:** `export_pdf.py`'yi yukarıdaki davranışla yeniden yaz. Eski elle PDF nesnesi yazan kod
  (`_page_image_jpeg`, xref üretimi, `_visual_order`) kaldırılır. `cli.py:29`'u güncelle.
- [ ] **Step 5:** Tüm testler: `.venv/bin/python -m pytest -q` → PASS. Eski
  `test_searchable_pdf_embeds_image_and_turkish_arabic_unicode` yeni API ile geçmeli ya da yeni testlere taşınmalı.
- [ ] **Step 6:** Uçtan uca duman testi (GPU'suz): `tesseract` motoruyla
  `./ocr-now input/tarama1.pdf -p 1 -m tesseract -f pdf`. Çıkan PDF pdfium ile açılır, metin çıkar ve sayfa
  boyutu kaynakla aynıdır. (Kullanıcının taraması yalnız yerelde kullanılır; çıktı Git'e girmez.)
- [ ] **Step 7:** Commit: `feat(pdf): aranabilir PDF orijinal sayfanın üstüne satır satır katman (pikepdf)`

### Task 4: Telefon/VPS akışında PDF ve belgeler

**Files:**
- Modify: `colab/runner.py` (`TELEFON_FORMATLARI` sabiti; `telefon_calistir`'ın varsayılan `ocr`'ı onu kullanır)
- Modify: `vps/colab_is.py:111` (`runner.ocr_calistir(..., formatlar=runner.TELEFON_FORMATLARI)`)
- Modify: `vps/izleyici.py` (`belgeleri_ilet`: `TELEGRAM_SINIRI`)
- Modify: `README.md` (`-f pdf` anlatımı + telefon akışı "Word ve aranabilir PDF"), `vps/hermes/ocr-lab/SKILL.md`,
  `CHANGELOG.md`
- Test: `tests/test_colab_runner.py`, `tests/test_vps.py`

**Interfaces:**
- Produces: `runner.TELEFON_FORMATLARI = "docx pdf txt"`; `izleyici.TELEGRAM_SINIRI = 50 * 1024 * 1024`.
- `ocr_calistir`'ın kendi `formatlar="docx txt"` varsayılanı değişmez (ocr_now.ipynb aynı kalır).

- [ ] **Step 1: Failing tests yaz.**
  - `test_telefon_default_ocr_asks_pdf`: `telefon_calistir` `_ocr` verilmeden çağrılır. `ocr_calistir`
    monkeypatch edilir ve aldığı `formatlar == "docx pdf txt"` olur. Var olan test kalıbına uyulur.
  - `test_colab_is_requests_pdf`: `vps/colab_is.py` kaynağında `formatlar=runner.TELEFON_FORMATLARI` geçer, ya
    da var olan colab_is testlerinin kalıbıyla çağrı argümanı denetlenir.
  - `test_belgeleri_ilet_sends_pdf`: `belgeler=["a.docx","a.pdf","a.txt"]` → gönderilen mesajda `.docx` ve
    `.pdf` için `MEDIA:` satırı vardır, `.txt` için yoktur.
  - `test_belgeleri_ilet_skips_oversized_pdf`: sahte `_run` `a.pdf`'yi `TELEGRAM_SINIRI + 1` baytlık yazar → `MEDIA`
    satırı yok; mesajda `"Telegram için çok büyük"` ve `"output/"` geçer. `.docx` yine gönderilir.
- [ ] **Step 2:** `.venv/bin/python -m pytest -q tests/test_colab_runner.py tests/test_vps.py` → FAIL.
- [ ] **Step 3:** Uygula. Büyük dosya indirildikten sonra boyutu ölçülür ve silinir. Mesaj satırı:
  `f"Telegram için çok büyük, Drive'daki output/ klasöründe: {ad}"`.
- [ ] **Step 4:** Belgeleri güncelle:
  - README'de `-f pdf` satırı: orijinal sayfa korunur, metin satır satır, tireli kelime bütün kopyalanır. Çok
    satırlı kopya okuyucuya göre satır satır gelir.
  - Telefon akışında Drive/Telegram'a "Word ve aranabilir PDF" gelir.
  - SKILL.md'deki kullanıcıya söylenen cümle de aynı şekilde güncellenir.
  - CHANGELOG'a "Unreleased" girişi eklenir.
- [ ] **Step 5:** Tüm testler: `.venv/bin/python -m pytest -q` → PASS.
- [ ] **Step 6:** Commit: `feat(vps): telefon akışı aranabilir PDF de üretir ve Telegram'a gönderir`

### Bitiş

- [ ] Bütün dalın incelemesi (subagent-driven-development son adımı).
- [ ] `git push` (kullanıcı 2026-11-23'te sormadan push'a izin verdi; VPS bir sonraki çalışmada `git pull` ile alır).
- [ ] Kullanıcıya elle kontrol listesi bırakılır. Telefonda (Drive ve Telegram) ve Windows'ta (Edge/Chrome)
  şunlar denenir: arama vurgusu, satır seçimi, çok satırlı kopya, tireli kelime. ActualText'in açılıp
  açılmayacağı bu sonuçlara göre kararlaştırılır.
