"""vtt-transfer SOURCE TARGET — carry VTT hinting sources over to a new version of a font."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from . import hprj
from .core import TransferError, transfer
from .report import build


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="vtt-transfer",
        description="Переносит исходники VTT-хинтов (TSI0–TSI5, cvt, fpgm, prep, gasp, cvar/TSIC) из "
                    "хинтованной версии TrueType-шрифта в новую и пишет HTML-отчёт о том, что осталось дохинтовать.",
    )
    p.add_argument("source", type=Path, help="старая версия с VTT-исходниками (*.VTT.ttf)")
    p.add_argument("target", type=Path, help="новая версия шрифта (нехинтованный TTF/VF)")
    p.add_argument("-o", "--output", type=Path,
                   help="путь результата (по умолчанию <target>.VTT.ttf рядом с target)")
    p.add_argument("--report", type=Path, help="путь HTML-отчёта (по умолчанию рядом с результатом)")
    p.add_argument("--no-compile", action="store_true", help="не компилировать через vttcompilepy")
    p.add_argument("--no-remap", action="store_true",
                   help="не переписывать номера точек в глифах с изменённой структурой")
    p.add_argument("--hprj", type=Path,
                   help="проект VTThinter исходника (по умолчанию ищется <source без .VTT>.hprj рядом с source)")
    p.add_argument("--no-hprj", action="store_true", help="не обновлять .hprj")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = p.parse_args(argv)

    for path in (a.source, a.target):
        if not path.is_file():
            p.error(f"файл не найден: {path}")
    log = (lambda *m: None) if a.quiet else (lambda *m: print("·", *m, file=sys.stderr))

    try:
        res = transfer(a.source, a.target, output=a.output, remap=not a.no_remap,
                       compile_=not a.no_compile, log=log)
    except TransferError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2

    base = res.output_path.name.removesuffix(".VTT.ttf").removesuffix(".ttf")
    report_path = a.report or res.output_path.with_name(base + ".vtt-transfer.html")
    res.hprj = None
    if not a.no_hprj:
        src_h = a.hprj or hprj.find_for(a.source)
        if a.hprj and not a.hprj.is_file():
            p.error(f"файл не найден: {a.hprj}")
        if src_h:
            out_h = res.output_path.with_name(base + ".hprj")
            if out_h.resolve() == Path(src_h).resolve():
                out_h = out_h.with_name(base + ".updated.hprj")
            log("обновление .hprj…")
            res.hprj = hprj.update(res, Path(src_h), out_h)
    log("отчёт…")
    report_path.write_text(build(res), encoding="utf-8")

    G = res.glyphs.values()
    count = lambda *s: sum(1 for r in G if r.status in s)
    errors = [c for c in res.checks if c.level == "error"]
    print(f"{res.output_path}")
    print(f"{report_path}")
    if res.hprj:
        added = sum(len(c.added) for c in res.hprj.classes)
        print(f"{res.hprj.output}")
        print(f"  .hprj: добавлено в классы {added}, без класса {len(res.hprj.unassigned)}")
    print(f"  перенесено: {count('transferred')}, ремап: {count('remapped')}, "
          f"перехинтовать: {count('rehint', 'compile_error')}, "
          f"композитов без своих инструкций: {count('composite_dropped')}, "
          f"сменили тип: {count('type_changed')}, "
          f"новых: {count('new_simple')} простых без хинтов / {count('new_twin')} с хинтами двойника / "
          f"{count('new_composite')} композитов, "
          f"удалено: {len(res.removed)}")
    for c in res.checks:
        if c.level in ("warn", "error"):
            print(f"  {'!' if c.level == 'error' else '~'} {c.title}: {c.detail}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
