#!/usr/bin/env python3
"""OCR test ve çeviri. Argümansız başlatınca mod / PDF / model / sayfa sorulur.
  Test   → modelleri karşılaştırır: output/<pdf>_karsilastirma_syf1.md (+ her model için ayrı dosya)
  Normal → tek modelle çevirir:     output/<model>/<pdf>_<model>.md

  ./run.sh                                   # soru-cevaplı başlat
  ./run.sh --durum                           # hangi motor hazır, hangisi eksik
  ./run.sh -e tesseract glm-ocr -p 1-3       # sormadan test
  ./run.sh -m normal -e glm-ocr              # sormadan tam çeviri
CER: gt/tarama5_syf1.txt gibi elle yazılmış doğru metin varsa otomatik hesaplanır.
"""
import argparse, base64, csv, json, os, shlex, shutil, signal, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parent  # input/, models/, output/ buna göre

TESS_LANG = os.environ.get("TESS_LANG", "tur")
KRAKEN_MODEL = os.environ.get("KRAKEN_MODEL", "models/catmus-print-large.mlmodel")
MAX_TOKENS = 4096
VLLM_FLAGS = "--max-model-len 8192 --gpu-memory-utilization 0.85 --mm-processor-cache-gb 0 --no-enable-prefix-caching"  # 8 GB GPU için

# VLM'ler OpenAI uyumlu sunucu üzerinden çağrılır. Port boşsa `serve` ile açılır, iş bitince kapatılır.
# Sunucuyu kendin açtıysan (docker, ollama vs.) sadece portu eşle; script ona bağlanır.
VLM = {
    "paddleocr-vl": dict(port=8001, prompt="OCR:",
        serve="tools/llama.cpp/llama-server -m models/PaddleOCR-VL-1.6.gguf --mmproj models/PaddleOCR-VL-1.6-mmproj.gguf --temp 0 -ngl 99 -c 8192 --port 8001"),
    "glm-ocr": dict(port=8002, prompt="Text Recognition:",
        serve="vllm serve zai-org/GLM-OCR --port 8002 $VLLM"),
    "lightonocr": dict(port=8003, prompt=None,  # LightOnOCR metin istemez, sadece görsel
        serve="""vllm serve lightonai/LightOnOCR-2-1B --port 8003 --limit-mm-per-prompt '{"image": 1}' $VLLM"""),
    "deepseek-ocr2": dict(port=8004, prompt="Free OCR.",  # bf16 hâli 8 GB'a sığmıyor → fp8
        serve="vllm serve deepseek-ai/DeepSeek-OCR-2 --port 8004 --quantization fp8 --enforce-eager $VLLM"),
    "dots.mocr": dict(port=8005, prompt="Extract the text content from this image.",  # 6 GB → fp8
        serve="""vllm serve dots-studio/dots.mocr --port 8005 --trust-remote-code --chat-template-content-format string --quantization fp8 --enforce-eager --mm-processor-kwargs '{"max_pixels": 4000000}' $VLLM"""),
}


def tesseract(img):
    return subprocess.run(["tesseract", str(img), "stdout", "-l", TESS_LANG],
                          capture_output=True, text=True, check=True).stdout


def kraken(img):
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.txt"
        subprocess.run(["kraken", "-i", str(img), str(out), "segment", "-bl", "ocr", "-m", KRAKEN_MODEL],
                       capture_output=True, text=True, check=True)
        return out.read_text()


LOCAL = {"tesseract": tesseract, "kraken": kraken}
ENGINES = [*LOCAL, *VLM]


def missing(name):
    """Motor çalışamayacaksa nedenini döndürür, hazırsa None."""
    if name == "tesseract":
        return None if shutil.which("tesseract") else "tesseract kurulu değil"
    if name == "kraken":
        if not shutil.which("kraken"):
            return "kraken kurulu değil"
        return None if Path(KRAKEN_MODEL).exists() else f"model yok: {KRAKEN_MODEL}"
    cfg = VLM[name]
    if served_model(f"http://127.0.0.1:{cfg['port']}/v1"):
        return None
    cmd = shlex.split(cfg["serve"])
    if not shutil.which(cmd[0]):
        return f"{cmd[0]} kurulu değil"
    lost = [a for a in cmd if a.endswith(".gguf") and not Path(a).exists()]
    return f"dosya yok: {', '.join(lost)}" if lost else None


def served_model(base):
    try:
        with urllib.request.urlopen(base + "/models", timeout=3) as r:
            return json.load(r)["data"][0]["id"]
    except Exception:
        return None


def start_server(name, cfg, log_dir):
    base = f"http://127.0.0.1:{cfg['port']}/v1"
    proc = None
    if not served_model(base):
        log = open(log_dir / f"{name}.server.log", "w")
        print(f"[{name}] sunucu başlatılıyor (log: {log.name})")
        proc = subprocess.Popen(shlex.split(cfg["serve"].replace("$VLLM", VLLM_FLAGS)), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + 1200
        while not served_model(base):
            if proc.poll() is not None or time.time() > deadline:
                stop_server(proc)
                raise RuntimeError(f"sunucu ayağa kalkmadı, bkz. {log.name}")
            time.sleep(3)
    return base, served_model(base), proc


def stop_server(proc):
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


def vlm(img, base, model, prompt):
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img.read_bytes()).decode()}}]
    if prompt:
        content.append({"type": "text", "text": prompt})
    body = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": MAX_TOKENS}
    req = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def cer(ref, hyp):
    """Karakter hata oranı (boşluklar normalize edilir)."""
    ref, hyp = " ".join(ref.split()), " ".join(hyp.split())
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        cur = [i]
        for j, b in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def parse_pages(spec, n):
    if not spec:
        return list(range(n))
    pages = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        pages += range(int(a) - 1, int(b or a))
    return [p for p in pages if p < n]


def render(pdfs, spec, dpi, out_dir):
    """Sayfaları PNG'ye çevirir: [(pdf adı, sayfa no, png)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for pdf_path in pdfs:
        pdf = pdfium.PdfDocument(str(pdf_path))
        for i in parse_pages(spec, len(pdf)):
            img = out_dir / f"{pdf_path.stem}_syf{i + 1}.png"
            if not img.exists():
                pdf[i].render(scale=dpi / 72).to_pil().save(img)
            jobs.append((pdf_path.stem, i + 1, img))
    return jobs


def span(pages):
    return f"syf{pages[0]}" if len(pages) == 1 else f"syf{pages[0]}-{pages[-1]}"


def ask(question, default):
    return input(f"{question} [{default}]: ").strip() or default


def choose(title, options, default, multi=False):
    """Numaralı liste gösterir, seçilen indeksleri döndürür. multi: '1 3' ya da 'hepsi'."""
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        answer = ask("Seçim" + (" (ör. 1 3 ya da hepsi)" if multi else ""), default)
        if multi and answer == "hepsi":
            return list(range(len(options)))
        try:
            idx = [int(x) - 1 for x in answer.replace(",", " ").split()]
        except ValueError:
            idx = []
        if idx and all(0 <= i < len(options) for i in idx) and (multi or len(idx) == 1):
            return idx
        print("  Geçersiz seçim, tekrar dene.")


def interactive(ready, pdfs):
    """Program başladıktan sonra mod, PDF, model ve sayfaları sorar."""
    modes = ["Test   — seçili modelleri karşılaştır (sonuçlar tek dosyada)",
             "Normal — tek modelle PDF'i çevir (tek belge)"]
    mode = ["test", "normal"][choose("Mod:", modes, "1")[0]]
    if len(pdfs) > 1:
        pdfs = [pdfs[i] for i in choose("PDF:", [p.name for p in pdfs], "hepsi", multi=True)]
    if mode == "test":
        engines = [ready[i] for i in choose("Modeller:", ready, "hepsi", multi=True)]
    else:
        engines = [ready[choose("Model:", ready, "1")[0]]]
    counts = ", ".join(f"{p.stem}: {len(pdfium.PdfDocument(str(p)))} syf" for p in pdfs)
    while True:
        pages = ask(f"\nSayfalar — ör. 1-3,7 ya da all ({counts})", "1" if mode == "test" else "all")
        try:
            parse_pages(None if pages == "all" else pages, 1)
            break
        except ValueError:
            print("  Geçersiz sayfa aralığı.")
    return mode, pdfs, engines, pages


def write_comparison(path, stem, pages, engines, results, errors):
    lines = [f"# {stem} — OCR karşılaştırması", "",
             f"{time.strftime('%Y-%m-%d %H:%M')} · {', '.join(engines)}", ""]
    for page, img in pages:
        lines += [f"## Sayfa {page}", "", f"![sayfa {page}](_pages/{img.name})", "",
                  "| Model | Süre (sn) | Karakter | CER |", "|---|---|---|---|"]
        for e in engines:
            r = results.get((stem, page, e))
            lines.append(f"| {e} | {r[1]:.1f} | {len(r[0])} | {r[2]} |" if r else f"| {e} | — | — | HATA |")
        for e in engines:
            r = results.get((stem, page, e))
            lines += ["", f"### {e}", "", "````text",
                      r[0].strip() if r else f"HATA: {errors.get(e, 'çalışmadı')}", "````"]
        lines.append("")
    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", type=Path, nargs="*", help="varsayılan: input/*.pdf")
    ap.add_argument("-m", "--mod", choices=["test", "normal"], default="test")
    ap.add_argument("-e", "--engines", nargs="+", choices=ENGINES, default=ENGINES)
    ap.add_argument("-p", "--pages", help="ör. 1-3,7 ya da all (varsayılan: test → 1, normal → all)")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--out", type=Path, default=Path("output"))
    ap.add_argument("--gt", type=Path, default=Path("gt"), help="referans metinler: gt/tarama5_syf1.txt ...")
    ap.add_argument("--durum", action="store_true", help="motorların hazır olup olmadığını göster ve çık")
    args = ap.parse_args()
    asking = len(sys.argv) == 1 and sys.stdin.isatty()
    pdfs = [p.resolve() for p in args.pdfs]
    out, gt_dir = args.out.resolve(), args.gt.resolve()
    os.chdir(ROOT)

    status = {name: missing(name) for name in args.engines}
    for name, why in status.items():
        print(f"  {'✓' if not why else '✗'} {name:14} {why or 'hazır'}")
    ready = [name for name, why in status.items() if not why]
    if args.durum:
        return
    if not ready:
        sys.exit("\nHazır motor yok — kurulum için ./kurulum.sh")
    pdfs = pdfs or sorted(Path("input").glob("*.pdf"))
    if not pdfs:
        sys.exit("input/ içinde PDF yok.")

    if asking:
        try:
            mode, pdfs, engines, pages = interactive(ready, pdfs)
        except (KeyboardInterrupt, EOFError):
            sys.exit("\nİptal edildi.")
    else:
        mode, engines, pages = args.mod, ready, args.pages or ("1" if args.mod == "test" else "all")
    print(f"\n▶ {mode} · {', '.join(p.name for p in pdfs)} · {', '.join(engines)} · sayfa {pages}")

    jobs = render(pdfs, None if pages == "all" else pages, args.dpi, out / "_pages")
    by_pdf = {}
    for stem, page, img in jobs:
        by_pdf.setdefault(stem, []).append((page, img))
    (out / "_logs").mkdir(parents=True, exist_ok=True)
    rows, results, errors, docs = [], {}, {}, []
    for name in engines:
        print(f"\n=== {name} ===", flush=True)
        proc = None
        try:
            if name in VLM:
                base, model, proc = start_server(name, VLM[name], out / "_logs")
                run = lambda img: vlm(img, base, model, VLM[name]["prompt"])
            else:
                run = LOCAL[name]
            (out / name).mkdir(exist_ok=True)
            for stem, page, img in jobs:
                t = time.time()
                text = run(img)
                dt = time.time() - t
                gt = gt_dir / f"{stem}_syf{page}.txt"
                score = round(cer(gt.read_text(), text), 4) if gt.exists() else ""
                results[stem, page, name] = (text, dt, score)
                rows.append([name, stem, str(page), str(round(dt, 2)), str(len(text)), str(score)])
                if mode == "test":
                    (out / name / f"{stem}_{name}_syf{page}.md").write_text(text)
                else:  # tek belge, sayfa sayfa eklenir (yarıda kesilse de yazılan kalır)
                    nums = [p for p, _ in by_pdf[stem]]
                    doc = out / name / (f"{stem}_{name}.md" if pages == "all" else f"{stem}_{name}_{span(nums)}.md")
                    with doc.open("w" if page == nums[0] else "a") as f:
                        f.write(("" if page == nums[0] else "\n\n") + f"<!-- sayfa {page} -->\n\n{text.strip()}")
                    if page == nums[0]:
                        docs.append(doc)
                print(f"  {stem} syf{page}  {dt:.1f}s  {len(text)} kr  {score}", flush=True)
        except Exception as e:
            errors[name] = f"{e} {getattr(e, 'stderr', '') or ''}".strip()
            print(f"[{name}] HATA: {errors[name]}", file=sys.stderr)
        finally:
            stop_server(proc)

    # Önceki koşuların satırlarını koru; aynı motor+pdf+sayfa yenisiyle değişir
    summary = out / "summary.csv"
    old = list(csv.reader(summary.open()))[1:] if summary.exists() else []
    new_keys = {tuple(r[:3]) for r in rows}
    merged = sorted([r for r in old if tuple(r[:3]) not in new_keys] + rows)
    with summary.open("w", newline="") as f:
        csv.writer(f).writerows([["engine", "pdf", "page", "seconds", "chars", "cer"], *merged])

    print()
    if mode == "test":
        for stem, pages_imgs in by_pdf.items():
            path = out / f"{stem}_karsilastirma_{span([p for p, _ in pages_imgs])}.md"
            write_comparison(path, stem, pages_imgs, engines, results, errors)
            print(f"Karşılaştırma: {path}")
    for doc in docs:
        print(f"Belge: {doc}")
    print(f"Özet: {summary}")


if __name__ == "__main__":
    main()
