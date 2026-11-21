"""Faz 2 testleri: harici OpenAI-uyumlu API motoru, profile özel prompt'lar ve yapısal JSON/CSV fatura çıktısı."""
import csv
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading

import pytest
from PIL import Image

from ocrnow import engines, export_structured, identity, paths, profiles
from ocrnow.gui import server as gui_server
from ocrnow.layout import Block, Page


def _page(text: str, *, source: str = "fatura.png", index: int = 1) -> Page:
    return Page(source=source, index=index, width=800, height=1200, engine="api", mode="duz",
                blocks=[Block("Text", None, text)])


def test_api_config_file_and_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS", tmp_path)
    for var in ("OCR_API_BASE", "OPENAI_BASE_URL", "OCR_API_MODEL", "OCR_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    assert engines.missing("api") is not None
    engines.save_api_config("http://localhost:11434/v1/", "qwen2.5vl", "secret-123")
    cfg = engines.api_config()
    assert cfg == {"base_url": "http://localhost:11434/v1", "model": "qwen2.5vl", "api_key": "secret-123"}
    assert engines.missing("api") is None

    # Boş/None api_key ile kaydedince mevcut anahtar korunur
    engines.save_api_config("https://api.example.com/v1", "gpt-4o", None)
    assert engines.api_config()["api_key"] == "secret-123"

    # Ortam değişkeni dosyayı ezer
    monkeypatch.setenv("OCR_API_MODEL", "gemini-2.5-flash")
    assert engines.api_config()["model"] == "gemini-2.5-flash"


def test_api_identity_excludes_key_and_uses_prompt_override(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS", tmp_path)
    for var in ("OCR_API_BASE", "OPENAI_BASE_URL", "OCR_API_MODEL", "OCR_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    engines.save_api_config("https://api.example.com/v1", "gpt-4o", "gizli-anahtar-xyz")

    rt = identity.runtime("api", tess={"version": None, "language": "tur", "data_files": None})
    p1 = rt.profile("duz", prompt_override=profiles.get("fatura").prompt)
    p2 = rt.profile("duz", prompt_override=profiles.get("ders-notu").prompt)
    assert p1["cacheable"] is True
    assert p1["id"] != p2["id"]
    assert "gizli-anahtar-xyz" not in json.dumps(p1)


def test_api_server_sends_auth_and_prompt_override(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS", tmp_path)
    for var in ("OCR_API_BASE", "OPENAI_BASE_URL", "OCR_API_MODEL", "OCR_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    received: dict = {}

    class FakeAPI(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received["auth"] = self.headers.get("Authorization")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received["body"] = body
            resp = json.dumps({"choices": [{"message": {"content": "API cevabı"}, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    srv = HTTPServer(("127.0.0.1", 0), FakeAPI)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        engines.save_api_config(f"http://127.0.0.1:{port}/v1", "test-model", "tok-99")
        img = tmp_path / "p.png"
        Image.new("RGB", (40, 40), "white").save(img)
        with engines.server("api", prompt_override="ÖZEL İSTEM") as run:
            out = run(img, "duz")
        assert out == "API cevabı"
        assert received["auth"] == "Bearer tok-99"
        assert received["body"]["model"] == "test-model"
        assert received["body"]["messages"][0]["content"][1]["text"] == "ÖZEL İSTEM"
    finally:
        srv.shutdown()
        srv.server_close()


def test_structured_json_and_csv_from_model_json(tmp_path: Path):
    payload = '```json\n{"satici":"Kırtasiye AŞ","tarih":"2026-11-20","fatura_no":"INV-42","kdv":"20.00","toplam":"120.00","kalemler":[{"aciklama":"Defter","adet":2,"birim_fiyat":"50","tutar":"100"}]}\n```'
    pages = [_page(payload)]
    jout = export_structured.write_json(pages, tmp_path / "fatura.json")
    data = json.loads(jout.read_text(encoding="utf-8"))
    assert data[0]["veri"]["satici"] == "Kırtasiye AŞ"
    assert data[0]["veri"]["kalemler"][0]["aciklama"] == "Defter"

    cout = export_structured.write_csv(pages, tmp_path / "fatura.csv")
    rows = list(csv.DictReader(cout.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 1
    assert rows[0]["satici"] == "Kırtasiye AŞ"
    assert rows[0]["aciklama"] == "Defter"
    assert rows[0]["toplam"] == "120.00"


def test_structured_fallback_from_plain_text(tmp_path: Path):
    text = "ABC Market\nTarih: 15.10.2026\nFatura No: FT-901\nKDV: 18,50\nGenel Toplam: 118,50"
    cout = export_structured.write_csv([_page(text)], tmp_path / "f2.csv")
    rows = list(csv.DictReader(cout.read_text(encoding="utf-8").splitlines()))
    assert rows[0]["satici"] == "ABC Market"
    assert rows[0]["tarih"] == "15.10.2026"
    assert rows[0]["fatura_no"] == "FT-901"
    assert rows[0]["toplam"] == "118,50"


def test_gui_api_state_masks_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS", tmp_path)
    monkeypatch.setattr(paths, "INPUT", tmp_path / "in")
    for var in ("OCR_API_BASE", "OPENAI_BASE_URL", "OCR_API_MODEL", "OCR_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    engines.save_api_config("https://api.openai.com/v1", "gpt-4o", "sk-secret")
    st = gui_server.App().state()
    assert st["api"] == {"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "anahtar_var": True}
    assert "sk-secret" not in json.dumps(st)
