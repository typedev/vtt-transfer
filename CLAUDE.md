# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI that transfers VTT (Visual TrueType) hinting sources from a hinted version of a TrueType font (static or variable, `*.VTT.ttf`) into a new, unhinted version of the same font, compiles it with `vttcompilepy`, and writes an HTML report of what was transferred, remapped, and still needs hinting. A second CLI, `vtt-ship`, builds release fonts from VTT sources. README.md is the authoritative description of the matching rules and user-facing behaviour.

## Commands

Managed with `uv` (Python ≥3.11, hatchling build, deps: fonttools, vttcompilepy, brotli, skia-pathops).

```sh
uv sync                                         # set up .venv
uv run vtt-transfer OLD.VTT.ttf NEW.ttf         # writes NEW.VTT.ttf + NEW.vtt-transfer.html (+ NEW.hprj if found)
uv run vtt-transfer OLD.VTT.ttf NEW.ttf --report-lang en   # report language: en|ru (default ru)
uv run vtt-transfer OLD.VTT.ttf NEW.ttf --no-compile --no-remap -q
uv run vtt-ship path/font.VTT.ttf [--woff2]     # writes hinted/font.ttf without TSI* tables
uv tool install -e .                            # global editable install of both commands
```

There are no tests, linter, or formatter configured. Verify changes by running the CLI on real font pairs (e.g. an older hinted `*.VTT.ttf` against a new unhinted build of the same family) and inspecting the report and the exit code (`vtt-transfer`: 0 ok, 1 verification errors, 2 transfer impossible; `vtt-ship`: 1 if any font failed).

## Architecture

All code is in `src/vtt_transfer/`:

- `core.py` — the whole transfer pipeline in one long `transfer()` function, split into sections by `# ----` comments: requirements (glyf present, TSI1/TSI3 in source, equal UPM; failures raise `TransferError`) → axes/avar/gvar-region checks → glyph analysis and rename detection → per-glyph transfer (TSI1 assembly, TSI3 VTT Talk, bytecode) → character group (TSI5) assignment for new glyphs → global tables (`cvt`, `fpgm`, `prep`, `gasp`, `cvar`/`TSIC`, maxp hint fields) → compile → verification against the target font → save. Results accumulate in a `TransferResult` (`glyphs`: target name → `GlyphResult` with a status constant like `TRANSFERRED`, `REMAPPED`, `REHINT`, `NEW_TWIN`…, plus `checks` as `Check(level, title, detail)`). Notes, check texts, actions and group reasons are `Msg` objects, not strings — see `i18n.py`.
  - Glyph identity is `GlyphInfo.structure` (kind, contour ends, on-curve flags, component count). Same structure → copy as is; changed → point remap by exact default-master coordinates via `point_mapping`, else hints dropped. A composite that became simple gets its talk assembled from the components' talk (`component_talk`, point numbers shifted into the flattened list) and is then remapped the same way.
  - Composite assembly (`OFFSET`/`SOFFSET`/`ANCHOR` lines) is always regenerated from the new `glyf` (`parse_composite_asm`, `render_component`), keeping the source's prefix lines and composite style.
  - Compilation loop: on a `vttcompilepy.CompileError` mentioning `glyph N`, that glyph's hints are stripped, status set to `COMPILE_ERROR`, and compilation retried.
- `talk.py` — VTT Talk parsing. A whitelist (`SUPPORTED`) of commands whose point-number arguments are known; `analyze` collects referenced points, `remap` renumbers them, `y_only` keeps Y commands (used for X-shifted twins like combining accents). Glyphs using any other command are never auto-remapped — extend the whitelist only with commands whose argument types are certain.
- `hprj.py` — updates a VTThinter autohinter project (`.hprj`) next to the source: maps classes to TSI5 groups by majority, applies renames/removals/new glyphs. Edits values in place by regex, preserving formatting and CRLF line endings; never overwrites the source `.hprj`.
- `i18n.py` — the message catalog (`MESSAGES`: key → (English, Russian)), `M(key, **params)` to create a `Msg`, `tr()` to render one. `str(msg)` is English (used for console output); the report renders in the chosen language via `Translator` (`t(...)` for plain text, `t.h(...)` for trusted-markup templates with escaped parameters).
- `report.py` — `build(res, lang)` renders the self-contained HTML report (inline SVG glyph drawings via fontTools pens, old vs. new overlays).
- `cli.py` — `vtt-transfer` entry point: argparse, calls `transfer`, `hprj.update`, `report.build`, prints a summary.
- `static.py` — cutting statics out of a hinted VF: `named_instances`, `cut` (instancer with overlaps
  kept → transfer source; `removeOverlaps` + hinting stripped → transfer target). Library only so far;
  used by `work/make_instances.py` and by hintbench.
- `ship.py` — `vtt-ship` entry point: compiles with vttcompilepy, strips TSI* tables, and `compare()`s against the designer's unhinted reference (`<name without .VTT>.ttf`); refuses to write on mismatch unless `--force`.

## Conventions

- Language policy: everything in the repo is English — CLI help, log/console output, README and other docs, commit messages, identifiers and code comments. The HTML report is bilingual (`--report-lang en|ru`, default `ru`). Chat with the user in whatever language they write in.
- Any text that can reach the report goes through the `i18n.py` catalog with both translations; never put a literal sentence into `GlyphResult.notes`, `Check`, `res.actions`, `group_reason` or `report.py`. Raw data (glyph names, table tags, compiler output) is passed as a plain string or a parameter. Russian text lives only in `i18n.py`.
- Section titles and intros in `report.py` come from the `sec_<id>` / `sec_<id>_intro` keys, derived from the section id.
- Remapping deliberately requires exact coordinate matches — a point moved by one unit is treated as lost. Don't add fuzzy matching.
- The target must be built with the same compiler as the hinted source (fontmake; fontc drops implied on-curve points and breaks round glyphs). When a transfer shows unexpected losses on unchanged designs, check the compiler first — `doc/compilers.md`.
- Verification treats re-encoded tables as unchanged if `compile()` output matches; non-hinting tables (`gvar`, `GSUB`, `GPOS`…) must stay identical to the target font.
