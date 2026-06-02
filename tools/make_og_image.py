#!/usr/bin/env python3
"""Generate static/og.png — the 1200x630 social-share card for mlbsched.run.

Dev-only tool. NOT a runtime dependency: run it locally (needs Pillow) and
commit the resulting static/og.png. Re-run only when the card design changes.

    ./venv/bin/pip install pillow
    ./venv/bin/python tools/make_og_image.py
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── palette (matches server.py html_wrap + the ANSI output) ─────────────────────
BG      = "#0d1117"
PANEL   = "#161b22"
BORDER  = "#30363d"
FG      = "#e6edf3"
BLUE    = "#58a6ff"
GRAY    = "#6e7681"
GREEN   = "#3fb950"
RED     = "#f85149"
CYAN    = "#39c5cf"
ORANGE  = "#ff7b00"   # Mets nod
YELLOW  = "#d29922"

W, H = 1200, 630
FONT = "/System/Library/Fonts/Menlo.ttc"   # index 0 = Regular, 1 = Bold


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size, index=1 if bold else 0)


def segments(draw, x, y, parts, f):
    """Draw colored (text, color) segments left-to-right; return final x."""
    for text, color in parts:
        draw.text((x, y), text, font=f, fill=color)
        x += draw.textlength(text, font=f)
    return x


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # window panel
    d.rounded_rectangle([40, 40, W - 40, H - 40], radius=18, fill=PANEL, outline=BORDER, width=2)

    # title-bar dots
    for i, c in enumerate((RED, YELLOW, GREEN)):
        cx = 78 + i * 30
        d.ellipse([cx - 9, 70, cx + 9, 88], fill=c)
    d.text((150, 69), "mlbsched.run — bash", font=font(22), fill=GRAY)

    # hero wordmark
    hero = font(94, bold=True)
    segments(d, 80, 138, [("mlbsched", FG), (".run", BLUE)], hero)

    # subtitle
    d.text((84, 252), "MLB scores & schedule in your terminal.", font=font(34), fill=GRAY)

    # faux prompt + live scoreboard sample
    mono = font(36, bold=True)
    y = 332
    segments(d, 84, y, [("$ ", GRAY), ("curl ", GREEN), ("mlbsched.run", BLUE)], mono)
    y += 58
    rows = [
        [("NYM", ORANGE), ("  2  ", FG), ("@", GRAY), ("  SEA", CYAN), ("  2   ", FG), ("▲ 8th", GREEN)],
        [("LAD", BLUE),   ("  1  ", FG), ("@", GRAY), ("  ARI", RED),  ("  1   ", FG), ("▼ 7th", GREEN)],
        [("DET", BLUE),   (" 10  ", FG), ("@", GRAY), ("  TB ", CYAN), ("  9   ", FG), ("Final", GRAY)],
    ]
    for row in rows:
        segments(d, 84, y, row, mono)
        y += 50

    # footer strip
    foot = font(25)
    segments(d, 84, H - 92,
             [("live scores", FG), ("  ·  ", GRAY), ("standings", FG), ("  ·  ", GRAY),
              ("odds", FG), ("  ·  ", GRAY), ("30+ commands", FG), ("  ·  ", GRAY),
              ("JSON API", FG)], foot)

    out = Path(__file__).resolve().parent.parent / "static" / "og.png"
    out.parent.mkdir(exist_ok=True)
    img.save(out, "PNG")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
