# vtt-transfer

Transfers VTT hinting sources from a hinted version of a TrueType font (static or variable) to a new version of the same font and writes an HTML report: what was transferred, what needs extra hinting, what to check.

## Installation

```sh
uv tool install vtt-transfer      # or: pipx install vtt-transfer
```

This puts `vtt-transfer`, `vtt-var2static` and `vtt-ship` on the PATH. Python 3.11 or newer; the VTT
compiler comes from [vttcompilepy](https://pypi.org/project/vttcompilepy/) wheels (Windows, macOS,
Linux x86_64).

From a checkout (`-e` picks up code changes without reinstalling):

```sh
uv tool install -e /path/to/vtt-transfer
# or only the project's local environment:
cd /path/to/vtt-transfer && uv sync
```

## Usage

```sh
vtt-transfer OLD.VTT.ttf NEW.ttf                     # after installation
uv run vtt-transfer OLD.VTT.ttf NEW.ttf              # from the vtt-transfer folder
uv run --project /path/to/vtt-transfer vtt-transfer OLD.VTT.ttf NEW.ttf   # from any folder
```

Next to `NEW.ttf` you get:

- `NEW.VTT.ttf` — the new font with VTT sources (TSI0–TSI3, TSI5), `cvt`, `fpgm`, `prep`, `gasp` and `cvar`/`TSIC` (if present), compiled with vttcompilepy. It opens in VTT and work continues as usual.
- `NEW.vtt-transfer-report.html` — the report.

Options: `-o` output path, `--report` report path, `--report-lang en|ru` report language (default `ru`), `--no-compile` skip compilation, `--no-remap` no point remapping, `-q`.

Exit code: 0 — all good, 1 — verification errors (see the report), 2 — transfer impossible (no TSI tables, different UPM).

## How glyphs are matched

1. **By name.** If a glyph is gone, a rename is looked for: the same Unicode and the same point structure, or a single new glyph with identical points.
2. **Structure unchanged** (contours, points, on/off-curve, number of components): VTT Talk, assembly and bytecode are carried over as is. The GID in the headers is updated.
3. **Structure changed:** the reference points of the VTT Talk are looked up in the new outline by exact default-master coordinates; phantom points move with the outline. If all are found, the numbers are rewritten (status "remapped"); otherwise the hints are removed, the glyph goes to "Re-hint", and the old VTT Talk is attached in the report. VTT Talk is parsed with the command signatures of Microsoft's compiler, so every command whose point arguments are typed is remapped, points of vector overrides (`YLink(0,4:9>18)`) included; commands with untyped arguments (Call, Inline/ASM, BeginCodePath, AutoHinter) and the obsolete ones the compiler refuses (Tail, DiagSerifs…) are manual work.
   **Composite decomposed into a simple glyph** (e.g. after overlap removal): the VTT Talk is assembled from the components' talk, with point numbers shifted by each component's position in the flattened point list, and then remapped the same way. A component's phantom point is kept only if it lands exactly on a phantom point of the glyph (taking the component offset into account); otherwise it is lost. Scaled or flipped components are not reused. The composite's own instructions are lost and flagged.
4. **Composites:** the assembly code (`OFFSET`/`SOFFSET`) is always regenerated from the new `glyf` (GIDs, offsets, transformations, USE_MY_METRICS), with prefixes (`OVERLAP[]` etc.) as in the source. The composite's own instructions are kept only if the point structure of its components did not change.
5. **New simple glyphs:** if the points exactly match a hinted old glyph, all hints are copied; if they are shifted along X only (combining accents made from spacing ones), only Y commands are copied. The rest get a character group by analogy with glyphs of the same Unicode category in the source.
6. **New composites** are hinted if all their components are; the report shows which components they are waiting for.

## Checks

After compilation the tool verifies the result against the new font: outlines and composites match, and tables unrelated to hinting (`gvar`, `GSUB`, `GPOS`…) have unchanged content. A compile error in a single glyph removes its hints and goes to the report; the rest is compiled.

## Limitations

- **Build the new font with the same compiler and options as the hinted source.** fontc drops implied on-curve points (exactly halfway between two off-curve points), fontmake keeps them unless `--drop-implied-oncurves` is passed. On one family a fontc build broke 14 hinted glyphs (`O`, `Q`, `©`, `®`, `°`…) and their composites that a fontmake build of the same sources transferred cleanly. The report flags reference points lost this way and warns when many glyphs are hit. See [doc/compilers.md](https://github.com/typedev/vtt-transfer/blob/master/doc/compilers.md).
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

For a static cut by `vtt-var2static`, the VF's project (`family-vf.hprj` next to the VF, or `--hprj`) is
copied as `<name>.hprj` with `TTFFileName`/`XMLFileName` set to the static and every `cvt`/`value` pair
set to the static's CVT (the same as the VF's without `cvar`; a value that already disagreed with the
VF's CVT is corrected and printed). The glyph lists stay as they are. The stem range of each class is
checked against the strokes measured on the static (the thickest straight or round stroke, by the
reference glyphs of `vtt_transfer.measure`: H I L / E, n m h u / z, 1 / 2 4 7, O o 0); a class outside
its `stem min`–`max` is printed as a warning — bold statics need a wider range than the VF's default
master. Classes without reference glyphs (superiors, parens, accents) are not measured.

## Statics cut from a hinted VF: vtt-var2static

```sh
vtt-var2static path/family-vf.VTT.ttf [-o DIR] [-i "Condensed Light" -i Bold] [--keep-overlaps] [--ship [--woff2]]
```

Cuts every named instance of a hinted variable font (or only the ones given with `-i`: subfamily or
file name, case and spaces ignored) into `DIR/<name>/` (default `statics/` next to the font):

`<name>` is the font's file name without `-vf`/`_vf` plus the instance's subfamily without spaces
(`family-vf.VTT.ttf`, `Condensed Light` → `family-CondensedLight`); an italic VF's `_italic`/`-italic`
is dropped when the subfamily says Italic, so `family_italic-vf` gives `family-BoldItalic`.

- `<name>.overlap.VTT.ttf` — the instance with overlaps and VTT sources (the transfer source);
- `<name>.ttf` — overlaps removed, hinting stripped (the transfer target);
- `<name>.VTT.ttf` — the target with the hints transferred and compiled; open it in VTT;
- `<name>.vtt-transfer-report.html` — the transfer report: what was remapped, what needs re-hinting;
- `<name>.hprj` — the VF's VTThinter project for this static, if the VF has one (see below).

`--keep-overlaps` skips overlap removal: the instance keeps its hints as they are and is written as
`<name>.VTT.ttf`, with nothing to transfer. `--ship` also writes release fonts without VTT sources into
`DIR/hinted/` (as `vtt-ship`, checked against `<name>.ttf`), `--woff2` adds `.woff2`. Options
`--report-lang`, `-q`, `--hprj`, `--no-hprj` as in `vtt-transfer`. Exit code: 0 — all good, 1 — some instance failed (cut,
transfer verification or ship check), 2 — the font has no VTT sources or no named instances.

### Library

`vtt_transfer.static` cuts the named instances of a hinted variable font
(`doc/static-instances-from-vf.md`): `named_instances(vf, family)` lists them, `cut(vf_path, instance,
out_dir, remove_overlaps=True)` writes `<name>.overlap.VTT.ttf` (the instance with overlaps and VTT
sources — the transfer source) and, with overlap removal, `<name>.ttf` (overlaps removed, hinting
stripped — the transfer target). Overlap removal can turn a hinted on-curve point into an implied one
(exactly between its off-curve neighbours); `cut` puts such points back, so the outline is the same and
the remap finds them. Transferring source → target carries the hints over by exact remap.
`vtt-var2static` runs it for every instance. Needs `skia-pathops`.

## VTT sources from scratch (library)

`vtt_transfer.fresh.prepare(font)` gives an unhinted static TrueType font VTT sources from Microsoft's
templates (`src/vtt_transfer/templates/`, MIT, from microsoft/VisualTrueType — see `NOTICE.md`), as VTT's
"Prepare Font" + "Control Program Only" would: glyf instructions and `fpgm`/`prep`/`cvt` dropped; the
standard font program; the Control Program template with the values of `vtt_transfer.measure` written
into its CVT lines (`2: 690 /* cap height */ /* measured: H E F … (17/17) */`, unmeasured slots marked);
character groups (TSI5) from `chargrp.txt` by Unicode, unencoded glyphs by their base name, else by Unicode
category; empty Talk, composites with `OFFSET[R]` assembly. The compiler generates the pre-program from
the Control Program. Returns the compiled font and a report (measurements, group counts).

`vtt_transfer.measure.measure(font)`: heights from the
dominant cluster of reference glyph tops/bottoms (Latin + Cyrillic; overshoots relative to their
height), stems from scanline ink runs, kept apart by group (UpperCase / LowerCase / Figure), direction
and straight/round, keyed by the template's CVT numbers. Diagonals, serifs and italic CVTs are not
measured.

For re-measuring one CVT from glyphs the operator picks: `measure_height(font, glyphs, "top"|"bottom")`,
`measure_overshoot(font, glyphs, side, parent_value)` (round − flat) and `measure_stem(font, glyphs, axis,
where, band=…)` take a string of characters (through the cmap) or a list of glyph names and return
`{value, samples, total, glyphs, values, missing}` (`values`: each glyph's samples, to spot the one that
disagrees; an empty or unknown set gives `value` None). `default_glyphs(cvt)` gives the set and
parameters `measure` uses for a template CVT, None for the ones it does not measure.

## Control Program (library)

`vtt_transfer.control` parses and edits the VTT Control Program (TSI1 `cvt` source). It is a port of
the compiler's grammar (VisualTrueType `CvtManager.cpp`). Parsing is token based: attributes stay set
until changed, comments nest, and indentation means nothing. `parse(text)` returns a `Program` with
the GROUP/CATEGORY declarations (TSI5 group numbers), the settings, the `ASM` statements, every CVT
(value, `~`/`=` inheritance, break ppem, deltas, attributes, the first two comments of its line,
absolute value) and the errors with their lines. It never raises. `set_cvt(text, n, value=…, ppem=…,
deltas=…, name=…, note=…)` and `set_setting(text, key, value)` change only the edited tokens and keep
comments, spacing and line ends. `add_cvt` places a new CVT next to the CVTs with the same attributes
(heights get the first free number below 40, others the first free from 65) and gives a square height
the rounding pair (`ASM("SVTCA[Y]")`, `ASM("CALL[], n, 89")`) when the program does that for its other
heights. `remove_cvt` removes a CVT with its comments and rounding pair, and refuses a CVT that is
another CVT's parent; it does not look at glyph Talk. xscale and fresh are built on it.

## X-height scaling (library)

`vtt_transfer.xscale` adds ttfautohint-style x-height scaling to a VTT-hinted font, in its sources. VTT
rounds every height CVT on its own, so at a size where the x-height rounds up the caps may round down;
ttfautohint rounds the x-height (with overshoot) and scales all heights by the same factor before
rounding. `apply(font, Params(mode="both"|"up", limit=14, maxppem=0))` adds two functions to the fpgm
source and one `ASM("CALL[] …")` line in the Control Program before the first height CVT, so the
scaling runs in the pre-program before VTT's own rounding. `find_heights` reads the height CVTs
(SquareHeight / RoundHeight; the x-height is the LowerCase one equal to `OS/2.sxHeight`), `detect`
reports the state of the scaling in the sources (including a call made stale by CVT edits), `remove`
takes it out. Only TSI1 is edited; the caller compiles.

## Release fonts: vtt-ship

```sh
vtt-ship path/family-vf.VTT.ttf path/family-italic-vf.VTT.ttf [--woff2]   # or uv run vtt-ship …
```

Compiles the VTT sources (vttcompilepy, the same compiler as in VTT) and writes a font without the TSI* tables: hints, `cvt`, `fpgm`, `prep`, `gasp` stay. The result goes to a `hinted/` subfolder next to the source, named without `.VTT` (`-o DIR` to write elsewhere).

Before writing, the result is checked against the designer's unhinted build (by default `<name without .VTT>.ttf` next to it, or `--reference`): outlines, composites with offsets and all tables unrelated to hinting must match. This catches stale composite code in VTT (VTT builds composites from the OFFSET lines in the sources, not from `glyf`). On a mismatch or a compile error the file is not written (`--force` to write anyway, `--no-check` to skip the check). Exit code 1 if any font failed.

## Development

```sh
uv sync
uv run python tests/smoke.py      # end-to-end run on a synthetic font, as in CI
```

## License

MIT, see [LICENSE](https://github.com/typedev/vtt-transfer/blob/master/LICENSE). Parts are ported from or copied out of Microsoft's
[VisualTrueType](https://github.com/microsoft/VisualTrueType) (MIT), see [NOTICE.md](https://github.com/typedev/vtt-transfer/blob/master/NOTICE.md).
