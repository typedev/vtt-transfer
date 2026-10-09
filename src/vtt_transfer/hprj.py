"""Update a VTThinter project file (.hprj) to a new version of the font.

The file is edited textually so that its formatting stays as the hinter wrote it:
only the <names array="..."> lists, TTFFileName and XMLFileName change.
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


def find_for(source_font: Path) -> Path | None:
    stem = source_font.stem
    for cand in (stem[:-4] if stem.endswith(".VTT") else None, stem):
        if cand:
            p = source_font.with_name(cand + ".hprj")
            if p.is_file():
                return p
    return None


def update(res: C.TransferResult, src_hprj: Path, out_path: Path) -> HprjResult:
    with open(src_hprj, encoding="utf-8", newline="") as f:   # keep CRLF/CR as written by the hinter
        text = f.read()
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

    m = TTF_RE.search(text)
    if m:
        old_ttf = m.group(2)
        new_ttf = res.target_path.name
        if old_ttf != new_ttf:
            text = text[:m.start(2)] + new_ttf + text[m.end(2):]
            out.files.append(("TTFFileName", old_ttf, new_ttf))
        mx = XML_RE.search(text)
        if mx and mx.group(2) == Path(old_ttf).stem + ".xml":
            new_xml = Path(new_ttf).stem + ".xml"
            if new_xml != mx.group(2):
                out.files.append(("XMLFileName", mx.group(2), new_xml))
                text = text[:mx.start(2)] + new_xml + text[mx.end(2):]

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return out
