# Dual Color Copy Mode (milestone 4): results

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
| Automated tests | 115 OK (21 new in `tests/test_dual.py`, web tests updated). |

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
