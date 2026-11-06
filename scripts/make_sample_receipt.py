"""Render samples/sample_receipt.json as a receipt-style PNG, for testing the photo upload.

Needs Pillow (not used by the app itself):  pip install pillow
Run:  python scripts/make_sample_receipt.py
"""
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "samples" / "sample_receipt.json").read_text(encoding="utf-8"))


def font(size: int, bold: bool = False):
    for name in (["consolab.ttf", "courbd.ttf", "DejaVuSansMono-Bold.ttf"] if bold
                 else ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


W, PAD = 620, 44
f, fb, fbig = font(24), font(24, True), font(36, True)
lines: list[tuple[str, str, object, str]] = []  # (left, right, font, align)


def add(left="", right="", fnt=f, align="lr"):
    lines.append((left, right, fnt, align))


add(data["merchant"].upper(), "", fbig, "c")
add("12 Pier Road, Bayview", "", f, "c")
add("Tel 555-0142", "", f, "c")
add()
add(f"Date: {data['date']}", "Table 7", f)
add("Server: Jordan", "Guests: 3", f)
add("-" * 38, "", f, "c")
for it in data["items"]:
    add(f"{it['quantity']} {it['name']}", f"{it['total_price']:.2f}")
add("-" * 38, "", f, "c")
add("Subtotal", f"{data['subtotal']:.2f}")
add("Sales Tax 8.5%", f"{data['tax']:.2f}")
add("TOTAL", f"${data['total']:.2f}", fb)
add()
add("Tip: ______________", "", f)
add()
add("Thank you, come again!", "", f, "c")

line_h = 40
H = PAD * 2 + line_h * len(lines) + 30
paper = Image.new("RGB", (W, H), (252, 250, 245))
d = ImageDraw.Draw(paper)
y = PAD
for left, right, fnt, align in lines:
    if align == "c":
        w = d.textlength(left, font=fnt)
        d.text(((W - w) / 2, y), left, font=fnt, fill=(35, 35, 40))
    else:
        d.text((PAD, y), left, font=fnt, fill=(35, 35, 40))
        if right:
            w = d.textlength(right, font=fnt)
            d.text((W - PAD - w, y), right, font=fnt, fill=(35, 35, 40))
    y += line_h

# Place the receipt on a table, slightly rotated, like a real photo.
random.seed(3)
paper = paper.rotate(-2.2, expand=True, fillcolor=(0, 0, 0, 0), resample=Image.BICUBIC)
bg = Image.new("RGB", (paper.width + 160, paper.height + 160), (92, 72, 58))
shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
ImageDraw.Draw(shadow).rectangle([90, 95, 90 + paper.width - 10, 95 + paper.height - 10], fill=(0, 0, 0, 120))
bg.paste(shadow.filter(ImageFilter.GaussianBlur(14)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(14)))
mask = Image.new("L", paper.size, 0)
ImageDraw.Draw(mask).polygon([(0, 0), (paper.width, 0), (paper.width, paper.height), (0, paper.height)], fill=255)
mask = paper.convert("L").point(lambda v: 255 if v > 5 else 0)
bg.paste(paper, (80, 80), mask)

out = ROOT / "samples" / "sample_receipt.png"
bg.save(out)
print(f"Wrote {out}")
