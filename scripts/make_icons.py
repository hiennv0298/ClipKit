"""Draw ClipKit's icon set (needs Pillow: pip install pillow).

    python scripts/make_icons.py [preview.png]

Writes into src/clipkit/webui/:
  clipkit.ico                 16-256 px, used for the .exe files, installer and shortcuts
  icon-32.png, icon-192.png, icon-512.png   for the app window and taskbar (favicon)
Each size is drawn on its own (8x supersampled) instead of shrinking one big
image, so small sizes stay sharp. Small sizes drop the "cut" detail.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src" / "clipkit" / "webui"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
TOP_LEFT, BOTTOM_RIGHT = (122, 104, 255), (171, 82, 246)  # violet -> purple, matches the app's accent


def _gradient(n: int) -> Image.Image:
    """Smooth diagonal gradient, top-left to bottom-right."""
    import numpy as np

    t = (np.arange(n)[:, None] + np.arange(n)[None, :]) / (2 * (n - 1))
    rgb = np.stack([a + (b - a) * t for a, b in zip(TOP_LEFT, BOTTOM_RIGHT)], axis=-1)
    return Image.fromarray(rgb.round().astype("uint8"), "RGB")


def _rounded_triangle(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]], r: float, fill: int) -> None:
    """Filled triangle with rounded corners: inset polygon + thick round-joined outline."""
    cx, cy = sum(p[0] for p in pts) / 3, sum(p[1] for p in pts) / 3
    inset = [(x + (cx - x) * r / max(1e-6, ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5) * 1.9,
              y + (cy - y) * r / max(1e-6, ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5) * 1.9) for x, y in pts]
    draw.polygon(inset, fill=fill)
    draw.line(inset + [inset[0]], fill=fill, width=int(r * 2), joint="curve")
    for x, y in inset:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def draw_icon(size: int) -> Image.Image:
    ss = 8
    n = size * ss
    small = size <= 32  # taskbar/title-bar sizes: plain play symbol, no cut detail
    pad = 0 if small else n * 0.04                 # transparent margin around the tile
    radius = (n - 2 * pad) * (0.24 if small else 0.235)
    tile_mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle((pad, pad, n - pad, n - pad), radius=radius, fill=255)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(_gradient(n), (0, 0), tile_mask)

    # play triangle, optically centered (nudged right)
    inner = n - 2 * pad
    h = inner * (0.50 if small else 0.46)
    w = h * 0.92
    cx, cy = n / 2 + w * 0.10, n / 2
    pts = [(cx - w / 2, cy - h / 2), (cx - w / 2, cy + h / 2), (cx + w / 2, cy)]
    play = Image.new("L", (n, n), 0)
    _rounded_triangle(ImageDraw.Draw(play), pts, r=h * 0.09, fill=255)

    if not small:
        # the "cut": a slanted slice near the tip; the tip is nudged off to the right,
        # like a clip being trimmed out of a longer video
        t = h * (0.085 if size >= 64 else 0.11)
        mx, my = cx + w * 0.06, cy                       # the slice passes through here
        dx, dy = w * 0.16, -h * 0.75                      # leaning right as it goes up
        x0, y0, x1, y1 = mx - dx, my - dy, mx + dx, my + dy
        left = Image.new("L", (n, n), 0)  # everything left of the slice
        ImageDraw.Draw(left).polygon([(x0, y0), (x1, y1), (x1 - n * 2, y1), (x0 - n * 2, y0)], fill=255)
        piece_left = ImageChops.multiply(play, left)
        tip = ImageChops.subtract(play, piece_left)
        length = (dx * dx + dy * dy) ** 0.5
        nx, ny = -dy / length, dx / length                 # normal pointing right of the slice
        moved = Image.new("L", (n, n), 0)
        moved.paste(tip, (int(nx * t * 0.8), int(ny * t * 0.8 - t * 0.35)))
        slit = Image.new("L", (n, n), 0)
        ImageDraw.Draw(slit).line((x0, y0, x1, y1), fill=255, width=int(t))
        play = ImageChops.lighter(ImageChops.subtract(piece_left, slit), moved)

    img.paste(Image.new("RGBA", (n, n), (255, 255, 255, 255)), (0, 0), play)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    icons = {s: draw_icon(s) for s in ICO_SIZES + [192, 512]}
    big = icons[256]
    big.save(OUT / "clipkit.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES],
             append_images=[icons[s] for s in ICO_SIZES if s != 256])
    for s in (32, 192, 512):
        icons[s].save(OUT / f"icon-{s}.png", optimize=True)
    print("wrote", OUT / "clipkit.ico", "and icon-32/192/512.png")

    if len(sys.argv) > 1:  # preview sheet: every size at 1:1 on light and dark
        shown = [16, 24, 32, 48, 64, 128, 256]
        width = sum(shown) + 20 * (len(shown) + 1)
        sheet = Image.new("RGB", (width, 2 * 296), (243, 243, 243))
        ImageDraw.Draw(sheet).rectangle((0, 296, width, 592), fill=(32, 32, 36))
        for row in (0, 296):
            x = 20
            for s in shown:
                sheet.paste(icons[s], (x, row + 148 - s // 2), icons[s])
                x += s + 20
        sheet.save(sys.argv[1])
        print("preview", sys.argv[1])


if __name__ == "__main__":
    main()
