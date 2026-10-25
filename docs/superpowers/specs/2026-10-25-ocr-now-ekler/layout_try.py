import sys, json, time, importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("bench", "bench.py"); b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
S = Path(sys.argv[1])
PROMPT = """Please output the layout information from the PDF image, including each layout element's bbox, its category, and the corresponding text content within the bbox.

1. Bbox format: [x1, y1, x2, y2]

2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].

3. Text Extraction & Formatting Rules:
    - Picture: For the 'Picture' category, the text field should be omitted.
    - Formula: Format its text as LaTeX.
    - Table: Format its text as HTML.
    - All Others (Text, Title, etc.): Format their text as Markdown.

4. Constraints:
    - The output text must be the original text from the image, with no translation.
    - All layout elements must be sorted according to human reading order.

5. Final Output: The entire output must be a single JSON object.
"""
cfg = b.VLM["dots.mocr"]
S.mkdir(exist_ok=True)
base, model, proc = b.start_server("dots.mocr", cfg, S)
try:
    t = time.time()
    out = b.vlm(Path("output/_pages/tarama5_syf1.png"), base, model, PROMPT)
    print(f"süre {time.time()-t:.1f} sn")
    (S / "dots_layout_syf1.json").write_text(out)
finally:
    b.stop_server(proc)
