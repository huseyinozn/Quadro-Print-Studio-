# Dual Color Copy Mode (milestone 4): results

## Review round 2: Two-Color Fidgets example from the left model, Z and Y reported separately

### Result: real fidget + current profile

**Refused.** No G-code is written and download stays blocked. There are two independent blockers, Z and Y. Colour mapping is OK.

| Topic | Status | Criterion | Where |
|---|---|---|---|
| Colour mapping | OK | The left model's T0 (colour 1) and T2 (colour 2) regions are the only source. T1 prints a copy of T0's regions and T3 a copy of T2's regions. Layers 1–3 use the same source geometry with each head's own current mesh and offsets. From layer 4, T1 follows T0 and T3 follows T2 in COPY. The verifier confirms that each head reproduces its colour region on 100/100 layers. | The slice's own right-model painting differed on 33 layers. For the built-in example it is replaced, and this is reported as replaced. |
| Height (Z) | **Blocked** | Layer thickness between consecutive layers must stay within 0.080–0.300 mm. A nozzle may not be more than 0.03 mm below material of the other colour (the same 0.03 mm as the verifier's existing `inpart_sink`). | See the table below. The problem is real in this model, not only possible. |
| Colour alignment (Y) | **Blocked** | The two colours of one model may be at most 0.100 mm apart. | 0.209 mm on both models, everywhere. Matching Z does not fix this. |

**Z is a real problem in this model.** I replayed the planned nozzle heights over the real geometry, layer by layer in print order (T0, T1, T2, T3, then pair A, then pair B), on a 0.1 mm height map per model. Bead footprint = half the line width; nozzle opening radius = 0.2 mm. On the left model colour 2 sits 0.119 mm below colour 1; on the right model it sits 0.119 mm above.

| Model | Finding | Count | Layers | Example (layer · head · physical X, Y · what is below or beside) |
|---|---|---|---|---|
| left | colour 1 on colour 2 from one layer to the next | 493 cells out of limits (of 679) | 14–17, 19–23, 31, 61–71, 81–88 | 14 · T0 · (129.54, 179.21) · 0.318 mm on colour 2 (T2) of layer 13 |
| left | bead edge at a colour border (colour 1 printed first, its edge lies on colour 2 of the layer below) | 21 676 cells out of limits | 3–100 | 4 · T0 · (134.02, 179.41) · 0.318 mm on colour 2 (T2) of layer 3 |
| left | nozzle prints lower than colour 1 beside it (same layer, printed just before) | 22 874 points, max 0.119 mm | 2–100 | 3 · T2 · (133.82, 179.20) · 0.118 mm into colour 1 (T0) |
| left | travel or wipe at layer height through colour 1 | 295 moves, max 0.119 mm | 2–6, 8, 14–20, 29–73, 76, 81–87 | 3 · T2 · (125.70, 179.20) · slicer line 2634 |
| right | colour 2 on colour 1 from one layer to the next | 164 cells out of limits (of 701) | 12–16, 20–21, 31, 71, 81–83, 86–88 | 12 · T3 · (195.83, 133.40) · 0.319 mm on colour 1 (T1) of layer 11 |
| right | bead edge at colour borders / nozzle lower than the colour beside it / travels | 0 / 0 / 0 | – | Colour 2 is higher here and printed after colour 1 |

Over a gap of more than one layer (bridges, sparse infill below), the colour changes shift by ±0.119 mm against the slice's own gap. This is reported but not judged as a layer thickness.

**The verifier finds the same problem independently.** For this check only, I generated a file anyway with the current profile. It was not delivered and must not be printed. The verifier rejects it with:

* `COPY_THICKNESS` 1484 times, measured 0.117–0.281 mm;
* `DESCENT_INTO_MATERIAL` (T2 descends 0.1195 mm into material);
* `FOUNDATION_FLOW` in layers 2–3 (E against the measured thickness at colour borders);
* `DUAL_REGISTRATION` 0.209 mm on both models.

Colour regions are 100/100 on all four heads. The only blockers left are Z and Y.

**Cross-check (test only, never offered):** the same real fidget with offsets that are consistent in Z and Y (T3 derived from T0, T1 and T2) generates and passes verification, with all four heads at 100/100 layers. The geometry and the pipeline are fine; the current offsets are what block it.

**Own uploads are not changed.** If the same file is uploaded as the user's own model, the right model is **not** replaced. It is refused with `DUAL_NOT_COPYABLE`, and the region that differs is marked.

Tolerances were not loosened, the profile was not changed and no recalibration was requested. The internal reference was only used for comparison: right paths = left paths shifted, layer 4+ in COPY, idle heads parked.

---


Two copies of a two-colour model. Each head always has the same job:

| Head | Model | Colour | Layers 1–3 | From layer 4 |
|---|---|---|---|---|
| T0 | left | 1 | alone | with T1 (pair A, T0 primary) |
| T1 | right | 1 | alone | with T0 (T1 in COPY, extruder synchronised) |
| T2 | left | 2 | alone | with T3 (pair B, T2 primary) |
| T3 | right | 2 | alone | with T2 (T3 in COPY, extruder synchronised) |

From layer 4, every layer goes like this: T0 + T1 print colour 1 on both models, retract, unsync, park. Then T2 + T3 print colour 2 of the same layer.

## Sequence

1. Start-up as in Farm/Copy: home, runtime mesh cleared (the measured mesh is built into Z), bed heated. There is no initial "park everything" move, so no carriage drives to an endstop and straight back.
2. Layers 1–3: T0, T1, T2, T3 each print their own region alone, on their own measured mesh plus their own four offsets. Each layer blends from the head's own bed height to the pair's common height. Before its first print, every head lays its own straight front prime line, and the prime line is followed directly by that head's first print (the verifier checks this).
3. Every turn starts with the head's own 10 × 10 mm solid tower layer (outer wall 16.7 mm/s, infill 100 mm/s, alternating direction, E from the flow area). After that comes the slice's wipe while retracting, a lift, the travel, and the deretract on the model.
4. Carriages go straight from one arrangement to the next. A waiting head only moves away from the printing head(s). The planner never moves it "right, then back left". The verifier enforces this (`CARRIAGE_DETOUR`).
5. Preheat is time-based. Each waiting head drops to standby (slice `standby_temperature_delta`). Its `M104` print temperature goes in at the point where the estimated remaining motion time before its `M109` reaches the slice `preheat_time` (17 s). It never goes before the head's own standby command. `M109` always waits before any extrusion.
6. Nothing comes from an old mesh or from old printer coordinates. Height and E are worked out from the uploaded profile's current mesh and its four offsets.

## Why a job can be refused (shown in the preview with numbers)

Each pair prints both models with **one** Z and **one** Y. On the left model, the T0 and T2 regions meet when Z_B − Z_A = oz2 − oz0. On the right model, the T1 and T3 regions meet when Z_B − Z_A = oz3 − oz1. If these two differ, the best split leaves a residual r = |(oz2−oz0) − (oz3−oz1)| / 2 at every colour boundary. Y works the same way: the two colours end up shifted against each other by r_y. X is always exact, because each pair has its own carriage spacing.

**Current profile (from the review ZIP):**

* Z: T2−T0 = +0.1063 mm and T3−T1 = −0.1312 mm, so a layer that changes colour comes out 0.081–0.319 mm thick. The allowed range is 0.080–0.300 mm, so this is **refused**.
* Y: T2−T0 = −0.0500 mm and T3−T1 = +0.3688 mm, so the colours are shifted 0.209 mm against each other. The allowed shift is 0.100 mm, so this is **refused**.

**Two-Color Fidgets example:** the right model's colour painting differs from the left model's on layers 1–33, around x 86.0–94.8, y 89.0–93.2 (left model coordinates). COPY makes the right head repeat the left head's paths, so this file cannot be printed as a copy (`DUAL_NOT_COPYABLE`). The preview marks the area with a dashed box.

Both refusals are listed with their reasons, the layout preview is still shown, the generate button is hidden, and download stays blocked (403).

To check that the verifier sees the real effect, a file was generated anyway with the current profile (forced, test only). The verifier rejects it with `COPY_THICKNESS` (worst −0.119 mm, which is the predicted residual) and `DUAL_REGISTRATION` (0.209 mm on both models).

## Results

| Test | Result |
|---|---|
| Synthetic two-colour input, 100 layers, 4 tools, test-only consistent offsets (CLI generate → independent verify) | PASS. Regions reproduced on T0 100/100, T1 100/100, T2 100/100 and T3 100/100 layers. Registration 0.000 / 0.000 mm. Synchronised from layer 4. First-layer error 5 µm. Layer thickness from layer 4 within 0.17 µm of nominal. Towers ≤ 10 mm with thickness error 0.19 µm. Towers 5.5 mm from the models. Pair spacing 61.25 mm. Final park and heaters off. |
| Phone: Two-Color Fidgets example + current profile | Refused with reasons and preview: mapping table, region paths for all four heads, dashed difference box, Z and Y reasons, no generate button. |
| Phone: own 4-tool upload + test-only consistent profile | Generate, then verify (PASS, T0–T3 100/100), then download. The downloaded SHA-256 equals the verified one. |
| Mutation: wrong head pair (T2 + T1 instead of T2 + T3 on layer 50) | Rejected: `DUAL_PAIR`, `DUAL_REGION`, `COLD_EXTRUDE`, `QPM_CLEARANCE`, … |
| Mutation: colour-2 region missing on layer 50 | Rejected: `DUAL_REGION` (T2/T3 layer 50), `VISIT_MISMATCH`, `COPY_INCOMPLETE` |
| Mutation: parked T2 extruder synchronised during pair A | Rejected: `OUT_PARKED_EXTRUDE`, `DUAL_PAIR` |
| Mutation: idle T3 moved towards T2 | Rejected: `CARRIAGE_DETOUR` |
| Copy preheat, long layers (60 × 50 mm box) | `M104 T1` 17 s ahead, inside T0's layer 5 |
| Copy preheat, short layers (24 × 18 mm box) | `M104 T1` 17 s ahead, already in T0's layer 2 (several layers back) |
| Copy preheat, very short wait (8 × 6 mm) | The whole wait is only 16.3 s, so heating starts at standby and `M109` waits for the rest. No cold extrusion. |
| OrcaCube Copy output | Only change: the old fixed "preheat while T0 prints layer 5" line is replaced by `M104 T1 S220 ; preheat 17 s ahead`. |
| Farm 40 Sharks | Byte-identical to the previous commit (SHA-256 9e113ffd…). |
| Automated tests | 121 OK after round 2 (`tests/test_dual.py` 26 tests, web tests updated). |

### Verifier changes found during this work

* **Preheat placement** was off by one line: the lead could come out up to one move short of `preheat_time`. Fixed and covered by tests.
* **Copy key rounding (verifier false positive, also present in milestone 3):** the same physical copy could fall on two sides of a 0.01 mm rounding step. Its own material then looked like "another copy" (`TRANSFER_CLEARANCE`, 40 × 30 mm box). Keys are now snapped to a known placement within 0.02 mm. There is a regression test for this.

## What the Dual verifier checks (independent of the plan)

* Each nozzle's deposits are rasterised per layer (0.25 mm cells). They must match **that nozzle's own** colour region of the slice, placed where the deposits themselves say it is. Up to 0.5 % may be missing or extra per layer.
* Registration of the two colours of each model is ≤ 0.10 mm.
* Pair membership is taken from carriage modes and extruder sync, not from comments: layers 1–3 must be [T0], [T1], [T2], [T3], and every layer from 4 must be [T0, T1] then [T2, T3].
* Layer thickness is measured per **model** (both colours together), so a colour boundary at the wrong height is caught.
* Towers: one per head per layer, printed before the model region, ≤ 10 mm square, wall and infill speeds as above, thickness on the head's own tower, and kept away from the models.
* Prime lines: one per head, directly followed by that head's own first print, in front of everything.
* Waiting heads never move towards the printing heads. A parked extruder never moves. All existing Farm and Copy checks still apply: temperatures, retraction, mesh state, limits, final state.

Software checks passed is a statement about the generated file only. It is not a print on hardware.
