"""End-to-end smoke test on a synthetic font, for CI (no real fonts in the repo).

Builds a small unhinted TrueType font, gives it VTT sources with `fresh.prepare` and a bit of Talk,
then runs vtt-transfer onto a new version (one glyph added, one outline moved) and vtt-ship on the
result. Run: `uv run python tests/smoke.py`. Exits non-zero on failure.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent

from vtt_transfer import cli, fresh, ship

UPM = 1000


def rect(pen, x0, y0, x1, y1):
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()


def oval(pen, x0, y0, x1, y1):
    xm, ym = (x0 + x1) // 2, (y0 + y1) // 2
    pen.moveTo((xm, y0))
    pen.qCurveTo((x0, y0), (x0, ym))
    pen.qCurveTo((x0, y1), (xm, y1))
    pen.qCurveTo((x1, y1), (x1, ym))
    pen.qCurveTo((x1, y0), (xm, y0))
    pen.closePath()


def glyph(*shapes):
    pen = TTGlyphPen(None)
    for draw, box in shapes:
        draw(pen, *box)
    return pen.glyph()


def build(new: bool) -> TTFont:
    glyphs = {
        ".notdef": glyph((rect, (50, 0, 450, 700))),
        "space": glyph(),
        "H": glyph((rect, (80, 0, 160, 700)), (rect, (440, 0, 520, 700)), (rect, (160, 320, 440, 390))),
        "O": glyph((oval, (50, -10, 650, 710)), (oval, (130, 60, 570, 640))),
        "o": glyph((oval, (40, -10, 500, 510)), (oval, (120, 60, 420, 440))),
        "x": glyph((rect, (40, 0, 120, 500 if not new else 510))),
        "grave": glyph((rect, (100, 560, 200, 700))),
    }
    cmap = {0x20: "space", 0x48: "H", 0x4F: "O", 0x6F: "o", 0x78: "x", 0x60: "grave"}
    if new:
        glyphs["n"] = glyph((rect, (60, 0, 140, 500)), (rect, (380, 0, 460, 500)))
        cmap[0x6E] = "n"
    order = list(glyphs) + ["ograve"]

    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap | {0xF2: "ograve"})
    fb.setupGlyf(glyphs | {"ograve": glyph()})
    glyf = fb.font["glyf"]
    comp = Glyph()
    comp.numberOfContours = -1
    comp.components = []
    for name, dx in (("o", 0), ("grave", 170)):
        c = GlyphComponent()
        c.glyphName, c.x, c.y, c.flags = name, dx, 0, 0x4
        comp.components.append(c)
    glyf["ograve"] = comp
    for n in order:
        glyf[n].recalcBounds(glyf)
    fb.setupHorizontalMetrics({n: (600, getattr(glyf[n], "xMin", 0)) for n in order})
    fb.setupHorizontalHeader(ascent=900, descent=-200)
    fb.setupNameTable({"familyName": "Smoke Test", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=750, sTypoDescender=-250, usWinAscent=900, usWinDescent=200,
                sxHeight=500, sCapHeight=700)
    fb.setupPost()
    fb.setupMaxp()
    return fb.font


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        sys.exit(1)


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        old, _ = fresh.prepare(build(new=False))
        talk = old["TSI3"].glyphPrograms
        talk["H"] = "YAnchor(1)\rYAnchor(0)\rXLink(0,4)\r"
        talk["x"] = "YAnchor(1)\rYAnchor(0)\r"
        old.save(d / "smoke.VTT.ttf")

        new = build(new=True)
        new.save(d / "smoke-new.ttf")

        rc = cli.main([str(d / "smoke.VTT.ttf"), str(d / "smoke-new.ttf"), "--report-lang", "en", "-q"])
        check(rc == 0, f"vtt-transfer exit code {rc}")
        out = d / "smoke-new.VTT.ttf"
        check(out.exists(), "transferred font written")
        check((d / "smoke-new.vtt-transfer-report.html").exists(), "report written")
        result = TTFont(out)
        check(result["TSI3"].glyphPrograms["H"].strip() != "", "talk of H transferred")
        check("fpgm" in result and "prep" in result, "compiled tables present")

        rc = ship.main([str(out), "--reference", str(d / "smoke-new.ttf"), "-q"])
        check(rc == 0, f"vtt-ship exit code {rc}")
        shipped = TTFont(d / "hinted" / "smoke-new.ttf")
        check(not any(t.startswith("TSI") for t in shipped.keys()), "shipped font has no TSI tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
