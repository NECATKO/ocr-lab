"""Yapısal çıktı dışa aktarıcıları (.json ve .csv): fatura, makbuz ve genel belge verileri."""
import csv
import io
import json
import re
from pathlib import Path

from ocrnow import paths
from ocrnow.export_txt import page_label, plain_text, split_blocks
from ocrnow.layout import Page

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S | re.I)
_DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
_TOTAL_RE = re.compile(r"(?:genel\s+toplam|toplam|total|tutar)\s*[:=]?\s*([0-9][0-9.,]*)", re.I)
_VAT_RE = re.compile(r"(?:kdv|vat|vergi)\s*(?:%?\d+)?\s*[:=]?\s*([0-9][0-9.,]*)", re.I)
_INV_NO_RE = re.compile(r"(?:fatura\s*no|belge\s*no|fiş\s*no|invoice\s*no|#)\s*[:=]?\s*([A-Z0-9/-]{3,})", re.I)


def _try_parse_json(text: str):
    t = text.strip()
    if m := _JSON_FENCE.search(t):
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    if t.startswith(("{", "[")):
        try:
            return json.loads(t)
        except ValueError:
            pass
    return None


def _fallback_invoice_fields(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    data: dict = {}
    if lines:
        data["baslik"] = lines[0][:120]
    if m := _DATE_RE.search(text):
        data["tarih"] = m.group(1)
    if m := _INV_NO_RE.search(text):
        data["fatura_no"] = m.group(1)
    if m := _VAT_RE.search(text):
        data["kdv"] = m.group(1)
    if m := _TOTAL_RE.search(text):
        data["toplam"] = m.group(1)
    data["ham_metin"] = text
    return data


def extract_structured(pages: list[Page]) -> list[dict]:
    """Her sayfa için yapısal sözlük döndürür; model JSON döndüyse ayrıştırır, yoksa alanları çıkarır."""
    records: list[dict] = []
    for page in pages:
        _, body, footnotes = split_blocks(page)
        text = "\n".join(t for b in body + footnotes if (t := plain_text(b)))
        parsed = _try_parse_json(text) if text else None
        base = {
            "kaynak": Path(page.source).name,
            "sayfa": page.index,
            "etiket": page_label(page) or None,
            "motor": page.engine,
            "basarisiz": page.failed,
        }
        if isinstance(parsed, dict):
            records.append({**base, "veri": parsed})
        elif isinstance(parsed, list):
            records.append({**base, "veri": {"kalemler": parsed}})
        else:
            records.append({**base, "veri": _fallback_invoice_fields(text)})
    return records


def write_json(pages: list[Page], out: Path) -> Path:
    data = extract_structured(pages)
    with paths.atomic_output(out) as tmp:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def write_csv(pages: list[Page], out: Path) -> Path:
    records = extract_structured(pages)
    buf = io.StringIO()
    # Herhangi bir kayıtta fatura kalemleri listesi varsa satır bazlı kalem CSV'si, yoksa sayfa özeti CSV'si yaz
    has_items = any(isinstance(r["veri"].get("kalemler"), list) and r["veri"]["kalemler"] for r in records)
    if has_items:
        cols = ["kaynak", "sayfa", "satici", "tarih", "fatura_no", "aciklama", "adet", "birim_fiyat", "tutar", "kdv", "toplam"]
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for r in records:
            v = r["veri"]
            items = v.get("kalemler") if isinstance(v.get("kalemler"), list) else []
            common = {
                "kaynak": r["kaynak"], "sayfa": r["sayfa"],
                "satici": v.get("satici") or v.get("baslik") or "",
                "tarih": v.get("tarih") or "", "fatura_no": v.get("fatura_no") or "",
                "kdv": v.get("kdv") or "", "toplam": v.get("toplam") or "",
            }
            if items:
                for it in items:
                    if isinstance(it, dict):
                        w.writerow({**common,
                                    "aciklama": it.get("aciklama") or it.get("ad") or "",
                                    "adet": it.get("adet") or "",
                                    "birim_fiyat": it.get("birim_fiyat") or "",
                                    "tutar": it.get("tutar") or ""})
                    else:
                        w.writerow({**common, "aciklama": str(it)})
            else:
                w.writerow(common)
    else:
        cols = ["kaynak", "sayfa", "satici", "tarih", "fatura_no", "kdv", "toplam", "ham_metin"]
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for r in records:
            v = r["veri"]
            w.writerow({
                "kaynak": r["kaynak"], "sayfa": r["sayfa"],
                "satici": v.get("satici") or v.get("baslik") or "",
                "tarih": v.get("tarih") or "", "fatura_no": v.get("fatura_no") or "",
                "kdv": v.get("kdv") or "", "toplam": v.get("toplam") or "",
                "ham_metin": (v.get("ham_metin") or "").replace("\n", " "),
            })
    with paths.atomic_output(out) as tmp:
        tmp.write_text(buf.getvalue(), encoding="utf-8")
    return out
