"""vtt-var2static VF.VTT.ttf — cut hinted statics out of a hinted variable font.

For every named instance (see vtt_transfer.static and doc/static-instances-from-vf.md) writes into
<out>/<name>/:
  <name>.overlap.VTT.ttf             the instance with overlaps and VTT sources (transfer source)
  <name>.ttf                         overlaps removed, hinting stripped (transfer target)
  <name>.VTT.ttf                     the target with the hints transferred and compiled
  <name>.vtt-transfer-report.html    the transfer report
  <name>.hprj                        the VF's VTThinter project for the static (if the VF has one)
With --keep-overlaps the instance itself is <name>.VTT.ttf and nothing is transferred. --ship writes
release fonts (vtt-ship) into <out>/hinted/.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

from . import __version__, hprj, setup_console, static
from .cli import summary
from .core import TransferError, default_report_path, transfer
from .i18n import DEFAULT_LANG, LANGS
from .report import build
from .ship import ship_one


def _key(s: str) -> str:
    """Instance names compare without case, spaces or punctuation: "Condensed Light" = condensedlight."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def static_hprj(src_hprj: Path, hinted: Path, ttf_name: str):
    """Write <name>.hprj next to the static and print what changed and which stem ranges miss."""
    out = hinted.with_name(hinted.name.removesuffix(".VTT.ttf") + ".hprj")
    h = hprj.for_static(src_hprj, out, TTFont(hinted), ttf_name)
    print(f"  {out}")
    if h.values:
        print("  hprj values from the static's CVT: " + ", ".join(
            f"{cls} {el} {old} → {new}" for cls, el, _, old, new in h.values))
    for s in h.stems:
        if s.outside:
            print(f"  ~ hprj: {s.cls} strokes X {s.x}, Y {s.y} outside stem {s.min}–{s.max}")


def main(argv=None) -> int:
    setup_console()
    p = argparse.ArgumentParser(
        prog="vtt-var2static",
        description="Cuts the named instances of a hinted variable font into hinted statics: overlaps "
                    "removed and the hints transferred onto the new outlines, with a report per static.",
    )
    p.add_argument("font", type=Path, help="hinted variable font with VTT sources (*.VTT.ttf)")
    p.add_argument("-o", "--output-dir", type=Path, help="output directory (default: statics/ next to the font)")
    p.add_argument("-i", "--instance", action="append", metavar="NAME",
                   help="only this instance: subfamily or file name, without regard to case and spaces (repeatable)")
    p.add_argument("--keep-overlaps", action="store_true",
                   help="do not remove overlaps: the instances keep their hints as they are, nothing is transferred")
    p.add_argument("--ship", action="store_true", help="also write release fonts without VTT sources into <out>/hinted/")
    p.add_argument("--woff2", action="store_true", help="with --ship: also write .woff2")
    p.add_argument("--hprj", type=Path,
                   help="VTThinter project of the VF (default: <font without .VTT>.hprj next to it)")
    p.add_argument("--no-hprj", action="store_true", help="do not write a .hprj per static")
    p.add_argument("--report-lang", choices=LANGS, default=DEFAULT_LANG,
                   help=f"report language (default: {DEFAULT_LANG})")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)
    if not a.font.is_file():
        p.error(f"file not found: {a.font}")
    if a.woff2 and not a.ship:
        p.error("--woff2 needs --ship")
    if a.hprj and not a.hprj.is_file():
        p.error(f"file not found: {a.hprj}")
    log = (lambda *m: None) if a.quiet else (lambda *m: print("·", *m, file=sys.stderr))

    vf = TTFont(a.font)
    if "TSI1" not in vf or "TSI3" not in vf:
        print(f"error: {a.font.name} has no VTT sources (TSI1/TSI3)", file=sys.stderr)
        return 2
    instances = static.named_instances(vf, static.family_name(a.font))
    if not instances:
        print(f"error: {a.font.name} has no named instances (fvar)", file=sys.stderr)
        return 2
    if a.instance:
        keys = {i.name: {_key(i.name), _key(i.subfamily)} for i in instances}
        unknown = [w for w in a.instance if not any(_key(w) in k for k in keys.values())]
        if unknown:
            p.error(f"no such instance: {', '.join(unknown)}; "
                    f"available: {', '.join(i.subfamily for i in instances)}")
        want = {_key(w) for w in a.instance}
        instances = [i for i in instances if keys[i.name] & want]

    out_dir = a.output_dir or a.font.parent / "statics"
    src_hprj = None if a.no_hprj else a.hprj or hprj.find_for(a.font)
    failed = 0
    for inst in instances:
        print(f"{inst.name}  {inst.location}")
        try:
            cut = static.cut(a.font, inst, out_dir / inst.name, remove_overlaps=not a.keep_overlaps, vf=vf, log=log)
        except static.StaticError as e:
            print(f"  ! {e}", file=sys.stderr)
            failed += 1
            continue
        hinted = cut.source.with_name(f"{inst.name}.VTT.ttf")
        if cut.target is None:
            cut.source.replace(hinted)
            print(f"  {hinted}")
            ok = True
        else:
            try:
                res = transfer(cut.source, cut.target, output=hinted, log=log)
            except TransferError as e:
                print(f"  ! {e}", file=sys.stderr)
                failed += 1
                continue
            report_path = default_report_path(res.output_path)
            log(f"{inst.name}: report…")
            report_path.write_text(build(res, a.report_lang), encoding="utf-8")
            print(f"  {res.output_path}")
            print(f"  {report_path}")
            for line in summary(res):
                print(f"  {line}")
            ok = not any(c.level == "error" for c in res.checks)
        if src_hprj:
            static_hprj(src_hprj, hinted, (cut.target or hinted).name)
        if ok and a.ship:
            ok = ship_one(hinted, out_dir / "hinted" / f"{inst.name}.ttf", cut.target, a.woff2, log)
        failed += 0 if ok else 1
    if failed:
        print(f"{failed} of {len(instances)} instances failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
