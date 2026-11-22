"""Generates assets/icon.ico (teal rounded square with sound bars) for the exe and window."""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets", "icon.ico")


def draw(size):
    s = size * 4  # supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(s * 0.22)
    for y in range(s):  # vertical gradient
        t = y / s
        c = (int(46 + (20 - 46) * t), int(230 + (166 - 230) * t), int(200 + (143 - 200) * t), 255)
        d.line([(0, y), (s, y)], fill=c)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], r, fill=255)
    img.putalpha(mask)
    bars = [0.25, 0.5, 0.85, 0.5, 0.25]
    bw = s * 0.09
    gap = s * 0.055
    total = len(bars) * bw + (len(bars) - 1) * gap
    x = (s - total) / 2
    for h in bars:
        bh = s * 0.62 * h
        y0 = (s - bh) / 2
        d.rounded_rectangle([x, y0, x + bw, y0 + bh], bw / 2, fill=(14, 17, 23, 255))
        x += bw + gap
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw(z) for z in sizes]
    imgs[-1].save(OUT, format="ICO", sizes=[(z, z) for z in sizes], append_images=imgs[:-1])
    print("wrote", os.path.abspath(OUT))
