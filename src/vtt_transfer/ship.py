"""vtt-ship FONT.VTT.ttf … — compile VTT sources and write release TTFs without them."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

from . import __version__
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
        return ["порядок/набор глифов отличается"]
    bad = []
    for n in ref.getGlyphOrder():
        a, b = ref["glyf"][n], out["glyf"][n]
        if a.isComposite() != b.isComposite():
            bad.append(f"{n} (тип)")
        elif a.isComposite():
            if [component_key(c) for c in a.components] != [component_key(c) for c in b.components]:
                bad.append(f"{n} (компоненты/смещения)")
        elif a.numberOfContours > 0:
            if list(a.coordinates) != list(b.coordinates) or list(a.endPtsOfContours) != list(b.endPtsOfContours):
                bad.append(f"{n} (контур)")
    if bad:
        issues.append(f"глифы отличаются ({len(bad)}): " + ", ".join(bad[:15]) + (" …" if len(bad) > 15 else ""))
    tables = []
    for t in ref.keys():
        if t == "GlyphOrder" or t in HINT_TABLES or t in SOURCE_TABLES or t in METRIC_CACHE_TABLES:
            continue
        if t not in out:
            tables.append(f"{t} (нет)")
            continue
        try:
            if ref[t].compile(ref) != out[t].compile(out):
                tables.append(t)
        except Exception as e:  # pragma: no cover - diagnostic only
            tables.append(f"{t} ({e})")
    if tables:
        issues.append("таблицы отличаются: " + ", ".join(tables))
    return issues


def ship_one(src: Path, out_path: Path, reference: Path | None, woff2: bool, log) -> bool:
    import vttcompilepy as vtt

    log(f"{src.name}: компиляция…")
    try:
        c = vtt.Compiler.from_file(src)
        c.compile_all()
    except vtt.CompileError as e:
        msg = e.args[0] if e.args else str(e)
        msg = (msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)).strip()
        print(f"✗ {src}: ошибка компиляции — {msg}", file=sys.stderr)
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
        notes.append("нет таблиц " + ", ".join(t.strip() for t in missing) + " — шрифт не хинтован")
        ok = False
    if "gasp" not in font:
        notes.append("нет gasp")
    if reference is not None:
        issues = compare(TTFont(reference), font)
        if issues:
            ok = False
            notes += [f"не совпадает с {reference.name}: {i}" for i in issues]
        else:
            notes.append(f"контуры, композиты и остальные таблицы совпадают с {reference.name}")

    if not ok:
        print(f"✗ {src}: " + "; ".join(notes), file=sys.stderr)
        print("  файл не записан (--force, чтобы записать всё равно)", file=sys.stderr)
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    font.save(out_path)
    print(f"✓ {out_path}")
    print(f"  байткод у {hinted} из {len(order)} глифов"
          + (f"; вырезаны также {', '.join(leftovers)}" if leftovers else ""))
    for n in notes:
        print(f"  {n}")
    if woff2:
        font.flavor = "woff2"
        w = out_path.with_suffix(".woff2")
        font.save(w)
        print(f"  {w}")
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="vtt-ship",
        description="Компилирует исходники VTT и пишет релизные TTF без таблиц TSI* (хинты остаются).",
    )
    p.add_argument("fonts", nargs="+", type=Path, help="шрифты с исходниками VTT (*.VTT.ttf)")
    p.add_argument("-o", "--output-dir", type=Path,
                   help="куда писать (по умолчанию подпапка hinted/ рядом с каждым шрифтом)")
    p.add_argument("--reference", type=Path, action="append",
                   help="нехинтованная сборка для сверки контуров (по умолчанию <имя без .VTT>.ttf рядом, если есть); "
                        "можно указать несколько раз в порядке шрифтов")
    p.add_argument("--no-check", action="store_true", help="не сверять с нехинтованной сборкой")
    p.add_argument("--force", action="store_true", help="записать, даже если сверка нашла расхождения")
    p.add_argument("--woff2", action="store_true", help="дополнительно записать .woff2")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)
    log = (lambda *m: None) if a.quiet else (lambda *m: print("·", *m, file=sys.stderr))
    if a.reference and len(a.reference) != len(a.fonts):
        p.error("--reference нужно указать для каждого шрифта")

    failed = 0
    for i, src in enumerate(a.fonts):
        if not src.is_file():
            print(f"✗ файл не найден: {src}", file=sys.stderr)
            failed += 1
            continue
        name = strip_vtt(src.name)
        out_dir = a.output_dir or src.parent / "hinted"
        out_path = out_dir / name
        if out_path.resolve() == src.resolve():
            p.error(f"результат перезаписал бы исходник: {out_path}")
        ref = None
        if not a.no_check:
            ref = a.reference[i] if a.reference else src.with_name(name)
            if not ref.is_file() or ref.resolve() == src.resolve():
                if a.reference:
                    p.error(f"файл не найден: {ref}")
                log(f"{src.name}: нехинтованной сборки {ref.name} рядом нет — сверка пропущена")
                ref = None
        if a.force and ref is not None:
            ok = ship_one(src, out_path, None, a.woff2, log)
            issues = compare(TTFont(ref), TTFont(out_path)) if ok else []
            for iss in issues:
                print(f"  ! не совпадает с {ref.name}: {iss}")
        else:
            ok = ship_one(src, out_path, ref, a.woff2, log)
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
