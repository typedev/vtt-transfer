"""Heights and stems measured from the outlines of an unhinted font, keyed by the CVT numbers of
Microsoft's Control Program template (`templates/cvttmpl.txt`).

Heights are the dominant cluster of the bounding-box tops/bottoms of reference glyphs, stems the
dominant cluster of ink runs cut by scanlines. Stems are kept apart by character group (UpperCase,
LowerCase, Figure), direction and straight/round, because the template's CVTs are laid out that way.

Everything here is a measurement the operator confirms (shown as comments in the Control Program), not
a decision: a slot without reference glyphs or without agreeing samples stays unmeasured.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

FLATTEN_STEPS = 24          # per curve segment; chord error is far below a unit on stem edges
X_SCAN = (0.28, 0.72)       # horizontal scans for X stems, fractions of the band (dodge bars and serifs)
NUDGES = (0.31, 1.17, -0.83)  # a scanline that hits a vertex is moved by these amounts

# Height slots: cvt → (what, chars, top?). Round slots are relative to their parent (`~ parent` in the
# template): the value written is round − flat.
HEIGHTS = {
    2: ("cap height", "HEFIKLTZ" "НПЕГТИМШЖ", True),
    8: ("cap baseline", "HEFIKLTZ" "НПЕГТИМШЖ", False),
    0: ("ascender", "bdhkl", True),
    6: ("x-height", "xzvwuy" "нпгтшиквж", True),
    10: ("lowercase baseline", "xzhiklmn" "нпгтшикж", False),
    14: ("descender", "pq" "рф", False),
    4: ("figure height", "1457", True),
    12: ("figure baseline", "1247", False),
}
ROUND_HEIGHTS = {
    3: ("cap overshoot", "OCGQS" "ОСЭЮ", True, 2),
    9: ("cap undershoot", "OCGQS" "ОСЭЮ", False, 8),
    7: ("x-height overshoot", "oecs" "оесэ", True, 6),
    11: ("lowercase undershoot", "oecs" "оесэ", False, 10),
    15: ("descender undershoot", "gj", False, 14),
    5: ("figure overshoot", "0368", True, 4),
    13: ("figure undershoot", "0368", False, 12),
}
# Stem slots: cvt → (what, chars, axis of the scanline, scan positions, band cvt). `axis` "y" = a
# horizontal scanline measuring an X (vertical) stroke; "x" = a vertical scanline at fractions of the
# glyph's width measuring a Y (horizontal) stroke. Positions for "y" are fractions of the band height;
# "mid" scans at the middle of the glyph's own bounding box.
STEMS = {
    68: ("UC X straight stroke", "HIL" "НПШ", "y", X_SCAN, 2),
    70: ("UC X round stroke", "OQ" "О", "y", "mid", 2),
    72: ("UC Y straight stroke", "EH" "ЕНТ", "x", {"E": (0.75,), "Е": (0.75,), "Т": (0.2, 0.8)}, 2),
    74: ("UC Y round stroke", "O" "О", "x", (0.5,), 2),
    81: ("LC X straight stroke", "nmhu" "нпш", "y", X_SCAN, 6),
    83: ("LC X round stroke", "o" "о", "y", "mid", 6),
    85: ("LC Y straight stroke", "z" "нпт", "x", {"т": (0.2, 0.8)}, 6),
    87: ("LC Y round stroke", "o" "о", "x", (0.5,), 6),
    97: ("figure X straight stroke", "1", "y", X_SCAN, 4),
    99: ("figure X round stroke", "0", "y", "mid", 4),
    # the bars of 2 (base), 4 (right of the stem) and 7 (top, past the diagonal); also the parent of 103
    101: ("figure Y straight stroke", "247", "x", {"2": (0.8,), "4": (0.9,), "7": (0.6,)}, 4),
    103: ("figure Y round stroke", "0", "x", (0.5,), 4),
}
# parents: the first measured of these (lowercase first: most text is lowercase)
X_PARENT, Y_PARENT = (81, 68, 97), (85, 72)


@dataclass
class Measured:
    cvt: int
    what: str
    value: int | None = None        # None: not measured
    samples: int = 0                # samples in the chosen cluster
    total: int = 0                  # all samples taken
    glyphs: list[str] = field(default_factory=list)
    parent: int | None = None       # for round heights and the parent stems: the cvt it is relative to / derived from


def _flatten(glyphset, name: str) -> list[list[tuple[float, float]]]:
    """Glyph outline as closed polylines, components decomposed."""
    pen = DecomposingRecordingPen(glyphset)
    glyphset[name].draw(pen)
    contours: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def quad(p0, p1, p2):
        for i in range(1, FLATTEN_STEPS + 1):
            t = i / FLATTEN_STEPS
            m = 1 - t
            cur.append((m * m * p0[0] + 2 * m * t * p1[0] + t * t * p2[0],
                        m * m * p0[1] + 2 * m * t * p1[1] + t * t * p2[1]))

    def cubic(p0, p1, p2, p3):
        for i in range(1, FLATTEN_STEPS + 1):
            t = i / FLATTEN_STEPS
            m = 1 - t
            cur.append((m ** 3 * p0[0] + 3 * m * m * t * p1[0] + 3 * m * t * t * p2[0] + t ** 3 * p3[0],
                        m ** 3 * p0[1] + 3 * m * m * t * p1[1] + 3 * m * t * t * p2[1] + t ** 3 * p3[1]))

    for op, args in pen.value:
        if op == "moveTo":
            if cur:
                contours.append(cur)
            cur = [args[0]]
        elif op == "lineTo":
            cur.append(args[0])
        elif op == "curveTo":
            pts, p0 = list(args), cur[-1]
            while len(pts) >= 3:
                cubic(p0, *pts[:3])
                p0, pts = pts[2], pts[3:]
        elif op == "qCurveTo":
            pts = list(args)
            if pts and pts[-1] is None:     # TrueType contour of off-curve points only
                start = ((pts[0][0] + pts[-2][0]) / 2, (pts[0][1] + pts[-2][1]) / 2)
                cur = [start]
                pts = pts[:-1] + [start]
            p0, offs, last = cur[-1], pts[:-1], pts[-1]
            for i, off in enumerate(offs):
                on = ((off[0] + offs[i + 1][0]) / 2, (off[1] + offs[i + 1][1]) / 2) if i + 1 < len(offs) else last
                quad(p0, off, on)
                p0 = on
            if not offs:
                cur.append(last)
        elif op in ("closePath", "endPath"):
            if cur:
                contours.append(cur)
                cur = []
    if cur:
        contours.append(cur)
    return contours


def _bounds(contours):
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _runs(contours, coord: float, axis: str) -> list[float]:
    """Ink run lengths along a horizontal (axis "y": y = coord) or vertical (axis "x") scanline,
    by even-odd pairing of edge crossings; [] when the line hits a vertex."""
    a_i, o_i = (1, 0) if axis == "y" else (0, 1)
    cross = []
    for c in contours:
        for i in range(len(c)):
            p, q = c[i], c[(i + 1) % len(c)]
            a, b = p[a_i], q[a_i]
            if (a - coord) * (b - coord) < 0:
                t = (coord - a) / (b - a)
                cross.append(p[o_i] + t * (q[o_i] - p[o_i]))
    cross.sort()
    if len(cross) % 2:
        return []
    return [cross[i + 1] - cross[i] for i in range(0, len(cross), 2)]


def _cluster(values: list[float], tol: float) -> list[tuple[int, int]]:
    """[(median, count)] of single-linkage clusters (gap > tol splits), largest first."""
    if not values:
        return []
    values = sorted(values)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] > tol:
            groups.append([v])
        else:
            groups[-1].append(v)
    reps = [(round(g[len(g) // 2]), len(g)) for g in groups]
    reps.sort(key=lambda rc: -rc[1])
    return reps


class _Font:
    def __init__(self, font: TTFont):
        self.gs = font.getGlyphSet()
        self.cmap = font.getBestCmap() or {}
        self.upm = font["head"].unitsPerEm
        self.h_tol = max(4, round(self.upm * 0.005))
        self.s_tol = max(3, round(self.upm * 0.003))
        self._chars: dict[str, str] | None = None
        self._cache: dict[str, list] = {}

    def char(self, name: str) -> str | None:
        """A character mapped to the glyph (the lowest code point), None if unencoded."""
        if self._chars is None:
            self._chars = {}
            for code, n in sorted(self.cmap.items(), reverse=True):
                self._chars[n] = chr(code)
        return self._chars.get(name)

    def resolve(self, glyphs) -> list[tuple[str | None, str | None, str]]:
        """[(glyph name or None, character or None, as given)]: a str is a string of characters
        (through the cmap), any other iterable holds glyph names."""
        if isinstance(glyphs, str):
            return [(self.cmap.get(ord(ch)), ch, ch) for ch in glyphs]
        return [(n if n in self.gs else None, self.char(n), n) for n in glyphs]

    def contours(self, name: str | None):
        if name is None or name not in self.gs:
            return None
        if name not in self._cache:
            self._cache[name] = _flatten(self.gs, name)
        return self._cache[name] or None


def _font(font) -> _Font:
    return font if isinstance(font, _Font) else _Font(font)


def _result(vals: list[float], used: list[str], tol: float, missing: list[str]) -> dict:
    """{value, samples, total, glyphs, values, missing}: value is the median of the dominant cluster,
    None unless it has 2 samples or is the only sample; values maps each glyph to its samples."""
    flat = [v for vs in vals for v in vs]
    out = {"value": None, "samples": 0, "total": len(flat), "glyphs": used,
           "values": {n: [round(v) for v in vs] for n, vs in zip(used, vals)}, "missing": missing}
    cl = _cluster(flat, tol)
    if cl and (cl[0][1] >= 2 or len(flat) == 1):
        out["value"], out["samples"] = cl[0]
    return out


def measure_height(font, glyphs, side: str, *, tol: float | None = None) -> dict:
    """A height from the outline tops (side "top") or bottoms ("bottom") of `glyphs` (a str of
    characters, or glyph names): the dominant cluster of the extremes, as `measure` does.

    Returns {value, samples, total, glyphs, values, missing}; `values` maps each measured glyph to
    [its extreme], `missing` lists the entries without a glyph or an outline. Never raises on an empty
    or unknown set (value None, total 0)."""
    if side not in ("top", "bottom"):
        raise ValueError(f"side must be 'top' or 'bottom', not {side!r}")
    f = _font(font)
    vals, used, missing = [], [], []
    for name, _, given in f.resolve(glyphs):
        b = _bounds(cs) if (cs := f.contours(name)) else None
        if b:
            vals.append([b[3] if side == "top" else b[1]])
            used.append(name)
        else:
            missing.append(given)
    return _result(vals, used, f.h_tol if tol is None else tol, missing)


def measure_overshoot(font, glyphs, side: str, parent_value: int | None, *, tol: float | None = None) -> dict:
    """A round height relative to its flat parent (`~ parent` in the template): `measure_height` of
    the round `glyphs` minus `parent_value`. value is None when either is unknown; `values` stay
    absolute, `absolute` holds the round height itself."""
    m = measure_height(font, glyphs, side, tol=tol)
    m["absolute"] = m["value"]
    m["value"] = None if m["value"] is None or parent_value is None else m["value"] - parent_value
    return m


def _scan(contours, coord, axis):
    for d in NUDGES:
        runs = _runs(contours, coord + d, axis)
        if runs:
            return runs
    return []


def measure_stem(font, glyphs, axis: str, where, *, band: tuple[float, float] | None = None,
                 tol: float | None = None) -> dict:
    """A stroke width from ink runs cut by scanlines through `glyphs` (a str of characters, or glyph
    names), as `measure` does for its STEMS.

    axis "y": horizontal scanlines, measuring X (vertical) strokes; `where` is "mid" (the middle of each
    glyph's bounding box) or fractions of `band` = (bottom, top), by default (0, upm/2).
    axis "x": vertical scanlines at fractions of each glyph's width, measuring Y (horizontal) strokes;
    `where` is a tuple of fractions or a dict of them per character or glyph name (others at 0.5).
    Runs outside (s_tol, 0.35 upm] are dropped. Returns the dict of `measure_height`."""
    if axis not in ("x", "y"):
        raise ValueError(f"axis must be 'x' or 'y', not {axis!r}")
    f = _font(font)
    lo, hi = band if band is not None else (0, f.upm * 0.5)
    max_stem = f.upm * 0.35
    vals, used, missing = [], [], []
    for name, ch, given in f.resolve(glyphs):
        b = _bounds(cs) if (cs := f.contours(name)) else None
        if not b:
            missing.append(given)
            continue
        if axis == "y":
            coords = [(b[1] + b[3]) / 2] if where == "mid" else [lo + (hi - lo) * t for t in where]
        else:
            fr = (where.get(ch) or where.get(name) or (0.5,)) if isinstance(where, dict) else where
            coords = [b[0] + (b[2] - b[0]) * (t + d) for t in fr for d in (-0.05, 0.05)]
        got = [w for c in coords for w in _scan(cs, c, axis) if f.s_tol < w <= max_stem]
        if got:
            vals.append(got)
            used.append(name)
    return _result(vals, used, f.s_tol if tol is None else tol, missing)


_BASELINE = {2: 8, 6: 10, 4: 12}


def default_glyphs(cvt: int) -> dict | None:
    """What `measure` measures a CVT of the template from, for prefilling a re-measure:
    {kind: "height" | "overshoot" | "stem", what, chars, ...}. Heights add `side`, overshoots `side`
    and `parent` (the flat CVT the value is relative to), stems `axis`, `where` and `band` (the height
    CVTs (bottom, top) the "y" scan fractions refer to). None for CVTs `measure` does not measure
    from glyphs (the stroke parents 65–67 are copies, see `measure`)."""
    if cvt in HEIGHTS:
        what, chars, top = HEIGHTS[cvt]
        return {"kind": "height", "what": what, "chars": chars, "side": "top" if top else "bottom"}
    if cvt in ROUND_HEIGHTS:
        what, chars, top, parent = ROUND_HEIGHTS[cvt]
        return {"kind": "overshoot", "what": what, "chars": chars, "side": "top" if top else "bottom",
                "parent": parent}
    if cvt in STEMS:
        what, chars, axis, where, band = STEMS[cvt]
        return {"kind": "stem", "what": what, "chars": chars, "axis": axis, "where": where,
                "band": (_BASELINE[band], band)}
    return None


def measure(font: TTFont) -> dict[int, Measured]:
    """Heights and stems of a static font, keyed by the template's CVT numbers."""
    f = _Font(font)
    out: dict[int, Measured] = {}

    def keep(cvt, what, m, parent=None):
        out[cvt] = Measured(cvt, what, m["value"], m["samples"], m["total"], m["glyphs"], parent)

    for cvt, (what, chars, top) in HEIGHTS.items():
        keep(cvt, what, measure_height(f, chars, "top" if top else "bottom"))
    for cvt, (what, chars, top, parent) in ROUND_HEIGHTS.items():
        keep(cvt, what, measure_overshoot(f, chars, "top" if top else "bottom", out[parent].value), parent)
    for cvt, (what, chars, axis, where, band) in STEMS.items():
        band_top = out[band].value or (f.upm * 0.7 if band == 2 else f.upm * 0.5)
        band_bot = out[_BASELINE[band]].value or 0
        keep(cvt, what, measure_stem(f, chars, axis, where, band=(band_bot, band_top)))

    # the parents: 66 (X) and 67 (Y) follow the lowercase straight strokes, the grand parent 65 is the
    # thinner of the two (so both may break loose from it at their own ppem)
    for cvt, what, order in ((66, "X stroke parent", X_PARENT), (67, "Y stroke parent", Y_PARENT)):
        src = next((c for c in order if out[c].value), None)
        out[cvt] = Measured(cvt, what, out[src].value if src else None, parent=src)
    src = min((c for c in (66, 67) if out[c].value), key=lambda c: out[c].value, default=None)
    out[65] = Measured(65, "grand parent", out[src].value if src else None, parent=src)
    return out


def as_json(measured: dict[int, Measured]) -> dict:
    return {str(k): asdict(v) for k, v in sorted(measured.items())}
