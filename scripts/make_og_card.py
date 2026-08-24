#!/usr/bin/env python3
"""Regenerate assets/og-card.jpg, the 1200x630 image social platforms show.

Set in Manrope on the site's palette so the preview matches the page it
links to. Run after changing the profile photo, the name, or the tagline.

Needs the Manrope variable TTF, which is not in the repo (the site ships
woff2, which Pillow cannot read). Fetched to a cache dir on first run.
"""
import io
import os
import pathlib
import urllib.request

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
PHOTO = ROOT / "assets/profile-pics/michiel-2026.jpg"
OUT = ROOT / "assets/og-card.jpg"
CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "miba-og"
TTF = CACHE / "Manrope.ttf"
TTF_URL = ("https://raw.githubusercontent.com/google/fonts/main/ofl/manrope/"
           "Manrope%5Bwght%5D.ttf")

W, H = 1200, 630
BG = (244, 247, 246)      # --bg
INK = (22, 35, 59)        # --ink
SOFT = (70, 85, 110)      # --soft
ACCENT = (26, 95, 212)    # --accent

NAME = "Michiel Bakker"
ROLE = "Assistant Professor, MIT"
TAGS = "LLMs · AI safety · AI governance"
URL = "miba.dev"


def font(px, weight):
    if not TTF.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(TTF_URL, headers={"User-Agent": "miba-og"})
        TTF.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    f = ImageFont.truetype(str(TTF), px)
    f.set_variation_by_axes([weight])
    return f


def rounded(im, radius):
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.size[0] - 1, im.size[1] - 1],
                                           radius=radius, fill=255)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def main():
    card = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(card)

    # square-crop the photo from the top, where the face is
    src = Image.open(PHOTO).convert("RGB")
    sw, sh = src.size
    side = min(sw, sh)
    src = src.crop(((sw - side) // 2, 0, (sw - side) // 2 + side, side))

    size = 430
    px, py = 84, (H - size) // 2
    card.paste(rounded(src.resize((size, size), Image.LANCZOS), 26), (px, py),
               rounded(src.resize((size, size), Image.LANCZOS), 26))

    x = px + size + 74
    f_name = font(70, 800)
    f_role = font(30, 500)
    f_tags = font(25, 500)
    f_url = font(25, 600)

    # measure the block so it centres against the photo rather than the canvas
    gap_a, gap_b, rule_h, gap_c, gap_d = 20, 36, 4, 34, 12
    h_name = f_name.getbbox(NAME)[3] - f_name.getbbox(NAME)[1]
    h_role = f_role.getbbox(ROLE)[3] - f_role.getbbox(ROLE)[1]
    h_tags = f_tags.getbbox(TAGS)[3] - f_tags.getbbox(TAGS)[1]
    h_url = f_url.getbbox(URL)[3] - f_url.getbbox(URL)[1]
    total = h_name + gap_a + h_role + gap_b + rule_h + gap_c + h_tags + gap_d + h_url
    y = py + (size - total) // 2

    d.text((x, y), NAME, font=f_name, fill=INK, anchor="lt")
    y += h_name + gap_a
    d.text((x, y), ROLE, font=f_role, fill=SOFT, anchor="lt")
    y += h_role + gap_b
    d.rounded_rectangle([x, y, x + 96, y + rule_h], radius=2, fill=ACCENT)
    y += rule_h + gap_c
    d.text((x, y), TAGS, font=f_tags, fill=SOFT, anchor="lt")
    y += h_tags + gap_d
    d.text((x, y), URL, font=f_url, fill=ACCENT, anchor="lt")

    # JPEG at 92: a photo dominates the card, so PNG costs ~4x for no gain
    card.save(OUT, "JPEG", quality=92, optimize=True, progressive=True)
    print(f"wrote {OUT.relative_to(ROOT)} {card.size[0]}x{card.size[1]} "
          f"{OUT.stat().st_size / 1024:.0f}KB")


if __name__ == "__main__":
    main()
