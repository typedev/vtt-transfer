"""Transfer of VTT hinting sources from one version of a TrueType font to another."""

from __future__ import annotations

import copy
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from fontTools import agl
from fontTools.ttLib import TTFont, newTable

from . import talk as vtalk
from .i18n import M, Msg

VOLT_TABLES = ("TSIV", "TSID", "TSIJ", "TSIP", "TSIS", "TSIB")
METRIC_CACHE_TABLES = ("hdmx", "LTSH", "VDMX")
MAXP_HINT_FIELDS = (
    "maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs",
    "maxInstructionDefs", "maxStackElements", "maxSizeOfInstructions",
)

FLAG_ROUND = 0x0004
FLAG_USE_MY_METRICS = 0x0200

COMP_PREFIX_RE = re.compile(
    r"^\s*(OVERLAP|NONOVERLAP|USEMYMETRICS|UNSCALEDCOMPONENTOFFSET|SCALEDCOMPONENTOFFSET)\[\]\s*$"
)
COMP_LINE_RE = re.compile(r"^\s*(S?OFFSET|ANCHOR)\[([^\]]*)\]\s*,\s*(.*?)\s*$")

# glyph statuses
TRANSFERRED = "transferred"          # same structure, hints carried over
UNHINTED_SAME = "unhinted_same"      # same structure, nothing to carry (no hints in source)
REMAPPED = "remapped"                # structure changed, point numbers rewritten
REHINT = "rehint"                    # structure changed, hints dropped
CHANGED_UNHINTED = "changed_unhinted"  # structure changed, but there were no hints anyway
COMPOSITE_DROPPED = "composite_dropped"  # composite re-linked, its own instructions dropped
TYPE_CHANGED = "type_changed"        # simple <-> composite
NEW_SIMPLE = "new_simple"
NEW_TWIN = "new_twin"                # new glyph, hints copied from an identical source glyph
NEW_COMPOSITE = "new_composite"
NEW_EMPTY = "new_empty"
REMOVED = "removed"
COMPILE_ERROR = "compile_error"


class TransferError(Exception):
    pass


@dataclass
class GlyphInfo:
    name: str
    kind: str                      # simple / composite / empty
    ends: tuple = ()
    oncurve: tuple = ()
    coords: list = field(default_factory=list)
    components: list = field(default_factory=list)   # component glyph names
    unicodes: list = field(default_factory=list)

    @property
    def structure(self):
        return (self.kind, self.ends, self.oncurve, len(self.components))

    @property
    def npoints(self):
        return len(self.oncurve)


@dataclass
class GlyphResult:
    name: str                      # target name (source name for removed glyphs)
    status: str
    kind: str = ""
    source: str | None = None
    gid: int | None = None
    hinted_in_source: bool = False
    geometry_changed: bool = False
    notes: list = field(default_factory=list)          # Msg
    lost_points: list = field(default_factory=list)   # dicts: index, xy, nearest, nearest_xy
    mapping: dict = field(default_factory=dict)
    old_talk: str = ""
    old_asm: str = ""
    group: int | None = None
    group_reason: Msg | str = ""
    unhinted_components: list = field(default_factory=list)
    used_in: list = field(default_factory=list)


@dataclass
class Check:
    level: str   # ok / info / warn / error
    title: Msg
    detail: Msg | str = ""


@dataclass
class TransferResult:
    source_path: Path
    target_path: Path
    output_path: Path | None = None
    glyphs: dict = field(default_factory=dict)        # target name -> GlyphResult
    removed: list = field(default_factory=list)       # GlyphResult
    renames: dict = field(default_factory=dict)       # source -> target
    checks: list = field(default_factory=list)
    actions: list = field(default_factory=list)       # Msg: technical log of what was copied
    groups: dict = field(default_factory=dict)        # group id -> example source glyph names
    compile_log: list = field(default_factory=list)
    compiled: bool = False
    composite_style: str = ""
    twins: dict = field(default_factory=dict)        # new target glyph -> source twin
    still_unhinted_simple: list = field(default_factory=list)
    src_font: TTFont | None = None
    out_font: TTFont | None = None

    def by_status(self, *statuses):
        return [g for g in self.glyphs.values() if g.status in statuses]


# ---------------------------------------------------------------- helpers

def glyph_infos(font: TTFont) -> dict[str, GlyphInfo]:
    glyf = font["glyf"]
    rev = {}
    for cp, name in font.getBestCmap().items():
        rev.setdefault(name, []).append(cp)
    cache: dict[str, tuple] = {}

    def flat(name, depth=0):
        if name in cache:
            return cache[name]
        g = glyf[name]
        if g.isComposite() and depth < 16:
            ends, flags, base = [], [], 0
            for c in g.components:
                e, f = flat(c.glyphName, depth + 1)
                ends += [x + base for x in e]
                flags += f
                base += len(f)
            r = (tuple(ends), tuple(flags))
        elif g.numberOfContours > 0:
            r = (tuple(g.endPtsOfContours), tuple(int(x) & 1 for x in g.flags))
        else:
            r = ((), ())
        cache[name] = r
        return r

    infos = {}
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.isComposite():
            kind = "composite"
        elif g.numberOfContours > 0:
            kind = "simple"
        else:
            kind = "empty"
        ends, oncurve = flat(name)
        coords = []
        if kind != "empty":
            c, _, _ = g.getCoordinates(glyf)
            coords = [(int(round(x)), int(round(y))) for x, y in c]
        comps = [c.glyphName for c in g.components] if kind == "composite" else []
        infos[name] = GlyphInfo(name, kind, ends, oncurve, coords, comps, sorted(rev.get(name, [])))
    return infos


def codepoint_of(info: GlyphInfo) -> int | None:
    if info.unicodes:
        return info.unicodes[0]
    base = info.name.split(".")[0]
    u = agl.toUnicode(base)
    if u and len(u) == 1:
        return ord(u)
    return None


def role_of(info: GlyphInfo) -> str | None:
    base = info.name.split(".")[0].lower()
    if base.endswith(("cmb", "comb")) or "cmb_" in base or "comb_" in base:
        return "Mn"
    cp = codepoint_of(info)
    if cp is None:
        return None
    ch = chr(cp)
    uname = unicodedata.name(ch, "")
    if any(w in uname for w in ("SUPERSCRIPT", "SUBSCRIPT", "SUPERIOR", "INFERIOR")):
        return "sup"
    cat = unicodedata.category(ch)
    if cat in ("Ps", "Pe"):
        return "paren"
    if cat == "Lt":
        return "Lu"
    return cat


def char_label(info: GlyphInfo) -> str:
    cp = codepoint_of(info)
    return f"U+{cp:04X}" if cp is not None else ""


def program_bytes(glyph) -> bytes:
    if hasattr(glyph, "program") and glyph.program is not None:
        try:
            return glyph.program.getBytecode()
        except Exception:
            return b"?"
    return b""


def split_lines(text: str):
    if "\r\n" in text:
        sep = "\r\n"
    elif "\n" in text and "\r" not in text:
        sep = "\n"
    else:
        sep = "\r"
    return re.split(r"\r\n|\r|\n", text), sep


@dataclass
class CompBlock:
    prefixes: list
    op: str
    flag: str
    args: list


def parse_composite_asm(text: str):
    """Split VTT composite assembly into (header lines, component blocks, body lines, separator)."""
    lines, sep = split_lines(text or "")
    header, blocks, body, pending = [], [], [], []
    for ln in lines:
        s = ln.strip()
        in_components = not any(b.strip() for b in body)
        if in_components and not blocks and not pending and (s.startswith("/*") or not s):
            if s:
                header.append(ln)
            continue
        if in_components:
            m = COMP_PREFIX_RE.match(ln)
            if m:
                pending.append(m.group(1))
                continue
            m = COMP_LINE_RE.match(ln)
            if m:
                blocks.append(CompBlock(pending, m.group(1), m.group(2), [a.strip() for a in m.group(3).split(",")]))
                pending = []
                continue
            if not s:
                continue
        body.extend(f"{p}[]" for p in pending)
        pending = []
        body.append(ln)
    while body and not body[-1].strip():
        body.pop()
    return header, blocks, body, sep


def lines_have_code(lines) -> bool:
    return vtalk.has_code("\n".join(lines))


def render_component(prefixes, flag, gid, comp) -> list[str]:
    out = [f"{p}[]" for p in prefixes]
    if hasattr(comp, "firstPt"):
        out.append(f"ANCHOR[{flag}], {gid}, {comp.firstPt}, {comp.secondPt}")
    elif hasattr(comp, "transform"):
        t = comp.transform
        out.append(
            f"SOFFSET[{flag}], {gid}, {comp.x}, {comp.y}, "
            f"{t[0][0]:.4f}, {t[0][1]:.4f}, {t[1][0]:.4f}, {t[1][1]:.4f}"
        )
    else:
        out.append(f"OFFSET[{flag}], {gid}, {comp.x}, {comp.y}")
    return out


def regions(font: TTFont) -> set:
    out = set()
    if "gvar" not in font:
        return out
    for variations in font["gvar"].variations.values():
        for v in variations:
            out.add(tuple(sorted((k, tuple(round(x, 4) for x in r)) for k, r in v.axes.items())))
    return out


def axes(font: TTFont):
    if "fvar" not in font:
        return []
    return [(a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in font["fvar"].axes]


def avar_maps(font: TTFont):
    if "avar" not in font:
        return None
    return {k: sorted(v.items()) for k, v in font["avar"].segments.items()}


def source_is_hinted(font, info: GlyphInfo, asm: str, talk_text: str) -> bool:
    prog = program_bytes(font["glyf"][info.name])
    if info.kind == "simple":
        return bool(prog) or vtalk.has_code(talk_text)
    if info.kind == "composite":
        _, _, body, _ = parse_composite_asm(asm)
        return bool(prog) or lines_have_code(body) or vtalk.has_code(talk_text)
    return False


def role_name(role: str) -> Msg | str:
    try:
        return M(f"role_{role}")
    except KeyError:
        return role


def point_mapping(s: GlyphInfo, t: GlyphInfo, refs: set[int]):
    """Map referenced source point numbers to target ones by exact default-master coordinates."""
    n_o, n_n = s.npoints, t.npoints
    idx: dict = {}
    for i, p in enumerate(t.coords):
        idx.setdefault(p, []).append(i)
    mapping, lost = {}, []
    for r in sorted(refs):
        if r >= n_o:
            k = r - n_o
            if k < 4:
                mapping[r] = n_n + k   # phantom points follow the outline
            else:
                lost.append(dict(index=r, xy=None, reason=M("lost_range")))
            continue
        p = s.coords[r]
        cands = [i for i in idx.get(p, []) if t.oncurve[i] == s.oncurve[r]]
        if len(cands) == 1:
            mapping[r] = cands[0]
            continue
        entry = dict(index=r, xy=p, on=bool(s.oncurve[r]))
        if len(cands) > 1:
            entry["reason"] = M("lost_ambiguous", p=p)
        elif t.coords:
            j = min(range(n_n), key=lambda i: (t.coords[i][0] - p[0]) ** 2 + (t.coords[i][1] - p[1]) ** 2)
            entry.update(nearest=j, nearest_xy=t.coords[j],
                         reason=M("lost_missing", p=p, j=j, q=t.coords[j]))
        lost.append(entry)
    if len(set(mapping.values())) != len(mapping):
        lost.append(dict(index=-1, xy=None, reason=M("lost_collision")))
    return mapping, lost


def default_output_path(target: Path) -> Path:
    stem = target.stem
    if stem.endswith(".VTT"):
        stem = stem[:-4] + ".transferred"
    return target.with_name(f"{stem}.VTT.ttf")


def transfer(source, target, *, output=None, remap=True, compile_=True, log=lambda *a: None) -> TransferResult:
    source, target = Path(source), Path(target)
    res = TransferResult(source, target)
    src, tgt = TTFont(source), TTFont(target)
    res.src_font = src

    # ------------------------------------------------------------ requirements
    for f, label in ((src, "source"), (tgt, "target")):
        if "glyf" not in f:
            raise TransferError(f"The {label} font has no glyf table — a TrueType font is required.")
    if "TSI1" not in src or "TSI3" not in src:
        raise TransferError("The source font has no VTT sources (TSI1/TSI3 tables) — nothing to transfer.")
    if src["head"].unitsPerEm != tgt["head"].unitsPerEm:
        raise TransferError(
            f"UPM differs: {src['head'].unitsPerEm} → {tgt['head'].unitsPerEm}. "
            "CVT values and deltas cannot be transferred across a UPM change."
        )
    res.checks.append(Check("ok", M("chk_upm_same"), str(tgt["head"].unitsPerEm)))

    a_s, a_t = axes(src), axes(tgt)
    same_axes = a_s == a_t
    fmt_axes = lambda a: ", ".join(f"{t} {mn:g}–{df:g}–{mx:g}" for t, mn, df, mx in a) or M("axes_none")
    if same_axes:
        res.checks.append(Check("ok", M("chk_axes_same"), fmt_axes(a_t)))
    else:
        res.checks.append(Check("warn", M("chk_axes_differ"),
                                M("chk_axes_differ_detail", old=fmt_axes(a_s), new=fmt_axes(a_t))))
    same_avar = avar_maps(src) == avar_maps(tgt)
    if not same_avar:
        res.checks.append(Check("warn", M("chk_avar_differ"), M("chk_avar_differ_detail")))
    r_s, r_t = regions(src), regions(tgt)
    if r_s == r_t:
        res.checks.append(Check("ok", M("chk_regions_same"), M("chk_regions_count", n=len(r_t))))
    else:
        res.checks.append(Check("warn", M("chk_regions_differ"),
                                M("chk_regions_differ_detail", old=len(r_s), new=len(r_t),
                                  added=len(r_t - r_s), gone=len(r_s - r_t))))

    had_hints = [t for t in ("TSI1", "TSI3", "fpgm", "prep", "cvt ") if t in tgt]
    if had_hints:
        res.checks.append(Check("warn", M("chk_target_hinted"),
                                M("chk_target_hinted_detail", tables=", ".join(had_hints))))

    # ------------------------------------------------------------ glyph analysis
    log("analysing glyphs…")
    si, ti = glyph_infos(src), glyph_infos(tgt)
    s_order, t_order = src.getGlyphOrder(), tgt.getGlyphOrder()
    s_set, t_set = set(s_order), set(t_order)
    t_gid = {n: i for i, n in enumerate(t_order)}

    fresh = [g for g in t_order if g not in s_set]
    fresh_set = set(fresh)
    t_cmap = tgt.getBestCmap()
    used: set = set()
    for g in s_order:
        if g in t_set:
            continue
        cands = []
        for cp in si[g].unicodes:
            n = t_cmap.get(cp)
            if n in fresh_set and n not in used and n not in cands:
                cands.append(n)
        pick = next((n for n in cands if ti[n].structure == si[g].structure), None)
        if pick is None and si[g].kind != "empty":
            same = [n for n in fresh if n not in used and ti[n].structure == si[g].structure
                    and ti[n].coords == si[g].coords]
            if len(same) == 1:
                pick = same[0]
        if pick is None and cands:
            pick = cands[0]
        if pick:
            res.renames[g] = pick
            used.add(pick)
    t2s = {g: g for g in t_order if g in s_set}
    t2s.update({t: s for s, t in res.renames.items()})

    s_asm = src["TSI1"].glyphPrograms
    s_talk = src["TSI3"].glyphPrograms
    s_groups = src["TSI5"].glyphGrouping if "TSI5" in src else {}
    hinted_src = {g: source_is_hinted(src, si[g], s_asm.get(g, ""), s_talk.get(g, "")) for g in s_order}
    twin_index: dict = {}
    for g in s_order:
        if si[g].kind == "simple" and hinted_src[g] and vtalk.has_code(s_talk.get(g, "")):
            twin_index.setdefault(si[g].structure, []).append(g)

    def find_twin(info):
        """Hinted source glyph with the same points, identical or shifted along X only."""
        shifted = None
        for o in twin_index.get(info.structure, []):
            d = {(b[0] - a[0], b[1] - a[1]) for a, b in zip(si[o].coords, info.coords)}
            if len(d) == 1:
                dx, dy = d.pop()
                if dx == 0 and dy == 0:
                    return o, 0
                if dy == 0 and shifted is None:
                    shifted = (o, dx)
        return shifted if shifted else (None, 0)

    # composite style used by VTT in the source font (for newly generated composite code)
    pref_counter, flag_counter, sep = Counter(), Counter(), "\r"
    for g in s_order:
        if si[g].kind == "composite" and s_asm.get(g):
            _, blocks, _, sep = parse_composite_asm(s_asm[g])
            for b in blocks:
                pref_counter[tuple(p for p in b.prefixes if p != "USEMYMETRICS")] += 1
                flag_counter[b.flag] += 1
    style_prefixes = list(pref_counter.most_common(1)[0][0]) if pref_counter else []
    res.composite_style = " ".join(f"{p}[]" for p in style_prefixes) + " OFFSET[…]"

    n_asm: dict = {}
    n_talk: dict = {}
    n_groups: dict = {}

    def composite_asm(tname, blocks=None, header=None, body=None, csep=sep):
        tg = tgt["glyf"][tname]
        notes = []
        lines = [re.sub(r"TT glyph \d+", f"TT glyph {t_gid[tname]}", h) for h in (header or [])]
        if not lines:
            lines = [f"/* TT glyph {t_gid[tname]}, char ({tname}) */"]
        for i, c in enumerate(tg.components):
            if blocks and i < len(blocks):
                prefixes, flag = list(blocks[i].prefixes), blocks[i].flag
            else:
                prefixes = list(style_prefixes)
                flag = "R" if c.flags & FLAG_ROUND else "r"
            want = bool(c.flags & FLAG_USE_MY_METRICS)
            if want != ("USEMYMETRICS" in prefixes):
                prefixes = [p for p in prefixes if p != "USEMYMETRICS"]
                if want:
                    prefixes.insert(0, "USEMYMETRICS")
                if blocks:
                    notes.append(M("note_umm", c=c.glyphName))
            if hasattr(c, "firstPt"):
                notes.append(M("note_anchor", c=c.glyphName))
            elif hasattr(c, "transform"):
                notes.append(M("note_soffset", c=c.glyphName))
            lines += render_component(prefixes, flag, t_gid[c.glyphName], c)
        if body:
            lines += body
        return csep.join(lines), notes

    def set_program(tname, prog):
        g = tgt["glyf"][tname]
        if prog is not None:
            g.program = copy.deepcopy(prog)
        elif g.isComposite():
            if hasattr(g, "program"):
                del g.program
        else:
            from fontTools.ttLib.tables import ttProgram
            g.program = ttProgram.Program()
            g.program.fromBytecode(b"")

    log("transferring…")
    for tname in t_order:
        info, gid = ti[tname], t_gid[tname]
        sname = t2s.get(tname)
        twin, twin_dx = find_twin(info) if sname is None and info.kind == "simple" else (None, 0)
        if twin is not None:
            r = GlyphResult(tname, NEW_TWIN, info.kind, twin, gid, True)
            if twin_dx:
                r.notes.append(M("note_twin_shifted", twin=twin, dx=twin_dx))
            else:
                r.notes.append(M("note_twin_exact", twin=twin))
            if src["hmtx"][twin][0] != tgt["hmtx"][tname][0]:
                r.notes.append(M("note_width", old=src["hmtx"][twin][0], new=tgt["hmtx"][tname][0]))
            r.old_talk = s_talk.get(twin, "")
            talk_twin = re.sub(r"(GlyphID |VTTTalk glyph )\d+", lambda m: f"{m.group(1)}{gid}", s_talk[twin], count=1)
            if twin_dx:
                talk_twin, n_x = vtalk.y_only(talk_twin)
                if n_x:
                    r.notes.append(M("note_x_removed", n=n_x))
            n_talk[tname] = talk_twin
            if s_asm.get(twin) and not twin_dx:
                n_asm[tname] = re.sub(r"TT glyph \d+", f"TT glyph {gid}", s_asm[twin], count=1)
            if twin in s_groups:
                n_groups[tname] = s_groups[twin]
            sg = src["glyf"][twin]
            set_program(tname, sg.program if hasattr(sg, "program") and not twin_dx else None)
            res.twins[tname] = twin
            res.glyphs[tname] = r
            continue
        if sname is None:
            status = {"empty": NEW_EMPTY, "composite": NEW_COMPOSITE}.get(info.kind, NEW_SIMPLE)
            r = GlyphResult(tname, status, info.kind, None, gid)
            if info.kind == "composite":
                n_asm[tname], notes = composite_asm(tname)
                r.notes += notes
            set_program(tname, None)
            res.glyphs[tname] = r
            continue

        s = si[sname]
        r = GlyphResult(tname, "", info.kind, sname, gid, hinted_src[sname])
        r.old_talk = s_talk.get(sname, "")
        if sname != tname:
            r.notes.append(M("note_renamed", old=sname, new=tname))
        if sname in s_groups:
            n_groups[tname] = s_groups[sname]
        talk_src, asm_src = s_talk.get(sname, ""), s_asm.get(sname, "")
        sg = src["glyf"][sname]
        src_prog = sg.program if hasattr(sg, "program") else None
        r.geometry_changed = s.coords != info.coords

        if s.kind != info.kind:
            r.status = TYPE_CHANGED
            r.notes.append(M("note_type", old=M(f"kind_{s.kind}"), new=M(f"kind_{info.kind}")))
            if info.kind == "composite":
                n_asm[tname], notes = composite_asm(tname)
                r.notes += notes
            set_program(tname, None)

        elif info.kind == "empty":
            r.status = UNHINTED_SAME
            set_program(tname, None)

        elif info.kind == "simple":
            if s.structure == info.structure:
                r.status = TRANSFERRED if r.hinted_in_source else UNHINTED_SAME
                if talk_src:
                    n_talk[tname] = re.sub(r"(GlyphID |VTTTalk glyph )\d+", lambda m: f"{m.group(1)}{gid}", talk_src, count=1)
                if asm_src:
                    n_asm[tname] = re.sub(r"TT glyph \d+", f"TT glyph {gid}", asm_src, count=1)
                set_program(tname, src_prog)
            elif not r.hinted_in_source:
                r.status = CHANGED_UNHINTED
                set_program(tname, None)
            else:
                refs, unsupported, _ = vtalk.analyze(talk_src)
                reasons = []
                mapping, lost = point_mapping(s, info, refs)
                if not vtalk.has_code(talk_src):
                    reasons.append(M("note_asm_only"))
                elif unsupported:
                    reasons.append(M("note_unsupported", cmds=", ".join(unsupported[:5])))
                if not remap:
                    reasons.append(M("note_no_remap"))
                if lost:
                    reasons.append(M("note_lost", lost=len(lost), total=len(refs)))
                r.notes.append(M("note_points", a=s.npoints, b=info.npoints, c=len(s.ends), d=len(info.ends)))
                if reasons:
                    r.status = REHINT
                    r.lost_points = lost
                    r.notes += reasons
                    set_program(tname, None)
                else:
                    r.status = REMAPPED
                    r.mapping = {k: v for k, v in mapping.items() if k != v}
                    new_talk = vtalk.remap(talk_src, mapping)
                    n_talk[tname] = re.sub(r"(GlyphID |VTTTalk glyph )\d+", lambda m: f"{m.group(1)}{gid}", new_talk, count=1)
                    set_program(tname, None)

        else:  # composite -> composite
            header, blocks, body, csep = parse_composite_asm(asm_src)
            tg = tgt["glyf"][tname]
            same_struct = s.structure == info.structure
            if len(blocks) != len(tg.components):
                blocks = None
            renamed_c = [f"{a} → {b}" for a, b in zip(s.components, info.components) if a != b]
            if renamed_c:
                r.notes.append(M("note_components", pairs=", ".join(renamed_c)))
            s_off = [(getattr(c, "x", None), getattr(c, "y", None)) for c in sg.components]
            t_off = [(getattr(c, "x", None), getattr(c, "y", None)) for c in tg.components]
            if len(s.components) == len(info.components) and s_off != t_off:
                r.notes.append(M("note_offsets"))
            if len(s.components) != len(info.components):
                r.notes.append(M("note_ncomponents", a=len(s.components), b=len(info.components)))
            keep = same_struct and lines_have_code(body)
            n_asm[tname], notes = composite_asm(tname, blocks, header, body if keep else None, csep)
            r.notes += notes
            if same_struct and vtalk.has_code(talk_src):
                n_talk[tname] = talk_src
            set_program(tname, src_prog if same_struct and src_prog is not None else None)
            if r.hinted_in_source and not same_struct and (lines_have_code(body) or vtalk.has_code(talk_src)):
                r.status = COMPOSITE_DROPPED
                r.old_asm = "\n".join(body)
                changed = [b for a, b in zip(s.components, info.components) if si[a].structure != ti[b].structure]
                r.notes.append(M("note_comp_dropped_in", names=", ".join(changed)) if changed
                               else M("note_comp_dropped"))
            else:
                r.status = TRANSFERRED if r.hinted_in_source else UNHINTED_SAME
        res.glyphs[tname] = r

    mapped_src = set(t2s.values())
    for g in s_order:
        if g not in mapped_src:
            res.removed.append(GlyphResult(g, REMOVED, si[g].kind, g, None, hinted_src[g]))

    # ------------------------------------------------------------ character groups
    role_groups: dict = {}
    comp_groups = Counter()
    for g in s_order:
        grp = s_groups.get(g, 0)
        if si[g].kind == "simple":
            ro = role_of(si[g])
            if ro:
                role_groups.setdefault(ro, Counter())[grp] += 1
        elif si[g].kind == "composite":
            comp_groups[grp] += 1
    for grp in sorted(set(s_groups.values()) | {0}):
        members = [g for g in s_order if s_groups.get(g, 0) == grp and si[g].kind == "simple"]
        members.sort(key=lambda g: (not si[g].unicodes, s_order.index(g)))
        res.groups[grp] = members[:10]

    # combining marks whose group is already known in the result (transferred or copied from a twin)
    mn_known = Counter(n_groups[t] for t in t_order if t in n_groups and ti[t].kind == "simple"
                       and role_of(ti[t]) == "Mn")
    for tname in t_order:
        r = res.glyphs[tname]
        if tname in n_groups:
            r.group = n_groups[tname]
            continue
        info = ti[tname]
        if info.kind == "composite":
            grp = comp_groups.most_common(1)[0][0] if comp_groups else 0
            reason = M("grp_composites")
        elif info.kind == "empty":
            grp, reason = 0, M("grp_empty")
        else:
            base = tname.split(".")[0]
            ro = role_of(info)
            if base != tname and base in n_groups:
                grp, reason = n_groups[base], M("grp_base", base=base)
            elif ro == "Mn" and mn_known:
                grp = mn_known.most_common(1)[0][0]
                reason = M("grp_marks")
            elif ro and role_groups.get(ro):
                grp, n = role_groups[ro].most_common(1)[0]
                reason = M("grp_role", role=role_name(ro))
            elif ro == "Mn" and role_groups.get("Sk"):
                grp, n = role_groups["Sk"].most_common(1)[0]
                reason = M("grp_spacing")
            else:
                grp, reason = 0, M("grp_unknown")
        n_groups[tname] = grp
        r.group, r.group_reason = grp, reason

    # ------------------------------------------------------------ hinted state in the result
    memo: dict = {}

    def t_hinted(n, depth=0):
        if n in memo:
            return memo[n]
        info = ti[n]
        if info.kind == "empty":
            v = True
        elif info.kind == "simple":
            v = vtalk.has_code(n_talk.get(n, "")) or bool(program_bytes(tgt["glyf"][n]))
        else:
            v = depth < 16 and all(t_hinted(c, depth + 1) for c in info.components)
        memo[n] = v
        return v

    def leaves(n, depth=0):
        out = []
        for c in ti[n].components:
            if ti[c].kind == "composite" and depth < 16:
                out += leaves(c, depth + 1)
            else:
                out.append(c)
        return out

    users: dict = {}
    for n in t_order:
        if ti[n].kind == "composite":
            for leaf in dict.fromkeys(leaves(n)):
                users.setdefault(leaf, []).append(n)
    for n in t_order:
        r = res.glyphs[n]
        if ti[n].kind == "composite":
            r.unhinted_components = [c for c in dict.fromkeys(leaves(n)) if not t_hinted(c)]
        r.used_in = users.get(n, [])
    res.still_unhinted_simple = [n for n in t_order if ti[n].kind == "simple" and not t_hinted(n)]

    # ------------------------------------------------------------ global tables
    for tag in ("TSI0", "TSI1", "TSI2", "TSI3", "TSI5", "TSIC", "cvar") + VOLT_TABLES:
        if tag in tgt:
            del tgt[tag]
    for tag in ("cvt ", "fpgm", "prep"):
        if tag in src:
            tgt[tag] = copy.deepcopy(src[tag])
            res.actions.append(M("act_copied", tag=tag.strip()))
        elif tag in tgt:
            del tgt[tag]
    if "gasp" in src:
        old = dict(tgt["gasp"].gaspRange) if "gasp" in tgt else None
        new = dict(src["gasp"].gaspRange)
        tgt["gasp"] = copy.deepcopy(src["gasp"])
        if old is not None and old != new:
            res.checks.append(Check("info", M("chk_gasp_replaced"), M("chk_gasp_replaced_detail", old=old, new=new)))
        res.actions.append(M("act_copied", tag="gasp"))
    if "cvar" in src or "TSIC" in src:
        if same_axes and same_avar:
            for tag in ("cvar", "TSIC"):
                if tag in src:
                    tgt[tag] = copy.deepcopy(src[tag])
                    res.actions.append(M("act_copied_short", tag=tag))
            res.checks.append(Check("ok", M("chk_cvar_ok")))
        else:
            res.checks.append(Check("warn", M("chk_cvar_skipped"), M("chk_cvar_skipped_detail")))
    else:
        res.checks.append(Check("info", M("chk_cvar_none"), M("chk_cvar_none_detail")))
    volt = [t for t in VOLT_TABLES if t in src]
    if volt:
        res.checks.append(Check("info", M("chk_volt"), ", ".join(volt)))
    for tag in METRIC_CACHE_TABLES:
        if tag in tgt:
            del tgt[tag]
            res.checks.append(Check("info", M("chk_metric_removed", tag=tag), M("chk_metric_removed_detail")))
    for fld in MAXP_HINT_FIELDS:
        if hasattr(src["maxp"], fld):
            setattr(tgt["maxp"], fld, max(getattr(src["maxp"], fld), getattr(tgt["maxp"], fld, 0)))
    tgt["head"].flags = (tgt["head"].flags & ~0x18) | (src["head"].flags & 0x18)
    res.actions.append(M("act_maxp"))

    tsi1, tsi3, tsi5 = newTable("TSI1"), newTable("TSI3"), newTable("TSI5")
    tsi1.glyphPrograms, tsi1.extraPrograms = n_asm, dict(src["TSI1"].extraPrograms)
    tsi3.glyphPrograms, tsi3.extraPrograms = n_talk, dict(src["TSI3"].extraPrograms)
    tsi5.glyphGrouping = n_groups
    tgt["TSI0"], tgt["TSI1"], tgt["TSI2"], tgt["TSI3"], tgt["TSI5"] = newTable("TSI0"), tsi1, newTable("TSI2"), tsi3, tsi5
    res.actions.append(M("act_tsi"))

    # ------------------------------------------------------------ compile
    out = tgt
    if compile_:
        try:
            import vttcompilepy as vtt
        except ImportError:
            vtt = None
            res.checks.append(Check("warn", M("chk_no_vttcompilepy"), M("chk_no_vttcompilepy_detail")))
        if vtt is not None:
            log("compiling (vttcompilepy)…")
            stripped: set = set()
            for _ in range(len(t_order) + 1):
                try:
                    c = vtt.Compiler.from_ttfont(tgt)
                    c.compile_all()
                    out = c.get_ttfont(vtt.StripLevel.STRIP_NOTHING)
                    res.compiled = True
                    break
                except vtt.CompileError as e:
                    msg = e.args[0] if e.args else str(e)
                    msg = (msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)).strip()
                    res.compile_log.append(msg)
                    m = re.search(r"glyph (\d+)", msg)
                    if not m or int(m.group(1)) in stripped or int(m.group(1)) >= len(t_order):
                        res.checks.append(Check("error", M("chk_compile_failed"), msg))
                        break
                    gid = int(m.group(1))
                    stripped.add(gid)
                    name = t_order[gid]
                    n_talk[name] = ""
                    if ti[name].kind == "composite":
                        lines, csep = split_lines(n_asm.get(name, ""))
                        n_asm[name] = csep.join(l for l in lines if COMP_PREFIX_RE.match(l)
                                                or COMP_LINE_RE.match(l) or l.strip().startswith("/*"))
                    else:
                        n_asm[name] = ""
                    set_program(name, None)
                    gr = res.glyphs[name]
                    gr.notes.append(M("note_compile_error", msg=msg))
                    gr.status = COMPILE_ERROR
            if res.compiled:
                ok_msg = M("chk_compile_all") if not stripped else M("chk_compile_partial", n=len(stripped))
                res.checks.append(Check("ok" if not stripped else "warn", M("chk_compile_ok"), ok_msg))

    # ------------------------------------------------------------ verification
    log("verifying the result…")
    ref = TTFont(target)
    bad = []
    for n in t_order:
        a, b = ref["glyf"][n], out["glyf"][n]
        if a.isComposite() != b.isComposite():
            bad.append(M("bad_type", n=n))
        elif a.isComposite():
            ca = [(c.glyphName, getattr(c, "x", None), getattr(c, "y", None),
                   tuple(round(v, 4) for row in getattr(c, "transform", []) for v in row)) for c in a.components]
            cb = [(c.glyphName, getattr(c, "x", None), getattr(c, "y", None),
                   tuple(round(v, 4) for row in getattr(c, "transform", []) for v in row)) for c in b.components]
            if ca != cb:
                bad.append(M("bad_components", n=n, a=str(ca), b=str(cb)))
        elif a.numberOfContours > 0:
            if list(a.coordinates) != list(b.coordinates) or list(a.endPtsOfContours) != list(b.endPtsOfContours):
                bad.append(M("bad_outline", n=n))
    if bad:
        res.checks.append(Check("error", M("chk_outlines_differ"),
                                M("chk_outlines_differ_detail", items=bad[:20], more=" …" if len(bad) > 20 else "")))
    else:
        res.checks.append(Check("ok", M("chk_outlines_ok"), M("n_glyphs", n=len(t_order))))
    if res.compiled:
        skip = {"glyf", "loca", "maxp", "head", "gasp", "cvt ", "fpgm", "prep", "cvar"} | set(METRIC_CACHE_TABLES)
        diff = []
        for t in ref.keys():
            if t == "GlyphOrder" or t in skip or t not in out:
                continue
            if ref.reader[t] == out.reader[t]:
                continue
            try:   # re-encoded (e.g. gvar after glyf recompilation) is fine, changed content is not
                if ref[t].compile(ref) == out[t].compile(out):
                    continue
            except Exception:
                pass
            diff.append(t)
        if diff:
            res.checks.append(Check("warn", M("chk_tables_changed"), ", ".join(diff)))
        else:
            res.checks.append(Check("ok", M("chk_tables_ok")))
        lost_prog = [n for n, r in res.glyphs.items() if r.status == TRANSFERRED and ti[n].kind == "simple"
                     and not program_bytes(out["glyf"][n])]
        if lost_prog:
            res.checks.append(Check("warn", M("chk_empty_bytecode"), ", ".join(lost_prog[:30])))
        sizes = [len(program_bytes(out["glyf"][n])) for n in t_order]
        out["maxp"].maxSizeOfInstructions = max([out["maxp"].maxSizeOfInstructions] + sizes)

    res.output_path = Path(output) if output else default_output_path(target)
    out.save(res.output_path)
    res.out_font = TTFont(res.output_path)
    return res
