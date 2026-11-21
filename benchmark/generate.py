"""Referans metinlerinden ağsız, görüntü tabanlı örnek benchmark PDF'leri üretir."""
import argparse
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
DPI = 200
SIZE = (1200, 1650)
DEFAULT_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
# İkinci sayfaların bozulması kontrollüdür; gerçek telefon fotoğrafı değildir.
STYLES = {"kasagi": ("temiz", "telefon"), "hurriyet": ("temiz", "dipnot"), "istiklal": ("temiz", "telefon")}


def draw_page(reference: str, font_path: Path, style: str) -> Image.Image:
    header, body, *tail = reference.strip().split("\n\n")
    *notes, number = tail
    if len(notes) > 1 or not number.isdecimal():
        raise ValueError("Başlık, gövde, isteğe bağlı dipnotlar ve sayfa numarası bekleniyor.")
    img = Image.new("L", SIZE, 252)
    draw = ImageDraw.Draw(img)
    fonts = [ImageFont.truetype(str(font_path), size) for size in (31, 33, 25)]
    draw.text((600, 112), header, fill=25, font=fonts[0], anchor="mt")
    for i, line in enumerate(body.splitlines()):
        if draw.textlength(line, font=fonts[1]) > 1000 or i >= 23:
            raise ValueError(f"Gövde satırı sayfaya sığmıyor: {line}")
        draw.text((100, 240 + 44 * i), line, fill=24, font=fonts[1])
    if notes:
        draw.line((100, 1280, 350, 1280), fill=55, width=2)
        for i, line in enumerate(notes[0].splitlines()):
            if draw.textlength(line, font=fonts[2]) > 1000 or i >= 8:
                raise ValueError(f"Dipnot satırı sayfaya sığmıyor: {line}")
            draw.text((100, 1300 + 28 * i), line, fill=24, font=fonts[2])
    draw.text((1120 if int(number) % 2 else 80, 1530), number, fill=25, font=fonts[0], anchor="mt")
    if style == "telefon":
        img = img.transform(SIZE, Image.Transform.PERSPECTIVE, (1.05, -.014, -6, .018, 1.018, -14, .00007, -.00003),
                            resample=Image.Resampling.BICUBIC, fillcolor=191).filter(ImageFilter.GaussianBlur(.9))
        shades = Image.linear_gradient("L").rotate(90).resize(SIZE)
        img = Image.blend(img, shades, .22)
    return img.convert("RGB")


def generate(output: Path, font: Path = DEFAULT_FONT) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    made = []
    stamp = time.gmtime(0)
    for book, styles in STYLES.items():
        images = [draw_page((HERE / "referans" / f"{book}_syf{i}.txt").read_text(encoding="utf-8"), font, style)
                  for i, style in enumerate(styles, 1)]
        path = output / f"{book}.pdf"
        images[0].save(path, resolution=DPI, save_all=True, append_images=images[1:],
                       quality=83, title=book, creationDate=stamp, modDate=stamp)
        made.append(path)
    return made


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cikti", type=Path, default=HERE.parent / "output" / "benchmark-ornek")
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT, help="serif TTF dosyası (varsayılan: DejaVu Serif)")
    args = parser.parse_args()
    for path in generate(args.cikti, args.font):
        print(path)


if __name__ == "__main__":
    main()
