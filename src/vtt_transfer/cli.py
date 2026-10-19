"""vtt-transfer SOURCE TARGET — carry VTT hinting sources over to a new version of a font."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, setup_console
from . import hprj
from .core import TransferError, default_report_path, transfer
from .i18n import DEFAULT_LANG, LANGS
from .report import build


def summary(res) -> list[str]:
    """Console lines: glyph counts by status, then the warnings and errors of the checks."""
    G = res.glyphs.values()
    count = lambda *s: sum(1 for r in G if r.status in s)
    out = [f"transferred: {count('transferred')}, remapped: {count('remapped')}, "
           f"to re-hint: {count('rehint', 'compile_error')}, "
           f"composites without own instructions: {count('composite_dropped')}, "
           f"type changed: {count('type_changed')}, "
           f"new: {count('new_simple')} simple unhinted / {count('new_twin')} with twin hints / "
           f"{count('new_composite')} composites, "
           f"removed: {len(res.removed)}"]
    for c in res.checks:
        if c.level in ("warn", "error"):
            out.append(f"{'!' if c.level == 'error' else '~'} {c.title}: {c.detail}")
    return out


def main(argv=None) -> int:
    setup_console()
    p = argparse.ArgumentParser(
        prog="vtt-transfer",
        description="Transfers VTT hinting sources (TSI0–TSI5, cvt, fpgm, prep, gasp, cvar/TSIC) from a hinted "
                    "version of a TrueType font to a new one and writes an HTML report of what is left to hint.",
    )
    p.add_argument("source", type=Path, help="old version with VTT sources (*.VTT.ttf)")
    p.add_argument("target", type=Path, help="new version of the font (unhinted TTF/VF)")
    p.add_argument("-o", "--output", type=Path,
                   help="output path (default: <target>.VTT.ttf next to target)")
    p.add_argument("--report", type=Path, help="HTML report path (default: next to the output)")
    p.add_argument("--report-lang", choices=LANGS, default=DEFAULT_LANG,
                   help=f"report language (default: {DEFAULT_LANG})")
    p.add_argument("--no-compile", action="store_true", help="do not compile with vttcompilepy")
    p.add_argument("--no-remap", action="store_true",
                   help="do not rewrite point numbers in glyphs whose structure changed")
    p.add_argument("--hprj", type=Path,
                   help="VTThinter project of the source (default: <source without .VTT>.hprj next to source)")
    p.add_argument("--no-hprj", action="store_true", help="do not update the .hprj")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)

    for path in (a.source, a.target):
        if not path.is_file():
            p.error(f"file not found: {path}")
    log = (lambda *m: None) if a.quiet else (lambda *m: print("·", *m, file=sys.stderr))

    try:
        res = transfer(a.source, a.target, output=a.output, remap=not a.no_remap,
                       compile_=not a.no_compile, log=log)
    except TransferError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    base = res.output_path.name.removesuffix(".VTT.ttf").removesuffix(".ttf")
    report_path = a.report or default_report_path(res.output_path)
    res.hprj = None
    if not a.no_hprj:
        src_h = a.hprj or hprj.find_for(a.source)
        if a.hprj and not a.hprj.is_file():
            p.error(f"file not found: {a.hprj}")
        if src_h:
            out_h = res.output_path.with_name(base + ".hprj")
            if out_h.resolve() == Path(src_h).resolve():
                out_h = out_h.with_name(base + ".updated.hprj")
            log("updating .hprj…")
            res.hprj = hprj.update(res, Path(src_h), out_h)
    log("report…")
    report_path.write_text(build(res, a.report_lang), encoding="utf-8")

    errors = [c for c in res.checks if c.level == "error"]
    print(f"{res.output_path}")
    print(f"{report_path}")
    if res.hprj:
        added = sum(len(c.added) for c in res.hprj.classes)
        print(f"{res.hprj.output}")
        print(f"  .hprj: added to classes {added}, without a class {len(res.hprj.unassigned)}")
    for line in summary(res):
        print(f"  {line}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
