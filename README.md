# vtt-transfer

Transfers VTT hinting sources from a hinted version of a TrueType font (static or variable) to a new version of the same font and writes an HTML report: what was transferred, what needs extra hinting, what to check.

## Installation

Globally, so that `vtt-transfer` and `vtt-ship` work from any folder (`-e` picks up code changes without reinstalling):

```sh
uv tool install -e /path/to/vtt-transfer
```

Or only the project's local environment:

```sh
cd /path/to/vtt-transfer
uv sync
```

## Usage

```sh
vtt-transfer OLD.VTT.ttf NEW.ttf                     # after uv tool install
uv run vtt-transfer OLD.VTT.ttf NEW.ttf              # from the vtt-transfer folder
uv run --project /path/to/vtt-transfer vtt-transfer OLD.VTT.ttf NEW.ttf   # from any folder
```

Next to `NEW.ttf` you get:

- `NEW.VTT.ttf` — the new font with VTT sources (TSI0–TSI3, TSI5), `cvt`, `fpgm`, `prep`, `gasp` and `cvar`/`TSIC` (if present), compiled with vttcompilepy. It opens in VTT and work continues as usual.
- `NEW.vtt-transfer.html` — the report.

Options: `-o` output path, `--report` report path, `--report-lang en|ru` report language (default `ru`), `--no-compile` skip compilation, `--no-remap` no point remapping, `-q`.

Exit code: 0 — all good, 1 — verification errors (see the report), 2 — transfer impossible (no TSI tables, different UPM).

## How glyphs are matched

1. **By name.** If a glyph is gone, a rename is looked for: the same Unicode and the same point structure, or a single new glyph with identical points.
2. **Structure unchanged** (contours, points, on/off-curve, number of components): VTT Talk, assembly and bytecode are carried over as is. The GID in the headers is updated.
3. **Structure changed:** the reference points of the VTT Talk are looked up in the new outline by exact default-master coordinates; phantom points move with the outline. If all are found, the numbers are rewritten (status "remapped"); otherwise the hints are removed, the glyph goes to "Re-hint", and the old VTT Talk is attached in the report. Remapping supports only commands with unambiguous argument types (Anchor/Link/Dist/Shift/Interpolate/IPAnchor/Align/Delta/Smooth and the rounding commands NoRound/DownToGrid/UpToGrid/HalfGrid/DoubleGrid); everything else is manual work.
   **Composite decomposed into a simple glyph** (e.g. after overlap removal): the VTT Talk is assembled from the components' talk, with point numbers shifted by each component's position in the flattened point list, and then remapped the same way. A component's phantom point is kept only if it lands exactly on a phantom point of the glyph (taking the component offset into account); otherwise it is lost. Scaled or flipped components are not reused. The composite's own instructions are lost and flagged.
4. **Composites:** the assembly code (`OFFSET`/`SOFFSET`) is always regenerated from the new `glyf` (GIDs, offsets, transformations, USE_MY_METRICS), with prefixes (`OVERLAP[]` etc.) as in the source. The composite's own instructions are kept only if the point structure of its components did not change.
5. **New simple glyphs:** if the points exactly match a hinted old glyph, all hints are copied; if they are shifted along X only (combining accents made from spacing ones), only Y commands are copied. The rest get a character group by analogy with glyphs of the same Unicode category in the source.
6. **New composites** are hinted if all their components are; the report shows which components they are waiting for.

## Checks

After compilation the tool verifies the result against the new font: outlines and composites match, and tables unrelated to hinting (`gvar`, `GSUB`, `GPOS`…) have unchanged content. A compile error in a single glyph removes its hints and goes to the report; the rest is compiled.

## Limitations

- **Build the new font with the same compiler and options as the hinted source.** fontc drops implied on-curve points (exactly halfway between two off-curve points), fontmake keeps them unless `--drop-implied-oncurves` is passed. On one family a fontc build broke 14 hinted glyphs (`O`, `Q`, `©`, `®`, `°`…) and their composites that a fontmake build of the same sources transferred cleanly. See [doc/compilers.md](doc/compilers.md).
- Remapping uses default-master coordinates: a point moved by even one unit is considered lost. This is intentional.
- Composite code with point attachment (`ANCHOR`) is generated but flagged for manual review.
- VOLT tables (TSIV etc.) are not transferred.

## Autohinter project (.hprj)

If a VTThinter project lies next to the source (`family.hprj` for `family.VTT.ttf`) or is passed with `--hprj`, an updated copy (`NEW.hprj`) is written next to the result. The original file is not touched; `--no-hprj` disables the update.

- Each class is matched to a TSI5 group by the majority of its glyphs (for example: UpperCase → 2, LowerCase → 3, Figures → 4, Superiors → 1, Parens → 8, Accents → 9).
- Renamed glyphs are replaced in the lists in place, removed ones are dropped.
- New simple glyphs are added to the class of their group (the group is assigned the same way as in TSI5). Group 0 glyphs are not put into classes. Glyphs that became composites stay in the list and are flagged in the report.
- `TTFFileName`/`XMLFileName` are rewritten to the new font's name if it differs.
- Only these values change; formatting and line endings (CRLF) are preserved.

## Statics cut from a hinted VF (library)

`vtt_transfer.static` cuts the named instances of a hinted variable font
(`doc/static-instances-from-vf.md`): `named_instances(vf, family)` lists them, `cut(vf_path, instance,
out_dir, remove_overlaps=True)` writes `<name>.overlap.VTT.ttf` (the instance with overlaps and VTT
sources — the transfer source) and, with overlap removal, `<name>.ttf` (overlaps removed, hinting
stripped — the transfer target). Transferring source → target carries the hints over by exact remap.
`work/make_instances.py` runs it for every instance; hintbench uses it from its UI. Needs
`skia-pathops`. A `vtt-static` CLI is planned.

## Release fonts: vtt-ship

```sh
vtt-ship path/family-vf.VTT.ttf path/family-italic-vf.VTT.ttf [--woff2]   # or uv run vtt-ship …
```

Compiles the VTT sources (vttcompilepy, the same compiler as in VTT) and writes a font without the TSI* tables: hints, `cvt`, `fpgm`, `prep`, `gasp` stay. The result goes to a `hinted/` subfolder next to the source, named without `.VTT` (`-o DIR` to write elsewhere).

Before writing, the result is checked against the designer's unhinted build (by default `<name without .VTT>.ttf` next to it, or `--reference`): outlines, composites with offsets and all tables unrelated to hinting must match. This catches stale composite code in VTT (VTT builds composites from the OFFSET lines in the sources, not from `glyf`). On a mismatch or a compile error the file is not written (`--force` to write anyway, `--no-check` to skip the check). Exit code 1 if any font failed.
