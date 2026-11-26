"""Rebuild the Baatsun logo files in this directory.

    python3 -m venv /tmp/v && /tmp/v/bin/pip install fonttools cairosvg
    curl -fsSLO https://github.com/google/fonts/raw/main/ofl/mukta/Mukta-ExtraBold.ttf
    curl -fsSLO https://github.com/google/fonts/raw/main/ofl/tirodevanagarihindi/TiroDevanagariHindi-Regular.ttf
    /tmp/v/bin/python brand/build.py Mukta-ExtraBold.ttf TiroDevanagariHindi-Regular.ttf

The mark: Mukta ExtraBold's ब with everything above its headstroke clipped
away, and the Baatsun pill set in its place. Cream on a deep-green tile.
"""
import os
import sys

import cairosvg
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

MUKTA, TIRO = sys.argv[1], sys.argv[2]
OUT = os.path.dirname(os.path.abspath(__file__))
CREAM, GREEN, INK = '#faf8f3', '#1f3d2e', '#1b1a17'
PX0, PX1, PY0, PY1 = -60, 620, 566, 676  # the pill, in Mukta's font units
CUT = 528  # the letter is clipped above this: that was its headstroke

mukta = TTFont(MUKTA)
pen = SVGPathPen(mukta.getGlyphSet())
mukta.getGlyphSet()[mukta.getBestCmap()[ord('ब')]].draw(pen)
BA = pen.getCommands()


def mark(box, cx, cy, uid='c'):
    s = box / PY1
    tx, ty = cx - (PX0 + PX1) / 2 * s, cy + (PY1 / 2) * s
    defs = f'<clipPath id="{uid}"><rect x="-200" y="-200" width="1400" height="{CUT + 200}"/></clipPath>'
    body = (
        f'<g transform="translate({tx:.3f} {ty:.3f}) scale({s:.5f} {-s:.5f})">'
        f'<path d="{BA}" fill="{CREAM}" clip-path="url(#{uid})"/>'
        f'<rect x="{PX0}" y="{PY0}" width="{PX1 - PX0}" height="{PY1 - PY0}" rx="{(PY1 - PY0) / 2}" fill="{CREAM}"/></g>'
    )
    return defs, body


def tile(margin):
    size, ts = 512, 512 - 2 * margin
    defs, body = mark(ts * 0.62, size / 2, size / 2 + ts * 0.01)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
        f'<title>Baatsun</title><defs>{defs}</defs>'
        f'<rect x="{margin}" y="{margin}" width="{ts}" height="{ts}" rx="{ts * 0.23:.1f}" fill="{GREEN}"/>{body}</svg>'
    )


def lockup():
    tiro = TTFont(TIRO)
    gs, cmap = tiro.getGlyphSet(), tiro.getBestCmap()
    h, size = 160, 128
    s = size / tiro['head'].unitsPerEm
    x0, base = h + 34, h * 0.72
    wpen, x = SVGPathPen(gs), 0
    for ch in 'baatsun':
        g = gs[cmap[ord(ch)]]
        g.draw(TransformPen(wpen, (s, 0, 0, -s, x0 + x * s, base)))
        x += g.width
    total = x0 + x * s + 4
    defs, body = mark(h * 0.62, h / 2, h / 2 + 1)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total:.0f} {h}" width="{total:.0f}" height="{h}">'
        f'<title>Baatsun</title><defs>{defs}</defs>'
        f'<rect width="{h}" height="{h}" rx="{h * 0.23:.1f}" fill="{GREEN}"/>{body}'
        f'<path d="{wpen.getCommands()}" fill="{INK}"/></svg>'
    )


icon, full = tile(margin=24), tile(margin=0)
files = {'baatsun.svg': icon, 'baatsun-tile.svg': full, 'baatsun-lockup.svg': lockup()}
for name, svg in files.items():
    with open(os.path.join(OUT, name), 'w') as f:
        f.write(svg)
os.makedirs(os.path.join(OUT, 'png'), exist_ok=True)
for n in (16, 24, 32, 48, 64, 128, 256, 512):
    cairosvg.svg2png(bytestring=icon.encode(), write_to=os.path.join(OUT, 'png', f'baatsun-{n}.png'),
                     output_width=n, output_height=n)
cairosvg.svg2png(bytestring=full.encode(), write_to=os.path.join(OUT, 'png', 'baatsun-tile-180.png'),
                 output_width=180, output_height=180)
print('wrote', ', '.join(files), 'and png/')
