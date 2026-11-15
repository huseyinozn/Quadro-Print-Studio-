"""Dual Color Copy Mode: room for the filament where one colour is printed on the other,
and local layer-height compensation.

Inputs are the per-layer measurements of ``dualz.analyse_z`` (planned nozzle heights replayed
over the real slice geometry, per model):

* centre room: nozzle tip minus the top of the other colour of the layer below, under the
  bead CENTRE (colour 1 on colour 2: pair A = T0 left / T1 right; colour 2 on colour 1:
  pair B = T2 left / T3 right);
* edge room: the same under the bead EDGE where a colour border continues from layer to layer;
* lateral: colour 2 is printed after colour 1 of the same layer, beside it.

Compensation model (general, any Dual plan). Per layer L above the independent layers, the
commanded Z of pair A and of pair B may be raised by u_A(L), u_B(L) (both models of a pair
move together: one shared Z per pair). With them

    centre/edge room c1-on-c2 = base + u_A(L) - u_B(L-1)
    centre/edge room c2-on-c1 = base + u_B(L) - u_A(L-1)
    colour 2 beside colour 1  = delta + u_B(L) - u_A(L)          (>= -into_material)
    colour-n layer on itself  = h + u_n(L) - u_n(L-1)             (within the layer limits)

and u_A = u_B = 0 again at the last layer (model height kept). The extrusion amount is scaled
with the local room (flow area A(t, w)); a pair's two extruders are synchronised, so one E must
fit the room on BOTH models. The left/right room difference at the same model point equals
the tool-offset mismatch (T3-T1) - (T2-T0) and does not change with u_A/u_B.
"""
from __future__ import annotations

import math

import numpy as np

from .dualtol import DUAL_LIMITS, EPS


def flow_area(t, w):
    return max(0.0, (w - t * (1.0 - math.pi / 4.0)) * t)


def analyse_room(plan, za, room_min, t_layer=(0.08, 0.30), flow_tol=0.017, width=0.45,
                 step=0.002, return_step=None, edge_goal=True, dual=None):
    lim = DUAL_LIMITS
    into, zdev = lim["into_material"], lim["z_deviation"]
    F = plan.foundation_layers
    nL = len(plan.layer_z)
    h = {L: plan.layer_z[L - 1] - (plan.layer_z[L - 2] if L >= 2 else 0.0) for L in range(1, nL + 1)}
    sides = za["sides"]
    deltas = {"left": plan.pair_z["left_delta"], "right": plan.pair_z["right_delta"]}

    def base(side, L, key):
        v = sides[side]["room_layers"].get(str(L), {}).get(key)
        return None if v is None else v["min"]

    def base_max(side, L, key):
        v = sides[side]["room_layers"].get(str(L), {}).get(key)
        return None if v is None else v.get("max", v["min"])

    # ---- 1. as planned (no compensation) ------------------------------------------------------
    before = {}
    for side in sides:
        c = [base(side, L, k) for L in range(1, nL + 1) for k in ("c1_on_c2", "c2_on_c1")]
        e = [base(side, L, k) for L in range(1, nL + 1) for k in ("edge_c1", "edge_c2")]
        c = [v for v in c if v is not None]
        e = [v for v in e if v is not None]
        before[side] = dict(centre_min=min(c) if c else None, centre_max=max(c) if c else None,
                            edge_min=min(e) if e else None)

    from .dualtol import small_negative

    def ok_room(v, need):
        return v is None or v >= need - EPS or small_negative(v)
    # every centre room must be enough (>= room_min) or a small negative accepted deviation: a positive
    # room below room_min is never accepted
    pos_short = []
    for side in sides:
        for L, v in sides[side]["room_layers"].items():
            for key in ("c1_on_c2", "c2_on_c1"):
                mp = (v.get(key) or {}).get("min_pos")
                if mp is not None and mp < room_min - EPS:
                    pos_short.append(dict(side=side, layer=int(L), pair="T0+T1" if key == "c1_on_c2" else "T2+T3", room=mp,
                                          slicer_line=v[key].get("slicer_line")))
    pos_short.sort(key=lambda x: x["layer"])
    ok_before = not pos_short and all(ok_room(before[sd]["centre_min"], room_min) and ok_room(before[sd]["edge_min"], 0.0) for sd in before)
    if ok_before:
        # no compensation needed: every room is enough, or a small negative accepted deviation
        nothing = dict(uA={}, uB={})
    # ---- 2. greedy local compensation (layers above the independent ones) --------------------------
    grid = np.arange(-0.40, 0.60 + 1e-9, step)
    UA, UB = np.meshgrid(grid, grid, indexing="ij")
    uA = {L: 0.0 for L in range(0, nL + 1)}
    uB = {L: 0.0 for L in range(0, nL + 1)}
    unmet = []
    for L in (range(F + 1, nL + 1) if not ok_before else []):
        pa, pb = uA[L - 1], uB[L - 1]
        hard = np.ones_like(UA, dtype=bool)
        short = np.zeros_like(UA)                      # how far the room goals are missed (mm)
        for side in sides:
            d = deltas[side]
            # colour 2 beside colour 1 (printed after it): never more than into_material lower
            hard &= d + UB - UA >= -into - EPS
            for key, expr in (("c1_on_c2", UA - pb), ("c2_on_c1", UB - pa)):
                b = base(side, L, key)
                if b is None:
                    continue
                room = b + expr
                hard &= np.abs(room - h[L]) <= zdev + EPS           # owner Z deviation limit
                hard &= room >= -into - EPS                          # nozzle not into the other colour
                bmx = base_max(side, L, key)
                hard &= np.abs(bmx + expr - h[L]) <= zdev + EPS
                short += np.maximum(0.0, room_min - room)
            for key, expr in (("edge_c1", UA - pb), ("edge_c2", UB - pa)):
                b = base(side, L, key)
                if b is None:
                    continue
                room = b + expr
                hard &= room >= -into - EPS
                if edge_goal:
                    short += np.maximum(0.0, 0.0 - room)         # goal: no negative room at the edge
        # each colour on itself: layer limits
        hard &= (h[L] + UA - pa >= t_layer[0] - EPS) & (h[L] + UA - pa <= t_layer[1] + EPS)
        hard &= (h[L] + UB - pb >= t_layer[0] - EPS) & (h[L] + UB - pb <= t_layer[1] + EPS)
        if return_step is not None:
            hard &= (UA >= pa - return_step - EPS) & (UB >= pb - return_step - EPS)
        if not hard.any():
            unmet.append(dict(layer=L, reason="no pair heights satisfy the hard limits"))
            uA[L], uB[L] = pa, pb
            continue
        cost = np.where(hard, short * 1000.0 + np.abs(UA) + np.abs(UB), np.inf)
        i = np.unravel_index(int(np.argmin(cost)), cost.shape)
        uA[L], uB[L] = float(UA[i]), float(UB[i])
        if short[i] > 1e-9:
            unmet.append(dict(layer=L, short_mm=round(float(short[i]), 4)))

    # ---- 3. result with the compensation ---------------------------------------------------------
    after = {s: dict(centre_min=None, centre_max=None, edge_min=None) for s in sides}
    changed = [L for L in range(F + 1, nL + 1) if abs(uA[L]) > 1e-9 or abs(uB[L]) > 1e-9]
    for L in range(F + 1, nL + 1):
        for key, expr in (("c1_on_c2", uA[L] - uB[L - 1]), ("c2_on_c1", uB[L] - uA[L - 1])):
            for side in sides:
                b = base(side, L, key)
                if b is None:
                    continue
                r = b + expr
                a = after[side]
                a["centre_min"] = r if a["centre_min"] is None else min(a["centre_min"], r)
                a["centre_max"] = r if a["centre_max"] is None else max(a["centre_max"], r)
        for side in sides:
            for key, expr in (("edge_c1", uA[L] - uB[L - 1]), ("edge_c2", uB[L] - uA[L - 1])):
                b = base(side, L, key)
                if b is not None:
                    a = after[side]
                    a["edge_min"] = b + expr if a["edge_min"] is None else min(a["edge_min"], b + expr)

    # ---- 4. structural bound for continuing colour borders ------------------------------------------
    # Over a run where colour 1 on the right model keeps running over colour 2's edge AND colour 2
    # on the left keeps running beside colour 1, the u terms cancel on average:
    #   mean edge room (right) <= h - (delta_R - delta_L) + into_material
    mism_z = deltas["right"] - deltas["left"]
    hn = h.get(F + 1, 0.2)
    edge_bound = hn - abs(mism_z) + into
    edge_layers = sorted({int(L) for s in sides for L, v in sides[s]["room_layers"].items()
                          if any(k.startswith("edge_") and v[k]["min"] < 0 for k in v)})
    res = dict(room_min_required=room_min, edge_room_required=0.0, flow_tolerance=flow_tol,
               before=before, after={s: {k: (round(v, 4) if v is not None else None) for k, v in a.items()} for s, a in after.items()},
               layers_changed=changed,
               offsets={str(L): [round(uA[L], 4), round(uB[L], 4)] for L in changed},
               back_on_target_from=next((L for L in range(nL, F, -1) if abs(uA[L]) > 1e-9 or abs(uB[L]) > 1e-9), F) + 1,
               ends_on_target=abs(uA[nL]) < 1e-9 and abs(uB[nL]) < 1e-9,
               max_raise=round(max([0.0] + [max(uA[L], uB[L]) for L in changed]), 4),
               goals_unmet=unmet,
               model_room_difference=round(mism_z, 4), edge_room_bound=round(edge_bound, 4), edge_layers=edge_layers)
    # synchronised E, point by point on the moves the two heads of a pair print at the same time
    if dual is not None:
        sp0 = sync_flow_points(dual, plan, room_min, flow_tol)
        sp1 = sync_flow_points(dual, plan, room_min, flow_tol, uA={L: uA[L] for L in changed}, uB={L: uB[L] for L in changed}) \
            if changed else sp0
        res["sync_as_planned"] = dict(first_conflict=sp0["first_conflict"], stats=sp0["stats"])
        res["sync_with_compensation"] = dict(first_conflict=sp1["first_conflict"], stats=sp1["stats"], examples=sp1["examples"])
        res["sync_flow_failures"] = sp1["examples"]
        res["flow_warning"] = sp1["stats"].get("flow_warning")
    else:
        res["sync_flow_failures"] = []
    centre_ok = all(ok_room(a["centre_min"], room_min) for a in after.values()) and (bool(changed) or not pos_short)
    res["positive_room_short"] = pos_short[:20]
    edge_ok = all(ok_room(a["edge_min"], 0.0) for a in after.values())
    res["accepted_small_negative"] = dict(
        rule_mm=DUAL_LIMITS["small_negative_room"],
        centre={sd: after[sd]["centre_min"] for sd in after if after[sd]["centre_min"] is not None and small_negative(after[sd]["centre_min"])},
        edge={sd: after[sd]["edge_min"] for sd in after if after[sd]["edge_min"] is not None and small_negative(after[sd]["edge_min"])},
        sync=(res.get("sync_with_compensation") or {}).get("stats", {}).get("accepted_examples", []),
        sync_points=(res.get("sync_with_compensation") or {}).get("stats", {}).get("accepted_points", 0),
        sync_moves=(res.get("sync_with_compensation") or {}).get("stats", {}).get("accepted_moves", 0))
    res["feasible"] = bool(centre_ok and edge_ok and not res["sync_flow_failures"] and res["ends_on_target"])
    res["conditions"] = dict(centre_room=centre_ok, edge_room=edge_ok, synchronised_flow=not res["sync_flow_failures"],
                             back_on_target=res["ends_on_target"])
    return res


# ======================================================================================
# Synchronised E, point by point
# ======================================================================================
def sync_flow_points(dual, plan, room_min, flow_tol=0.017, uA=None, uB=None, spacing=0.1, max_items=20):
    """Replays both models together, layer by layer, pair turn by pair turn. For every point of
    every extrusion move printed by a synchronised pair (layers above the independent ones) it
    takes the room under the nozzle on BOTH models at that same slice point, the E each head
    would need there (flow area A(room, w) / A(h, w), +/- flow_tol), and intersects the two
    acceptable E intervals. An empty intersection (or no room on one model) is a conflict.

    Independent layers (one head at a time, own E) are replayed for the deposits only.
    uA/uB: optional per-layer raise of pair A / pair B (local compensation).
    """
    from .dualz import _Lattice, _samples, _kernel, RES
    from .dualplan import PAIR
    EPS_ = EPS
    uA = uA or {}
    uB = uB or {}
    F = plan.foundation_layers
    nL = len(plan.layer_z)
    m0, m2 = dual.models[0], dual.models[2]
    bx0 = min(m0.bbox_motion[0], m2.bbox_motion[0]) - 2
    by0 = min(m0.bbox_motion[1], m2.bbox_motion[1]) - 2
    bx1 = max(m0.bbox_motion[2], m2.bbox_motion[2]) + 2
    by1 = max(m0.bbox_motion[3], m2.bbox_motion[3]) + 2
    lat = _Lattice(plan, bx0, by0, bx1, by1)
    slot = plan.slots[0]
    pad = 1.0
    gx = int(math.ceil((bx1 - bx0 + 2 * pad) / RES)) + 1
    gy = int(math.ceil((by1 - by0 + 2 * pad) / RES)) + 1
    side_of = {0: "left", 2: "left", 1: "right", 3: "right"}
    ref = {"left": 0, "right": 1}
    shift = {t: np.subtract(plan.phys_xy(t, 0.0, 0.0), plan.phys_xy(ref[side_of[t]], 0.0, 0.0)) for t in range(4)}
    top = {s: np.full((gx, gy), -1e9) for s in ref}
    col = {s: np.zeros((gx, gy), dtype=np.int8) for s in ref}
    lay = {s: np.zeros((gx, gy), dtype=np.int16) for s in ref}
    cur_layer = [0]
    kern = {}
    h = {L: plan.layer_z[L - 1] - (plan.layer_z[L - 2] if L >= 2 else 0.0) for L in range(1, nL + 1)}
    stats = dict(points=0, both_on_material=0, on_other_colour=0, compensated_points=0, conflict_points=0,
                 conflict_moves=0, conflict_layers={}, max_flow_ratio=1.0, room_min={"left": None, "right": None})
    first = None
    examples = []

    def stamp(s, t, ci, cj, tips, widths):
        for r in np.unique(widths):
            if r not in kern:
                kern[r] = _kernel(r)
            K = kern[r]
            sel = widths == r
            ii = (ci[sel][:, None] + K[None, :, 0]).ravel()
            jj = (cj[sel][:, None] + K[None, :, 1]).ravel()
            tt = np.repeat(tips[sel], len(K))
            lin = ii.astype(np.int64) * gy + jj
            order = np.lexsort((tt, lin))
            lin, tt = lin[order], tt[order]
            fst = np.ones(len(lin), dtype=bool)
            fst[1:] = lin[1:] != lin[:-1]
            top[s].flat[lin[fst]] = tt[fst]
            col[s].flat[lin[fst]] = 1 if t in (0, 1) else 2
            lay[s].flat[lin[fst]] = cur_layer[0]

    def head_points(t, mdl, L, extra):
        ext = [m for m in mdl.blocks[L - 1].moves() if m.kind == "extrude"]
        if not ext:
            return None
        xs, ys, k = _samples([(m.x0, m.y0, m.x, m.y) for m in ext], spacing)
        tips = lat.tip(t, L, xs, ys) + extra
        ci = np.rint((xs + shift[t][0] - bx0 + pad) / RES).astype(np.int32)
        cj = np.rint((ys + shift[t][1] - by0 + pad) / RES).astype(np.int32)
        w = np.round(np.array([m.width for m in ext])[k] / 2.0, 3)
        return dict(ext=ext, xs=xs, ys=ys, k=k, tips=tips, ci=ci, cj=cj, w=w)

    for L in range(1, nL + 1):
        cur_layer[0] = L
        turns = [(t,) for t in range(4)] if L <= F else [PAIR["A"], PAIR["B"]]
        for heads in turns:
            src = dual.models[PAIR["A" if heads[0] in (0, 1) else "B"][0]]
            extra = (uA.get(L, 0.0) if heads[0] in (0, 1) else uB.get(L, 0.0)) if L > F else 0.0
            pts = {t: head_points(t, src, L, extra) for t in heads}
            if any(p is None for p in pts.values()):
                continue
            if len(heads) == 2:
                a, b = heads
                pa, pb = pts[a], pts[b]
                ra = pa["tips"] - top[side_of[a]][pa["ci"], pa["cj"]]
                rb = pb["tips"] - top[side_of[b]][pb["ci"], pb["cj"]]
                # only where both nozzles print directly on layer L-1 (a layer thickness); over older
                # layers (sparse infill gaps, bridges) the slice's own E applies on both models
                ok_a = (top[side_of[a]][pa["ci"], pa["cj"]] > -1e8) & (lay[side_of[a]][pa["ci"], pa["cj"]] == L - 1)
                ok_b = (top[side_of[b]][pb["ci"], pb["cj"]] > -1e8) & (lay[side_of[b]][pb["ci"], pb["cj"]] == L - 1)
                both = ok_a & ok_b
                stats["points"] += len(ra)
                stats["both_on_material"] += int(both.sum())
                oth = (col[side_of[a]][pa["ci"], pa["cj"]] != (1 if a in (0, 1) else 2)) | (col[side_of[b]][pb["ci"], pb["cj"]] != (1 if b in (0, 1) else 2))
                stats["on_other_colour"] += int((both & oth).sum())
                for s_, r_ in ((side_of[a], ra), (side_of[b], rb)):
                    if both.any():
                        v = float(r_[both].min())
                        stats["room_min"][s_] = v if stats["room_min"][s_] is None else min(stats["room_min"][s_], v)
                wv = 2.0 * pa["w"]
                A = lambda r: np.maximum(0.0, (wv - r * (1.0 - math.pi / 4.0)) * r)
                need_a = A(np.maximum(ra, 0.0)) / A(np.full_like(ra, h[L]))
                need_b = A(np.maximum(rb, 0.0)) / A(np.full_like(rb, h[L]))
                lo = np.maximum(need_a, need_b) * (1.0 - flow_tol)
                hi = np.minimum(need_a, need_b) * (1.0 + flow_tol)
                from .dualtol import DUAL_LIMITS as _DLm
                neg_lim = _DLm["small_negative_room"]
                # the file prints the slice E (100 %) on these moves; it must lie in both heads' intervals
                e_ok_a = (need_a * (1.0 - flow_tol) <= 1.0 + 1e-12) & (1.0 <= need_a * (1.0 + flow_tol) + 1e-12)
                e_ok_b = (need_b * (1.0 - flow_tol) <= 1.0 + 1e-12) & (1.0 <= need_b * (1.0 + flow_tol) + 1e-12)
                small_a = (ra < 0) & (ra > -neg_lim)
                small_b = (rb < 0) & (rb > -neg_lim)
                big_neg = (ra <= -neg_lim) | (rb <= -neg_lim)
                no_e = both & ~(e_ok_a & e_ok_b)
                # accepted: the mismatch comes from a small negative room on one model; the positive model
                # (if any) still gets an E inside its own interval or the room there is also small negative
                # owner rule: a small negative room (< 0.050 mm) on an extrusion move is an accepted deviation;
                # the move keeps the slice E (100 %), reported as a warning together with both heads' need
                acc = no_e & ~big_neg & (small_a | small_b)
                if acc.any():
                    stats["accepted_points"] = stats.get("accepted_points", 0) + int(acc.sum())
                    mv = np.unique(pa["k"][acc])
                    stats["accepted_moves"] = stats.get("accepted_moves", 0) + len(mv)
                    for i in np.nonzero(acc)[0][:1]:
                        m = pa["ext"][int(pa["k"][i])]
                        if len(stats.setdefault("accepted_examples", [])) < max_items:
                            stats["accepted_examples"].append(dict(layer=L, pair="T%d+T%d" % (a, b), slicer_line=m.line,
                                                                   room_left=round(float(ra[i]), 4), room_right=round(float(rb[i]), 4),
                                                                   e_left_pct=round(float(need_a[i]) * 100, 1),
                                                                   e_right_pct=round(float(need_b[i]) * 100, 1), e_applied_pct=100.0))
                bad = no_e & ~acc
                # independent flow mismatch: both rooms positive and the one E fits only one head
                indep = bad & (ra > 0) & (rb > 0)
                stats["independent_points"] = stats.get("independent_points", 0) + int(indep.sum())
                # owner rule: enough positive room under BOTH heads and only the amount differs -> quality
                # warning; both heads use the slice E of that move
                flow_w = indep & (ra >= room_min - EPS_) & (rb >= room_min - EPS_)
                if flow_w.any():
                    mvw = np.unique(pa["k"][flow_w])
                    fw = stats.setdefault("flow_warning", dict(points=0, moves=0, max_ratio=1.0, max_diff_pct=0.0, where=None, examples=[]))
                    fw["points"] += int(flow_w.sum())
                    fw["moves"] += len(mvw)
                    rat = np.where(flow_w, np.maximum(need_a, need_b) / np.maximum(np.minimum(need_a, need_b), 1e-9), 1.0)
                    dif = np.where(flow_w, np.abs(need_a - need_b) * 100.0, 0.0)
                    q = int(np.argmax(dif))
                    if dif[q] > fw["max_diff_pct"]:
                        m = pa["ext"][int(pa["k"][q])]
                        fw["max_diff_pct"], fw["max_ratio"] = float(dif[q]), float(rat[q])
                        fw["where"] = dict(layer=L, pair="T%d+T%d" % (a, b), slicer_line=m.line, room_left=round(float(ra[q]), 4),
                                           room_right=round(float(rb[q]), 4), e_left_pct=round(float(need_a[q]) * 100, 1),
                                           e_right_pct=round(float(need_b[q]) * 100, 1), e_applied_pct=100.0)
                    if len(fw["examples"]) < max_items:
                        m = pa["ext"][int(pa["k"][q])]
                        fw["examples"].append(dict(layer=L, pair="T%d+T%d" % (a, b), slicer_line=m.line,
                                                   room_left=round(float(ra[q]), 4), room_right=round(float(rb[q]), 4),
                                                   e_left_pct=round(float(need_a[q]) * 100, 1), e_right_pct=round(float(need_b[q]) * 100, 1)))
                    bad = bad & ~flow_w
                comp = both & ~bad & ((np.abs(need_a - 1.0) > flow_tol) | (np.abs(need_b - 1.0) > flow_tol))
                stats["compensated_points"] += int(comp.sum())
                if bad.any():
                    stats["conflict_points"] += int(bad.sum())
                    mv = np.unique(pa["k"][bad])
                    stats["conflict_moves"] += len(mv)
                    key = "%d %s" % (L, "T%d+T%d" % (a, b))
                    stats["conflict_layers"][key] = stats["conflict_layers"].get(key, 0) + len(mv)
                    rat = np.where(bad & (np.minimum(need_a, need_b) > 0), np.maximum(need_a, need_b) / np.maximum(np.minimum(need_a, need_b), 1e-9), 1.0)
                    stats["max_flow_ratio"] = max(stats["max_flow_ratio"], float(rat.max()))
                    for i in np.nonzero(bad)[0][:: max(1, int(bad.sum() // 3))][:3]:
                        m = pa["ext"][int(pa["k"][i])]
                        pxa = plan.phys_xy(a, float(pa["xs"][i]) + slot.dx, float(pa["ys"][i]) + slot.dy)
                        pxb = plan.phys_xy(b, float(pb["xs"][i]) + slot.dx, float(pb["ys"][i]) + slot.dy)
                        item = dict(layer=L, pair="T%d+T%d" % (a, b), slicer_line=m.line, feature=m.ftype,
                                    kind=("independent flow mismatch" if (ra[i] > 0 and rb[i] > 0) else "negative room >= %.3f mm" % neg_lim),
                                    e_applied_pct=100.0,
                                    left_xy=[round(pxa[0], 2), round(pxa[1], 2)], right_xy=[round(pxb[0], 2), round(pxb[1], 2)],
                                    room_left=round(float(ra[i]), 4), room_right=round(float(rb[i]), 4),
                                    e_left_pct=round(float(need_a[i]) * 100, 1), e_right_pct=round(float(need_b[i]) * 100, 1),
                                    below_left="colour %d" % col[side_of[a]][pa["ci"][i], pa["cj"][i]],
                                    below_right="colour %d" % col[side_of[b]][pb["ci"][i], pb["cj"][i]])
                        if first is None:
                            first = item
                        if len(examples) < max_items:
                            examples.append(item)
            for t in heads:
                p = pts[t]
                stamp(side_of[t], t, p["ci"], p["cj"], p["tips"], p["w"])
    stats["conflict_layers"] = dict(sorted(stats["conflict_layers"].items(), key=lambda kv: int(kv[0].split()[0])))
    return dict(flow_tolerance=flow_tol, first_conflict=first, examples=examples, stats=stats,
                feasible=first is None)
