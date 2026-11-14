"""Milestone-1 acceptance run (reproducible).

python3 tools/run_acceptance.py --fixtures DIR --out DIR

DIR must contain single_shark.gcode, profile.zip and reference_40.gcode.
Generation runs in an isolated folder that contains ONLY the slice and the
profile; the oracle is used afterwards, by `verify --oracle`, only.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
PY = sys.executable
CANDIDATE_NAME = "CANDIDATE_farm40_shark_software-verified_NOT-hardware-validated.gcode"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run(cmd, log, cwd=None):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)
    el = round(time.time() - t0, 2)
    rel = [os.path.relpath(c, ROOT) if os.path.isabs(c) and c.startswith(ROOT) else c for c in cmd]
    log.append(dict(cmd=" ".join(rel), exit_code=p.returncode, elapsed_s=el, stdout_tail=p.stdout[-3000:], stderr_tail=p.stderr[-2000:]))
    print("[%5.1fs] exit=%d  %s" % (el, p.returncode, " ".join(rel)[:160]))
    return p.returncode, el


def derive_profile(src, dst, drop_mesh=False, drop_offsets=False, offset_delta=None):
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if drop_offsets and info.filename.endswith("bed_pin_offsets.json"):
                continue
            if drop_mesh and info.filename.endswith("printer.cfg"):
                txt = data.decode()
                txt = re.sub(r"#\*# \[bed_mesh [^\]]+\].*?(?=#\*# \[|\Z)", "", txt, flags=re.S)
                data = txt.encode()
            if offset_delta and info.filename.endswith("bed_pin_offsets.json"):
                o = json.loads(data)
                for t, d in offset_delta.items():
                    for k, v in d.items():
                        o[t][k] += v
                data = json.dumps(o, indent=4).encode()
            zout.writestr(info, data)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    fx = os.path.abspath(a.fixtures)
    out = os.path.abspath(a.out)
    shutil.rmtree(out, ignore_errors=True)
    for d in ("gen_inputs", "candidate", "results", "work"):
        os.makedirs(os.path.join(out, d))
    log = []
    T0 = time.time()
    summary = dict(started=time.strftime("%Y-%m-%d %H:%M:%S"), python=sys.version.split()[0])

    # 1) isolated generation inputs: slice + profile only
    gi = os.path.join(out, "gen_inputs")
    shutil.copy(os.path.join(fx, "single_shark.gcode"), gi)
    shutil.copy(os.path.join(fx, "profile.zip"), gi)
    SL, PR = os.path.join(gi, "single_shark.gcode"), os.path.join(gi, "profile.zip")
    ORACLE = os.path.join(fx, "reference_40.gcode")
    summary["inputs"] = dict(slice_sha256=sha(SL), profile_sha256=sha(PR), oracle_sha256=sha(ORACLE),
                             gen_inputs_listing=sorted(os.listdir(gi)))
    R = os.path.join(out, "results")
    CAND = os.path.join(out, "candidate", CANDIDATE_NAME)
    cli = [PY, "-m", "quadro_studio"]

    rc, _ = run(cli + ["inspect", "--slice", SL, "--profile", PR, "--out", os.path.join(R, "inspect.json")], log)
    summary["inspect_ok"] = rc == 0
    rc, _ = run(cli + ["plan", "--slice", SL, "--profile", PR, "--out", os.path.join(R, "plan.json"),
                       "--preview", os.path.join(R, "preview.svg")], log)
    summary["plan_ok"] = rc == 0
    rc, tgen = run([PY, os.path.join(ROOT, "tools", "audit_run.py"), os.path.join(R, "generation_audit.json"), "--"]
                   + ["generate", "--slice", SL, "--profile", PR, "--out", CAND, "--report", os.path.join(R, "generate.json")], log)
    summary["generate_ok"] = rc == 0
    audit = json.load(open(os.path.join(R, "generation_audit.json")))
    allowed = {SL, PR, CAND, CAND + ".partial", os.path.join(R, "generate.json")}
    summary["generation_isolation"] = dict(files_opened=[os.path.relpath(p, out) for p in audit["files_opened"]],
                                           only_slice_profile_and_outputs=set(audit["files_opened"]) <= allowed,
                                           oracle_opened=ORACLE in audit["files_opened"])
    # determinism: regenerate and compare hash
    CAND2 = os.path.join(out, "work", "regen.gcode")
    run(cli + ["generate", "--slice", SL, "--profile", PR, "--out", CAND2], log)
    summary["deterministic_rerun_identical"] = sha(CAND) == sha(CAND2)
    os.remove(CAND2)

    # 2) independent verification, then oracle comparison
    rc, tver = run(cli + ["verify", "--slice", SL, "--profile", PR, "--gcode", CAND, "--oracle", ORACLE,
                          "--out", os.path.join(R, "validation.json")], log)
    summary["verify_exit"] = rc

    # 3) negative cases: each must refuse and write no G-code
    W = os.path.join(out, "work")
    neg = []

    def negative(name, args, expect_code):
        o = os.path.join(W, name + ".gcode")
        rc, el = run(cli + ["generate"] + args + ["--out", o, "--report", os.path.join(W, name + ".json")], log)
        rep = json.load(open(os.path.join(W, name + ".json"))) if os.path.exists(os.path.join(W, name + ".json")) else {}
        codes = sorted({d["code"] for d in rep.get("diagnostics", []) if d["severity"] == "error"})
        ok = rc == 2 and not os.path.exists(o) and expect_code in codes
        neg.append(dict(case=name, exit_code=rc, gcode_written=os.path.exists(o), error_codes=codes, expected=expect_code, passed=ok,
                        elapsed_s=el))

    p_nomesh = derive_profile(PR, os.path.join(W, "profile_no_mesh.zip"), drop_mesh=True)
    p_nooff = derive_profile(PR, os.path.join(W, "profile_no_offsets.zip"), drop_offsets=True)
    negative("missing_mesh", ["--slice", SL, "--profile", p_nomesh], "PROFILE_MESH_MISSING")
    negative("missing_offsets", ["--slice", SL, "--profile", p_nooff], "PROFILE_OFFSETS_MISSING")
    txt = open(SL).read()
    bad = txt.replace(";LAYER:3\n", ";LAYER:3\nG2 X150 Y150 I5 J0 E0.1\n", 1)
    open(os.path.join(W, "arc.gcode"), "w").write(bad)
    negative("unsupported_gcode_arc", ["--slice", os.path.join(W, "arc.gcode"), "--profile", PR], "SLICE_UNSUPPORTED_GCODE")
    bad = txt.replace(";LAYER:4\n", ";LAYER:4\nT1\n", 1)
    open(os.path.join(W, "tool.gcode"), "w").write(bad)
    negative("unsupported_second_tool", ["--slice", os.path.join(W, "tool.gcode"), "--profile", PR], "SLICE_UNSUPPORTED_TOOL")
    bad = txt.replace(";LAYER:2\n", ";LAYER:2\nG1 X10 Y10 F3000\n", 1)
    open(os.path.join(W, "motion.gcode"), "w").write(bad)
    negative("unknown_motion", ["--slice", os.path.join(W, "motion.gcode"), "--profile", PR], "SLICE_UNKNOWN_MOTION")
    negative("unsafe_packing_col_pitch_30", ["--slice", SL, "--profile", PR, "--col-pitch", "30"], "PLAN_COPY_GAP")
    negative("unsafe_packing_row_pitch_54", ["--slice", SL, "--profile", PR, "--row-pitch", "54"], "PLAN_COPY_GAP")
    negative("carriage_clearance_sync_50", ["--slice", SL, "--profile", PR, "--sync-pitch", "50", "--cols-per-zone", "1"], "PLAN_COPY_CLEARANCE")
    negative("count_not_multiple_of_4", ["--slice", SL, "--profile", PR, "--count", "42"], "PLAN_COUNT")
    negative("count_48_off_bed", ["--slice", SL, "--profile", PR, "--count", "48"], "PLAN_BED_BOUNDS")
    summary["negative_cases"] = neg

    # 4) regeneration cases (each verified independently)
    regen = []

    def regen_case(name, gen_args, verify_args, expect_pass=True):
        o = os.path.join(W, name + ".gcode")
        rc1, e1 = run(cli + ["generate"] + gen_args + ["--out", o, "--report", os.path.join(W, name + "_gen.json")], log)
        rc2, e2 = (run(cli + ["verify"] + verify_args + ["--gcode", o, "--out", os.path.join(W, name + "_verify.json")], log)
                   if rc1 == 0 else (None, 0))
        v = json.load(open(os.path.join(W, name + "_verify.json")))["candidate_result"] if rc2 is not None else {}
        regen.append(dict(case=name, generate_exit=rc1, verify_exit=rc2, passed=(rc1 == 0 and rc2 == 0) == expect_pass,
                          copies_complete=v.get("copies_complete"), errors=v.get("error_count"),
                          gcode_sha256=sha(o) if os.path.exists(o) else None, elapsed_s=round(e1 + e2, 2),
                          min_gap=v.get("min_gap_between_copies")))
        return o

    regen_case("count_16_shark", ["--slice", SL, "--profile", PR, "--count", "16"],
               ["--slice", SL, "--profile", PR, "--count", "16"])
    import synth
    box = synth.write(os.path.join(W, "synthetic_box.gcode"), size=(24.0, 16.0), layers=15)
    regen_case("different_model_box_24", ["--slice", box, "--profile", PR, "--count", "24", "--preset", "auto"],
               ["--slice", box, "--profile", PR, "--count", "24"])
    p_cal = derive_profile(PR, os.path.join(W, "profile_modified_calibration.zip"),
                           offset_delta={"T1": {"z": 0.05}, "T2": {"x": 0.20, "y": 0.10}})
    o_cal = regen_case("modified_calibration_T1z_plus0.05_T2xy_plus0.2_0.1", ["--slice", SL, "--profile", p_cal],
                       ["--slice", SL, "--profile", p_cal])
    # the modified-calibration output must differ from the candidate exactly where the calibration acts
    from quadro_studio.profile import load_profile
    from quadro_studio.slicer import load_slice
    from quadro_studio.plan import make_plan, LayoutOptions
    m, _ = load_slice(SL)
    pa, _ = load_profile(PR)
    pb, _ = load_profile(p_cal)
    pla, _ = make_plan(m, pa, LayoutOptions())
    plb, _ = make_plan(m, pb, LayoutOptions())
    s = pla.slots[0]
    mv = m.blocks[0].moves()[0]
    x, y = mv.x + s.dx, mv.y + s.dy
    regen[-1]["expected_effects"] = dict(
        T1_layer1_dz=round(plb.zbase(1, 1, x, y) - pla.zbase(1, 1, x, y), 6),
        T0_layer1_dz=round(plb.zbase(1, 0, x, y) - pla.zbase(1, 0, x, y), 6),
        T2_carriage_copy_delta_dx=round(plb.copy_carriage_delta[2] - pla.copy_carriage_delta[2], 6),
        common_surface_layer5_dz_at_slot0=round(plb.zbase(5, 0, x, y) - pla.zbase(5, 0, x, y), 6),
        output_differs=sha(o_cal) != sha(CAND))
    summary["regeneration_cases"] = regen

    # 5) unit + pipeline tests
    env = dict(os.environ, QUADRO_FIXTURES=os.path.join(out, "gen_inputs"))
    t0 = time.time()
    p = subprocess.run([PY, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, capture_output=True, text=True, env=env)
    tail = p.stderr.strip().splitlines()[-3:]
    summary["unit_tests"] = dict(exit_code=p.returncode, summary=tail, elapsed_s=round(time.time() - t0, 2),
                                 cmd="QUADRO_FIXTURES=<fixtures> python3 -m unittest discover -s tests -v")
    with open(os.path.join(R, "unit_tests.log"), "w") as f:
        f.write(p.stderr)

    summary["elapsed_total_s"] = round(time.time() - T0, 1)
    summary["candidate"] = dict(file=CANDIDATE_NAME, sha256=sha(CAND), bytes=os.path.getsize(CAND), generate_elapsed_s=tgen,
                                verify_elapsed_s=tver)
    summary["commands"] = log
    with open(os.path.join(R, "acceptance_run.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("negative cases passed: %d/%d" % (sum(n["passed"] for n in neg), len(neg)))
    print("regeneration cases passed: %d/%d" % (sum(r["passed"] for r in regen), len(regen)))
    print("unit tests:", tail)
    print("total %.1fs" % (time.time() - T0))


if __name__ == "__main__":
    main()
