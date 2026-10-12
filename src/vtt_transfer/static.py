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
        out.append(Instance(f"{family}-{re.sub(r'[^A-Za-z0-9]+', '', sub)}", sub, dict(inst.coordinates)))
    return out


def cut(vf_path: Path, instance: Instance, out_dir: Path, *, remove_overlaps: bool = True,
        vf: TTFont | None = None, log=lambda *a: None) -> Cut:
    """Write the instance's transfer source (and, with `remove_overlaps`, its target) into `out_dir`."""
    from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont

    vf = vf or TTFont(vf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{instance.name}: instancing…")
    src = instantiateVariableFont(vf, instance.location, inplace=False,
                                  overlap=OverlapMode.KEEP_AND_SET_FLAGS, updateFontNames=True)
    missing = [t for t in ("TSI1", "TSI3") if t not in src]
    if missing:
        raise StaticError(f"{instance.name}: instancer dropped {', '.join(missing)}")
    if "TSIC" in src:   # the cvar source is meaningless without fvar
        del src["TSIC"]
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
    target = out_dir / f"{instance.name}.ttf"
    tgt.save(target)
    return Cut(instance, source, target)
