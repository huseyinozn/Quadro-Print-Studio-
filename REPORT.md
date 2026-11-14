# Farm Mode milestone 1 — review report v2 (after review 02)

**Status: CANDIDATE.** The 40-shark file was generated from the slice and the printer profile only, then checked by an independent simulator. All software checks pass (0 errors). It is **not validated on hardware** and two physical uncertainties need a decision before any print (section 6).

| | |
|---|---|
| Candidate | `candidate/CANDIDATE_v2_farm40_shark_software-verified_NOT-hardware-validated.gcode` (1054084 lines, sha256 `9e113ffdfba15c8c…`) |
| Copies | 40 found, 40 complete over all 83 layers, 10 COPY groups |
| Generation | 21.6 s, inputs opened: `candidate/CANDIDATE_v2_farm40_shark_software-verified_NOT-hardware-validated.gcode.partial`, `gen_inputs/profile.zip`, `gen_inputs/single_shark.gcode`, `results/generate.json`; oracle opened: **False**; rerun byte-identical: **True** |
| Verification | 118.5 s (candidate + oracle simulation + comparison) |
| Tests | QUADRO_FIXTURES=<fixtures> python3 -m unittest discover -s tests -v — Ran 70 tests in 17.521s OK |
| Negative cases | 10/10 refused with the expected diagnostic and no G-code written |
| Verifier mutation cases (real candidate) | 4/4 rejected with the expected diagnostic |
| Regeneration cases | 3/3 generated and independently verified |
| Motion-only time estimate | 2.00 h (no accel, no heat-up; rough) |
| Whole acceptance run | 559 s |

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
| Final state | heaters off True, fans off True, carriages parked at endstop positions True, T0 PRIMARY/others INACTIVE True, extruders unsynced True, mesh `loaded:default` | |

## 3. Comparison with the reviewed 40-shark oracle (after generation, verify stage only)

Same simulator, same slicer alignment. 3136640 aligned moves compared.

* Placements: 40/40 matched, max difference **0.0000 mm**. Visit order per head identical: **True**. Feedrate mismatches: 0.
* Z at every aligned slicer endpoint: max |Δ| **0.00037 mm** (tolerance 0.001), worst at head T0 layer 18 slicer line 32076.
* E in layer 1 and layers ≥6: identical within 2.6e-05 (rounding).

### 3a. Foundation E — historical rule verified (parity question, not a production rule)

Re-computed here from the supplied old mesh (141253) and old generated commands, without using them in generation: 190520 positive deposition pieces on layers 2–5 of the four heads aligned (XY mismatches: 0). For each piece at its end point `E_printed = round(E_old × h_new / h_old, 8)` reproduces the printed reference exactly for 190519 pieces and within one unit of the 8th decimal for 1 (max |Δ| 1.0e-08 mm; the remaining unit comes from evaluating h in double precision). Old E itself is reproduced by slicer E apportioned over ≤ 4 mm pieces × A(h_old,w)/A(0.2,w) at the piece end point within 3.7e-05 mm (0.047 %). With the remesh ratio this two-step rule gives **0.1606971 mm** for T1 slot 8 layer 3 line 4384 (printed 0.160697).

So the printed file is the *flow-area rule at the old mesh, linearly rescaled to the new mesh*. The candidate applies the flow-area rule directly at the current mesh. That is the production method (a user supplies one current profile; the remesh needs a previous file and mesh). The parity difference is therefore explained: head **T1**, layer **3**, slicer line **4384**: oracle 0.160697 mm, candidate 0.167885 mm (+0.007188 mm, +4.47 %); per head the candidate/oracle ratio − 1 is T0 -1.22..+3.79 %; T1 -1.73..+4.47 %; T2 -1.37..+3.60 %; T3 -3.67..+3.82 %.

### 3b. Foundation E — candidate versus the direct specification (independent of parity)

Spec: E = slicer E × piece share × A(h,w)/A(0.2,w), with h re-measured by the verifier from the output itself (plane fit of the previous layer of the same copy). Tolerance per move 1.7 % (derived from the thickness estimator, not from the oracle).

| Layer/head | moves checked | E actual (mm) | E spec (mm) | signed error (mm) | abs error (mm) | max rel | min rel |
|---|---|---|---|---|---|---|---|
| L2/T0 | 7342 | 170.0990 | 170.0979 | +0.00106 | 0.01766 | +0.273 % | -0.148 % |
| L2/T1 | 7220 | 119.5164 | 119.5178 | -0.00145 | 0.01502 | +0.409 % | -0.143 % |
| L2/T2 | 7410 | 123.9089 | 123.9079 | +0.00094 | 0.01406 | +0.374 % | -0.261 % |
| L2/T3 | 7354 | 148.8363 | 148.8362 | +0.00011 | 0.02538 | +0.458 % | -0.478 % |
| L3/T0 | 8005 | 147.1596 | 147.1594 | +0.00012 | 0.00959 | +0.217 % | -0.105 % |
| L3/T1 | 8061 | 127.4043 | 127.4000 | +0.00439 | 0.01719 | +0.231 % | -0.187 % |
| L3/T2 | 8080 | 113.2315 | 113.2309 | +0.00062 | 0.01068 | +0.255 % | -0.262 % |
| L3/T3 | 8099 | 147.3823 | 147.3805 | +0.00172 | 0.03377 | +0.583 % | -0.317 % |
| L4/T0 | 7936 | 149.2027 | 149.2030 | -0.00026 | 0.00955 | +0.237 % | -0.212 % |
| L4/T1 | 7941 | 108.3863 | 108.3869 | -0.00055 | 0.00930 | +0.254 % | -0.271 % |
| L4/T2 | 7915 | 112.0673 | 112.0676 | -0.00032 | 0.00985 | +0.292 % | -0.218 % |
| L4/T3 | 7888 | 131.5081 | 131.5086 | -0.00047 | 0.01289 | +0.377 % | -0.665 % |
| L5/T0 | 8013 | 138.1638 | 138.1647 | -0.00084 | 0.00836 | +0.310 % | -0.418 % |
| L5/T1 | 7976 | 102.8184 | 102.8194 | -0.00099 | 0.00911 | +0.426 % | -0.543 % |
| L5/T2 | 8085 | 104.1186 | 104.1197 | -0.00112 | 0.00871 | +0.352 % | -0.583 % |
| L5/T3 | 7984 | 123.0684 | 123.0688 | -0.00046 | 0.01003 | +0.328 % | -0.375 % |

For comparison, the oracle measured against the same direct spec: max 4.20 % (3565 moves above 1.7 %) — expected, because it follows the historical remesh rule (3a).

Intentional differences (candidate vs oracle):

1. **Prime lines.** Oracle: four 100 mm lines at y = [1.25, 2.25, 3.25, 4.25], 1.0 mm apart, est. width 1.80 mm → 3 overlapping pairs (0.80 mm overlap; each head prints over the previous line). Candidate: same amount (E15/100 mm, from the profile's `start_print`), two lanes y = 1.00 / 3.51, left/right halves, no overlap, 1.93 mm from the first copies.
2. **End-of-part wipe.** The oracle replaces the slicer's wipe after each part (3320 head-visits) by a stationary 0.4 mm retract; the candidate keeps the slicer wipe (same total retraction).
3. **Transfer height.** Oracle: z_layer + 1.2 mm; candidate: highest deposit + head z-offset + 1.0 mm. Minimum measured clearance oracle 1.029 mm, candidate 1.000 mm.
4. **Layer-1 transfers** use the slice's `initial_layer_travel_acceleration` (1000) instead of 7000; park/descent heights differ slightly.
5. Pieces are bisected where the mesh is curved so straight moves follow the surface within 1 µm (1001151 pieces for 953570 model moves); one zero-length `G1 … E0` no-op of the slice is dropped.

## 4. Transitions and parking

Every final position is called *parked* (at the endstop positions), not homed: no homing happens at the end.

| Line | Phase | T0 | T1 | T2 | T3 | Z | zero-margin pairs at target |
|---|---|---|---|---|---|---|---|
| 25 | park (endstop positions) | -169.00 | -112.75 | 412.75 | 469.00 | 8.00 | T0/T1, T2/T3 |
| 45 | arrange T0 | 10.00 | 356.50 | 412.75 | 469.00 | 8.00 | T1/T2, T2/T3 |
| 66728 | park after T0 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 | T0/T1, T2/T3 |
| 66737 | arrange T1 | -169.00 | 190.06 | 412.75 | 469.00 | 8.00 | T2/T3 |
| 133376 | park after T1 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 | T0/T1, T2/T3 |
| 133385 | arrange T2 | -169.00 | -112.75 | 8.45 | 469.00 | 8.00 | T0/T1 |
| 199971 | park after T2 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 | T0/T1, T2/T3 |
| 199980 | arrange T3 | -169.00 | -112.75 | -56.50 | 188.26 | 8.00 | T0/T1, T1/T2 |
| 266869 | park after T3 | -169.00 | -112.75 | 412.75 | 469.00 | 6.20 | T0/T1, T2/T3 |
| 266883 | COPY entry | 63.01 | 135.07 | 205.46 | 277.27 | 6.20 | - |
| 1054075 | final park | -169.00 | -112.75 | 412.75 | 469.00 | 21.79 | T0/T1, T2/T3 |

**Parked-head discrepancy (your observation: during the T3 foundation T0 looked correctly parked, T1 was right of −112.75 and T2 immediately right of T1).** Commanded state in both the oracle and the candidate during that phase: T0 −169.00, T1 −112.75, T2 −56.50, i.e. T1/T2 commanded exactly at the 56.25 mm minimum clearance — **0.000 mm margin** (T0/T1 also 0 by the endstop positions). The verifier proves only *commanded* non-overlap along every modelled path (min margin on all parallel-move paths 0.0 mm). It cannot show when or why T1 was displaced, nor that the T2 right-then-left detour caused it; that needs physical confirmation (e.g. checking T1 position before/after the T3-arrange move). With zero commanded margin any displacement of T1 to the right means physical contact with T2. Option added, not default (parity kept): `--park-margin M` keeps stacked parks M mm above the minimum clearance (e.g. T2 at −56.5 + M during the T3 run).

**T2 detour (kept for parity, not redesigned):** after its foundation T2 is parked right at 412.75 and then sent left to −56.5 for the T3 run: path 206.976 → 412.75 → -56.5, 411.6 mm of extra carriage travel versus a direct move. Commanded gaps stay ≥ 56.25 mm along the whole path (affine non-ARRIVING branch, checked at 21 points).

Cleanup after the last extrusion (exact commands): `SET_FAN_SPEED FAN=fan_t0 SPEED=0; SET_FAN_SPEED FAN=fan_t1 SPEED=0; SET_FAN_SPEED FAN=fan_t2 SPEED=0; SET_FAN_SPEED FAN=fan_t3 SPEED=0; M400; SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder; SYNC_EXTRUDER_MOTION EXTRUDER=extruder1 MOTION_QUEUE=extruder1; SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=extruder2; SYNC_EXTRUDER_MOTION EXTRUDER=extruder3 MOTION_QUEUE=extruder3; SET_DUAL_CARRIAGE CARRIAGE=carriage_t0 MODE=PRIMARY; ACTIVATE_EXTRUDER EXTRUDER=extruder; SET_GCODE_OFFSET X=0 Y=0 Z=0 MOVE=0; G90; M83; SET_DUAL_CARRIAGE CARRIAGE=carriage_t1 MODE=INACTIVE; SET_DUAL_CARRIAGE CARRIAGE=carriage_t2 MODE=INACTIVE; SET_DUAL_CARRIAGE CARRIAGE=carriage_t3 MODE=INACTIVE; M400; SET_VELOCITY_LIMIT ACCEL=7000 ACCEL_TO_DECEL=3500; QUADRO_PARALLEL_MOVE T0=-169.00000 T1=-112.75000 T2=412.75000 T3=469.00000 SPEED=350 MIN_CLEARANCE=56.25; M400; TURN_OFF_HEATERS; SET_FAN_SPEED FAN=fan_t0 SPEED=0; SET_FAN_SPEED FAN=fan_t1 SPEED=0; SET_FAN_SPEED FAN=fan_t2 SPEED=0; SET_FAN_SPEED FAN=fan_t3 SPEED=0; BED_MESH_PROFILE LOAD=default; M82; M117 Farm Mode complete`. Motors are left enabled (no `M84`), as in the oracle.

## 5. Hardware assumptions (clean statement)

Software-verified means: the commanded motion satisfies every check in `CHECKS.md` under the machine model below. Nothing has been run on hardware.

1. Measured inputs taken as true: the saved mesh (`default`), tool offsets (bed_pin_offsets.json), carriage/Y/Z limits, min clearance 56.25 mm, velocity/acceleration limits, extruder limits from the profile.
2. `_PRINT_HOME TEMP BED_TEMP` homes all axes, probes Z at (150,150) (mesh value there 0), leaves T0 primary/active at X150 with zero G-code offset, T1 at 356.5 and T2/T3 at their endstops (`QUADRO_DISTRIBUTE_X`). Not executed offline.
3. `QUADRO_PARALLEL_MOVE` without ARRIVING/Y/Z behaves as the supplied firmware's `_execute_parallel_move`: largest-displacement carriage driven, others scaled (COPY/MIRROR) → all carriages affine in one progress variable; other profiles are rejected.
4. `SET_DUAL_CARRIAGE … MODE=COPY` copies the primary's X motion with the carriage offset at that moment; `SYNC_EXTRUDER_MOTION` keeps each extruder's rotation distance and pressure advance.
5. `M109`/`M190` macros wait for temperature. T1–T3 start primed (loaded filament, not retracted).
6. Carriages stay where commanded (no missed steps, no external displacement) — **contradicted once by your observation** (section 4).
7. The bed is as measured, including the deviating point (section 6).

Validated scope: reference layout preset (reference-300, 40 copies) compared against the reviewed oracle; other counts/models are verified by the same software checks but have no physical print evidence.

## 6. Physical uncertainties that need your decision

1. **Mesh points that deviate markedly from their neighbours** (evaluated on this printer's mesh, threshold 0.25 mm; measured values used unchanged, never replaced):
   * (258.75, 295.00) = -0.9387 mm, -0.523 mm vs. neighbour median -0.4156 mm. Your note: this dip was measured twice (−0.956875, then −0.938750); its physical depth is not independently confirmed. In the supplied older mesh (141253) the same probe point read −0.463125 mm and no point deviated. Copies depending on it (> 0.01 mm):
     * T0/slot8 — first layer up to 0.000 mm, foundation top up to 0.137 mm: first-layer height depends on this point by up to 0.000 mm
     * T0/slot9 — first layer up to 0.000 mm, foundation top up to 0.152 mm: first-layer height depends on this point by up to 0.000 mm
     * T1/slot8 — first layer up to 0.000 mm, foundation top up to 0.137 mm: first-layer height depends on this point by up to 0.000 mm
     * T1/slot9 — first layer up to 0.000 mm, foundation top up to 0.152 mm: first-layer height depends on this point by up to 0.000 mm
     * T2/slot8 — first layer up to 0.000 mm, foundation top up to 0.137 mm: first-layer height depends on this point by up to 0.000 mm
     * T2/slot9 — first layer up to 0.011 mm, foundation top up to 0.152 mm: first-layer height depends on this point by up to 0.011 mm
     * T3/slot8 — first layer up to 0.274 mm, foundation top up to 0.137 mm: first-layer nozzle height depends on this point by up to 0.274 mm (nominal first layer 0.20 mm); if the bed there is not as low as measured, the nozzle would touch the bed
     * T3/slot9 — first layer up to 0.302 mm, foundation top up to 0.152 mm: first-layer nozzle height depends on this point by up to 0.302 mm (nominal first layer 0.20 mm); if the bed there is not as low as measured, the nozzle would touch the bed
   → Kept as measured. Before printing: confirm the bed shape at this point (or exclude those copies with a smaller count/layout).
2. **Profile freshness cannot be proven** from the archive (all entries carry the same timestamp). Mesh 2026-11-13 14:37 and offsets from the Nov 12 snapshot are assumed current.
3. Prime lines lie at y < 5 mm, outside the measured mesh (edge row is used, as Klipper does).

## 7. Negative and regeneration cases

Verifier mutation cases on the real candidate (must be rejected):

| Case | Exit | Diagnostics | Rejected as expected |
|---|---|---|---|
| missing_acceleration | 3 | ACCELERATION, VISIT_MISMATCH | True |
| changed_pressure_advance_T2 | 3 | PRESSURE_ADVANCE | True |
| narrow_gap_changed_park_T2 | 3 | CARRIAGE_CLEARANCE, QPM_CLEARANCE | True |
| unsupported_arriving_profile | 3 | QPM_UNSUPPORTED_PROFILE | True |

Adversarial unit tests additionally mutate a synthetic output (missing/changed/over-limit acceleration, missing/changed pressure advance, narrowed and crossing park trajectories, ARRIVING and Y profiles, lowered transfer, removed or duplicated model moves, first layer too low, changed feedrate, missing heater shutdown / final park / unsync, over-temperature, cold extrusion, unknown command, stacked runtime mesh) and the planner (extreme offsets, steep mesh, implausible offsets, negative park margin).

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
python3 tools/run_acceptance.py --fixtures <folder with single_shark.gcode, profile.zip, reference_40.gcode> --historical <folder with historical_foundations.txt, old_mesh.cfg> --out <out>
python3 tools/write_report.py --acceptance <out> --out REPORT.md
```

Commands and timings of this run: `results/acceptance_run.json`; machine-readable checks: `results/validation.json`; layout: `results/plan.json`, `results/preview.svg`/`.png`.

## 9. Open questions

1. Can T1's position be checked physically before and after the T3-arrange move (line of 'arrange T3' in section 4)? Should `--park-margin` (e.g. 2 mm) become the default once confirmed?
2. Is the dip at the deviating mesh point physically confirmed? Until then the dependent copies stay flagged.
3. Keep the direct flow-area method for foundation E (production), with the historical remesh only as a parity explanation?
4. Keep the T2 right-then-left park for parity, or allow a direct T2 move in milestone 2?
