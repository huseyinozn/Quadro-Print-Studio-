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
                 step=0.002, return_step=None, edge_goal=True):
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

    # ---- 2. greedy local compensation (layers above the independent ones) --------------------------
    grid = np.arange(-0.40, 0.60 + 1e-9, step)
    UA, UB = np.meshgrid(grid, grid, indexing="ij")
    uA = {L: 0.0 for L in range(0, nL + 1)}
    uB = {L: 0.0 for L in range(0, nL + 1)}
    unmet = []
    for L in range(F + 1, nL + 1):
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
    sync = []
    for L in range(F + 1, nL + 1):
        for key, pair, expr in (("c1_on_c2", "T0+T1", uA[L] - uB[L - 1]), ("c2_on_c1", "T2+T3", uB[L] - uA[L - 1])):
            rooms = {}
            for side in sides:
                b = base(side, L, key)
                if b is None:
                    continue
                r = b + expr
                rooms[side] = r
                a = after[side]
                a["centre_min"] = r if a["centre_min"] is None else min(a["centre_min"], r)
                a["centre_max"] = r if a["centre_max"] is None else max(a["centre_max"], r)
            if len(rooms) == 2:
                tl, tr = rooms["left"], rooms["right"]
                al, ar = flow_area(max(tl, 0.0), width), flow_area(max(tr, 0.0), width)
                # one synchronised E for both models: sized for the smaller room (no overfill)
                e_rel = min(al, ar) / flow_area(h[L], width)
                mism = (max(al, ar) / max(min(al, ar), 1e-9) - 1.0) if min(al, ar) > 0 else float("inf")
                sync.append(dict(layer=L, pair=pair, colours="colour 1 on colour 2" if key == "c1_on_c2" else "colour 2 on colour 1",
                                 room_left=round(tl, 4), room_right=round(tr, 4),
                                 e_left_rel=round(al / flow_area(h[L], width), 3), e_right_rel=round(ar / flow_area(h[L], width), 3),
                                 e_given_rel=round(e_rel, 3), flow_mismatch=round(mism, 3) if math.isfinite(mism) else None,
                                 ok=math.isfinite(mism) and mism <= flow_tol))
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
               goals_unmet=unmet, sync_flow=sync, sync_flow_failures=[s_ for s_ in sync if not s_["ok"]],
               model_room_difference=round(mism_z, 4), edge_room_bound=round(edge_bound, 4), edge_layers=edge_layers)
    centre_ok = all(a["centre_min"] is None or a["centre_min"] >= room_min - EPS for a in after.values())
    edge_ok = all(a["edge_min"] is None or a["edge_min"] >= -EPS for a in after.values())
    res["feasible"] = bool(centre_ok and edge_ok and not res["sync_flow_failures"] and res["ends_on_target"])
    res["conditions"] = dict(centre_room=centre_ok, edge_room=edge_ok, synchronised_flow=not res["sync_flow_failures"],
                             back_on_target=res["ends_on_target"])
    return res
