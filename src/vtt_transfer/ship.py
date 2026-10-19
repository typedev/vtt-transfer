"""vtt-ship FONT.VTT.ttf … — compile VTT sources and write release TTFs without them."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

from . import __version__, setup_console
from .core import METRIC_CACHE_TABLES, program_bytes

SOURCE_TABLES = ("TSI0", "TSI1", "TSI2", "TSI3", "TSI5", "TSIC", "TSIV", "TSID", "TSIJ", "TSIP", "TSIS", "TSIB")
HINT_TABLES = {"glyf", "loca", "maxp", "head", "hmtx", "gasp", "cvt ", "fpgm", "prep", "cvar"}


def strip_vtt(name: str) -> str:
    stem = name[:-4] if name.lower().endswith(".ttf") else name
    return (stem[:-4] if stem.endswith(".VTT") else stem) + ".ttf"


def component_key(c):
    return (c.glyphName, getattr(c, "x", None), getattr(c, "y", None),
            tuple(round(v, 4) for row in getattr(c, "transform", []) for v in row))


def compare(ref: TTFont, out: TTFont) -> list[str]:
    """Differences that are not hinting: outlines, composites, other tables."""
    issues = []
    if ref.getGlyphOrder() != out.getGlyphOrder():
        return ["glyph order/set differs"]
    bad = []
    for n in ref.getGlyphOrder():
        a, b = ref["glyf"][n], out["glyf"][n]
        if a.isComposite() != b.isComposite():
            bad.append(f"{n} (type)")
        elif a.isComposite():
            if [component_key(c) for c in a.components] != [component_key(c) for c in b.components]:
                bad.append(f"{n} (components/offsets)")
        elif a.numberOfContours > 0:
            if list(a.coordinates) != list(b.coordinates) or list(a.endPtsOfContours) != list(b.endPtsOfContours):
                bad.append(f"{n} (outline)")
    if bad:
        issues.append(f"glyphs differ ({len(bad)}): " + ", ".join(bad[:15]) + (" …" if len(bad) > 15 else ""))
    tables = []
    for t in ref.keys():
        if t == "GlyphOrder" or t in HINT_TABLES or t in SOURCE_TABLES or t in METRIC_CACHE_TABLES:
            continue
        if t not in out:
            tables.append(f"{t} (missing)")
            continue
        try:
            if ref[t].compile(ref) != out[t].compile(out):
                tables.append(t)
        except Exception as e:  # pragma: no cover - diagnostic only
            tables.append(f"{t} ({e})")
    if tables:
        issues.append("tables differ: " + ", ".join(tables))
    return issues


def ship_one(src: Path, out_path: Path, reference: Path | None, woff2: bool, log) -> bool:
    import vttcompilepy as vtt

    log(f"{src.name}: compiling…")
    try:
        c = vtt.Compiler.from_file(src)
        c.compile_all()
    except vtt.CompileError as e:
        msg = e.args[0] if e.args else str(e)
        msg = (msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)).strip()
        print(f"✗ {src}: compile error — {msg}", file=sys.stderr)
        return False
    font = c.get_ttfont(vtt.StripLevel.STRIP_SOURCE)

    leftovers = [t for t in SOURCE_TABLES if t in font]
    for t in leftovers:
        del font[t]
    missing = [t for t in ("cvt ", "fpgm", "prep") if t not in font]
    order = font.getGlyphOrder()
    sizes = [len(program_bytes(font["glyf"][n])) for n in order]
    hinted = sum(1 for s in sizes if s)
    font["maxp"].maxSizeOfInstructions = max([font["maxp"].maxSizeOfInstructions] + sizes)

    ok = True
    notes = []
    if missing:
        notes.append("no " + ", ".join(t.strip() for t in missing) + " tables — the font is not hinted")
        ok = False
    if "gasp" not in font:
        notes.append("no gasp")
    if reference is not None:
        issues = compare(TTFont(reference), font)
        if issues:
            ok = False
            notes += [f"does not match {reference.name}: {i}" for i in issues]
        else:
            notes.append(f"outlines, composites and other tables match {reference.name}")

    if not ok:
        print(f"✗ {src}: " + "; ".join(notes), file=sys.stderr)
        print("  file not written (use --force to write it anyway)", file=sys.stderr)
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    font.save(out_path)
    print(f"✓ {out_path}")
    print(f"  bytecode in {hinted} of {len(order)} glyphs"
          + (f"; also stripped {', '.join(leftovers)}" if leftovers else ""))
    for n in notes:
        print(f"  {n}")
    if woff2:
        font.flavor = "woff2"
        w = out_path.with_suffix(".woff2")
        font.save(w)
        print(f"  {w}")
    return True


def main(argv=None) -> int:
    setup_console()
    p = argparse.ArgumentParser(
        prog="vtt-ship",
        description="Compiles VTT sources and writes release TTFs without the TSI* tables (hints are kept).",
    )
    p.add_argument("fonts", nargs="+", type=Path, help="fonts with VTT sources (*.VTT.ttf)")
    p.add_argument("-o", "--output-dir", type=Path,
                   help="output directory (default: hinted/ next to each font)")
    p.add_argument("--reference", type=Path, action="append",
                   help="unhinted build to check outlines against (default: <name without .VTT>.ttf next to the font, "
                        "if present); repeat once per font, in the same order")
    p.add_argument("--no-check", action="store_true", help="do not check against the unhinted build")
    p.add_argument("--force", action="store_true", help="write even if the check found differences")
    p.add_argument("--woff2", action="store_true", help="also write .woff2")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)
    log = (lambda *m: None) if a.quiet else (lambda *m: print("·", *m, file=sys.stderr))
    if a.reference and len(a.reference) != len(a.fonts):
        p.error("--reference must be given for every font")

    failed = 0
    for i, src in enumerate(a.fonts):
        if not src.is_file():
            print(f"✗ file not found: {src}", file=sys.stderr)
            failed += 1
            continue
        name = strip_vtt(src.name)
        out_dir = a.output_dir or src.parent / "hinted"
        out_path = out_dir / name
        if out_path.resolve() == src.resolve():
            p.error(f"the output would overwrite the source: {out_path}")
        ref = None
        if not a.no_check:
            ref = a.reference[i] if a.reference else src.with_name(name)
            if not ref.is_file() or ref.resolve() == src.resolve():
                if a.reference:
                    p.error(f"file not found: {ref}")
                log(f"{src.name}: no unhinted build {ref.name} next to it — check skipped")
                ref = None
        if a.force and ref is not None:
            ok = ship_one(src, out_path, None, a.woff2, log)
            issues = compare(TTFont(ref), TTFont(out_path)) if ok else []
            for iss in issues:
                print(f"  ! does not match {ref.name}: {iss}")
        else:
            ok = ship_one(src, out_path, ref, a.woff2, log)
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
