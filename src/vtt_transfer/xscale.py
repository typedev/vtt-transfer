"""ttfautohint-style x-height scaling for VTT-hinted fonts, written into the VTT sources.

VTT rounds every height CVT on its own, so at a size where the x-height rounds up the caps may
round down (e.g. at 9 ppem: x 5 px, caps 6 px). ttfautohint instead rounds the x-height
(with overshoot) and scales all heights by the same factor before rounding them. This module
adds that step to a VTT font, in its sources:

* two functions in the fpgm source (TSI1 'fpgm'): FDEF n scales the heights, FDEF n+1 is its
  per-CVT loop body (LOOPCALL);
* one ASM("CALL[], …") line in the Control Program (TSI1 'cvt') before the first height CVT,
  so it runs in the pre-program before VTT's own rounding (fn 89) and overshoot links (fn 85).

At run time, with x = cvt[x-height] + cvt[x-height overshoot] (cvar already applied):
    xr = floor(x + (6 <= ppem <= limit ? 52 : 40) / 64)
    cvt[i] = cvt[i] * xr / x   for every SquareHeight / RoundHeight CVT
mode "up" never shrinks (xr = max(xr, x)); maxppem > 0 limits it to ppem <= maxppem.

Nothing is compiled here: the caller compiles the edited sources (vttcompilepy compile_all).
See hintbench/docs/XHEIGHT-SCALING.md for the measurements behind it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VERSION = 1
MARK_BEGIN = f"/* >>> vtt-transfer xscale v{VERSION}: ttfautohint-style x-height scaling */"
MARK_END = "/* <<< vtt-transfer xscale */"
CALL_COMMENT = "/* vtt-transfer xscale: scale height CVTs to the rounded x-height (fpgm fn {n}) */"

DEFAULT_GROUPS = ("UpperCase", "LowerCase", "Figure", "Other")
COLORS = {"Grey", "Black", "White"}
DIRECTIONS = {"X", "Y"}
HEIGHT_FEATURES = ("SquareHeight", "RoundHeight")

# `7: 8 ~ 6 @61` (relative to a parent) or `68: 140 = 66 @27` (absolute, inherits until 27 ppem)
_CVT_RE = re.compile(r"^(\d+)\s*:\s*(-?\d+)(?:\s*([~=])\s*(\d+)\s*@\s*(\d+))?\s*(.*)$")
_FDEF_RE = re.compile(r"FDEF\[\s*\]\s*,\s*(\d+)")
_ASM_RE = re.compile(r'^\s*ASM\(\s*"([^"]*)"\s*\)\s*$')
_CALL_ARGS_RE = re.compile(r"^CALL\[\s*\]\s*,\s*([-\d,\s]+)$")
# ASM lines of Microsoft's template that do not touch CVT values: projection vector, fn 89 (round a
# CVT for the rendering environment), WS 16 (the ppem above which accents are placed with RES rounding)
_STD_ASM_RE = re.compile(r"^(SVTCA\[\s*[XY]\s*\]|CALL\[\s*\]\s*,\s*\d+\s*,\s*89|WS\[\s*\]\s*,\s*16\s*,\s*\d+)$")


class XScaleError(Exception):
    pass


@dataclass(frozen=True)
class Params:
    mode: str = "both"     # "both" (ttfautohint) or "up" (only enlarge)
    limit: int = 14        # ttfautohint -x: the eager rounding applies at 6..limit ppem
    maxppem: int = 0       # apply only at ppem <= maxppem; 0 = all sizes

    def __post_init__(self):
        if self.mode not in ("both", "up"):
            raise ValueError(f"mode must be 'both' or 'up', not {self.mode!r}")
        if not 0 <= self.limit <= 32767 or not 0 <= self.maxppem <= 32767:
            raise ValueError("limit and maxppem must be 0..32767")


# ---------------------------------------------------------------- sources

def _sources(font):
    if "TSI1" not in font:
        raise XScaleError("the font has no VTT sources (TSI1)")
    return font["TSI1"].extraPrograms


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"


def _lines(text: str) -> list[str]:
    return re.split(r"\r\n|\r|\n", text)


def blank_comments(text: str) -> str:
    """Comments replaced by spaces, line breaks kept, so line numbers and columns stay valid. VTT's
    comments nest (Microsoft's Control Program template has `/* … */` examples inside comments)."""
    out, depth, i, n = [], 0, 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "/*":
            depth += 1
            out.append("  ")
            i += 2
        elif two == "*/" and depth:
            depth -= 1
            out.append("  ")
            i += 2
        else:
            c = text[i]
            out.append(c if not depth or c in "\r\n" else " ")
            i += 1
    return "".join(out)



def _parse_cvt(src: str) -> dict:
    """Height CVTs of a VTT Control Program, with the line numbers needed to edit it."""
    declared = set(DEFAULT_GROUPS)
    code = _lines(blank_comments(src))
    for s in code:
        m = re.match(r"\s*GROUP\s+(\S+)", s)
        if m:
            declared.add(m.group(1))
    raw = _lines(src)
    comment = CALL_COMMENT.split("(")[0]
    group = feature = None
    group_line = None
    cvts = []       # dicts: idx, value, parent, ppem, group, feature, line, group_line
    asm = []        # (line, instruction text, follows our call comment) of every ASM() line
    odd = []        # (line, text) of unparsed text after a CVT declaration
    for i, s in enumerate(code):
        s = s.strip()
        if not s:
            continue
        m = _ASM_RE.match(s)
        if m or s.startswith("ASM("):
            prev = next((r.strip() for r in reversed(raw[:i]) if r.strip()), "")
            asm.append((i, m.group(1).strip() if m else s, prev.startswith(comment)))
            continue
        m = _CVT_RE.match(s)
        if m:
            rel = m.group(3) == "~"
            cvts.append(dict(idx=int(m.group(1)), value=int(m.group(2)),
                             parent=int(m.group(4)) if rel else None,
                             ppem=int(m.group(5)) if rel else None,
                             group=group, feature=feature, line=i,
                             group_line=i if group_line is None else group_line))
            if m.group(6).strip():
                odd.append((i, s))
            continue
        if "=" in s or "@" in s or s.startswith(("GROUP", "CATEGORY")):
            continue  # settings (InstructionsOn @…, CvtCutIn = …) and declarations
        for w in s.split():
            if w in declared:
                group, feature, group_line = w, None, i
            elif w not in COLORS and w not in DIRECTIONS:
                feature = w   # SquareHeight, RoundHeight, stroke features, …
    return dict(cvts=cvts, asm=asm, odd=odd)


def find_heights(font) -> dict:
    """The CVTs to scale, read from the Control Program.

    Returns {xh, xo, cvts, groups, errors, warnings}: xh = the LowerCase SquareHeight CVT equal to
    OS/2.sxHeight, xo = its RoundHeight overshoot (-1 if none), cvts = every SquareHeight /
    RoundHeight CVT in source order, groups = {group: [cvt, …]}. Any error makes the result
    unusable (apply() refuses); warnings are things the scaling will not cover."""
    errors, warnings = [], []
    out = dict(xh=None, xo=-1, cvts=[], groups={}, errors=errors, warnings=warnings)
    try:
        src = _sources(font).get("cvt", "")
    except XScaleError as e:
        errors.append(str(e))
        return out
    if not src.strip():
        errors.append("the Control Program (TSI1 'cvt') is empty")
        return out
    p = _parse_cvt(src)
    heights = [c for c in p["cvts"] if c["feature"] in HEIGHT_FEATURES]
    seen = set()
    for c in heights:
        if c["idx"] not in seen:
            seen.add(c["idx"])
            out["cvts"].append(c["idx"])
            out["groups"].setdefault(c["group"], []).append(c["idx"])
    if not heights:
        errors.append("no SquareHeight / RoundHeight CVTs in the Control Program")
        return out

    os2 = font["OS/2"] if "OS/2" in font else None
    sx = getattr(os2, "sxHeight", 0) if os2 is not None else 0
    if not sx:
        errors.append("OS/2.sxHeight is missing or 0")
    else:
        cand = [c for c in heights if c["group"] == "LowerCase" and c["feature"] == "SquareHeight"
                and c["value"] == sx]
        if len(cand) != 1:
            found = ", ".join(str(c["idx"]) for c in cand) or "none"
            errors.append(f"expected one LowerCase SquareHeight CVT equal to sxHeight {sx}, found {found}")
        else:
            out["xh"] = cand[0]["idx"]
            over = [c for c in heights if c["feature"] == "RoundHeight" and c["parent"] == out["xh"]
                    and c["group"] == "LowerCase"]
            if not over:
                warnings.append(f"no RoundHeight overshoot linked to x-height CVT {out['xh']}: "
                                "the square x-height is used as the reference")
            else:
                out["xo"] = over[0]["idx"]
                if len(over) > 1:
                    warnings.append(f"several overshoots linked to x-height CVT {out['xh']} "
                                    f"({', '.join(str(c['idx']) for c in over)}): using {out['xo']}")

    for line, text in p["odd"]:
        warnings.append(f"Control Program line {line + 1}: unparsed text after a CVT ({text[:60]}) — "
                        "CVT deltas run after the scaling, tuned for unscaled values")
    for line, text, marked in p["asm"]:
        if _STD_ASM_RE.match(text) or (marked and _parse_call(text)):
            continue
        warnings.append(f"Control Program line {line + 1}: ASM(\"{text[:60]}\") runs after the scaling")
    if re.search(r"\bDELTAC\d|\bDelta\b", blank_comments(src)):
        warnings.append("the Control Program has CVT deltas; they run after the scaling, "
                        "tuned for unscaled values")
    return out


# ---------------------------------------------------------------- generated code

def free_fdef(font) -> int:
    """First function number n such that n and n+1 are above every FDEF in the fpgm source
    (gaps below are left alone: VTT and VTThinter reserve ranges)."""
    nums = [int(x) for x in _FDEF_RE.findall(blank_comments(_sources(font).get("fpgm", "")))]
    return max(nums) + 1 if nums else 0


def fdef_source(n: int, nl: str = "\n") -> str:
    """fpgm source of FDEF n (scaling) and FDEF n+1 (its LOOPCALL body), between markers."""
    text = f"""{MARK_BEGIN}

FDEF[], {n}

/* Scale height CVTs to the rounded x-height, before they are rounded (fn 89).

   CALL[], <cvt 1>, …, <cvt k>, <k>, <x-height cvt>, <overshoot cvt or -1>,
           <limit>, <maxppem or 0>, <up: 1 = only enlarge>, {n}

   x  = cvt[x-height] + cvt[overshoot]
   xr = floor(x + (6 <= ppem <= limit ? 52/64 : 40/64))
   cvt[i] = cvt[i] * xr / x, applied at ppem <= maxppem (all sizes if 0) */

#BEGIN
#PUSHOFF
/* c1..ck, k, xh, xo, limit, maxppem, up */
SVTCA[Y]
#PUSH, 4
MINDEX[]
DUP[]
#PUSH, 0
LT[]
IF[]
    POP[]
    #PUSH, 0
ELSE[]
    RCVT[]
EIF[]
#PUSH, 5
MINDEX[]
RCVT[]
ADD[]
/* c1..ck, k, limit, maxppem, up, x */
#PUSH, 3
MINDEX[]
DUP[]
#PUSH, 0
EQ[]
SWAP[]
MPPEM[]
GTEQ[]
OR[]
#PUSH, 2
CINDEX[]
#PUSH, 0
GT[]
AND[]
/* c1..ck, k, limit, up, x, (maxppem = 0 or ppem <= maxppem) and x > 0 */
IF[]
    MPPEM[]
    #PUSH, 4
    CINDEX[]
    LTEQ[]
    MPPEM[]
    #PUSH, 6
    GTEQ[]
    AND[]
    IF[]
        #PUSH, 52
    ELSE[]
        #PUSH, 40
    EIF[]
    #PUSH, 2
    CINDEX[]
    ADD[]
    FLOOR[]
    /* c1..ck, k, limit, up, x, xr */
    #PUSH, 3
    MINDEX[]
    IF[]
        #PUSH, 2
        CINDEX[]
        MAX[]
    EIF[]
ELSE[]
    SWAP[]
    POP[]
    DUP[]
EIF[]
/* c1..ck, k, limit, x, xr (xr = x: nothing to do) */
#PUSH, 3
MINDEX[]
POP[]
#PUSH, 3
MINDEX[]
/* c1..ck, x, xr, k */
#PUSH, {n + 1}
LOOPCALL[]
POP[]
POP[]
#PUSHON
#END
ENDF[]

FDEF[], {n + 1}

/* Loop body of fn {n}: cvt[c] = cvt[c] * xr / x unless xr = x.

   STACK: …, c, x, xr  ->  …, x, xr */

#BEGIN
#PUSHOFF
#PUSH, 3
MINDEX[]
#PUSH, 2
CINDEX[]
#PUSH, 4
CINDEX[]
NEQ[]
IF[]
    DUP[]
    RCVT[]
    #PUSH, 3
    CINDEX[]
    MUL[]
    #PUSH, 4
    CINDEX[]
    DIV[]
    WCVTP[]
ELSE[]
    POP[]
EIF[]
#PUSHON
#END
ENDF[]

{MARK_END}
"""
    return text.replace("\n", nl)


def call_args(n: int, params: Params, heights: dict) -> list[int]:
    cvts = heights["cvts"]
    return [*cvts, len(cvts), heights["xh"], heights["xo"], params.limit, params.maxppem,
            1 if params.mode == "up" else 0, n]


def call_asm(n: int, params: Params, heights: dict) -> str:
    """The Control Program line calling fn n. Push widths are VTT's business (it emits
    PUSHB/PUSHW per value when it compiles)."""
    return 'ASM("CALL[], ' + ", ".join(str(a) for a in call_args(n, params, heights)) + '")'


def stack_depth(heights: dict) -> int:
    """Stack elements the call needs at its peak, its own arguments and function number included
    (k + 7 pushed, one more inside fn n)."""
    return len(heights["cvts"]) + 8


def _parse_call(text: str):
    m = _CALL_ARGS_RE.match(text.strip())
    if not m:
        return None
    try:
        a = [int(x) for x in m.group(1).split(",")]
    except ValueError:
        return None
    if len(a) < 7:
        return None
    n, up, maxppem, limit, xo, xh, k = a[-1], a[-2], a[-3], a[-4], a[-5], a[-6], a[-7]
    cvts = a[:-7]
    if k != len(cvts) or up not in (0, 1):
        return None
    return dict(fdef=n, params=Params("up" if up else "both", limit, maxppem), xh=xh, xo=xo, cvts=cvts)


def _our_fdef(fpgm: str):
    """(n, start, end) of our function block in the fpgm source, or None."""
    a = fpgm.find(MARK_BEGIN)
    if a < 0:
        return None
    b = fpgm.find(MARK_END, a)
    if b < 0:
        raise XScaleError("fpgm source: xscale begin marker without end marker")
    m = _FDEF_RE.search(fpgm, a, b)
    if not m:
        raise XScaleError("fpgm source: no FDEF between the xscale markers")
    return int(m.group(1)), a, b + len(MARK_END)


def detect(font) -> dict | None:
    """State of the scaling in the sources, or None if neither the function nor the call is there.

    {fdef, fdef_present, call_present, call_position_ok, params, xh, xo, cvts, stale}:
    call_position_ok = the call comes before the first height CVT; stale = the call's CVTs differ
    from what find_heights() reads now (e.g. CVTs added since)."""
    src = _sources(font)
    fpgm, cvt = src.get("fpgm", ""), src.get("cvt", "")
    ours = _our_fdef(fpgm)
    n = ours[0] if ours else None
    p = _parse_cvt(cvt)
    # our call: right after our comment line, or calling our function
    calls = [(line, _parse_call(text)) for line, text, marked in p["asm"]
             if _parse_call(text) and (marked or _parse_call(text)["fdef"] == n)]
    if ours is None and not calls:
        return None
    out = dict(fdef=n, fdef_present=ours is not None, call_present=bool(calls), call_position_ok=False,
               params=None, xh=None, xo=None, cvts=None, stale=False)
    if calls:
        line, c = calls[0]
        out.update(fdef=c["fdef"] if n is None else n, params=c["params"], xh=c["xh"], xo=c["xo"],
                   cvts=c["cvts"])
        first = min((h["line"] for h in p["cvts"] if h["feature"] in HEIGHT_FEATURES), default=None)
        out["call_position_ok"] = len(calls) == 1 and (first is None or line < first)
        h = find_heights(font)
        out["stale"] = (h["cvts"], h["xh"], h["xo"]) != (c["cvts"], c["xh"], c["xo"])
    return out


# ---------------------------------------------------------------- editing

def _remove_call(cvt: str, n: int | None) -> tuple[str, bool]:
    """Drop our call (and its comment line) from the Control Program."""
    drop = set()
    lines = _lines(cvt)
    for line, text, marked in _parse_cvt(cvt)["asm"]:
        c = _parse_call(text)
        if c and (marked or c["fdef"] == n):
            drop.add(line)
            if marked:
                drop.add(max(j for j in range(line) if lines[j].strip()))
                if line + 1 < len(lines) and not lines[line + 1].strip():
                    drop.add(line + 1)   # the blank line apply() put after it
    keep = [s for i, s in enumerate(lines) if i not in drop]
    return _newline(cvt).join(keep), bool(drop)


def remove(font) -> bool:
    """Remove the functions and the call from the sources. Returns whether anything changed."""
    src = _sources(font)
    d = detect(font)
    if d is None:
        return False
    changed = False
    fpgm = src.get("fpgm", "")
    ours = _our_fdef(fpgm)
    if ours:
        _, a, b = ours
        nl = _newline(fpgm)
        if fpgm.startswith(nl, b):
            b += len(nl)
        src["fpgm"] = fpgm[:a] + fpgm[b:]
        changed = True
    cvt, c = _remove_call(src.get("cvt", ""), d["fdef"])
    if c:
        src["cvt"] = cvt
    return changed or c


def apply(font, params: Params = Params()) -> dict:
    """Add (or update) the scaling in the TSI1 fpgm and Control Program sources. Compiles nothing.

    Reuses our function if it is already there, replaces an existing call. Raises XScaleError if
    find_heights() reports errors. Returns {fdef, call, xh, xo, cvts, stack_depth, warnings}."""
    h = find_heights(font)
    if h["errors"]:
        raise XScaleError("; ".join(h["errors"]))
    src = _sources(font)
    fpgm = src.get("fpgm", "")
    ours = _our_fdef(fpgm)
    if ours:
        n = ours[0]
    else:
        n = free_fdef(font)
        nl = _newline(fpgm) if fpgm else "\r"
        src["fpgm"] = fpgm + ("" if not fpgm or fpgm.endswith(nl) else nl) + fdef_source(n, nl)
    cvt, _ = _remove_call(src.get("cvt", ""), n)
    nl = _newline(cvt)
    p = _parse_cvt(cvt)
    first = min((c for c in p["cvts"] if c["feature"] in HEIGHT_FEATURES), key=lambda c: c["line"])
    at = first["group_line"]
    lines = _lines(cvt)
    call = call_asm(n, params, h)
    lines[at:at] = [CALL_COMMENT.format(n=n), call, ""]
    src["cvt"] = nl.join(lines)
    return dict(fdef=n, call=call, xh=h["xh"], xo=h["xo"], cvts=h["cvts"],
                stack_depth=stack_depth(h), warnings=h["warnings"])
