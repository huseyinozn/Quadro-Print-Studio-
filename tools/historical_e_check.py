"""Test-only: verify the historical foundation E rule of the printed reference.

Claim under test (review 02): the printed reference is a *remesh* of an older
generated file. For every positive model deposition piece on layers 2..F,
at the piece end point,
    E_printed = round(E_old * h_new / h_old, 8)
where h_old / h_new are the local foundation thicknesses under the old and the
current mesh. Secondly, old E ~= slicer E apportioned over the old <= 4 mm
pieces x A(h_old,w)/A(h0,w) at the piece end point.

This tool reads private historical assets given on the command line and writes
only numbers. It is NOT used by generation: production has one current profile.

python3 tools/historical_e_check.py --slice S --profile P --old-gcode OLD --old-mesh OLD_MESH.cfg \
    --reference REF --out historical_e_check.json
"""
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quadro_studio.slicer import load_slice
from quadro_studio.profile import load_profile, parse_cfg, split_save_config
from quadro_studio.plan import make_plan, LayoutOptions
from quadro_studio.mesh import BedMesh
from quadro_studio.geometry import flow_area

W = re.compile(r"([XYZEF])([-+]?\d*\.?\d+)")


def sections(path, max_layer):
    out = {}
    key = None
    with open(path) as f:
        for ln, l in enumerate(f, 1):
            if l.startswith("; SHARK"):
                m = re.match(r"; SHARK layer=(\d+) slot=(\d+) tools=([\d,]+)", l)
                L = int(m.group(1))
                key = (L, int(m.group(2)), m.group(3)) if L <= max_layer else None
                if key:
                    out[key] = []
                continue
            if key and l.startswith("G1 ") and " E" in l and " X" in l:
                w = {k: float(v) for k, v in W.findall(l)}
                if w.get("E", 0) > 0:
                    out[key].append((ln, w["X"], w["Y"], w["Z"], w["E"]))
            elif l.startswith("QUADRO_PARALLEL_MOVE"):
                key = None
    return out


def load_mesh_cfg(path):
    txt = open(path).read()
    _, saved = split_save_config("#*# <---------------------- SAVE_CONFIG ---------------------->\n" + txt)
    secs, order = parse_cfg(saved)
    name = [n for n in order if n.startswith("bed_mesh ")][0]
    s = secs[name]
    pts = [[float(v) for v in r.split(",") if v.strip()] for r in s["points"].split("\n") if r.strip()]
    return BedMesh(pts, float(s["min_x"]), float(s["max_x"]), float(s["min_y"]), float(s["max_y"]),
                   int(s["x_count"]), int(s["y_count"]), int(s["mesh_x_pps"]), int(s["mesh_y_pps"]),
                   s["algo"], float(s["tension"]), "old")


def main():
    ap = argparse.ArgumentParser()
    for k in ("slice", "profile", "old_gcode", "old_mesh", "reference", "out"):
        ap.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    ap.add_argument("--foundation-layers", type=int, default=5)
    a = ap.parse_args()
    F = a.foundation_layers
    model, _ = load_slice(a.slice)
    prof, _ = load_profile(a.profile)
    plan, _ = make_plan(model, prof, LayoutOptions())
    old_mesh = load_mesh_cfg(a.old_mesh)
    old = sections(a.old_gcode, F)
    ref = sections(a.reference, F)
    off = plan.offsets
    res = dict(pieces=0, xy_mismatch=0, remesh_max_abs_err=0.0, remesh_worst=None,
               old_model_max_abs_err=0.0, old_model_max_rel_err=0.0, old_model_worst=None, by_layer_head={})
    target = None
    for key in sorted(old):
        L, slot, tools = key
        if L < 2 or "," in tools:
            continue
        t = int(tools)
        o, r = old[key], ref.get(key)
        if r is None or len(r) != len(o):
            res.setdefault("section_mismatch", []).append([L, slot, t, len(o), len(r or [])])
            continue
        blk = model.blocks[L - 1]
        # slicer moves for the two-step approximation (pieces in slicer order)
        sm = [m for m in blk.moves() if m.kind == "extrude"]
        si = 0
        acc = None
        for (lo, xo, yo, zo, eo), (lr, xr, yr, zr, er) in zip(o, r):
            res["pieces"] += 1
            if abs(xo - xr) > 1e-6 or abs(yo - yr) > 1e-6:
                res["xy_mismatch"] += 1
                continue
            x = xo - plan.sync_pitch * t - off[t][0]
            y = yo
            h_new = plan.thickness(L, t, x, y)
            h_old = plan.thickness(L, t, x, y, old_mesh)
            pred = round(eo * h_new / h_old, 8)
            err = abs(pred - er)
            if err < 5e-9:
                res["exact_8_decimals"] = res.get("exact_8_decimals", 0) + 1
            elif err < 1.5e-8:
                res["one_unit_8th_decimal"] = res.get("one_unit_8th_decimal", 0) + 1
            else:
                res["larger_than_one_unit"] = res.get("larger_than_one_unit", 0) + 1
            b = res["by_layer_head"].setdefault("L%d/T%d" % (L, t), dict(pieces=0, e_printed=0.0, e_old=0.0, max_abs_err=0.0))
            b["pieces"] += 1
            b["e_printed"] += er
            b["e_old"] += eo
            b["max_abs_err"] = max(b["max_abs_err"], err)
            if err > res["remesh_max_abs_err"]:
                res["remesh_max_abs_err"] = err
                res["remesh_worst"] = dict(layer=L, slot=slot, head="T%d" % t, ref_line=lr, old_line=lo, printed=er, predicted=pred)
    # two-step approximation of OLD E per slicer move (pieces = ceil(len/4)) at piece end points
    for key in sorted(old):
        L, slot, tools = key
        if L < 2 or "," in tools:
            continue
        t = int(tools)
        o = old[key]
        s = plan.slots[slot]
        k = 0
        for m in model.blocks[L - 1].moves():
            if m.kind != "extrude":
                continue
            n = max(1, int(math.ceil(m.xy_len / 4.0 - 1e-9)))
            for i in range(1, n + 1):
                if k >= len(o):
                    break
                ex = m.x0 + (m.x - m.x0) * i / n + s.dx
                ey = m.y0 + (m.y - m.y0) * i / n + s.dy
                h_old = plan.thickness(L, t, ex, ey, old_mesh)
                approx = m.e / n * flow_area(h_old, m.width) / flow_area(m.height if hasattr(m, "height") else 0.2, m.width)
                lo, xo, yo, zo, eo = o[k]
                k += 1
                d = abs(approx - eo)
                if d > res["old_model_max_abs_err"]:
                    res["old_model_max_abs_err"] = d
                    res["old_model_worst"] = dict(layer=L, slot=slot, head="T%d" % t, slicer_line=m.line, old=eo, approx=approx)
                res["old_model_max_rel_err"] = max(res["old_model_max_rel_err"], d / eo)
                if L == 3 and slot == 8 and t == 1 and m.line == 4384:
                    # reproduce the reviewed example with the remesh ratio applied to the approximation
                    hn = plan.thickness(L, t, ex, ey)
                    target = (target or 0.0) + approx * hn / h_old
                    res.setdefault("line4384_pieces", []).append(dict(old=eo, approx_old=approx, h_old=h_old, h_new=hn))
    if target is not None:
        res["line4384_two_step_printed_estimate"] = round(target, 7)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "by_layer_head"}, indent=1)[:3000])


if __name__ == "__main__":
    main()
