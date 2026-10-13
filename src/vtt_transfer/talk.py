"""VTT Talk parsing: which arguments of which commands are point numbers, and renumbering them.

Only commands whose argument types are known for sure are supported. A glyph whose
VTT Talk uses anything else is never remapped automatically (it goes to the re-hint list).
"""

from __future__ import annotations

import re

COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
CALL_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*)\s*\(([^()]*)\)")
INT_RE = re.compile(r"^\s*\d+\s*$")

# every numeric argument is a point
POINT_LIST = {
    "YShift", "XShift",
    "YInterpolate", "XInterpolate",
    "YIPAnchor", "XIPAnchor",
    "YAlign", "XAlign",
    "YNoRound", "XNoRound",
    "YDownToGrid", "XDownToGrid", "YUpToGrid", "XUpToGrid",
    "YHalfGrid", "XHalfGrid",
    "YDoubleGrid", "XDoubleGrid",
}
# (point [, cvt])
ANCHOR = {"YAnchor", "XAnchor", "ResYAnchor", "ResXAnchor"}
# (point, point [, non-numeric min-distance flags])
DIST = {"YDist", "XDist", "ResYDist", "ResXDist"}
# (point, point [, cvt] [, non-numeric flags])
LINK = {"YLink", "XLink", "ResYLink", "ResXLink"}
# (point, deltas@ppems ...)
DELTA = {"YDelta", "XDelta"}
NO_ARGS = {"Smooth", "XSmooth", "YSmooth"}

SUPPORTED = POINT_LIST | ANCHOR | DIST | LINK | DELTA | NO_ARGS


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub(" ", text)


def has_code(text: str) -> bool:
    return bool(strip_comments(text or "").strip())


def _point_positions(cmd: str, args: list[str]) -> list[int] | None:
    """Indices of point arguments, or None if the command/argument shape is not understood."""
    args = [a for a in args]
    nonempty = [a for a in args if a.strip()]
    numeric = [i for i, a in enumerate(args) if INT_RE.match(a)]
    if cmd in NO_ARGS:
        return [] if not nonempty else None
    if cmd in POINT_LIST:
        return numeric if len(numeric) == len(nonempty) and numeric else None
    if cmd in ANCHOR:
        if len(nonempty) in (1, 2) and all(INT_RE.match(a) for a in nonempty):
            return [0]
        return None
    if cmd in DIST:
        if len(args) >= 2 and 0 in numeric and 1 in numeric and len(numeric) == 2:
            return [0, 1]
        return None
    if cmd in LINK:
        if len(args) >= 2 and 0 in numeric and 1 in numeric and len(numeric) <= 3:
            if len(numeric) == 3 and numeric[2] != 2:
                return None
            return [0, 1]
        return None
    if cmd in DELTA:
        if args and INT_RE.match(args[0]) and all("@" in a for a in args[1:] if a.strip()):
            return [0]
        return None
    return None


def _code_segments(text: str):
    """Yield (is_code, segment) pieces of text, comments kept verbatim."""
    pos = 0
    for m in COMMENT_RE.finditer(text):
        if m.start() > pos:
            yield True, text[pos:m.start()]
        yield False, m.group(0)
        pos = m.end()
    if pos < len(text):
        yield True, text[pos:]


def analyze(text: str) -> tuple[set[int], list[str], list[str]]:
    """Return (referenced points, unsupported commands, all commands)."""
    refs: set[int] = set()
    unsupported: list[str] = []
    commands: list[str] = []
    for is_code, seg in _code_segments(text or ""):
        if not is_code:
            continue
        for m in CALL_RE.finditer(seg):
            cmd, args = m.group(1), m.group(2).split(",")
            commands.append(cmd)
            pos = _point_positions(cmd, args)
            if pos is None:
                unsupported.append(f"{cmd}({m.group(2).strip()})")
                continue
            refs.update(int(args[i]) for i in pos)
        # anything code-like that is not a recognised call (e.g. Inline, #directives)
        rest = CALL_RE.sub(" ", seg).strip()
        if rest:
            unsupported.append(rest[:60])
    return refs, unsupported, commands


def remap(text: str, mapping: dict[int, int], unmapped: str | None = None) -> str:
    """Renumber point arguments according to mapping. Caller must ensure all refs are mapped,
    unless `unmapped` is given: then a call referencing an unmapped point is commented out
    as `/* <unmapped>: <call> */`, keeping its original numbers."""

    def fix_call(m: re.Match) -> str:
        cmd, raw = m.group(1), m.group(2)
        args = raw.split(",")
        pos = _point_positions(cmd, args)
        if not pos:
            return m.group(0)
        if unmapped is not None and any(int(args[i]) not in mapping for i in pos):
            return f"/* {unmapped}: {m.group(0)} */"
        for i in pos:
            old = int(args[i])
            args[i] = re.sub(r"\d+", str(mapping[old]), args[i], count=1)
        return f"{m.group(0)[: m.start(2) - m.start(0)]}{','.join(args)})"

    out = []
    for is_code, seg in _code_segments(text):
        out.append(CALL_RE.sub(fix_call, seg) if is_code else seg)
    return "".join(out)


X_CMD_RE = re.compile(r"^(Res)?X[A-Z]")


def y_only(text: str) -> tuple[str, int]:
    """Drop X-direction commands (X*, ResX*), keep Y commands, Smooth() and comments."""
    removed = 0

    def drop(m: re.Match) -> str:
        nonlocal removed
        if X_CMD_RE.match(m.group(1)):
            removed += 1
            return ""
        return m.group(0)

    out = []
    for is_code, seg in _code_segments(text):
        out.append(CALL_RE.sub(drop, seg) if is_code else seg)
    return "".join(out), removed


# ---------------------------------------------------------------- structured commands
# Used by viewers (e.g. hintbench) to draw the hints; shares the argument typing above
# with analyze/remap so a viewer and the transfer understand VTT Talk the same way.

_AXIS_RE = re.compile(r"^(Res)?([XY])?([A-Z][A-Za-z]*)$")
_KINDS = {
    "Anchor": "anchor", "Link": "link", "Dist": "dist", "Shift": "shift",
    "Interpolate": "interpolate", "IPAnchor": "ipanchor", "Align": "align",
    "Delta": "delta", "NoRound": "round", "DownToGrid": "round", "UpToGrid": "round",
    "HalfGrid": "round", "DoubleGrid": "round", "Smooth": "smooth",
}
_LINE_RE = re.compile(r"\r\n|\r|\n")


def command_kind(cmd: str) -> tuple[str, str | None, bool]:
    """(kind, axis 'x'/'y'/None, res) for a VTT Talk command name; kind 'other' if unknown."""
    m = _AXIS_RE.match(cmd)
    if not m:
        return "other", None, False
    res, axis, base = bool(m.group(1)), m.group(2), m.group(3)
    return _KINDS.get(base, "other"), axis.lower() if axis else None, res


def _arg_spans(cmd: str, args: list[str], ppos: list[int] | None, has_cvt: bool, at: int) -> list[dict]:
    """Offsets of the typed arguments of one call; `at` is the offset of its first argument."""
    if ppos is None:
        return []
    out = []
    for i, arg in enumerate(args):
        body = arg.strip()
        if body:
            role = ("point" if i in ppos else
                    "cvt" if has_cvt and ((cmd in ANCHOR and i == 1) or (cmd in LINK and i == 2)) else
                    "delta" if cmd in DELTA and i >= 1 else None)
            if role:
                start = at + len(arg) - len(arg.lstrip())
                out.append(dict(start=start, end=start + len(body), role=role))
        at += len(arg) + 1
    return out


def commands(text: str) -> list[dict]:
    """Every call in the VTT Talk of a glyph, in order.

    Each item: cmd, kind, axis, res, args (stripped), points (point numbers in argument
    order; empty if the shape is not understood), cvt (Anchor/Link), deltas (Delta),
    supported, line (1-based), start/end (offsets in text), spans (offsets in text of the
    arguments that are points, a cvt or deltas: {start, end, role})."""
    text = text or ""
    starts = [0] + [m.end() for m in _LINE_RE.finditer(text)]
    out = []
    pos = 0
    for is_code, seg in _code_segments(text):
        if is_code:
            for m in CALL_RE.finditer(seg):
                cmd, raw = m.group(1), m.group(2)
                args = raw.split(",")
                ppos = _point_positions(cmd, args)
                kind, axis, res = command_kind(cmd)
                stripped = [a.strip() for a in args]
                item = dict(cmd=cmd, kind=kind, axis=axis, res=res, args=[a for a in stripped if a],
                            points=[int(stripped[i]) for i in ppos] if ppos else [],
                            cvt=None, deltas=[], supported=ppos is not None)
                if ppos is not None and cmd in ANCHOR and len(item["args"]) == 2:
                    item["cvt"] = int(item["args"][1])
                elif ppos is not None and cmd in LINK and len(stripped) >= 3 and INT_RE.match(stripped[2]):
                    item["cvt"] = int(stripped[2])
                elif ppos is not None and cmd in DELTA:
                    item["deltas"] = item["args"][1:]
                a = pos + m.start()
                item["start"], item["end"] = a, pos + m.end()
                item["spans"] = _arg_spans(cmd, args, ppos, item["cvt"] is not None, pos + m.start(2))
                item["line"] = sum(1 for s in starts if s <= a)
                out.append(item)
        pos += len(seg)
    return out
