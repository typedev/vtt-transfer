"""VTT Talk parsing: every command, its flags and typed arguments, and renumbering points.

The scanner and the argument matching follow Microsoft's compiler (VisualTrueType,
src/TMTParser.cpp: GetSym, Parameter, MatchParameter and the tmtCmd[] table), so a call
is understood here exactly when its arguments fit the command's formal parameters there.
Point checks that need the glyph (range of point and cvt numbers) and contextual errors
(e.g. italic flags without GlyphStrokeAngle) are left to the compiler.

A call is `remappable` when every point it references is a plain number in a point
argument; commands with untyped arguments (Call, Inline/ASM, BeginCodePath, DiagEndCtrl…)
are parsed for viewing but never remapped, so a glyph using them goes to the re-hint list.
"""

from __future__ import annotations

from bisect import bisect_right
from fractions import Fraction

# ---------------------------------------------------------------- formal parameter types
# In the order of Microsoft's ParamType: Match() compares ranges of it.
(VOID, ANY_N, KNOT, KNOT_TTV, KNOT_TTV_XY, CVT, COMP_LOGIC, CVT_LOGIC, PHASE, ANGLE100, COLOR,
 SERIF, CURVE, RADIUS, RATIONAL, POS_RATIONAL, PPEM_SIZE, PPEM_N, RANGE_PPEM, RANGE_PPEM_COLOR,
 ANY_S, MIN_DIST_FLAG, MIN_DIST, DIR_FLAG, ANGLE_FLAG, STROKE_FLAG, POST_ROUND_FLAG) = range(27)
MAND, OPT, OPT_R = range(3)
MAX_FPS = 10

_K, _KT, _KX, _C, _P = KNOT, KNOT_TTV, KNOT_TTV_XY, CVT, PPEM_SIZE
_DELTA = [(KNOT, MAND), (RANGE_PPEM_COLOR, MAND), (RANGE_PPEM_COLOR, OPT_R)]
_GRID = [(KNOT, MAND), (KNOT, OPT_R)]
_DIAG = [(DIR_FLAG, MAND), (DIR_FLAG, MAND), (_K, MAND), (_K, MAND), (_K, MAND), (_K, MAND), (_C, OPT), (_P, OPT)]


def _m(*types):
    return [(t, MAND) for t in types]


# (name, minimal abbreviation, formal parameters) — tmtCmd[], same order (binary search)
COMMANDS: list[tuple[str, int, list]] = [
    ("Align", 2, _m(_K, _K, _K) + [(_K, OPT_R)]),
    ("ASM", 2, _m(ANY_S)),
    ("AutoHinter", 2, _m(ANY_S)),
    ("BeginCodePath", 1, _m(ANY_N)),
    ("Call", 2, [(ANY_N, MAND), (ANY_N, OPT_R)]),
    ("CompilerLogic", 2, _m(COMP_LOGIC)),
    ("CvtAllocation", 4, _m(ANY_S)),
    ("CvtLogic", 4, _m(CVT_LOGIC)),
    ("DAlign", 2, _m(_K, _K, _K) + [(_K, OPT_R), (_P, OPT)]),
    ("DiagEndCtrl", 5, _m(_K, _K, _K, ANY_N)),
    ("Diagonal", 5, _DIAG),
    ("DiagSerifs", 5, _m(_K, _K)),
    ("DStroke", 2, _m(DIR_FLAG, DIR_FLAG, _K, _K, _K, _K) + [(_C, OPT)]),
    ("EndCodePath", 1, []),
    ("FixDStrokes", 1, []),
    ("GlyphStrokeAngle", 2, _m(_C, _C)),
    ("GrabHereInX", 2, _m(_K, _K)),
    ("Height", 1, _m(_K, _C)),
    ("Inline", 3, _m(ANY_S)),
    ("Intersect", 3, _m(_K, _K, _K, _K, _K) + [(_P, OPT), (_P, OPT)]),
    ("IStroke", 2, _m(DIR_FLAG, DIR_FLAG, _K, _K, _K, _K, _C, _C, PHASE) + [(_C, OPT)]),
    ("MainStrokeAngle", 1, _m(ANGLE100)),
    ("ProcessXSymmetrically", 8, []),
    ("ProcessYSymmetrically", 8, []),
    ("Quit", 1, []),
    ("ResIIPDDist", 8, _m(_K, _K, _K, _K, _K, _K)),
    ("ResIIPDLink", 8, _m(_K, _K, _K, _C, _K, _K, _C, _K)),
    ("ResXAnchor", 5, [(ANGLE_FLAG, OPT), (_KT, MAND), (_C, OPT)]),
    ("ResXDDist", 6, _m(_K, _KX, _K, _KX)),
    ("ResXDist", 6, [(ANGLE_FLAG, OPT), (_KT, MAND), (_KT, MAND), (MIN_DIST_FLAG, OPT)]),
    ("ResXDLink", 6, _m(_K, _KX, _C, _K, _KX, _C)),
    ("ResXIPAnchor", 7, [(ANGLE_FLAG, OPT), (POST_ROUND_FLAG, OPT)] + _m(_K, _K, _K)),
    ("ResXIPDDDist", 9, [(ANGLE_FLAG, OPT)] + _m(_K, _K, _KX, _K, _KX, _K)),
    ("ResXIPDDist", 9, _m(_K, _K, _K, _K, _K, _K)),
    ("ResXIPDDLink", 9, [(ANGLE_FLAG, OPT)] + _m(_K, _K, _KX, _C, _K, _KX, _C, _K)),
    ("ResXIPDist", 8, [(ANGLE_FLAG, OPT), (STROKE_FLAG, OPT)] + _m(_K, _K, _K, _K)),
    ("ResXIPDLink", 8, _m(_K, _K, _K, _C, _K, _K, _C, _K)),
    ("ResXIPLink", 7, [(ANGLE_FLAG, OPT), (STROKE_FLAG, OPT)] + _m(_K, _K, _K, _C, _K)),
    ("ResXLink", 5, [(ANGLE_FLAG, OPT)] + _m(_KT, _KT, _C) + [(MIN_DIST_FLAG, OPT)]),
    ("ResYAnchor", 5, [(ANGLE_FLAG, OPT), (_KT, MAND), (_C, OPT)]),
    ("ResYDDist", 6, _m(_K, _KX, _K, _KX)),
    ("ResYDist", 6, [(ANGLE_FLAG, OPT), (_KT, MAND), (_KT, MAND), (MIN_DIST_FLAG, OPT)]),
    ("ResYDLink", 6, _m(_K, _KX, _C, _K, _KX, _C)),
    ("ResYIPAnchor", 7, [(ANGLE_FLAG, OPT)] + _m(_K, _K, _K)),
    ("ResYIPDDDist", 9, _m(_K, _K, _KX, _K, _KX, _K)),
    ("ResYIPDDist", 9, _m(_K, _K, _K, _K, _K, _K)),
    ("ResYIPDDLink", 9, _m(_K, _K, _KX, _C, _K, _KX, _C, _K)),
    ("ResYIPDist", 8, [(ANGLE_FLAG, OPT), (STROKE_FLAG, OPT)] + _m(_K, _K, _K, _K)),
    ("ResYIPDLink", 8, _m(_K, _K, _K, _C, _K, _K, _C, _K)),
    ("ResYIPLink", 7, [(ANGLE_FLAG, OPT), (STROKE_FLAG, OPT)] + _m(_K, _K, _K, _C, _K)),
    ("ResYLink", 5, [(ANGLE_FLAG, OPT)] + _m(_KT, _KT, _C) + [(MIN_DIST_FLAG, OPT)]),
    ("Scoop", 2, _m(_K, _K, _K)),
    ("Serif", 3, _m(DIR_FLAG, SERIF, _K)),   # more knots by serif type, see _serif_fps
    ("SetItalicStrokeAngle", 16, []),
    ("SetItalicStrokePhase", 16, []),
    ("Smooth", 2, [(ANGLE_FLAG, OPT)]),
    ("Stroke", 2, _DIAG),
    ("Tail", 2, _m(_K, _K, _K, _K)),
    ("TweakMetrics", 2, []),
    ("VacuFormLimit", 9, _m(_P)),
    ("VacuFormRound", 9, _m(DIR_FLAG, DIR_FLAG, CURVE, RADIUS, _K, _K, _K, _K)),
    ("XAlign", 3, _m(_K, _K, _K) + [(_K, OPT_R), (_P, OPT)]),
    ("XAnchor", 3, [(ANGLE_FLAG, OPT), (_KT, MAND)]),
    ("XBDelta", 2, _DELTA),
    ("XCDelta", 2, _DELTA),
    ("XDelta", 3, _DELTA),
    ("XDiagonal", 4, _DIAG),
    ("XDist", 4, [(ANGLE_FLAG, OPT), (POST_ROUND_FLAG, OPT)] + _m(_KT, _KT) + [(MIN_DIST, OPT)]),
    ("XDoubleGrid", 4, _GRID),
    ("XDownToGrid", 4, _GRID),
    ("XGDelta", 2, _DELTA),
    ("XHalfGrid", 2, _GRID),
    ("XInterpolate", 3, [(ANGLE_FLAG, OPT)] + _m(_KT, _KT, _KT) + [(_KT, OPT_R)]),
    ("XInterpolate0", 13, []),
    ("XInterpolate1", 13, []),
    ("XIPAnchor", 4, [(ANGLE_FLAG, OPT), (POST_ROUND_FLAG, OPT)] + _m(_KT, _KT, _KT) + [(_KT, OPT_R)]),
    ("XLink", 2, [(ANGLE_FLAG, OPT), (POST_ROUND_FLAG, OPT)] + _m(_KT, _KT) + [(_C, OPT), (MIN_DIST, OPT)]),
    ("XMove", 2, _m(RATIONAL, _K) + [(_K, OPT_R)]),
    ("XNoRound", 2, _GRID),
    ("XRound", 2, _m(_K, _K) + [(_C, OPT)]),
    ("XShift", 3, [(ANGLE_FLAG, OPT)] + _m(_K, _KT) + [(_KT, OPT_R)]),
    ("XSmooth", 3, [(ANGLE_FLAG, OPT)]),
    ("XStem", 4, _m(_K, _K) + [(_C, OPT)]),
    ("XStroke", 4, _m(_K, _K, _K, _K) + [(_C, OPT)]),
    ("XUpToGrid", 2, _GRID),
    ("YAlign", 3, _m(_K, _K, _K) + [(_K, OPT_R), (_P, OPT)]),
    ("YAnchor", 3, [(ANGLE_FLAG, OPT), (_KT, MAND), (_C, OPT)]),
    ("YBDelta", 2, _DELTA),
    ("YCDelta", 2, _DELTA),
    ("YDelta", 3, _DELTA),
    ("YDiagonal", 4, _DIAG),
    ("YDist", 4, [(ANGLE_FLAG, OPT)] + _m(_KT, _KT) + [(MIN_DIST, OPT)]),
    ("YDoubleGrid", 4, _GRID),
    ("YDownToGrid", 4, _GRID),
    ("YGDelta", 2, _DELTA),
    ("YHalfGrid", 2, _GRID),
    ("YInterpolate", 3, [(ANGLE_FLAG, OPT)] + _m(_KT, _KT, _KT) + [(_KT, OPT_R)]),
    ("YInterpolate0", 13, []),
    ("YInterpolate1", 13, []),
    ("YIPAnchor", 4, [(ANGLE_FLAG, OPT)] + _m(_KT, _KT, _KT) + [(_KT, OPT_R)]),
    ("YLink", 2, [(ANGLE_FLAG, OPT)] + _m(_KT, _KT) + [(_C, OPT), (MIN_DIST, OPT)]),
    ("YMove", 2, _m(RATIONAL, _K) + [(_K, OPT_R)]),
    ("YNoRound", 2, _GRID),
    ("YRound", 2, _m(_K, _K) + [(_C, OPT)]),
    ("YShift", 3, [(ANGLE_FLAG, OPT)] + _m(_K, _KT) + [(_KT, OPT_R)]),
    ("YSmooth", 3, [(ANGLE_FLAG, OPT)]),
    ("YStem", 4, _m(_K, _K) + [(_C, OPT)]),
    ("YStroke", 4, _m(_K, _K, _K, _K) + [(_C, OPT)]),
    ("YUpToGrid", 2, _GRID),
]
_FPS = {name: fps for name, _, fps in COMMANDS}
# parsed by the compiler, then refused: "Sorry, this command is no longer supported"
OBSOLETE = {"CompilerLogic", "CvtAllocation", "CvtLogic", "DiagEndCtrl", "DiagSerifs", "ProcessXSymmetrically",
            "ProcessYSymmetrically", "Tail", "TweakMetrics"}
_INTERPOLATE = {"XInterpolate", "YInterpolate", "XIPAnchor", "YIPAnchor"}

# (kind, axis) for drawing; the axis of XStroke & co. is the command's, None for diagonal ones
KINDS: dict[str, tuple[str, str | None]] = {}
for _name, _, _ in COMMANDS:
    _res = _name.startswith("Res")
    _rest = _name[3:] if _res else _name
    _axis = _rest[0].lower() if _rest[0] in "XY" and _rest[1:2].isupper() else None
    _base = _rest[1:] if _axis else _rest
    KINDS[_name] = ({
        "Anchor": "anchor", "Link": "link", "Dist": "dist", "Shift": "shift", "Interpolate": "interpolate",
        "IPAnchor": "ipanchor", "Align": "align", "DAlign": "align", "Delta": "delta", "BDelta": "delta",
        "CDelta": "delta", "GDelta": "delta", "NoRound": "round", "DownToGrid": "round", "UpToGrid": "round",
        "HalfGrid": "round", "DoubleGrid": "round", "Smooth": "smooth", "Move": "move", "Stem": "stem",
        "Round": "curve", "Stroke": "stroke", "DStroke": "stroke", "IStroke": "stroke", "Diagonal": "diagonal",
        "DDist": "ddist", "DLink": "dlink", "IPDist": "ipdist", "IPDDist": "ipdist", "IPDDDist": "ipdist",
        "IIPDDist": "ipdist", "IPLink": "iplink", "IPDLink": "iplink", "IPDDLink": "iplink", "IIPDLink": "iplink",
        "Height": "height", "Intersect": "intersect", "Scoop": "scoop", "Serif": "serif", "GrabHereInX": "grab", "VacuFormLimit": "vacuform", "VacuFormRound": "vacuform",
        "Call": "call", "Inline": "asm", "ASM": "asm",
    }.get(_base, "control"), "x" if _name == "GrabHereInX" else _axis)

ROLES = {KNOT: "point", KNOT_TTV: "point", KNOT_TTV_XY: "point", CVT: "cvt", RANGE_PPEM: "delta",
         RANGE_PPEM_COLOR: "delta", PPEM_SIZE: "ppem", PPEM_N: "ppem", MIN_DIST_FLAG: "mindist",
         MIN_DIST: "mindist", ANY_S: "string", ANY_N: "number", DIR_FLAG: "flag", ANGLE_FLAG: "flag",
         STROKE_FLAG: "flag", POST_ROUND_FLAG: "flag"}   # everything else: "value"
FLAG_TYPES = {"<": DIR_FLAG, ">": DIR_FLAG, "/": ANGLE_FLAG, "//": ANGLE_FLAG, "||": STROKE_FLAG,
              "|<": STROKE_FLAG, ">|": STROKE_FLAG, "$": POST_ROUND_FLAG}
FLAG_NAMES = {DIR_FLAG: "dir", ANGLE_FLAG: "angle", STROKE_FLAG: "stroke", POST_ROUND_FLAG: "postround"}

# kept for callers that list the commands the transfer can remap
SUPPORTED = {name for name, _, fps in COMMANDS if not any(t in (ANY_N, ANY_S) for t, _ in fps)} - OBSOLETE - {
    "XInterpolate0", "XInterpolate1", "YInterpolate0", "YInterpolate1"}


# ---------------------------------------------------------------- scanner
class _Tok:
    __slots__ = ("sym", "start", "end", "val")

    def __init__(self, sym, start, end, val=None):
        self.sym, self.start, self.end, self.val = sym, start, end, val


_PUNCT = {"(": "(", "[": "(", ")": ")", "]": ")", "+": "+", "-": "-", "*": "*", ":": ":", "%": "%",
          ",": ",", ";": ";", "@": "@", "^": "^", "$": "$", "¨": "$", "³": ">=", "¯": "//"}


def _alnum(ch: str) -> bool:
    return ch == "_" or ("0" <= ch <= "9") or ("A" <= ch <= "Z") or ("a" <= ch <= "z")


def _skip_comment(text: str, i: int) -> int:
    """End of the (nested) comment opening at i; len(text) if it is never closed."""
    depth, n = 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            depth, i = depth + 1, i + 2
        elif text.startswith("*/", i):
            depth, i = depth - 1, i + 2
            if not depth:
                return i
        else:
            i += 1
    return n


def _comments(text: str):
    """(start, end) of every top-level comment."""
    i, n = 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            j = _skip_comment(text, i)
            yield i, j
            i = j
        elif text[i] == '"':   # a literal can hold "/*"
            j = text.find('"', i + 1)
            i = n if j < 0 else j + 1
        else:
            i += 1


def _tokens(text: str) -> list[_Tok]:
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if "\0" < ch <= " ":
            i += 1
        elif text.startswith("/*", i):
            i = _skip_comment(text, i)
        elif "0" <= ch <= "9":
            j = i
            while j < n and _alnum(text[j]):
                j += 1
            sym, word = "num", text[i:j]
            if j < n and text[j] == "." and text[j + 1:j + 2] != ".":
                j += 1
                while j < n and _alnum(text[j]):
                    j += 1
                sym = "rat"
            body = text[i:j]
            ok = body.replace(".", "").isdigit() and int(word) < 32768
            out.append(_Tok(sym if ok else "err", i, j, Fraction(body) if ok else None))
            i = j
        elif ch.isascii() and ch.isalpha():
            j = i
            while j < n and _alnum(text[j]):
                j += 1
            out.append(_Tok("ident", i, j, text[i:j].replace("_", "")))
            i = j
        elif ch == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j + 1
            out.append(_Tok("str" if text[j - 1:j] == '"' and j > i + 1 else "err", i, j, text[i + 1:j - 1]))
            i = j
        else:
            two = text[i:i + 2]
            if two in (">=", ">|", "<|", "||", "|<", "|>", "//", ".."):
                sym = {"<|": "|<", "|>": ">|"}.get(two, two)
                out.append(_Tok(sym, i, i + 2))
                i += 2
            elif ch in "<>/.":
                out.append(_Tok(ch, i, i + 1))
                i += 1
            else:
                out.append(_Tok(_PUNCT.get(ch, "err"), i, i + 1))
                i += 1
    return out


def resolve(name: str) -> str | None:
    """Canonical command for a name as the compiler reads it: case-insensitive, underscores
    ignored, abbreviations down to the minimal length; Interpolate0/1 are Interpolate."""
    entry = name.replace("_", "").upper()
    lo, hi = 0, len(COMMANDS) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        ident, min_match, _ = COMMANDS[mid]
        diff = 0
        for k in range(min_match):
            a = ident[k].upper() if k < len(ident) else "\0"
            b = entry[k] if k < len(entry) else "\0"
            diff = ord(a) - ord(b)
            if diff:
                break
        if diff > 0:
            hi = mid - 1
        elif diff < 0:
            lo = mid + 1
        else:
            return ident[:-1] if ident[-1] in "01" else ident
    return None


# ---------------------------------------------------------------- arguments
class _Bad(Exception):
    pass


class _Arg:
    """One actual parameter: its type as the scanner sees it, value, refs and ttv override."""

    def __init__(self, toks: list[_Tok], text: str):
        self.toks, self.start, self.end = toks, toks[0].start, toks[-1].end
        self.raw = text[self.start:self.end]
        self.value = None
        self.ttv = None         # {dir, from, to} with token spans
        self.plain = False      # a single natural number: a point argument we can renumber
        self.type = self._parse()

    def _expr(self, i: int) -> tuple[int, Fraction, bool]:
        t = self.toks
        sign = 1
        if i < len(t) and t[i].sym in "+-":
            sign, i = (-1 if t[i].sym == "-" else 1), i + 1
        i, v, rat = self._term(i)
        v *= sign
        while i < len(t) and t[i].sym in ("+", "-"):
            op = t[i].sym
            i, v2, r2 = self._term(i + 1)
            v, rat = (v + v2 if op == "+" else v - v2), rat or r2
        return i, v, rat

    def _term(self, i: int):
        i, v, rat = self._factor(i)
        t = self.toks
        while i < len(t) and t[i].sym in ("*", "/"):
            op = t[i].sym
            i, v2, r2 = self._factor(i + 1)
            if op == "*":
                v = v * v2
            else:
                if not v2:
                    raise _Bad
                if not rat and not r2 and v % v2:
                    rat = True
                v = v / v2
            rat = rat or r2
        return i, v, rat

    def _factor(self, i: int):
        t = self.toks
        if i < len(t) and t[i].sym in ("num", "rat"):
            return i + 1, t[i].val, t[i].sym == "rat"
        if i < len(t) and t[i].sym == "(":
            i, v, rat = self._expr(i + 1)
            if i < len(t) and t[i].sym == ")":
                return i + 1, v, rat
        raise _Bad

    def _parse(self) -> int:
        t, n = self.toks, len(self.toks)
        s = t[0].sym
        if s == "str":
            if n > 1:
                raise _Bad
            self.value = t[0].val
            return ANY_S
        if s == "@":
            i, v, rat = self._expr(1)
            if i != n or rat:
                raise _Bad
            self.value = int(v)
            return PPEM_SIZE
        if s in (">=", "<"):
            if n == 1:
                return MIN_DIST_FLAG
            if s == "<":
                raise _Bad
            if t[1].sym == "(":   # >=(pixels, @ppem, pixels, …)
                i = self._expr(2)[0]
                while i < n and t[i].sym == ",":
                    if t[i + 1:i + 2] and t[i + 1].sym == "@":
                        i = self._expr(i + 2)[0]
                    else:
                        raise _Bad
                    if not (i < n and t[i].sym == ","):
                        raise _Bad
                    i = self._expr(i + 1)[0]
                if i != n - 1 or t[i].sym != ")":
                    raise _Bad
            elif self._expr(1)[0] != n:
                raise _Bad
            return MIN_DIST
        i, v, rat = self._expr(0)
        self.value = v if rat else int(v)
        if i == n:
            self.plain = n == 1 and s == "num"
            return (POS_RATIONAL if v >= 0 else RATIONAL) if rat else ANY_N
        if t[i].sym == "@":   # delta: amount @ ppem[..ppem] ; … [% color]
            i += 1
            while True:
                i, lo, r = self._expr(i)
                if r:
                    raise _Bad
                if i < n and t[i].sym == "..":
                    i, hi, r = self._expr(i + 1)
                    if r or hi < lo:
                        raise _Bad
                if i < n and t[i].sym == ";":
                    i += 1
                    continue
                break
            if i == n:
                return RANGE_PPEM
            if t[i].sym == "%" and self._expr(i + 1)[0] == n:
                return RANGE_PPEM_COLOR
            raise _Bad
        if t[i].sym in (":", ">", "^") and not rat:   # knot [: from] (> | ^) [to]
            if not (n > 0 and s == "num" and i == 1):
                raise _Bad   # an expression as a knot with an override: legal, but never written
            ttv = {"from": None, "to": None}
            if t[i].sym == ":":
                if not (i + 2 < n and t[i + 1].sym == "num" and t[i + 2].sym in (">", "^")):
                    raise _Bad
                ttv["from"] = t[i + 1]
                i += 2
            if t[i].sym not in (">", "^"):
                raise _Bad
            ttv["dir"] = t[i].sym
            i += 1
            if i < n:
                if i != n - 1 or t[i].sym != "num":
                    raise _Bad
                ttv["to"] = t[i]
            elif ttv["from"] is not None:
                raise _Bad
            if ttv["from"] is not None and ttv["from"].val == ttv["to"].val:
                raise _Bad
            self.ttv, self.plain = ttv, True
            return KNOT_TTV if ttv["to"] is not None else KNOT_TTV_XY
        raise _Bad


def _match(form: int, act: int) -> bool:
    return (act == form
            or (act == ANY_N and ANY_N <= form <= POS_RATIONAL)
            or (KNOT_TTV <= act <= KNOT_TTV_XY and KNOT_TTV <= form <= KNOT_TTV_XY)
            or (act == RANGE_PPEM and form == RANGE_PPEM_COLOR)
            or (act == POS_RATIONAL and RATIONAL <= form <= POS_RATIONAL)
            or (act == MIN_DIST_FLAG and form == MIN_DIST))


def _serif_fps(fps: list, serif_type) -> list:
    """AdjustFPs: the number of knots of Serif follows its type."""
    n = {0: 9, 1: 6, 2: 6, 3: 6, 4: 8}.get(serif_type, 0)
    fps = list(fps)
    for i in range(3, n):
        fps[i] = fps[2]
    for i in range(n, MAX_FPS - 1):
        fps[i] = (VOID, MAND)
    return fps


def _assign(cmd: str, acts: list, first_paren: int, serif=None) -> list[int] | None:
    """Formal type of each actual parameter (MatchParameter), or None if they don't fit."""
    fps = (_FPS[cmd] + [(VOID, MAND)] * MAX_FPS)[:MAX_FPS]
    n, out = 0, []
    for k, act in enumerate(acts):
        if cmd in _INTERPOLATE and k > first_paren and k == len(acts) - 1 and n < MAX_FPS:
            fps[n] = (KNOT, fps[n][1])   # the last parent never overrides the vector
        tent = n
        while tent < MAX_FPS and not _match(fps[tent][0], act) and fps[tent][1] in (OPT, OPT_R):
            tent += 1
        if n < tent < MAX_FPS and _match(fps[tent][0], act):
            n = tent
        if n >= MAX_FPS or fps[n][0] == VOID or not _match(fps[n][0], act):
            return None
        out.append(fps[n][0])
        if fps[n][1] in (MAND, OPT):
            n += 1
        if cmd == "Serif" and k == first_paren:
            fps = _serif_fps(fps, serif)
    while n < MAX_FPS and fps[n][1] in (OPT, OPT_R):
        n += 1
    if n < MAX_FPS and fps[n][0] != VOID:
        return None
    return out


# ---------------------------------------------------------------- calls
def _calls(text: str):
    """Yield every call as a dict, plus ('junk', start, end) for code that is not a call."""
    toks = _tokens(text)
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        j = i + 1
        while j < n and toks[j].sym in FLAG_TYPES:
            j += 1
        if t.sym != "ident" or j >= n or toks[j].sym != "(":
            k = i + 1
            while k < n and toks[k].sym != "ident":
                k += 1
            yield ("junk", t.start, toks[k - 1].end)
            i = k
            continue
        flags, args, cur, depth, k, closed = toks[i + 1:j], [], [], 0, j + 1, False
        while k < n:
            s = toks[k].sym
            if s == "ident":
                break
            if s == ")" and not depth:
                closed = True
                break
            depth += {"(": 1, ")": -1}.get(s, 0)
            if s == "," and not depth:
                args.append(cur)
                cur = []
            else:
                cur.append(toks[k])
            k += 1
        if cur or args:
            args.append(cur)
        end = toks[k].end if closed else toks[k - 1].end
        yield _call(text, t, flags, args, closed, end)
        i = k + 1 if closed else k


def _call(text: str, name: _Tok, flags: list[_Tok], arg_toks: list[list[_Tok]], closed: bool, end: int) -> dict:
    cmd = resolve(name.val)
    kind, axis = KINDS.get(cmd, ("other", None))
    item = dict(cmd=cmd or text[name.start:name.end], kind=kind, axis=axis, res=bool(cmd and cmd.startswith("Res")),
                flags=[dict(flag=f.sym, type=FLAG_NAMES[FLAG_TYPES[f.sym]], start=f.start, end=f.end) for f in flags],
                args=[text[a[0].start:a[-1].end] for a in arg_toks if a],
                points=[], refs=[], ttv=[], cvt=None, cvts=[], deltas=[], spans=[],
                supported=False, remappable=False, obsolete=cmd in OBSOLETE,
                start=name.start, end=end, name_end=name.end)
    if cmd is None or not closed or any(not a for a in arg_toks):
        return item
    try:
        acts = [_Arg(a, text) for a in arg_toks]
    except (_Bad, IndexError):
        return item
    types = [FLAG_TYPES[f.sym] for f in flags] + [a.type for a in acts]
    formal = _assign(cmd, types, len(flags), acts[0].value if acts else None)
    if formal is None:
        return item
    spans, remappable = [], True
    for f in flags:
        spans.append(dict(start=f.start, end=f.end, role="flag"))
    for a, ft in zip(acts, formal[len(flags):]):
        role = ROLES.get(ft, "value")
        if role == "number" and cmd == "Call" and a is acts[-1]:
            role = "function"
        if ft in (ANY_N, ANY_S):
            remappable = False
        if role == "point":
            if not isinstance(a.value, int):
                return item
            item["points"].append(a.value)
            item["refs"].append(a.value)
            remappable = remappable and a.plain
            if a.ttv is None:
                spans.append(dict(start=a.start, end=a.end, role="point"))
                continue
            head = a.toks[0]
            spans.append(dict(start=head.start, end=head.end, role="point"))
            ov = dict(point=a.value, dir={">": "x", "^": "y"}[a.ttv["dir"]], **{"from": None, "to": None})
            if a.ttv["to"] is not None:
                ov["dir"] = "line" if a.ttv["dir"] == ">" else "perp"
                ov["from"] = int(a.ttv["from"].val) if a.ttv["from"] is not None else a.value
                ov["to"] = int(a.ttv["to"].val)
            item["ttv"].append(ov)
            for v in (a.ttv["from"], a.ttv["to"]):
                if v is not None:
                    item["refs"].append(int(v.val))
                    spans.append(dict(start=v.start, end=v.end, role="vector"))
            continue
        spans.append(dict(start=a.start, end=a.end, role=role))
        if role == "cvt":
            item["cvts"].append(int(a.value))
        elif role == "delta":
            item["deltas"].append(a.raw)
    item["cvt"] = item["cvts"][0] if item["cvts"] else None
    item["spans"] = spans
    item["supported"] = not item["obsolete"]
    item["remappable"] = remappable and item["supported"]
    return item


# ---------------------------------------------------------------- API
def strip_comments(text: str) -> str:
    out, pos = [], 0
    for a, b in _comments(text):
        out.append(text[pos:a] + " ")
        pos = b
    return "".join(out) + text[pos:]


def has_code(text: str) -> bool:
    return bool(strip_comments(text or "").strip())


def command_kind(cmd: str) -> tuple[str, str | None, bool]:
    """(kind, axis 'x'/'y'/None, res) for a VTT Talk command name; kind 'other' if unknown."""
    c = resolve(cmd)
    if c is None:
        return "other", None, False
    kind, axis = KINDS[c]
    return kind, axis, c.startswith("Res")


def analyze(text: str) -> tuple[set[int], list[str], list[str]]:
    """Return (referenced points, calls/code that cannot be remapped, all commands)."""
    text = text or ""
    refs: set[int] = set()
    unsupported: list[str] = []
    commands_: list[str] = []
    for c in _calls(text):
        if isinstance(c, tuple):
            unsupported.append(text[c[1]:c[2]].strip()[:60])
            continue
        commands_.append(c["cmd"])
        if c["remappable"]:
            refs.update(c["refs"])
        else:
            unsupported.append(" ".join(text[c["start"]:c["end"]].split())[:80])
    return refs, unsupported, commands_


def _rewrite(text: str, fix) -> str:
    out, pos = [], 0
    for c in _calls(text):
        if isinstance(c, tuple):
            continue
        new = fix(c)
        if new is not None:
            out.append(text[pos:c["start"]] + new)
            pos = c["end"]
    return "".join(out) + text[pos:]


def remap(text: str, mapping: dict[int, int], unmapped: str | None = None) -> str:
    """Renumber point arguments (vector override points included) according to mapping.
    Caller must ensure all refs are mapped, unless `unmapped` is given: then a call
    referencing an unmapped point is commented out as `/* <unmapped>: <call> */`, keeping
    its original numbers. Calls that are not remappable are left as they are."""

    def fix(c: dict) -> str | None:
        if not c["remappable"] or not c["refs"]:
            return None
        raw = text[c["start"]:c["end"]]
        if unmapped is not None and any(p not in mapping for p in c["refs"]):
            return f"/* {unmapped}: {raw} */"
        out, pos = [], c["start"]
        for s in c["spans"]:
            if s["role"] in ("point", "vector"):
                out.append(text[pos:s["start"]] + str(mapping[int(text[s["start"]:s["end"]])]))
                pos = s["end"]
        return "".join(out) + text[pos:c["end"]]

    return _rewrite(text, fix)


def y_only(text: str) -> tuple[str, int]:
    """Drop X-direction commands (X*, ResX*), keep Y commands, Smooth() and comments."""
    removed = 0

    def fix(c: dict) -> str | None:
        nonlocal removed
        name = c["cmd"][3:] if c["cmd"].startswith("Res") else c["cmd"]
        if name[:1] == "X" and name[1:2].isupper():
            removed += 1
            return ""
        return None

    return _rewrite(text, fix), removed


def commands(text: str) -> list[dict]:
    """Every call in the VTT Talk of a glyph, in order. Used by viewers (e.g. hintbench); it
    shares the parser with analyze/remap so a viewer and the transfer read Talk the same way.

    Each item:
      cmd         canonical command name (as the compiler expands it), or the text if unknown
      kind, axis  what it draws as (see KINDS) and 'x'/'y'/None; res: a Res* command
      flags       [{flag, type: dir|angle|stroke|postround, start, end}] before the "("
      args        argument texts (flags excluded)
      points      point arguments in order (vector override points not included)
      refs        every point the call references, override points included (for remapping)
      ttv         vector overrides: [{point, dir: x|y|line|perp, from, to}] (from/to for line/perp)
      cvt, cvts   first cvt argument / all of them; deltas: delta argument texts
      supported   the arguments fit the command's signature; remappable: and the transfer can
                  renumber it (no untyped arguments, every point a plain number)
      obsolete    a command the compiler parses but refuses (Tail, DiagSerifs…); supported=False,
                  arguments still typed
      start, end, name_end, line  offsets in text, 1-based line
      spans       [{start, end, role}] for flags and arguments, role one of point, vector, cvt,
                  delta, ppem, mindist, flag, value, number, function, string
    Unknown or malformed calls have supported=False and no points/spans. The compiler's checks
    against the glyph (point and cvt ranges, stroke angles, perpendicular vectors…) are not made."""
    text = text or ""
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n" or (ch == "\r" and text[i + 1:i + 2] != "\n")]
    out = []
    for c in _calls(text):
        if isinstance(c, tuple):
            continue
        c["line"] = bisect_right(starts, c["start"])
        out.append(c)
    return out
