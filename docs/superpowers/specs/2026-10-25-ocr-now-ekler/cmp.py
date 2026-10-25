import re, sys, unicodedata, collections, json
from pathlib import Path
R = Path('/home/hpwsl/ocr-lab')
MODELS = "tesseract kraken paddleocr-vl glm-ocr lightonocr deepseek-ocr2 dots.mocr".split()
gt_raw = (R/'gt/tarama5_syf1.txt').read_text()

def lev_ops(a, b):
    """Levenshtein + hizalama (karakter ya da kelime listeleri)."""
    n, m = len(a), len(b)
    D = [[0]*(m+1) for _ in range(n+1)]
    for i in range(n+1): D[i][0] = i
    for j in range(m+1): D[0][j] = j
    for i in range(1, n+1):
        ai, Di, Dp = a[i-1], D[i], D[i-1]
        for j in range(1, m+1):
            Di[j] = min(Dp[j]+1, Di[j-1]+1, Dp[j-1]+(ai != b[j-1]))
    ops, i, j = [], n, m
    while i or j:
        if i and j and D[i][j] == D[i-1][j-1]+(a[i-1] != b[j-1]):
            if a[i-1] != b[j-1]: ops.append(('S', a[i-1], b[j-1]))
            i, j = i-1, j-1
        elif i and D[i][j] == D[i-1][j]+1: ops.append(('D', a[i-1], '')); i -= 1
        else: ops.append(('I', '', b[j-1])); j -= 1
    return D[n][m], ops[::-1]

def raw(t): return " ".join(t.split())

APOS = "’‘ʿʾ`´'“”\""
def norm(t):
    t = unicodedata.normalize('NFC', t)
    t = re.sub(r'</?sup>|\^|\*\*|^#+\s*|^---+$|\|', ' ', t, flags=re.M)
    t = re.sub(r'(\w)[-¬]\s*\n\s*(\w)', r'\1\2', t)        # satır sonu tirelerini birleştir (kraken: ¬)
    t = re.sub(r'(?<=[^\W\d])(?=\d)', ' ', t)          # taştında724 -> taştında 724 (dipnot işareti)
    t = re.sub(r'(\w)-(\w)', r'\1-\2', t)
    t = re.sub(f'[{APOS}]', "'", t)
    t = re.sub(r'(\d)(?=\s|$)', r'\1', t)
    return " ".join(t.split())

TR = str.maketrans("ıİşŞğĞçÇöÖüÜ", "iIsSgGcCoOuU")
def base(t):
    t = norm(t).translate(TR)
    t = unicodedata.normalize('NFD', t)
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = t.replace("'", "")
    return " ".join(t.lower().split())

# kelime düzeyi için tire birleştirmelerini eşitle: "senâ-kâr" ~ "senâkâr"
def words(t): return re.sub(r'[^\w\s\']', ' ', t.replace('-', '')).split()

res = {}
for m in MODELS:
    hyp = (R/f'output/{m}/tarama5_{m}_syf1.md').read_text()
    r = {}
    for k, f in (('ham', raw), ('norm', norm), ('harf', base)):
        g, h = f(gt_raw), f(hyp)
        d, ops = lev_ops(g, h)
        r[k] = d/len(g)
        if k == 'norm': normops = ops
    gw, hw = words(norm(gt_raw)), words(norm(hyp))
    d, wops = lev_ops(gw, hw)
    r['wer'] = d/len(gw)
    sub = collections.Counter((a, b) for o, a, b in normops if o == 'S')
    r['top_sub'] = [f"{a}→{b} ×{c}" for (a, b), c in sub.most_common(8)]
    r['n_ops'] = collections.Counter(o for o, *_ in normops)
    r['wrong_words'] = [(a, b) for o, a, b in wops]
    res[m] = r

print(f"{'model':15} {'hamCER':>7} {'normCER':>8} {'harfCER':>8} {'WER':>6}")
for m in sorted(MODELS, key=lambda m: res[m]['norm']):
    r = res[m]
    print(f"{m:15} {r['ham']:7.2%} {r['norm']:8.2%} {r['harf']:8.2%} {r['wer']:6.2%}  ops={dict(r['n_ops'])}")
for m in sorted(MODELS, key=lambda m: res[m]['norm']):
    print(f"\n### {m}\n top: {', '.join(res[m]['top_sub'])}")
    print(" kelime hataları:", "; ".join(f"{a or '∅'}→{b or '∅'}" for a, b in res[m]['wrong_words']))
json.dump({m: {k: v for k, v in r.items() if k in ('ham','norm','harf','wer','top_sub','wrong_words')} for m, r in res.items()},
          open(sys.argv[1], 'w'), ensure_ascii=False, indent=1)
