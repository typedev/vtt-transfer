# Hinting static fonts from the hinted variable font

Status: design notes, not implemented. Written 2026-10-09.
fontTools behaviour below was checked against fontTools 4.66.1; numbers come from a feasibility run on a hinted variable sans (see [Feasibility](#feasibility)).

## Problem

A family ships both a variable font and a set of static fonts built from the same designspace. Hinting every static by hand in VTT does not scale: each new version would mean re-hinting N fonts. The goal is to hint only the VF and derive hinted statics from it automatically.

## Core idea: a static is an instance of the VF, hints included

In a hinted TrueType VF there is nothing to "interpolate" in the hints themselves:

- glyph programs, `fpgm` and `prep` are the same for the whole design space;
- only the outlines (`gvar`) and the control values (`cvar` → `cvt`) vary.

The rasterizer applies `gvar` to the outline and `cvar` to the CVT, then runs `prep` and the glyph program. `fontTools.varLib.instancer` does the same thing ahead of time: it applies `gvar` to `glyf` and `cvar` to `cvt` and keeps `fpgm`, `prep` and glyph programs untouched. The resulting static rasterizes like the VF at that location:

```sh
fonttools varLib.instancer Font-VF.ttf wght=700 wdth=100 --update-name-table -o Font-Bold.ttf
```

So the hinting operator is responsible for the VF at its named instances, and checking those instances in VTT doubles as QA for the statics.

## Pipeline without overlap removal

```
designspace → VF (unhinted) → vtt-transfer + VTT → hinted VF (.VTT.ttf)
            → vtt-ship → release VF
            → instancer (per named instance) → hinted statics
```

### Constraints

1. **Statics must be cut from the VF with instancer, not built separately with fontmake.** fontmake interpolates cubic UFO instances and runs cu2qu per instance, which produces different off-curve points than the VF. The hints are bound to point indices, so they would not fit.
2. **Overlaps stay in.** instancer sets `OVERLAP_SIMPLE` / `OVERLAP_COMPOUND` by default (`overlap=KEEP_AND_SET_FLAGS`), and current macOS and Windows rasterizers handle overlapping contours.
3. **Never use `instancer --remove-overlaps` on a hinted font.** It calls `removeOverlaps(varfont, ignoreErrors=…)` with the default `removeHinting=True` and strips the hinting from the whole font.

### Minor effects

- **CVT rounding.** In the VF the CVT plus `cvar` deltas are fractional at intermediate locations; instancer writes `cvt[i] += otRound(delta)`. The difference is a fraction of a unit and in practice invisible.
- **Deltas** (`DELTAP`/`DELTAC`) are not location-aware in the VF anyway, so they behave the same in statics.
- **Per-static fixes**, if ever needed (for example a CVT override for Bold only), should live as patches applied after instancer. Manual edits to a generated static will not survive the next rebuild.
- **`hdmx` / `LTSH` / `VDMX`** are not produced by instancer. Modern renderers do not need them; generating them for legacy GDI would be a separate step.
- **`gasp`** is carried over as is.

## When overlaps must be removed

Overlap removal is usually requested for older renderers, printing and PDF workflows, some applications, or by distribution platforms. If the requirement is soft, keeping overlaps removes the whole problem below. If it is hard, the hints have to be carried across the outline change.

### What fontTools actually does

From `fontTools/ttLib/removeOverlaps.py`:

- `removeTTGlyphOverlaps` takes simple glyphs and those **composites whose components overlap each other** (`componentsOverlap`). Composites with non-overlapping components (for example `й` = `и` + breve above) stay composites.
- The path is simplified with skia-pathops. The glyph is replaced only if the simplified path differs from the original (`_same_path`, contour order ignored). A replaced glyph is always unhinted, and the function returns `True` for it.
- With `removeHinting=False` every glyph that was not replaced keeps its program. This is the mode to use, and the set of replaced glyphs is the remap work list.
- In the ufo2ft/fontmake pipeline, decomposition is a separate filter that runs before overlap removal on the UFO level. That pipeline is not used here, since statics come from the VF.

So the order is: instancer **without** overlap removal → `removeOverlaps(font, removeHinting=False)` as a separate step → remap only the replaced glyphs.

### Remapping point indices by coordinates

Points outside the intersection zones are not moved by pathops. An on-curve point that survives keeps exactly the same coordinates, so a map "old index → new index" can be built by exact coordinate match. This is the same `point_mapping` that vtt-transfer already uses between font versions.

Remapping must be done on **VTT Talk**, not on bytecode:

- in bytecode, point numbers sit on the stack together with CVT numbers, function numbers, ppem values and delta arguments, and cannot be told apart in general;
- in TSI1 assembly the `Res*` commands compile to `CALL[]` of `fpgm` functions with opaque arguments;
- in VTT Talk argument types are explicit (`YLink(3,7,42)`: two points and a CVT), and `talk.py` already whitelists the commands whose point arguments are known.

Then compile the glyph programs for the specific static with vttcompilepy. The CVT is already instanced by instancer and must not be touched.

### Where it breaks

| Case | What happens | Handling |
|---|---|---|
| Hinted point lies inside an overlap | The point no longer exists (for example where a cedilla joins `C`, a ring sits on `A`, a slash crosses `O`) | Glyph goes to manual work |
| IUP neighbours change | Untouched points are interpolated by `Smooth()`/IUP inside their contour. After merging, contours change and new intersection points have different neighbours | Usually minor; covered by the render diff |
| Off-curve points on cut segments | pathops splits curves at intersections, so off-curve points there get new coordinates | References to them (e.g. `YInterpolate`) fail the exact match and go to manual work |
| Ambiguous coordinates | Duplicate points at contour joins give several candidates | `point_mapping` reports them as lost |
| Decomposed composite | The glyph becomes simple. Its "hints" were the components' programs plus the composite's own instructions | Build a combined VTT Talk: each component's talk with point numbers shifted by the component's offset in the flattened point list, then remap. The composite's own instructions (`SHC`, `CALL` after `OFFSET`) are lost and must be redone |
| Phantom points | Their numbers depend on the point count | Shift with the outline, as vtt-transfer already does |

Per the project convention, remapping stays exact: a point moved by one unit is lost, with no fuzzy matching. A useful extension would be to *suggest* a candidate for a lost point in the report, for example the new intersection point at the same height for a Y anchor, without applying it automatically.

## Feasibility

Source: a hinted VF (wght × wdth, 829 glyphs, 340 glyphs with VTT Talk). For each location: instancer without overlap removal, then `removeOverlaps(removeHinting=False, ignoreErrors=True)`, then a `point_mapping` attempt for every replaced hinted glyph. Composites were assembled from component talk with point offsets.

| Instance | Simple glyphs replaced | of which hinted | remapped | manual | Composites decomposed | with hinted components | remapped | manual | with own instructions (lost) |
|---|---|---|---|---|---|---|---|---|---|
| wght 200, wdth 80 | 65 | 41 | 18 | 23 | 48 | 48 | 22 | 26 | 5 |
| wght 400, wdth 100 | 69 | 42 | 17 | 25 | 57 | 57 | 20 | 37 | 5 |
| wght 700, wdth 100 | 64 | 40 | 16 | 24 | 47 | 47 | 17 | 30 | 5 |
| wght 800, wdth 100 | 65 | 41 | 17 | 24 | 47 | 47 | 17 | 30 | 5 |

Observations:

- 49–62 glyphs per static need manual attention (plus 5 composites whose own instructions are lost). That is far fewer than hinting a static from scratch, but not zero, and the work repeats for every static.
- The manual list is largely the same across instances (`Aring`, `Ccedilla`/`ccedilla`, `Oslash`/`oslash`, `uni015E`/`uni015F`, `uni0119`…), because the same constructions overlap at every weight.
- No glyph was blocked by unsupported VTT Talk commands. All failures were lost reference points.
- This source has no `cvar`, so the CVT is identical across instances. With `cvar` present the pipeline is the same, since instancer applies it.

Because the failures are structural (the same joins at every weight), a better long-term fix may be upstream: remove overlaps in the **VF sources** for the problem glyphs (or build them without overlaps from the start), so that VF and statics share one outline structure and need no remap. That is a design decision for the type designer, not the tool.

## Proposed tool shape

A third command, e.g. `vtt-static`:

```sh
vtt-static Font-VF.VTT.ttf --instances designspace|named [--remove-overlaps] -o statics/
```

1. Read named instances from `fvar` (or from the designspace).
2. For each instance: instancer with `KEEP_AND_SET_FLAGS`, name table update.
3. Without `--remove-overlaps`: strip TSI tables (as `vtt-ship` does) and write. Done.
4. With `--remove-overlaps`:
   1. Keep the instanced font with overlaps as reference `S`.
   2. `removeOverlaps(removeHinting=False)` → `S'`, recording the replaced glyphs.
   3. For each replaced glyph build the VTT Talk (combined for decomposed composites), remap by coordinates, and list the failures.
   4. Put the remapped talk into TSI3 of `S'`, compile with vttcompilepy, strip sources.
   5. **Verify**: render `S` and `S'` with FreeType (hinting on) over a ppem range in mono and grayscale and diff per glyph. Glyphs that differ go to the report together with the ones that failed to remap.
5. HTML report per static, reusing `report.py` (old/new overlay, lost points).

Step 4.5 is what makes the remap trustworthy: a static is only accepted if its hinted rendering matches the overlapping original, except for the glyphs explicitly listed for manual work.

## Open questions

- Is overlap removal a hard requirement for these families, and from whom (platform, client, printing)?
- Where do manual fixes for remap failures live so they survive rebuilds? Options: a per-static VTT Talk override file in the repo, or fixing the VF outlines upstream.
- Does any target still need `hdmx`/`VDMX` (legacy GDI)?
- Should the composite instruction loss (5 glyphs here) be solved by keeping those composites as composites, i.e. resolving the component overlap in the design instead?
