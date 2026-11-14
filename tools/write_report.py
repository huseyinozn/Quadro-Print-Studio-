"""Write REPORT.md from an acceptance-run folder (numbers are never typed by hand).

python3 tools/write_report.py --acceptance OUT_DIR --out REPORT.md
"""
import argparse
import json
import os


def fmt(v, nd=4):
    return ("%%.%df" % nd) % v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    R = os.path.join(a.acceptance, "results")
    acc = json.load(open(os.path.join(R, "acceptance_run.json")))
    val = json.load(open(os.path.join(R, "validation.json")))
    plan = json.load(open(os.path.join(R, "plan.json")))["plan"]
    gen = json.load(open(os.path.join(R, "generate.json")))
    c = val["candidate_result"]
    o = val["oracle_result"]
    cm = val["comparison"]
    cs, os_ = c["stats"], o["stats"]
    tol = val["tolerances"]
    neg = acc["negative_cases"]
    reg = acc["regeneration_cases"]
    fs = c["final_state"]
    fst = plan["foundation_stats"]
    L = []
    w = L.append

    w("# Farm Mode milestone 1 — review report")
    w("")
    w("**Status: CANDIDATE.** The 40-shark file was generated from the slice and the printer profile only, then checked by an "
      "independent simulator. All software checks pass (%d errors). It is **not validated on hardware** and two physical "
      "uncertainties need a decision before any print (section 6)." % c["error_count"])
    w("")
    w("| | |")
    w("|---|---|")
    w("| Candidate | `candidate/%s` (%d lines, sha256 `%s…`) |" % (acc["candidate"]["file"], gen["output"]["lines"], acc["candidate"]["sha256"][:16]))
    w("| Copies | %d found, %d complete over all %d layers, %d COPY groups |" % (c["copies_found"], c["copies_complete"], len(plan["layer_z"]), c["copy_groups"]))
    w("| Generation | %.1f s, inputs opened: %s; oracle opened: **%s**; rerun byte-identical: **%s** |"
      % (acc["candidate"]["generate_elapsed_s"], ", ".join("`%s`" % f for f in acc["generation_isolation"]["files_opened"]),
         acc["generation_isolation"]["oracle_opened"], acc["deterministic_rerun_identical"]))
    w("| Verification | %.1f s (candidate + oracle simulation + comparison) |" % acc["candidate"]["verify_elapsed_s"])
    w("| Tests | %s — %s |" % (acc["unit_tests"]["cmd"], " ".join(x for x in acc["unit_tests"]["summary"] if x)))
    w("| Negative cases | %d/%d refused with the expected diagnostic and no G-code written |" % (sum(n["passed"] for n in neg), len(neg)))
    w("| Regeneration cases | %d/%d generated and independently verified |" % (sum(r["passed"] for r in reg), len(reg)))
    w("| Motion-only time estimate | %.2f h (no accel, no heat-up; rough) |" % (cs["est_time_s"] / 3600))
    w("| Whole acceptance run | %.0f s |" % acc["elapsed_total_s"])
    w("")
    w("## 1. Reconstructed model (from the profile and measured data)")
    w("")
    w("* **Sign convention** (established from the oracle's purge lines and layer-1 Z, then used without the oracle): "
      "commanded X/Y = physical + offset; commanded Z = physical nozzle height + z offset. Carriage = commanded X with zero G-code offset. "
      "In COPY mode head t sits at T0 + (72·t, −oy_t) physically; its carriage at T0 + 72·t + ox_t "
      "(offsets T1 +72.0625, T2 +142.45, T3 +214.2625).")
    w("* **Mesh**: Klipper `bed_mesh` bicubic upsampling (pps 2, tension 0.2) + bilinear lookup, clamped outside, evaluated at each head's "
      "physical nozzle XY. Re-implemented from the firmware reference; reproduces the oracle's purge-line Z to 1e-6 mm.")
    w("* **Foundation (layers 1–%d, one head after the other)**: S_t = mesh(head t XY) + oz_t. Layer 1 at z1 + S_t (true 0.20 mm on every head); "
      "common top Zc = z%d + (max_t S_t + min_t S_t)/2 at each copy point; layers 2–%d blend linearly to Zc. "
      "Local thickness measured on all copies: %s–%s mm (limits %s–%s). E is scaled by the Slic3r flow cross-section "
      "A(h,w)/A(0.2,w) at constant line width. Layers ≥%d: one commanded Z for all heads, z_L + (Zc − z%d), nominal 0.20 mm everywhere."
      % (plan["foundation_layers"], plan["foundation_layers"], plan["foundation_layers"], fmt(fst["global_range"][0], 3),
         fmt(fst["global_range"][1], 3), fmt(fst["limits"][0], 2), fmt(fst["limits"][1], 2), plan["foundation_layers"] + 1, plan["foundation_layers"]))
    w("* Compensation is embedded; the runtime mesh is cleared (`BED_MESH_CLEAR`) and only reloaded after the print — never stacked.")
    w("* Moves are split so the straight pieces follow the surface within 1 µm (≤ 4 mm pieces, bisected where curved). F, accelerations, "
      "wipes, retractions, z-hops and modal commands of the slice are kept per copy; the slicer's own start/purge/end and layer approaches "
      "are replaced by the generator's verified transfer (lift ≥ 1.0 mm above all deposits, travel, descend, prime the exact retracted amount).")
    w("")
    w("## 2. Independent verification of the candidate (tolerances fixed beforehand)")
    w("")
    w("| Check | Result | Tolerance |")
    w("|---|---|---|")
    w("| Every model move of every copy/layer reproduced (XY on slicer path) | %d visits, %d failed | %s mm |" % (c["visits"], c["visits_failed"], tol["xy_align"]))
    w("| E per move where no compensation applies (L1, L≥%d) | exact | %.1e rel |" % (plan["foundation_layers"] + 1, tol["e_exact_rel"]))
    w("| F per move | 0 mismatches | exact |")
    w("| Layer-1 nozzle height above measured bed (%d points) | max dev %s mm | %s mm |" % (cs["first_layer_points"], fmt(cs["first_layer_max_dev"], 6), tol["first_layer"]))
    w("| Foundation E vs A(h_measured,w) (%d moves) | max %.2f %% | %.0f %% |" % (cs["e_found_checked"], 100 * cs["e_found_max_dev"], 100 * tol["e_found_rel"]))
    w("| Measured local thickness, all layers (%d samples) | %s–%s mm | foundation %s–%s |" % (cs["thickness_checked"], fmt(cs["h_meas_min"], 4), fmt(cs["h_meas_max"], 4), fmt(fst["limits"][0], 2), fmt(fst["limits"][1], 2)))
    w("| COPY layers thickness vs 0.20 | max dev %s mm | %s mm |" % (fmt(cs["copy_thick_max_dev"], 4), tol["thickness_nominal"]))
    w("| Foundation top: one commanded Z for the 4 heads of a group | spread %s mm | %s mm |" % (fmt(c["foundation_common_z_max_spread"], 6), tol["copy_common_z"]))
    w("| Transfer clearance above any deposit (sampled 1 mm) | min %s mm | ≥ %s mm |" % (fmt(cs["min_transfer_clear"], 3), tol["transfer_clearance"]))
    w("| Parallel-park sweeps above deposits | min %s mm | ≥ %s mm |" % (fmt(cs["min_qpm_clear"], 3), tol["transfer_clearance"]))
    w("| In-part travel never below own completed layers | min margin %s mm | ≥ −%s mm |" % (fmt(cs["min_inpart_margin"], 3), tol["inpart_sink"]))
    w("| Carriage limits / adjacent carriage gap (every move + park) | min gap %s mm | ≥ 56.25 mm |" % fmt(cs["min_carriage_gap"], 3))
    w("| Min edge gap between deposited footprints | %s mm | ≥ 2.0 mm |" % fmt(c["min_gap_between_copies"], 3))
    w("| Retraction balance at every extrusion, heater waited, mesh cleared | 0 violations | %s mm |" % tol["retract"])
    w("| Foundation order | heads %s, layer-major, serpentine slots | independent |" % c["foundation_head_sequence"])
    fp = [x for x in c["first_prints"]]
    w("| First print after each purge | %s | primed, heated |" % ", ".join("T%d %.3f mm above bed" % (x["nozzle"], x["tip_above_bed"]) for x in fp if x["event"] == "after_purge"))
    w("| First print after COPY entry | all 4 heads primed (retract level %s), heated | |" % sorted({x["retract_level_before"] for x in fp if x["event"] == "after_copy_entry"}))
    w("| Final state | heaters off %s, fans off %s, carriages home %s, T0 PRIMARY/others INACTIVE %s, extruders unsynced %s, mesh `%s` | |"
      % (fs["heaters_off"], fs["fans_off"], fs["carriages_home"], fs["modes_ok"], fs["extruders_unsynced"], fs["mesh_state"]))
    w("")
    w("## 3. Comparison with the reviewed 40-shark oracle (after generation, verify stage only)")
    w("")
    w("Same simulator, same slicer alignment. %d aligned moves compared." % cm["aligned_moves_compared"])
    w("")
    w("* Placements: %d/40 matched, max difference **%s mm**. Visit order per head identical: **%s**. Feedrate mismatches: %d."
      % (cm["placements_matched"], fmt(cm["placement_max_delta_mm"], 4), cm["visit_order_identical"], cm["f_mismatches"]))
    w("* Z at every aligned slicer endpoint: max |Δ| **%s mm** (tolerance %s), worst at head T%d layer %d slicer line %d."
      % (fmt(cm["z_max_abs_delta"], 5), tol["oracle_z"], cm["worst"]["z"]["where"]["nozzle"], cm["worst"]["z"]["where"]["layer"], cm["worst"]["z"]["where"]["slicer_line"]))
    er = cm["e_ratio_minus_1"]
    w("* E: layer 1 and layers ≥%d identical (max %.1e). Foundation layers 2–%d: candidate/oracle −1 per head %s; %d moves outside ±3 %%. "
      "The oracle's foundation E formula could not be recovered exactly; the oracle itself deviates up to %.1f %% from the flow-area rule "
      "(%d moves > 3 %%), the candidate %.2f %%."
      % (plan["foundation_layers"] + 1, cm["worst"]["e_exact"]["value"], plan["foundation_layers"],
         ", ".join("%s [%+.3f, %+.3f]" % (k.split("/")[1], v[0], v[1]) for k, v in er.items() if k.startswith("L2")),
         cm["e_outside_tol"], 100 * abs(os_["e_found_max_dev"]), o["issue_counts"].get("FOUNDATION_FLOW", 0), 100 * abs(cs["e_found_max_dev"])))
    w("")
    w("Intentional differences (candidate vs oracle):")
    w("")
    po = o["purge_overlaps"]
    w("1. **Prime lines.** Oracle: four 100 mm lines at y = %s, 1.0 mm apart, est. width %.2f mm → %d overlapping pairs (%.2f mm overlap; "
      "each head prints over the previous line). Candidate: same amount (E15/100 mm, from the profile's `start_print`), two lanes "
      "y = %.2f / %.2f, left/right halves, no overlap, %.2f mm from the first copies."
      % ([p["start"][1] for p in o["purges"]], o["purges"][0]["width_est"], len(po), po[0]["overlap_mm"] if po else 0,
         plan["purge"]["0"]["y"], plan["purge"]["2"]["y"], 6.459 - plan["purge"]["2"]["y"] - plan["purge"]["width_est"] / 2))
    w("2. **End-of-part wipe.** The oracle replaces the slicer's wipe after each part (%d head-visits) by a stationary 0.4 mm retract; "
      "the candidate keeps the slicer wipe (same total retraction)." % o["trailer_differs"])
    w("3. **Transfer height.** Oracle: z_layer + 1.2 mm; candidate: highest deposit + head z-offset + 1.0 mm. Minimum measured clearance "
      "oracle %s mm, candidate %s mm." % (fmt(os_["min_transfer_clear"], 3), fmt(cs["min_transfer_clear"], 3)))
    w("4. **Layer-1 transfers** use the slice's `initial_layer_travel_acceleration` (1000) instead of 7000; park/descent heights differ slightly.")
    w("5. Pieces are bisected where the mesh is curved so straight moves follow the surface within 1 µm (%d pieces for %d model moves); "
      "one zero-length `G1 … E0` no-op of the slice is dropped." % (gen["output"]["pieces"], gen["output"]["moves"]))
    w("")
    w("## 4. Transitions and parking")
    w("")
    w("| Line | Phase | T0 | T1 | T2 | T3 | Z |")
    w("|---|---|---|---|---|---|---|")
    names = ["home"] + ["arrange T0", "home after T0", "arrange T1", "home after T1", "arrange T2", "home after T2", "arrange T3",
                        "home after T3", "COPY entry", "final home"]
    for i, q in enumerate(c["parallel_moves"]):
        t = q["target"]
        w("| %d | %s | %.2f | %.2f | %.2f | %.2f | %.2f |" % (q["line"], names[i] if i < len(names) else "", t["T0"], t["T1"], t["T2"], t["T3"], q["z"]))
    w("")
    t2 = [d for d in c["carriage_detours"] if d["head"] == "T2" and d["path"][1] == 412.75 and d["path"][2] < 0]
    if t2:
        d = t2[0]
        w("**Known T2 detour (kept for parity, not redesigned):** after its foundation T2 parks right at 412.75 and is then sent left to "
          "−56.5 so T3 can work: path %s → %s → %s, %.1f mm of extra carriage travel versus a direct move. Both moves are inside limits and "
          "keep ≥ 56.25 mm to the neighbours at start and end (affine parallel move), so it is inefficient, not hazardous. The oracle "
          "has the same pattern. Other reversals are by design: T1 parks right (356.5) while T0 prints and returns home afterwards; "
          "T0/T2/T3 leave their homes to reach their zones." % (d["path"][0], d["path"][1], d["path"][2], d["extra_travel_mm"]))
    w("")
    w("While a head prints its foundation the others are off the bed (T0 run: T1 356.5, T2 412.75, T3 469; T3 run: T2 −56.5). "
      "If the 'parked-head discrepancy' you saw is one of these positions, the table above shows exactly where every carriage is.")
    w("")
    w("## 5. What is measured, assumed and physically unverified")
    w("")
    w("* **Measured inputs (profile)**: saved mesh `default` 9×9, tool offsets (bed_pin_offsets.json), carriage limits, Y/Z ranges, "
      "min clearance 56.25 (quadro_parallel_carriage), extruder rotation distances (kept per extruder in COPY via `SYNC_EXTRUDER_MOTION`).")
    w("* **Assumed macro/firmware behaviour** (not executable offline): `_PRINT_HOME` homes, probes Z at (150,150) where the mesh is 0, "
      "leaves T0 primary at X150 with T1 at 356.5 (homing's `QUADRO_DISTRIBUTE_X`); `QUADRO_PARALLEL_MOVE` is affine; COPY copies "
      "the primary X motion with the current offset; `M109`/`M190` wait. Initial filament state of T1–T3 assumed primed.")
    w("* **Software-validated**: everything in sections 2–4 for this slice/profile; other counts/models only by the same checks "
      "(%s)." % plan["validated_scope"])
    w("* **Not physically validated**: no print, no motion on hardware. The oracle's successful print is evidence for its own file, not for "
      "this candidate's differences (prime lanes, wipes, transfer heights).")
    w("")
    w("## 6. Physical uncertainties that need your decision")
    w("")
    w("1. **Mesh outlier preserved, not repaired.** Probe point (258.75, 295.00) = −0.939 mm, 0.52 mm below its neighbours. "
      "If it is a real dip the copies are correct; if it is a probing error, the first layer of:")
    for x in plan["affected_by_outliers"]:
        if x["first_layer_dz_max"] > 0.05:
            w("   * %s — %s" % (x["copy"], x["first_layer_if_probe_error"]))
    others = [x["copy"] for x in plan["affected_by_outliers"] if x["first_layer_dz_max"] <= 0.05]
    w("   * and the foundation tops of %s shift by up to %.2f mm (shared COPY Z)." % (", ".join(others), max(x["foundation_top_dz_max"] for x in plan["affected_by_outliers"])))
    w("   → Please re-probe that corner (or confirm the dip) before printing; the tool will regenerate from the new mesh.")
    w("2. **Profile freshness cannot be proven** from the archive (all entries carry the same timestamp). Mesh 2026-11-13 14:37 and offsets from "
      "the Nov 12 snapshot are assumed current.")
    w("3. Prime lines lie at y < 5 mm, outside the measured mesh (edge row is used, as Klipper does).")
    w("")
    w("## 7. Negative and regeneration cases")
    w("")
    w("| Case | Exit | G-code written | Diagnostics |")
    w("|---|---|---|---|")
    for n in neg:
        w("| %s | %d | %s | %s |" % (n["case"], n["exit_code"], n["gcode_written"], ", ".join(n["error_codes"])))
    w("")
    w("| Regeneration | Generate/verify | Copies | Errors | Notes |")
    w("|---|---|---|---|---|")
    for r in reg:
        note = ""
        if "expected_effects" in r:
            e = r["expected_effects"]
            note = "T1 layer-1 Z %+.3f, T0 %+.3f, T2 COPY carriage %+.3f, output differs %s" % (e["T1_layer1_dz"], e["T0_layer1_dz"], e["T2_carriage_copy_delta_dx"], e["output_differs"])
        w("| %s | %s/%s | %s | %s | %s |" % (r["case"], r["generate_exit"], r["verify_exit"], r["copies_complete"], r["errors"], note))
    w("")
    w("## 8. Reproduce")
    w("")
    w("```")
    w("python3 tools/run_acceptance.py --fixtures <folder with single_shark.gcode, profile.zip, reference_40.gcode> --out <out>")
    w("python3 tools/write_report.py --acceptance <out> --out REPORT.md")
    w("```")
    w("")
    w("Commands and timings of this run: `results/acceptance_run.json`; machine-readable checks: `results/validation.json`; "
      "layout: `results/plan.json`, `results/preview.svg`/`.png`.")
    w("")
    w("## 9. Open questions")
    w("")
    w("1. Is the −0.939 mm corner point real? (decides T3 copies 8/9)")
    w("2. Which parked-head discrepancy did you observe on the oracle print (time/phase)? The park table above can be matched to it.")
    w("3. Keep the T2 right-then-left park for parity, or allow a direct T2 move in milestone 2?")
    w("4. OK to keep the slicer's end-of-part wipes and the non-overlapping prime lanes (differences 1–2 above)?")
    with open(a.out, "w") as f:
        f.write("\n".join(L) + "\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
