"""Cut static instances out of a hinted variable font.

Each named instance becomes a hinted static (`instancer` keeps fpgm, prep and glyph programs and applies
gvar/cvar). When overlaps must be removed, the overlapping instance is kept as the transfer *source*
and an overlap-free, unhinted copy is written as the transfer *target*; `core.transfer()` then carries
the hints over by exact point remapping. See doc/static-instances-from-vf.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.ttLib import TTFont

# tables dropped from the overlap-free target: everything hinting-related, so the transfer starts clean
HINT_TABLES = ("TSI0", "TSI1", "TSI2", "TSI3", "TSI5", "TSIC", "TSIV", "TSIP", "TSID", "TSIJ", "TSIB",
               "cvt ", "fpgm", "prep", "cvar", "hdmx", "LTSH", "VDMX")


class StaticError(Exception):
    pass


@dataclass
class Instance:
    name: str            # file stem: <family>-<Subfamily>
    subfamily: str
    location: dict = field(default_factory=dict)   # axis tag -> user-space value


@dataclass
class Cut:
    instance: Instance
    source: Path           # <name>.overlap.VTT.ttf: the instance with overlaps and VTT sources
    target: Path | None    # <name>.ttf: overlaps removed, hinting stripped (None if overlaps are kept)


def family_name(vf_path: Path) -> str:
    return Path(vf_path).name.split(".")[0].removesuffix("_vf").removesuffix("-vf")


def named_instances(vf: TTFont, family: str) -> list[Instance]:
    if "fvar" not in vf:
        return []
    out = []
    for inst in vf["fvar"].instances:
        sub = vf["name"].getDebugName(inst.subfamilyNameID) or f"Instance{len(out) + 1}"
        # an italic VF's file name says italic, and so does each subfamily: say it once
        fam = re.sub(r"[_-]italic$", "", family, flags=re.I) if "italic" in sub.lower() else family
        out.append(Instance(f"{fam}-{re.sub(r'[^A-Za-z0-9]+', '', sub)}", sub, dict(inst.coordinates)))
    return out


def restore_implied_oncurves(source: TTFont, target: TTFont) -> dict[str, int]:
    """Put back on-curve points that overlap removal turned into implied ones.

    skia-pathops merges two quadratic segments into one qCurveTo when their shared on-curve point is
    exactly the midpoint of the neighbouring off-curves, so the target loses a point the hinted source
    has (often an extremum, depending on how the instance rounds). For every glyph the removal rebuilt,
    an on-curve point is inserted between two consecutive off-curves whose midpoint is an on-curve
    point of the source glyph. The outline is unchanged; exact point remapping finds the point again.
    Implied points the source did not have stay implied. `source` needs integer coordinates (a saved
    font, not an instancer result still in memory). Returns {glyph: points inserted}."""
    sg, tg = source["glyf"], target["glyf"]
    out = {}
    for name in target.getGlyphOrder():
        g = tg[name]
        if g.isComposite() or g.numberOfContours <= 0 or name not in sg:
            continue
        s_coords, s_ends, s_flags = sg[name].getCoordinates(sg)
        coords, ends, flags = list(g.coordinates), list(g.endPtsOfContours), list(g.flags)
        if s_ends == ends and list(s_coords) == coords:
            continue   # not rebuilt
        on = {tuple(c) for c, f in zip(s_coords, s_flags) if f & 1}
        new_c, new_f, new_e = [], [], []
        start = added = 0
        for end in ends:
            cc, ff = coords[start:end + 1], flags[start:end + 1]
            n = len(cc)
            for i in range(n):
                new_c.append(cc[i])
                new_f.append(ff[i])
                j = (i + 1) % n
                if n > 1 and not ff[i] & 1 and not ff[j] & 1:
                    sx, sy = cc[i][0] + cc[j][0], cc[i][1] + cc[j][1]
                    if sx % 2 == 0 and sy % 2 == 0 and (sx // 2, sy // 2) in on:
                        new_c.append((sx // 2, sy // 2))
                        new_f.append(1)
                        added += 1
            new_e.append(len(new_c) - 1)
            start = end + 1
        if added:
            from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates
            g.coordinates = GlyphCoordinates(new_c)
            g.flags = bytearray(new_f)
            g.endPtsOfContours = new_e
            out[name] = added
    return out


def instance_control_program(font: TTFont, default_cvt) -> list[int]:
    """Write the instanced CVT into the Control Program text of `font` (an instancer result).

    instancer applies cvar to the binary `cvt `, but the text still has the default master's values,
    and every compile (the transfer's, VTT's "Compile everything") rebuilds `cvt ` from the text. Each
    CVT whose value the instancer changed by d gets its written value shifted by d. A relative (`~`)
    CVT is no exception: `cvt ` stores the value as written and cvar varies that, the parent link is
    applied by the prep. Returns the CVT numbers edited."""
    from . import control

    tsi1 = font["TSI1"]
    text = tsi1.extraPrograms.get("cvt")
    if not text:
        return []
    inst = font["cvt "].values
    d = {n: inst[n] - default_cvt[n] for n in range(min(len(inst), len(default_cvt)))}
    cvts = control.parse(text).cvts
    edited = []
    for n, c in cvts.items():
        shift = d.get(n, 0)
        if shift:
            text = control.set_cvt(text, n, value=c["value"] + shift)
            edited.append(n)
    tsi1.extraPrograms["cvt"] = text
    return edited


def cut(vf_path: Path, instance: Instance, out_dir: Path, *, remove_overlaps: bool = True,
        vf: TTFont | None = None, log=lambda *a: None) -> Cut:
    """Write the instance's transfer source (and, with `remove_overlaps`, its target) into `out_dir`."""
    from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont

    vf = vf or TTFont(vf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{instance.name}: instancing…")
    try:
        src = instantiateVariableFont(vf, instance.location, inplace=False,
                                      overlap=OverlapMode.KEEP_AND_SET_FLAGS, updateFontNames=True)
    except ValueError as e:   # names come from STAT axis values; without them keep the VF's names
        log(f"{instance.name}: name table not updated ({e})")
        src = instantiateVariableFont(vf, instance.location, inplace=False,
                                      overlap=OverlapMode.KEEP_AND_SET_FLAGS, updateFontNames=False)
    missing = [t for t in ("TSI1", "TSI3") if t not in src]
    if missing:
        raise StaticError(f"{instance.name}: instancer dropped {', '.join(missing)}")
    if "TSIC" in src:   # the cvar source is meaningless without fvar
        del src["TSIC"]
    if "cvar" in vf and "cvt " in src:
        edited = instance_control_program(src, vf["cvt "].values)
        if edited:
            log(f"{instance.name}: {len(edited)} CVTs instanced in the Control Program")
    source = out_dir / f"{instance.name}.overlap.VTT.ttf"
    src.save(source)
    if not remove_overlaps:
        return Cut(instance, source, None)

    from fontTools.ttLib.removeOverlaps import removeOverlaps

    log(f"{instance.name}: removing overlaps…")
    tgt = TTFont(source)
    removeOverlaps(tgt, removeHinting=True, ignoreErrors=True)
    for tag in HINT_TABLES:
        if tag in tgt:
            del tgt[tag]
    glyf = tgt["glyf"]
    for g in glyf.glyphs.values():
        g.expand(glyf)
        g.removeHinting()
    restored = restore_implied_oncurves(TTFont(source), tgt)   # saved: coordinates rounded as in the source
    if restored:
        log(f"{instance.name}: restored {sum(restored.values())} implied on-curve points "
            f"in {len(restored)} glyphs")
    target = out_dir / f"{instance.name}.ttf"
    tgt.save(target)
    return Cut(instance, source, target)
