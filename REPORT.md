# Farm Mode milestone 1 — review report

**Status: CANDIDATE.** The 40-shark file was generated from the slice and the printer profile only, then checked by an independent simulator. All software checks pass (0 errors). It is **not validated on hardware** and two physical uncertainties need a decision before any print (section 6).

| | |
|---|---|
| Candidate | `candidate/CANDIDATE_farm40_shark_software-verified_NOT-hardware-validated.gcode` (1054084 lines, sha256 `1794069f8bc11e5c…`) |
| Copies | 40 found, 40 complete over all 83 layers, 10 COPY groups |
| Generation | 21.6 s, inputs opened: `candidate/CANDIDATE_farm40_shark_software-verified_NOT-hardware-validated.gcode.partial`, `gen_inputs/profile.zip`, `gen_inputs/single_shark.gcode`, `results/generate.json`; oracle opened: **False**; rerun byte-identical: **True** |
| Verification | 108.6 s (candidate + oracle simulation + comparison) |
| Tests | QUADRO_FIXTURES=<fixtures> python3 -m unittest discover -s tests -v — Ran 39 tests in 14.460s OK |
| Negative cases | 10/10 refused with the expected diagnostic and no G-code written |
| Regeneration cases | 3/3 generated and independently verified |
| Motion-only time estimate | 2.00 h (no accel, no heat-up; rough) |
| Whole acceptance run | 298 s |

## 1. Reconstructed model (from the profile and measured data)

* **Sign convention** (established from the oracle's purge lines and layer-1 Z, then used without the oracle): commanded X/Y = physical + offset; commanded Z = physical nozzle height + z offset. Carriage = commanded X with zero G-code offset. In COPY mode head t sits at T0 + (72·t, −oy_t) physically; its carriage at T0 + 72·t + ox_t (offsets T1 +72.0625, T2 +142.45, T3 +214.2625).
* **Mesh**: Klipper `bed_mesh` bicubic upsampling (pps 2, tension 0.2) + bilinear lookup, clamped outside, evaluated at each head's physical nozzle XY. Re-implemented from the firmware reference; reproduces the oracle's purge-line Z to 1e-6 mm.
* **Foundation (layers 1–5, one head after the other)**: S_t = mesh(head t XY) + oz_t. Layer 1 at z1 + S_t (true 0.20 mm on every head); common top Zc = z5 + (max_t S_t + min_t S_t)/2 at each copy point; layers 2–5 blend linearly to Zc. Local thickness measured on all copies: 0.129–0.271 mm (limits 0.08–0.30). E is scaled by the Slic3r flow cross-section A(h,w)/A(0.2,w) at constant line width. Layers ≥6: one commanded Z for all heads, z_L + (Zc − z5), nominal 0.20 mm everywhere.
* Compensation is embedded; the runtime mesh is cleared (`BED_MESH_CLEAR`) and only reloaded after the print — never stacked.
* Moves are split so the straight pieces follow the surface within 1 µm (≤ 4 mm pieces, bisected where curved). F, accelerations, wipes, retractions, z-hops and modal commands of the slice are kept per copy; the slicer's own start/purge/end and layer approaches are replaced by the generator's verified transfer (lift ≥ 1.0 mm above all deposits, travel, descend, prime the exact retracted amount).

## 2. Independent verification of the candidate (tolerances fixed beforehand)

| Check | Result | Tolerance |
|---|---|---|
| Every model move of every copy/layer reproduced (XY on slicer path) | 3320 visits, 0 failed | 0.002 mm |
| E per move where no compensation applies (L1, L≥6) | exact | 2.0e-03 rel |
| F per move | 0 mismatches | exact |
| Layer-1 nozzle height above measured bed (42322 points) | max dev -0.000005 mm | 0.0015 mm |
| Foundation E vs A(h_measured,w) (125309 moves) | max -0.66 % | 1.7 % (from thickness estimator, not from the oracle) |
| Measured local thickness, all layers (656332 samples) | 0.1290–0.2709 mm | foundation 0.08–0.30 |
| COPY layers thickness vs 0.20 | max dev 0.0020 mm | 0.004 mm |
| Foundation top: one commanded Z for the 4 heads of a group | spread 0.000000 mm | 0.0002 mm |
| Transfer clearance above any deposit (sampled 1 mm) | min 1.000 mm | ≥ 0.5 mm |
| Parallel-park sweeps above deposits | min 5.227 mm | ≥ 0.5 mm |
| In-part travel never below own completed layers | min margin 0.110 mm | ≥ −0.03 mm |
| Carriage limits / adjacent carriage gap (every move + park) | min gap 56.250 mm | ≥ 56.25 mm |
| Min edge gap between deposited footprints | 2.921 mm | ≥ 2.0 mm |
| Retraction balance at every extrusion, heater waited, mesh cleared | 0 violations | 0.0001 mm |
| Foundation order | heads [0, 1, 2, 3], layer-major, serpentine slots | independent |
| First print after each purge | T0 0.200 mm above bed, T1 0.200 mm above bed, T2 0.200 mm above bed, T3 0.200 mm above bed | primed, heated |
| First print after COPY entry | all 4 heads primed (retract level [0.0]), heated | |
| Final state | heaters off True, fans off True, carriages home True, T0 PRIMARY/others INACTIVE True, extruders unsynced True, mesh `loaded:default` | |

## 3. Comparison with the reviewed 40-shark oracle (after generation, verify stage only)

Same simulator, same slicer alignment. 3136640 aligned moves compared.

* Placements: 40/40 matched, max difference **0.0000 mm**. Visit order per head identical: **True**. Feedrate mismatches: 0.
* Z at every aligned slicer endpoint: max |Δ| **0.00037 mm** (tolerance 0.001), worst at head T0 layer 18 slicer line 32076.
* E in layer 1 and layers ≥6: identical within 2.6e-05 (rounding).
* **Open finding — foundation E (layers 2–5).** No tolerance is applied to this difference: the oracle's foundation E rule could not be recovered, so neither file is accepted as the reference for it. Per head, candidate/oracle − 1: T0 -1.22 %..+3.79 % (2651 of 41800 moves >1 %, 857 >2 %); T1 -1.73 %..+4.47 % (2707 of 41800 moves >1 %, 484 >2 %); T2 -1.37 %..+3.60 % (1956 of 41800 moves >1 %, 371 >2 %); T3 -3.67 %..+3.82 % (4436 of 41800 moves >1 %, 1338 >2 %).
  * Largest difference: head **T1**, layer **3**, slicer line **4384** (Internal solid infill, width 0.420 mm, slicer E 0.22450 mm): oracle **0.160697 mm**, candidate **0.167885 mm** (candidate − oracle +0.007188 mm, +4.47 %). Commanded nozzle Z there: candidate 0.20022, oracle 0.20022.
  * Same Z in both files, so the difference is only the E rule. The candidate follows A(h,w)/A(0.2,w) at the local thickness (independently re-measured: max 0.66 %, tolerance 1.7 % derived from the thickness estimator). The oracle deviates from that rule by up to 4.20 %. Cause **not verified — open finding**. Rules tested on the largest-difference move without reproducing the oracle value: flow-area or linear thickness ratio at piece midpoints, at piece end points or start points, and the surface of another head. The oracle's implied linear thickness there is 0.1432 mm while both files command the same Z.

Intentional differences (candidate vs oracle):

1. **Prime lines.** Oracle: four 100 mm lines at y = [1.25, 2.25, 3.25, 4.25], 1.0 mm apart, est. width 1.80 mm → 3 overlapping pairs (0.80 mm overlap; each head prints over the previous line). Candidate: same amount (E15/100 mm, from the profile's `start_print`), two lanes y = 1.00 / 3.51, left/right halves, no overlap, 1.93 mm from the first copies.
2. **End-of-part wipe.** The oracle replaces the slicer's wipe after each part (3320 head-visits) by a stationary 0.4 mm retract; the candidate keeps the slicer wipe (same total retraction).
3. **Transfer height.** Oracle: z_layer + 1.2 mm; candidate: highest deposit + head z-offset + 1.0 mm. Minimum measured clearance oracle 1.029 mm, candidate 1.000 mm.
4. **Layer-1 transfers** use the slice's `initial_layer_travel_acceleration` (1000) instead of 7000; park/descent heights differ slightly.
5. Pieces are bisected where the mesh is curved so straight moves follow the surface within 1 µm (1001151 pieces for 953570 model moves); one zero-length `G1 … E0` no-op of the slice is dropped.

## 4. Transitions and parking

| Line | Phase | T0 | T1 | T2 | T3 | Z |
|---|---|---|---|---|---|---|
| 25 | home | -169.00 | -112.75 | 412.75 | 469.00 | 8.00 |
| 45 | arrange T0 | 10.00 | 356.50 | 412.75 | 469.00 | 8.00 |
| 66728 | home after T0 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 |
| 66737 | arrange T1 | -169.00 | 190.06 | 412.75 | 469.00 | 8.00 |
| 133376 | home after T1 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 |
| 133385 | arrange T2 | -169.00 | -112.75 | 8.45 | 469.00 | 8.00 |
| 199971 | home after T2 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 |
| 199980 | arrange T3 | -169.00 | -112.75 | -56.50 | 188.26 | 8.00 |
| 266869 | home after T3 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 |
| 266883 | COPY entry | 63.01 | 135.07 | 205.46 | 277.27 | 6.20 |
| 1054075 | final home | -169.00 | -112.75 | 412.75 | 469.00 | 21.79 |

**Known T2 detour (kept for parity, not redesigned):** after its foundation T2 parks right at 412.75 and is then sent left to −56.5 so T3 can work: path 206.976 → 412.75 → -56.5, 411.6 mm of extra carriage travel versus a direct move. Both moves are inside limits and keep ≥ 56.25 mm to the neighbours at start and end (affine parallel move), so it is inefficient, not hazardous. The oracle has the same pattern. Other reversals are by design: T1 parks right (356.5) while T0 prints and returns home afterwards; T0/T2/T3 leave their homes to reach their zones.

While a head prints its foundation the others are off the bed (T0 run: T1 356.5, T2 412.75, T3 469; T3 run: T2 −56.5). If the 'parked-head discrepancy' you saw is one of these positions, the table above shows exactly where every carriage is.

## 5. What is measured, assumed and physically unverified

* **Measured inputs (profile)**: saved mesh `default` 9×9, tool offsets (bed_pin_offsets.json), carriage limits, Y/Z ranges, min clearance 56.25 (quadro_parallel_carriage), extruder rotation distances (kept per extruder in COPY via `SYNC_EXTRUDER_MOTION`).
* **Assumed macro/firmware behaviour** (not executable offline): `_PRINT_HOME` homes, probes Z at (150,150) where the mesh is 0, leaves T0 primary at X150 with T1 at 356.5 (homing's `QUADRO_DISTRIBUTE_X`); `QUADRO_PARALLEL_MOVE` is affine; COPY copies the primary X motion with the current offset; `M109`/`M190` wait. Initial filament state of T1–T3 assumed primed.
* **Software-validated**: everything in sections 2–4 for this slice/profile; other counts/models only by the same checks (reference layout preset (reference-300, 40 copies) compared against the reviewed oracle; other counts/models are verified by the same software checks but have no physical print evidence).
* **Not physically validated**: no print, no motion on hardware. The oracle's successful print is evidence for its own file, not for this candidate's differences (prime lanes, wipes, transfer heights).

## 6. Physical uncertainties that need your decision

1. **Mesh points that deviate markedly from their neighbours** (this printer's mesh, threshold 0.25 mm; values used unchanged):
   * (258.75, 295.00) = -0.9387 mm, -0.523 mm vs. neighbour median -0.4156 mm. Copies depending on it (> 0.01 mm):
     * T0/slot8 — first layer up to 0.000 mm, foundation top up to 0.137 mm: first-layer height depends on this point by up to 0.000 mm
     * T0/slot9 — first layer up to 0.000 mm, foundation top up to 0.152 mm: first-layer height depends on this point by up to 0.000 mm
     * T1/slot8 — first layer up to 0.000 mm, foundation top up to 0.137 mm: first-layer height depends on this point by up to 0.000 mm
     * T1/slot9 — first layer up to 0.000 mm, foundation top up to 0.152 mm: first-layer height depends on this point by up to 0.000 mm
     * T2/slot8 — first layer up to 0.000 mm, foundation top up to 0.137 mm: first-layer height depends on this point by up to 0.000 mm
     * T2/slot9 — first layer up to 0.011 mm, foundation top up to 0.152 mm: first-layer height depends on this point by up to 0.011 mm
     * T3/slot8 — first layer up to 0.274 mm, foundation top up to 0.137 mm: first-layer nozzle height depends on this point by up to 0.274 mm (nominal first layer 0.20 mm); if the bed there is not as low as measured, the nozzle would touch the bed
     * T3/slot9 — first layer up to 0.302 mm, foundation top up to 0.152 mm: first-layer nozzle height depends on this point by up to 0.302 mm (nominal first layer 0.20 mm); if the bed there is not as low as measured, the nozzle would touch the bed
   → Please check the bed at these point(s) before printing; after a new mesh the file is regenerated.
2. **Profile freshness cannot be proven** from the archive (all entries carry the same timestamp). Mesh 2026-11-13 14:37 and offsets from the Nov 12 snapshot are assumed current.
3. Prime lines lie at y < 5 mm, outside the measured mesh (edge row is used, as Klipper does).

## 7. Negative and regeneration cases

| Case | Exit | G-code written | Diagnostics |
|---|---|---|---|
| missing_mesh | 2 | False | PROFILE_MESH_MISSING |
| missing_offsets | 2 | False | PROFILE_OFFSETS_MISSING |
| unsupported_gcode_arc | 2 | False | SLICE_UNSUPPORTED_GCODE |
| unsupported_second_tool | 2 | False | SLICE_UNSUPPORTED_TOOL |
| unknown_motion | 2 | False | SLICE_UNKNOWN_MOTION |
| unsafe_packing_col_pitch_30 | 2 | False | PLAN_COPY_GAP, PLAN_MOTION_ENVELOPE |
| unsafe_packing_row_pitch_54 | 2 | False | PLAN_COPY_GAP, PLAN_MOTION_ENVELOPE |
| carriage_clearance_sync_50 | 2 | False | PLAN_BED_BOUNDS, PLAN_COPY_CLEARANCE, PLAN_MESH_COVERAGE, PLAN_PURGE_LANES, PLAN_Y_LIMITS |
| count_not_multiple_of_4 | 2 | False | PLAN_COUNT |
| count_48_off_bed | 2 | False | PLAN_BED_BOUNDS, PLAN_MESH_COVERAGE, PLAN_PURGE_LANES, PLAN_Y_LIMITS |

| Regeneration | Generate/verify | Copies | Errors | Notes |
|---|---|---|---|---|
| count_16_shark | 0/0 | 16 | 0 |  |
| different_model_box_24 | 0/0 | 24 | 0 |  |
| modified_calibration_T1z_plus0.05_T2xy_plus0.2_0.1 | 0/0 | 40 | 0 | T1 layer-1 Z +0.050, T0 +0.000, T2 COPY carriage +0.200, output differs True |

## 8. Reproduce

```
python3 tools/run_acceptance.py --fixtures <folder with single_shark.gcode, profile.zip, reference_40.gcode> --out <out>
python3 tools/write_report.py --acceptance <out> --out REPORT.md
```

Commands and timings of this run: `results/acceptance_run.json`; machine-readable checks: `results/validation.json`; layout: `results/plan.json`, `results/preview.svg`/`.png`.

## 9. Open questions

1. Is the bed really as measured at (258.75, 295.00)? (decides T3/slot8, T3/slot9)
2. Which parked-head discrepancy did you observe on the oracle print (time/phase)? The park table above can be matched to it.
3. Keep the T2 right-then-left park for parity, or allow a direct T2 move in milestone 2?
4. OK to keep the slicer's end-of-part wipes and the non-overlapping prime lanes (differences 1–2 above)?
5. Foundation E: which rule did the reviewed file use for layers 2–5 (open finding in section 3)?
