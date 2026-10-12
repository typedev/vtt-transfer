# Build the new version with the same compiler as the hinted source

Status: observed on a hinted variable sans family, 2026-10-11.

## Summary

VTT hints refer to point numbers. A compiler that changes the point structure of glyphs whose
design did not change makes the transfer lose those glyphs, and every composite built from them.

- **fontc** dropped *implied on-curve points* in this build: an on-curve point lying exactly halfway
  between two off-curve points (in all masters) can be omitted from `glyf`, since the rasterizer
  implies it.
- **fontmake** (ufo2ft / fontTools varLib) keeps them. Dropping is opt-in there:
  `fontmake --drop-implied-oncurves` (TrueType only, default off). The fontTools option was added,
  per its PR, for diffing fontmake output against fontc.

Rule: build every version of a hinted family with the same compiler and the same point-related
options as the font the VTT sources were made on. For our projects so far that is
fontmake with default options (no `--drop-implied-oncurves`). Record the compiler in the project's
build profile, and treat a compiler switch like an outline change of the whole font.

## Evidence

Source: the family's hinted VF (built with fontmake), 340 hinted simple glyphs. Target:
the same designspace built twice on 2026-10-11 — with `variableCompiler: fontc` in the build
profile, and again after that option was removed (fontmake).

| | fontc build | fontmake build |
|---|---|---|
| hinted simple glyphs with the source point structure | 320 | 334 |
| transferred | 527 | 563 |
| remapped | 2 | 0 |
| re-hint | 16 | 4 |
| changed, unhinted | 28 | 0 |
| composite lost own instructions | 22 | 0 |

The builds are about an hour apart with design commits in between, so not every difference in the
lower rows is the compiler. The structural comparison isolates it: **14 hinted glyphs differ from
the source only in the fontc build, and only by dropped implied on-curve points**:

`O` (27), `Q` (37), `copyright` (0 5 10 15 20 30), `registered` (10 20 28),
`degree` (0 4 8 12 19 25), `paragraph` (5), `Oslash` (39), `uni042E` (35), `uni0472` (31),
`uni221E` (4 20), `uniF406` (0), `uniF6D1` (8), `uni2117` (0 5 10 15 20 30),
`uni2300` (0 4 8 12 16 20 24 28) — source point numbers in parentheses.

- In 13 of them the dropped points are implied at all 18 named instances of the source as well.
- In 12 of them a dropped point is referenced by the VTT Talk — e.g. `O`: `YShift(9,27)` moves the
  top of the counter, and point 27 no longer exists — so the transfer cannot remap them (re-hint).
- In `paragraph` the dropped point (5) is not referenced: the remap succeeds, but all later point
  numbers shift by one.
- Composites inherit the change: `Odieresis`, `Oacute`… lose their own instructions because the
  point structure of their component `O` changed.

The other 6 hinted glyphs that differ from the source in both builds are design changes (e.g.
Greek `uni0394`, `uni03A9`, `uni03C0` redrawn), not the compiler.

## Recognising it in a report

A lost reference point that was an on-curve point exactly at the midpoint of its two off-curve
neighbours in the source, with the neighbours still present in the target, is almost certainly a
dropped implied on-curve. Many such glyphs in one transfer (round shapes: `O`, `Q`, `©`, `®`, `°`)
point at the build, not the design.

Possible extension (not implemented): detect this case in `point_mapping` and give the lost point
the reason "dropped as an implied on-curve — target built by a compiler that drops them (fontc,
fontmake --drop-implied-oncurves)", plus a check over the whole font.
