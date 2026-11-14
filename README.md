# Quadro Print Studio — Farm Mode (milestone 1)

Offline, deterministic converter that turns **one single-colour model, sliced
once** in Co Print Slicer into a **Farm Mode** print for a four-head Quadro:
identical copies, four heads printing synchronized.

Product flow (later milestones add the web UI): *Printer Profile → Mode →
Upload sliced G-code → Layout preview → Download*. In this milestone only
**Farm Mode** exists. Copy Mode and Dual Color Copy Mode are later milestones
and are not available.

Nothing here connects to a printer, modifies firmware, starts a print or
calls an AI service.

## Requirements

* Python ≥ 3.9 (tested with 3.13). **No third-party packages.**
* Optional: nothing else. Tests use the standard `unittest` module.

## Web app (Farm Mode)

```
python3 -m quadro_studio.web            # http://127.0.0.1:8765  (add --host 0.0.0.0 to open it from a phone on the same network)
```

Upload the printer profile ZIP and one sliced model, choose the number of copies
(multiple of 4), check the layout, then *Generate and verify*. The layout is computed
from the model's own size (`preset auto`); if the count does not fit, the page says why
and offers the largest count for which the full layout checks pass. The generated file
is verified by a separate `verify` process; only a file that passed, and whose SHA-256
still equals the verified hash, can be downloaded. Changing any input starts a new job;
old results are hidden. No API key, no printer connection, no external service.

## Command line

```
python3 -m quadro_studio inspect  --slice MODEL.gcode --profile PRINTER.zip [--out inspect.json]
python3 -m quadro_studio plan     --slice MODEL.gcode --profile PRINTER.zip [--count 40] [--out plan.json] [--preview layout.svg]
python3 -m quadro_studio generate --slice MODEL.gcode --profile PRINTER.zip [--count 40] --out farm.gcode [--report generate.json]
python3 -m quadro_studio verify   --slice MODEL.gcode --profile PRINTER.zip --gcode farm.gcode [--count 40] [--oracle GOLDEN.gcode] [--out validation.json]
python3 -m quadro_studio preview  --slice MODEL.gcode --profile PRINTER.zip [--count 40] --out layout.svg
```

* `generate` reads **only** the slice, the explicit printer profile and the
  layout options. It refuses (exit 2, no G-code written, stale output
  removed) when any blocking diagnostic exists.
* `verify` is an independent simulator of the output (it does not import the
  generator). Only `verify --oracle` may read a golden reference file, and only
  to compare.
* Layout options: `--count` (multiple of 4), `--preset reference-300|auto`,
  `--sync-pitch`, `--cols-per-zone`, `--col-pitch`, `--row-pitch`,
  `--min-gap`, `--foundation-layers`, `--standby-temp`,
  `--travel-clearance`, `--max-segment`, `--park-margin` (extra mm above the minimum carriage
  clearance for stacked parks; default 0 = reference parity). The named preset `reference-300` is the
  reviewed 8×5 layout (72 mm head spacing, 35 mm column shift, 58 mm rows,
  centred on the bed). `auto` derives pitches from the model footprint; it is
  checked by the same rules but makes no claim of optimal packing. To get more
  or smaller copies, re-slice a smaller model — G-code is never scaled.

What each verification check proves — and does not prove — is listed in `CHECKS.md`.

Acceptance run (reproduces every number in REPORT.md):

```
python3 tools/run_acceptance.py --fixtures FIXTURE_DIR [--historical HIST_DIR] --out OUT_DIR
python3 tools/write_report.py --acceptance OUT_DIR --out REPORT.md
QUADRO_FIXTURES=FIXTURE_DIR python3 -m unittest discover -s tests -v
```

## What a printer profile must contain

A `.zip` (or folder) with the Klipper config of *this* machine:
`printer.cfg` with its `SAVE_CONFIG` block (saved bed mesh), every file it
`[include]`s, the four carriages, four extruders with `fan_generic fan_tN`,
`[quadro_parallel_carriage]`, the macros `_PRINT_HOME`, `M109`, `M190`, and
`bed_pin_offsets.json` with the measured T0..T3 X/Y/Z corrections (T0 = 0).
Missing, ambiguous (several saved meshes without `--mesh-profile`), malformed
or implausible inputs are rejected with an actionable message.
Archive expansion is bounded (entries, per-file size, total size, compression
ratio); absolute paths, `..`, symlinks and device entries are rejected.
Config and macro text is parsed as data and **never executed**.
Profiles are private machine data: do not publish them as web assets.

Another printer needs its own saved mesh, offsets, limits and macro
semantics; nothing of the reference machine is baked into the defaults.

## Accepted slice features (phase 1)

Co Print Slicer export, Klipper flavour, relative E, no arc fitting, no
firmware retraction, one object, T0 only, by-layer sequence.

| Input | Handling |
|---|---|
| `G0/G1 X Y Z E F` | absolute XYZ, relative E; every model move is kept (translated, Z/E compensated, F preserved) |
| `G90`, `G21`, `M83`, `G92 E0` | modal setup (required / no-op) |
| `T0` | single tool selection before layer 1 |
| `start_print …` | parameters read (temperatures, extruders used); macro replaced by Farm start-up |
| `end_print` | replaced by Farm shutdown |
| `M104/M109 [T0] S`, `M140/M190 S` | one nozzle / bed temperature (changes during the print are rejected) |
| `M106 [P0] S` / `M107` | part fan → `SET_FAN_SPEED` on the printing head(s) |
| `SET_VELOCITY_LIMIT` | preserved verbatim at the same place in each copy |
| `SET_PRESSURE_ADVANCE ADVANCE=` | applied to all four extruders |
| `M73`, `SET_PRINT_STATS_INFO` | progress metadata, regenerated |
| `TIMELAPSE_TAKE_PHOTO` | dropped (it would park the toolhead mid-print) |
| zero-length `G1 … E0` | no-op, dropped |
| anything else (`G2/G3`, `G91`, `M82`, `G10/G11`, `T1-T3`, `M600`, `EXCLUDE_OBJECT_*`, unknown macros, motion outside an object block, …) | **rejected**, the message names the exact limitation |

Model paths are separated from slicer set-up/purge/end moves by the object
markers: each layer's *model block* runs from the first extrusion of the object
to the end of the retraction/wipe after the object's stop marker. The slicer's
approach (spiral lift, travel, lowering, prime) is replaced by the generator's
own verified transfer.

## Output dialect and assumed macro behaviour

Output uses `G1 G90 M83 M82 M220 M221 M400 M104 M109 M190 M117
SET_VELOCITY_LIMIT SET_PRESSURE_ADVANCE SET_FAN_SPEED SET_GCODE_OFFSET
SET_DUAL_CARRIAGE ACTIVATE_EXTRUDER SYNC_EXTRUDER_MOTION QUADRO_PARALLEL_MOVE
BED_MESH_CLEAR BED_MESH_PROFILE TURN_OFF_HEATERS SET_PRINT_STATS_INFO
_PRINT_HOME`. Assumptions (not executable offline, listed in REPORT.md):

* `_PRINT_HOME TEMP BED_TEMP` homes all axes and Z at X150 Y150, leaves T0
  primary/active with zero G-code offset and the runtime mesh cleared;
  homing's `QUADRO_DISTRIBUTE_X` leaves T0=150, T1=356.5, T2=412.75, T3=469.
* `QUADRO_PARALLEL_MOVE` is used and accepted by the verifier only in the
  non-ARRIVING, X-only form; as in the supplied firmware's `_execute_parallel_move`
  the largest-displacement carriage is driven and the others follow scaled, so all
  carriages are affine in one progress variable. `ARRIVING`, `PLAN_ONLY`, `Y`, `Z`
  are rejected as unverified motion profiles.
* All end positions are *parked* at the endstop coordinates; nothing is re-homed.
* `SET_DUAL_CARRIAGE … MODE=COPY` copies the primary's X motion with the
  carriage offset present at that moment; `SYNC_EXTRUDER_MOTION` keeps each
  extruder's own rotation distance and pressure advance.
* `M109`/`M190` macros wait for temperature (profile macros).

## Layout

```
quadro_studio/  mesh.py profile.py slicer.py plan.py generate.py verify.py preview.py cli.py
  web/          server.py (stdlib HTTP), messages.py (plain-English results), static/ (index.html, app.js, style.css)
tests/          unit, negative and end-to-end tests (+ synthetic, non-private inputs)
tools/          e2e_web.js (browser end-to-end, Playwright), serve_with_fault.py (test-only fault injection),
                run_acceptance.py, audit_run.py, write_report.py, build_bundle.py,
                historical_e_check.py (test-only; reads historical private assets given on the command line)
```
