"""ocr-gui sunucusu: form → ocr-now argümanları, yükleme, uç noktalar ve güvenlik denetimleri."""
import json
import sys
import textwrap
import threading
import urllib.error
import urllib.request

import pypdfium2 as pdfium
import pytest

from ocrnow import engines, paths
from ocrnow.gui import server
from ocrnow.gui.server import App, FormError, build_args, save_upload


def make_pdf(path, n):
    pdf = pdfium.PdfDocument.new()
    for _ in range(n):
        pdf.new_page(200, 300)
    pdf.save(str(path))
    pdf.close()
    return path


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INPUT", tmp_path / "input")
    monkeypatch.setattr(paths, "DOCS_OUT", tmp_path / "belgeler")
    paths.INPUT.mkdir()
    paths.DOCS_OUT.mkdir()
    make_pdf(paths.INPUT / "kitap.pdf", 5)
    return tmp_path


FORM = {"dosya": "kitap.pdf", "sayfalar": "", "ilk_sayfa": "", "formatlar": ["docx"], "isaretle": True,
        "model": "dots.mocr", "okuma": "duzen", "kesme": True, "dpi": 200}


def test_build_args_defaults(dirs):
    assert build_args(FORM) == [str(paths.INPUT / "kitap.pdf"), "-m", "dots.mocr", "-o", "duzen",
                                "--dpi", "200", "--isaretle", "-f", "docx"]


def test_build_args_all_options(dirs):
    form = FORM | {"sayfalar": " 1-3,5 ", "ilk_sayfa": "412", "formatlar": ["docx", "txt"], "isaretle": False,
                   "kesme": False, "dpi": 300}
    args = build_args(form)
    assert args[args.index("-p") + 1] == "1-3,5" and args[args.index("--ilk-sayfa") + 1] == "412"
    assert "--isaretleme" in args and "--kesme-yok" in args and args[-3:] == ["-f", "docx", "txt"]
    assert args[args.index("--dpi") + 1] == "300"


@pytest.mark.parametrize("change, message", [
    ({"dosya": "yok.pdf"}, "bulunamadı"),
    ({"dosya": "../kitap.pdf"}, "bulunamadı"),
    ({"sayfalar": "7-9"}, "(?i)sayfa"),
    ({"sayfalar": "abc"}, "(?i)sayfa"),
    ({"ilk_sayfa": "0"}, "İlk sayfa"),
    ({"ilk_sayfa": "x"}, "İlk sayfa"),
    ({"formatlar": []}, "biçim"),
    ({"formatlar": ["pdf"]}, "biçim"),
    ({"model": "yok"}, "model"),
    ({"model": "glm-ocr", "okuma": "duzen"}, "okuma"),
    ({"dpi": 10}, "DPI"),
])
def test_build_args_rejects_bad_form(dirs, change, message):
    with pytest.raises(FormError, match=message):
        build_args(FORM | change)


def test_save_upload_keeps_name_and_avoids_overwrite(dirs):
    first = save_upload("tarama 2.pdf", b"%PDF-a")
    assert first == paths.INPUT / "tarama 2.pdf" and first.read_bytes() == b"%PDF-a"
    assert save_upload("tarama 2.pdf", b"%PDF-a") == first                 # aynı içerik: aynı dosya (önbellek)
    assert save_upload("tarama 2.pdf", b"%PDF-b") == paths.INPUT / "tarama 2_2.pdf"


@pytest.mark.parametrize("name", ["x.exe", "", ".pdf"])
def test_save_upload_rejects_bad_names(dirs, name):
    with pytest.raises(FormError):
        save_upload(name, b"x")
    assert sorted(p.name for p in paths.INPUT.iterdir()) == ["kitap.pdf"]


def test_upload_path_component_is_dropped(dirs):
    assert save_upload("C:\\Users\\HP\\Desktop\\a.pdf", b"%PDF") == paths.INPUT / "a.pdf"
    assert save_upload("../../b.pdf", b"%PDF") == paths.INPUT / "b.pdf"


# ---- HTTP ----

FAKE = textwrap.dedent('''
    import json, sys
    args = sys.argv[1:]
    print("k: s. 1 (1/1) okunuyor…", flush=True)
    json.dump({"belgeler": [], "isler": [], "okunamayan": False, "args": args},
              open(args[args.index("--rapor") + 1], "w"))
''')


@pytest.fixture
def app_url(dirs, monkeypatch):
    script = dirs / "fake.py"
    script.write_text(FAKE)
    opened = []
    monkeypatch.setattr(engines, "missing", lambda name: None)
    app = App(cmd=[sys.executable, str(script)], opener=opened.append)
    httpd = server.make_server(app, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield url, app, opened
    httpd.shutdown()
    httpd.server_close()


def call(url, path, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    hdrs = {"Content-Type": "application/json"} if isinstance(body, dict) else {}
    req = urllib.request.Request(url + path, data, hdrs | (headers or {}), method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_index_page(app_url):
    status, body = call(app_url[0], "/")
    assert status == 200 and b"<title>" in body


def test_state_lists_inputs_and_models(app_url):
    _, body = call(app_url[0], "/api/durum")
    data = json.loads(body)
    assert data["dosyalar"] == [{"ad": "kitap.pdf", "sayfa": 5}]
    names = [m["ad"] for m in data["modeller"]]
    assert names[0] == "dots.mocr" and "tesseract" not in names    # varsayılan başta; tesseract yardımcıdır
    assert data["varsayilan"]["model"] == "dots.mocr" and data["is"] is None


def test_start_job_runs_and_reports(app_url):
    url, app, _ = app_url
    status, body = call(url, "/api/baslat", FORM)
    assert status == 200, body
    assert app.job.wait(20)
    snap = json.loads(call(url, "/api/is")[1])
    assert snap["durum"] == "bitti" and snap["ad"] == "kitap.pdf"
    assert snap["sonuc"]["args"][0] == str(paths.INPUT / "kitap.pdf")


def test_start_rejects_bad_form_with_message(app_url):
    status, body = call(app_url[0], "/api/baslat", FORM | {"sayfalar": "9"})
    assert status == 400 and "sayfa" in json.loads(body)["hata"]


def test_second_job_while_running_is_refused(app_url):
    url, app, _ = app_url

    class Busy:
        running = True
    app.job = Busy()
    status, _ = call(url, "/api/baslat", FORM)
    assert status == 409


def test_post_without_json_content_type_is_refused(app_url):
    """Başka bir sitenin sayfası (CSRF) basit form gönderisiyle iş başlatamasın."""
    status, _ = call(app_url[0], "/api/baslat", json.dumps(FORM).encode(),
                     {"Content-Type": "text/plain"}, method="POST")
    assert status == 403


def test_foreign_host_header_is_refused(app_url):
    """DNS yeniden bağlama: localhost dışı Host başlığıyla gelen istek reddedilir."""
    status, _ = call(app_url[0], "/api/durum", headers={"Host": "kotu.example:80"})
    assert status == 403


def test_upload_endpoint(app_url):
    data = make_pdf(paths.INPUT.parent / "src.pdf", 3).read_bytes()
    status, body = call(app_url[0], "/api/yukle", data,
                        {"X-Dosya-Adi": urllib.parse.quote("yeni ğ.pdf")}, method="POST")
    assert status == 200 and json.loads(body) == {"ad": "yeni ğ.pdf", "sayfa": 3}
    assert (paths.INPUT / "yeni ğ.pdf").read_bytes() == data


def test_unreadable_upload_is_refused_and_removed(app_url):
    status, body = call(app_url[0], "/api/yukle", b"bozuk", {"X-Dosya-Adi": "bozuk.pdf"}, method="POST")
    assert status == 400 and "hata" in json.loads(body)
    assert not (paths.INPUT / "bozuk.pdf").exists()


def test_upload_without_name_header_is_refused(app_url):
    status, _ = call(app_url[0], "/api/yukle", b"%PDF-x", {"Content-Type": "application/pdf"}, method="POST")
    assert status == 403


def test_document_download_only_from_docs_folder(app_url):
    (paths.DOCS_OUT / "k.docx").write_bytes(b"docx")
    status, body = call(app_url[0], "/belge/k.docx")
    assert status == 200 and body == b"docx"
    assert call(app_url[0], "/belge/..%2Finput%2Fkitap.pdf")[0] == 404
    assert call(app_url[0], "/belge/yok.docx")[0] == 404


def test_open_folder_endpoint(app_url):
    url, _, opened = app_url
    (paths.DOCS_OUT / "k.docx").write_bytes(b"docx")
    assert call(url, "/api/goster", {"yer": "belge", "ad": "k.docx"})[0] == 200
    assert call(url, "/api/goster", {"yer": "tarama", "ad": "kitap.pdf"})[0] == 200
    assert call(url, "/api/goster", {"yer": "belge", "ad": "../input/kitap.pdf"})[0] == 404
    assert opened == [paths.DOCS_OUT / "k.docx", paths.INPUT / "kitap.pdf"]


def test_health_endpoint_identifies_gui(app_url):
    assert json.loads(call(app_url[0], "/api/saglik")[1]) == {"ocr-gui": True}
