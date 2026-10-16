"""Update a VTThinter project file (.hprj) to a new version of the font, or derive one for a static
cut from the variable font.

The file is edited textually so that its formatting stays as the hinter wrote it: `update` changes
only the <names array="..."> lists, TTFFileName and XMLFileName; `for_static` the file names and the
CVT values.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import core as C

CLASS_RE = re.compile(r'<Class\s+name\s*=\s*"(?P<name>[^"]+)"\s*>(?P<body>.*?)</Class>', re.S)
NAMES_RE = re.compile(r'(<names\s+array\s*=\s*")([^"]*)(")')
TTF_RE = re.compile(r'(<TTFFileName\s+name\s*=\s*")([^"]*)(")')
XML_RE = re.compile(r'(<XMLFileName\s+name\s*=\s*")([^"]*)(")')
VALUE_RE = re.compile(r'(<(\w+)\s+cvt\s*=\s*"(\d+)"\s+value\s*=\s*")(-?\d+)(")')
STEM_RE = re.compile(r'<stem\s+min\s*=\s*"(\d+)"\s+max\s*=\s*"(\d+)"')

NEW_STATUSES = (C.NEW_SIMPLE, C.NEW_TWIN)


@dataclass
class ClassChange:
    name: str
    group: int | None
    count: int = 0
    added: list = field(default_factory=list)
    renamed: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    now_composite: list = field(default_factory=list)


@dataclass
class HprjResult:
    source: Path
    output: Path
    classes: list = field(default_factory=list)
    unassigned: list = field(default_factory=list)   # (glyph, group) new glyphs whose group has no class
    files: list = field(default_factory=list)        # (field, old, new)


@dataclass
class StemRange:
    cls: str
    min: int
    max: int
    x: int | None     # thickest of the measured straight / round vertical strokes (X widths)
    y: int | None     # the same for horizontal strokes (Y widths); both None: class not measured

    @property
    def outside(self) -> bool:
        return any(v is not None and not self.min <= v <= self.max for v in (self.x, self.y))


@dataclass
class StaticHprj:
    source: Path
    output: Path
    values: list = field(default_factory=list)   # (class, element, cvt, old, new) for changed values
    stems: list = field(default_factory=list)    # StemRange per class with a stem range
    files: list = field(default_factory=list)    # (field, old, new)


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:   # keep CRLF/CR as written by the hinter
        return f.read()


def _write(path: Path, text: str):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _set_files(text: str, new_ttf: str, files: list) -> str:
    """Point TTFFileName at `new_ttf`, and XMLFileName along with it if it followed the font's name."""
    m = TTF_RE.search(text)
    if not m:
        return text
    old_ttf = m.group(2)
    if old_ttf != new_ttf:
        text = text[:m.start(2)] + new_ttf + text[m.end(2):]
        files.append(("TTFFileName", old_ttf, new_ttf))
    mx = XML_RE.search(text)
    if mx and mx.group(2) == Path(old_ttf).stem + ".xml":
        new_xml = Path(new_ttf).stem + ".xml"
        if new_xml != mx.group(2):
            files.append(("XMLFileName", mx.group(2), new_xml))
            text = text[:mx.start(2)] + new_xml + text[mx.end(2):]
    return text


# measured stroke CVTs of measure.measure by the height band of its reference glyphs:
# (X straight, X round), (Y straight, Y round)
STROKES = {2: ((68, 70), (72, 74)), 6: ((81, 83), (85, 87)), 4: ((97, 99), (101, 103))}


def for_static(src_hprj: Path, out_path: Path, font, ttf_name: str) -> StaticHprj:
    """The VF's project for a static cut from it: TTFFileName set to `ttf_name`, every `cvt`/`value`
    pair set to the static's CVT (the VF's own values, or its cvar applied at the instance), and the
    class stem ranges checked against the strokes measured on the static.

    A class is measured if it holds the reference glyphs of `measure` (H I L…, n m h u…, 1…), by
    those glyphs; others (superiors, parens, accents) are not, as their diagonals and odd shapes
    give no reliable stroke width."""
    from . import measure

    text = _read(src_hprj)
    out = StaticHprj(src_hprj, out_path)
    cvt = list(font["cvt "].values) if "cvt " in font else []
    cmap = font.getBestCmap()
    measured = measure.measure(font)
    refs = {band: {cmap[ord(c)] for c in measure.STEMS[xs[0]][1] if ord(c) in cmap}
            for band, (xs, _) in STROKES.items()}

    def thickest(cvts):
        vals = [measured[c].value for c in cvts if measured[c].value]
        return max(vals) if vals else None
    pieces, last = [], 0
    for m in CLASS_RE.finditer(text):
        cls, body = m.group("name"), m.group("body")

        def value(v, cls=cls):
            n, old = int(v.group(3)), int(v.group(4))
            if n >= len(cvt) or cvt[n] == old:
                return v.group(0)
            out.values.append((cls, v.group(2), n, old, cvt[n]))
            return f"{v.group(1)}{cvt[n]}{v.group(5)}"

        pieces += [text[last:m.start("body")], VALUE_RE.sub(value, body)]
        last = m.end("body")
        st, nm = STEM_RE.search(body), NAMES_RE.search(body)
        if st and nm:
            names = set(nm.group(2).split())
            band = max(refs, key=lambda b: len(refs[b] & names))
            x = y = None
            if refs[band] & names:
                x, y = (thickest(c) for c in STROKES[band])
            out.stems.append(StemRange(cls, int(st.group(1)), int(st.group(2)), x, y))
    text = _set_files("".join(pieces) + text[last:], ttf_name, out.files)
    _write(out_path, text)
    return out


def find_for(source_font: Path) -> Path | None:
    stem = source_font.stem
    for cand in (stem[:-4] if stem.endswith(".VTT") else None, stem):
        if cand:
            p = source_font.with_name(cand + ".hprj")
            if p.is_file():
                return p
    return None


def update(res: C.TransferResult, src_hprj: Path, out_path: Path) -> HprjResult:
    text = _read(src_hprj)
    out = HprjResult(src_hprj, out_path)
    s_groups = res.src_font["TSI5"].glyphGrouping if "TSI5" in res.src_font else {}
    target_names = res.glyphs  # ordered like the target glyph order

    classes = []
    for m in CLASS_RE.finditer(text):
        nm = NAMES_RE.search(m.group("body"))
        if not nm:
            continue
        names = nm.group(2).split()
        votes = Counter(s_groups[n] for n in names if n in s_groups)
        group = votes.most_common(1)[0][0] if votes else None
        start = m.start("body") + nm.start(2)
        end = m.start("body") + nm.end(2)
        classes.append((m.group("name"), names, group, start, end, nm.group(2).endswith(" ")))

    group_cls: dict = {}
    for name, _, group, *_ in classes:
        if group is not None and group != 0:
            group_cls.setdefault(group, name)

    lists: dict = {}
    for name, names, group, *_ in classes:
        ch = ClassChange(name, group)
        new = []
        for n in names:
            if n in target_names:
                new.append(n)
                r = target_names[n]
                if r.kind == "composite" and r.status == C.TYPE_CHANGED:
                    ch.now_composite.append(n)
            elif n in res.renames:
                new.append(res.renames[n])
                ch.renamed.append((n, res.renames[n]))
            else:
                ch.removed.append(n)
        lists[name] = new
        out.classes.append(ch)
    by_name = {c.name: c for c in out.classes}

    listed = {n for v in lists.values() for n in v}
    for tname, r in target_names.items():
        is_new = r.status in NEW_STATUSES or (r.status == C.TYPE_CHANGED and r.kind == "simple")
        if not is_new or tname in listed:
            continue
        cls = group_cls.get(r.group)
        if cls:
            lists[cls].append(tname)
            by_name[cls].added.append(tname)
        elif r.group:
            out.unassigned.append((tname, r.group))

    # rewrite from the end so offsets stay valid
    for name, _, _, start, end, trailing in sorted(classes, key=lambda c: -c[3]):
        value = " ".join(lists[name]) + (" " if trailing else "")
        text = text[:start] + value + text[end:]
        by_name[name].count = len(lists[name])

    text = _set_files(text, res.target_path.name, out.files)
    _write(out_path, text)
    return out
