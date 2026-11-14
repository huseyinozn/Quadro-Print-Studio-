"""Command line: inspect | plan | generate | verify | preview.

Each stage is independent and deterministic. ``generate`` reads only the
slice, the profile and layout options; it refuses to write G-code if any
blocking diagnostic exists. Only ``verify`` may read a golden oracle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

from .diagnostics import Diagnostics, StageError
from .profile import load_profile, profile_checks
from .slicer import load_slice
from .plan import make_plan, LayoutOptions, PRESETS


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path, obj):
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=False, default=_jsonable)
        f.write("\n")


def _jsonable(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


def _layout_opts(a):
    o = LayoutOptions(count=a.count, preset=a.preset)
    for k in ("sync_pitch", "cols_per_zone", "col_pitch", "row_pitch", "min_gap", "foundation_layers",
              "standby_temp", "travel_clearance", "max_segment", "park_margin"):
        v = getattr(a, k, None)
        if v is not None:
            setattr(o, k, v)
    return o


def _add_inputs(p, profile=True):
    p.add_argument("--slice", required=True, help="sliced single-model G-code (Co Print Slicer)")
    if profile:
        p.add_argument("--profile", required=True, help="printer profile archive (.zip) or directory")
        p.add_argument("--mesh-profile", default=None, help="saved bed mesh name (required if several exist)")


def _add_layout(p):
    p.add_argument("--count", type=int, default=40, help="number of identical copies (multiple of 4)")
    p.add_argument("--preset", default="reference-300", choices=list(PRESETS) + ["auto"])
    p.add_argument("--sync-pitch", dest="sync_pitch", type=float)
    p.add_argument("--cols-per-zone", dest="cols_per_zone", type=int)
    p.add_argument("--col-pitch", dest="col_pitch", type=float)
    p.add_argument("--row-pitch", dest="row_pitch", type=float)
    p.add_argument("--min-gap", dest="min_gap", type=float)
    p.add_argument("--foundation-layers", dest="foundation_layers", type=int)
    p.add_argument("--standby-temp", dest="standby_temp", type=float)
    p.add_argument("--travel-clearance", dest="travel_clearance", type=float)
    p.add_argument("--max-segment", dest="max_segment", type=float)
    p.add_argument("--park-margin", dest="park_margin", type=float,
                   help="extra mm above the minimum carriage clearance for stacked parks (default 0 = reference parity)")


def load_inputs(a, diags):
    model, d1 = load_slice(a.slice)
    diags.extend(d1)
    prof, d2 = load_profile(a.profile, a.mesh_profile)
    diags.extend(d2)
    profile_checks(prof, diags)
    return model, prof


def cmd_inspect(a):
    t0 = time.time()
    diags = Diagnostics()
    out = dict(stage="inspect")
    try:
        model, d1 = load_slice(a.slice)
        diags.extend(d1)
        out["slice"] = dict(path=os.path.basename(a.slice), sha256=_sha(a.slice), generator=model.generator,
                            stats=model.stats, bbox_extrude=model.bbox_extrude, bbox_motion=model.bbox_motion,
                            footprint_mm=[round(model.bbox_extrude[2] - model.bbox_extrude[0], 3), round(model.bbox_extrude[3] - model.bbox_extrude[1], 3)],
                            nozzle_temp=model.nozzle_temp, bed_temp=model.bed_temp, pressure_advance=model.pressure_advance,
                            initial_retract=model.initial_retract, config=model.config, dropped_commands=model.dropped,
                            gap_events={k: [c.text for c in v] for k, v in model.gap_events.items() if v},
                            layer_z=[b.z for b in model.blocks])
    except StageError as e:
        diags.extend(e.diags)
    if a.profile:
        try:
            prof, d2 = load_profile(a.profile, a.mesh_profile)
            diags.extend(d2)
            outs = profile_checks(prof, diags)
            out["profile"] = prof.summary()
            out["profile"]["mesh_outliers"] = outs
        except StageError as e:
            diags.extend(e.diags)
    out["diagnostics"] = diags.as_list()
    out["ok"] = diags.ok()
    out["elapsed_s"] = round(time.time() - t0, 2)
    _write_json(a.out, out)
    print(diags.render() or "no diagnostics")
    print("inspect: %s (%.1fs)" % ("OK" if diags.ok() else "FAILED", time.time() - t0))
    return 0 if diags.ok() else 2


def build_plan(a, diags):
    model, prof = load_inputs(a, diags)
    if not diags.ok():
        raise StageError(diags)
    plan, d3 = make_plan(model, prof, _layout_opts(a))
    diags.extend(d3)
    return model, prof, plan


def cmd_plan(a):
    t0 = time.time()
    diags = Diagnostics()
    out = dict(stage="plan")
    plan = None
    try:
        model, prof, plan = build_plan(a, diags)
        if plan is not None:
            out["plan"] = plan.as_json()
    except StageError as e:
        diags = e.diags
    out["diagnostics"] = diags.as_list()
    out["ok"] = diags.ok() and plan is not None
    out["elapsed_s"] = round(time.time() - t0, 2)
    _write_json(a.out, out)
    if plan is not None and a.preview:
        from .preview import render_svg
        with open(a.preview, "w") as f:
            f.write(render_svg(model, prof, plan))
    print(diags.render() or "no diagnostics")
    if plan is not None:
        for c in plan.checks:
            print("  [%s] %s: %s" % ("ok" if c["ok"] else "FAIL", c["name"], c["detail"]))
    print("plan: %s (%.1fs)" % ("OK" if out["ok"] else "FAILED", time.time() - t0))
    return 0 if out["ok"] else 2


def cmd_generate(a):
    t0 = time.time()
    diags = Diagnostics()
    report = dict(stage="generate", inputs=dict(slice=os.path.basename(a.slice), profile=os.path.basename(a.profile)))
    plan = None
    try:
        model, prof, plan = build_plan(a, diags)
    except StageError as e:
        diags = e.diags
    ok = diags.ok() and plan is not None
    if ok:
        from .generate import generate
        text, info = generate(model, prof, plan, _sha(a.slice), prof.fingerprint)
        tmp = a.out + ".partial"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, a.out)
        report["output"] = dict(path=os.path.basename(a.out), **info)
        report["plan"] = plan.as_json()
    else:
        if os.path.exists(a.out):
            os.remove(a.out)    # never leave a stale printable file behind on failure
    report["diagnostics"] = diags.as_list()
    report["ok"] = ok
    report["elapsed_s"] = round(time.time() - t0, 2)
    _write_json(a.report, report)
    print(diags.render() or "no diagnostics")
    print("generate: %s -> %s (%.1fs)" % ("OK" if ok else "REFUSED (no G-code written)", a.out if ok else "-", time.time() - t0))
    return 0 if ok else 2


def cmd_verify(a):
    t0 = time.time()
    from .verify import Sim, analyse, compare, TOL
    diags = Diagnostics()
    model, prof = load_inputs(a, diags)
    if not diags.ok():
        print(diags.render())
        return 2
    nozzle = prof.extruders[0].nozzle_diameter
    hlim = (0.2 * nozzle, 0.75 * nozzle)
    sim = Sim(prof, model, "candidate", h_limits=hlim).run(a.gcode, a.foundation_layers)
    res = analyse(sim, expected_count=a.count, min_gap=a.min_gap)
    out = dict(stage="verify", candidate=os.path.basename(a.gcode), candidate_sha256=_sha(a.gcode),
               slice_sha256=_sha(a.slice), profile_fingerprint=prof.fingerprint, tolerances=TOL,
               candidate_result=res, profile_diagnostics=diags.as_list())
    print("candidate: %s, %d errors, copies complete %d/%s, %.1fs" % ("PASS" if res["passed"] else "FAIL", res["error_count"],
                                                                     res["copies_complete"], a.count, time.time() - t0))
    if a.oracle:
        t1 = time.time()
        osim = Sim(prof, model, "oracle", h_limits=hlim).run(a.oracle, a.foundation_layers)
        ores = analyse(osim, expected_count=a.count, min_gap=a.min_gap)
        cmp_ = compare(sim, osim, a.foundation_layers)
        out["oracle"] = os.path.basename(a.oracle)
        out["oracle_sha256"] = _sha(a.oracle)
        out["oracle_result"] = ores
        out["comparison"] = cmp_
        print("oracle (same checks): %s, %d errors (%.1fs)" % ("PASS" if ores["passed"] else "FAIL", ores["error_count"], time.time() - t1))
        w = cmp_["worst"]["e_found"]["where"] or {}
        print("comparison: placements max delta %.4f mm, Z max |delta| %.5f mm, F mismatches %d, order identical %s"
              % (cmp_["placement_max_delta_mm"], cmp_["z_max_abs_delta"], cmp_["f_mismatches"], cmp_["visit_order_identical"]))
        print("foundation E largest difference (open finding): %s layer %s slicer line %s: oracle %s mm, candidate %s mm"
              % (w.get("head"), w.get("layer"), w.get("slicer_line"), w.get("oracle_e_mm"), w.get("candidate_e_mm")))
    out["elapsed_s"] = round(time.time() - t0, 2)
    _write_json(a.out, out)
    return 0 if res["passed"] else 3


def cmd_preview(a):
    diags = Diagnostics()
    try:
        model, prof, plan = build_plan(a, diags)
    except StageError as e:
        print(e.diags.render())
        return 2
    if plan is None:
        print(diags.render())
        return 2
    from .preview import render_svg
    with open(a.out, "w") as f:
        f.write(render_svg(model, prof, plan))
    print("preview written to %s" % a.out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="quadro-studio", description="Quadro Print Studio - Farm Mode (offline, deterministic)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="parse and classify the slice and/or the printer profile")
    p.add_argument("--slice", required=True)
    p.add_argument("--profile", default=None)
    p.add_argument("--mesh-profile", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("plan", help="compute and check the layout without writing G-code")
    _add_inputs(p)
    _add_layout(p)
    p.add_argument("--out", default=None)
    p.add_argument("--preview", default=None, help="write an SVG layout preview")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("generate", help="write Farm Mode G-code (refuses on any blocking diagnostic)")
    _add_inputs(p)
    _add_layout(p)
    p.add_argument("--out", required=True)
    p.add_argument("--report", default=None)
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("verify", help="independently simulate and check generated G-code (optionally against an oracle)")
    _add_inputs(p)
    p.add_argument("--gcode", required=True)
    p.add_argument("--count", type=int, default=40)
    p.add_argument("--foundation-layers", dest="foundation_layers", type=int, default=5)
    p.add_argument("--min-gap", dest="min_gap", type=float, default=2.0)
    p.add_argument("--oracle", default=None, help="golden G-code used ONLY for comparison")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("preview", help="write an SVG of bed, heads, footprints and synchronized groups")
    _add_inputs(p)
    _add_layout(p)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_preview)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except StageError as e:
        print(e.diags.render())
        return 2


if __name__ == "__main__":
    sys.exit(main())
