"""Independent verification of Dual Color Copy Mode output.

Uses the same output-dialect interpreter as Farm/Copy (``verify.Sim``) and adds:

* every nozzle's deposits are rasterised per layer and compared with ITS OWN
  colour region of the slice (left model: T0 colour 1, T2 colour 2; right
  model: T1 colour 1, T3 colour 2). The comparison does not use the plan; the
  placement of each region is recovered from the deposits themselves;
* registration: the two colours of each model must land within
  ``registration_limit`` of each other (same slice frame);
* pair membership from the machine state (carriage modes and extruder sync),
  not from comments: layers 1..F exactly one head per visit in the order
  T0, T1, T2, T3; from F+1 exactly {T0,T1} then {T2,T3} on every layer;
* towers: one per head per layer before the head's model region, square
  <= tower size + line width, outer wall / infill speeds, thickness on top of
  the head's own tower, kept away from the models;
* prime lines: one straight line per head, immediately followed by that head's
  own first print (no other head extrudes in between);
* thickness is measured per MODEL (both colours together), so a colour
  boundary printed by the other pair at a different height is detected.
"""
from __future__ import annotations

import math

from .verify import Sim, PlaneCells, TOL, analyse

PAIR_PRIMARY = {0: 0, 1: 0, 2: 2, 3: 2}
SIDE = {0: "left", 2: "left", 1: "right", 3: "right"}
RES = 0.25


def _cells_of_segments(segs, dx=0.0, dy=0.0, res=RES):
    cells = set()
    for x0, y0, x1, y1 in segs:
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L / (res * 0.4)))
        for k in range(n + 1):
            x = x0 + (x1 - x0) * k / n + dx
            y = y0 + (y1 - y0) * k / n + dy
            cells.add((int(round(x / res)), int(round(y / res))))
    return cells


def _dilate(c, r=2):
    return {(a + i, b + j) for a, b in c for i in range(-r, r + 1) for j in range(-r, r + 1)}


def _model_segs(model, L):
    return [(m.x0, m.y0, m.x, m.y) for m in model.blocks[L - 1].moves() if m.kind == "extrude"]


class DualSim(Sim):
    dual = True
    # The two colour regions of one model touch: a head may descend next to the other
    # colour's line of the SAME layer, which sits at the same height up to output rounding.
    descend_margin = 0.002

    def __init__(self, prof, models, label, tower_size=10.0, tower_speeds=(16.7, 100.0), **kw):
        kw.setdefault("heads", (0, 1, 2, 3))
        super().__init__(prof, models[0], label, **kw)
        self.models = models
        self.tower = None
        self.tower_size = tower_size
        self.tower_speeds = tower_speeds
        self.towers = {}             # (t, L) -> dict(segs, feeds, line, e)
        self.tower_planes = {}       # t -> {L: PlaneCells}
        self.tower_thick = dict(checked=0, min=1e9, max=-1e9, max_dev=0.0, where=None)
        self.deposits = {}           # (t, L) -> [segments] of model-region extrusion
        self.visit_states = []       # actual machine state at every visit start
        self.ext_events = []         # compressed sequence of (line, head, context)

    # -------------------------------------------------------------- hooks
    def model_for(self, t):
        return self.models[PAIR_PRIMARY[t]]

    def owner_key(self, t, v):
        return ("model", SIDE[t])

    def current_layer(self):
        if self.tower is not None:
            return self.tower["layer"]
        return super().current_layer()

    def copy_key(self, t):
        if self.tower is not None:
            return ("tower", t)
        return super().copy_key(t)

    def start_visit(self, layer, slot, tools, line):
        super().start_visit(layer, slot, tools, line)
        moving = sorted(t for t in range(4) if self.mode[t] in ("PRIMARY", "COPY"))
        self.visit_states.append(dict(layer=layer, line=line, marker_tools=list(tools), moving=moving,
                                      primary=self.primary, extruding=sorted(self.moving_extruders())))

    def comment(self, s, ln):
        if s.startswith("; DUAL_TOWER_END"):
            self.tower = None
            return
        if s.startswith("; DUAL_TOWER"):
            self.end_visit()
            self.close_purge()
            L = int(s.split("layer=")[1].split()[0])
            self.tower = dict(layer=L, line=ln)
            return
        super().comment(s, ln)

    def _event(self, ln, t, ctx):
        if not self.ext_events or self.ext_events[-1][1:] != (t, ctx):
            self.ext_events.append((ln, t, ctx))

    def purge_piece(self, t, rec):
        if self.tower is not None:
            return
        self._event(rec[0], t, "purge")
        super().purge_piece(t, rec)

    def on_extrude(self, t, rec, ln):
        if self.tower is None:
            h = super().on_extrude(t, rec, ln)
            if self.visit is not None:
                self._event(ln, t, "model")
                self.deposits.setdefault((t, self.visit["layer"]), []).append((rec[1], rec[2], rec[3], rec[4]))
            return h
        # ---- tower extrusion
        ln_, x0, y0, x1, y1, z0, z1, e, f = rec[:9]
        L = self.tower["layer"]
        self._event(ln, t, "tower")
        key = self.expect_first.get(t)
        if key is not None:
            self.first_extrusion(t, rec, dict(layer=L))
        tw = self.towers.setdefault((t, L), dict(segs=[], feeds=set(), line=ln, e=0.0))
        tw["segs"].append((x0, y0, x1, y1))
        tw["feeds"].add(round(f, 3))
        tw["e"] += e
        planes = self.tower_planes.setdefault(t, {})
        cur = planes.get(L)
        if cur is None:
            cur = planes[L] = PlaneCells()
            planes.pop(L - 3, None)
        prev = planes.get(L - 1)
        if prev is not None:
            hp = prev.height(0.5 * (x0 + x1), 0.5 * (y0 + y1))
            if hp is not None:
                h = 0.5 * (z0 + z1) - hp
                st = self.tower_thick
                st["checked"] += 1
                st["min"], st["max"] = min(st["min"], h), max(st["max"], h)
                nominal = self.models[0].blocks[L - 1].height
                if L > self.found_layers:
                    if abs(h - nominal) > abs(st["max_dev"]):
                        st["max_dev"], st["where"] = h - nominal, dict(line=ln, head=t, layer=L)
                    if abs(h - nominal) > TOL["thickness_nominal"]:
                        self.issue("error", "TOWER_THICKNESS", "T%d tower layer %d thickness %.4f (nominal %.3f)" % (t, L, h, nominal), ln)
                elif not (self.h_limits[0] <= h <= self.h_limits[1]):
                    self.issue("error", "TOWER_THICKNESS", "T%d tower layer %d thickness %.4f outside limits" % (t, L, h), ln)
        elif L > 1:
            self.issue("error", "TOWER_BASE", "T%d tower layer %d has no tower layer %d below it" % (t, L, L - 1), ln)
        cur.add(x1, y1, z1)
        if L == 1:
            dev = (z1 - self.mesh.z(x1, y1)) - self.models[0].blocks[0].z
            if abs(dev) > TOL["first_layer"]:
                self.issue("error", "FIRST_LAYER_HEIGHT", "T%d tower layer-1 nozzle %.4f mm above bed" % (t, z1 - self.mesh.z(x1, y1)), ln)
        self.grid.deposit(x0, y0, x1, y1, z1, ("tower", t), L)
        return None


def analyse_dual(sim: DualSim, registration_limit=0.10, min_gap=2.0, region_tol=0.005):
    res = analyse(sim, expected_count=4, min_gap=min_gap)
    models = sim.models
    nL = models[0].layer_count
    F = sim.found_layers
    errs = []

    def err(code, msg, line=0):
        sim.issue("error", code, msg, line)
        errs.append(code)

    # ---- placement of each colour region, recovered from its deposits -----------------------
    shift = {}
    for t in range(4):
        segs = [s for (u, L), v in sim.deposits.items() if u == t for s in v]
        if not segs:
            err("DUAL_REGION", "T%d deposited nothing on its model region" % t)
            continue
        bx = min(min(s[0], s[2]) for s in segs)
        by = min(min(s[1], s[3]) for s in segs)
        mb = models[t].bbox_extrude
        shift[t] = (bx - mb[0], by - mb[1])
    res["dual_region_shift"] = {("T%d" % t): [round(v[0], 4), round(v[1], 4)] for t, v in shift.items()}
    reg = {}
    for side, (a, b) in (("left", (0, 2)), ("right", (1, 3))):
        if a in shift and b in shift:
            r = math.hypot(shift[b][0] - shift[a][0], shift[b][1] - shift[a][1])
            reg[side] = round(r, 4)
            if r > registration_limit + 1e-9:
                err("DUAL_REGISTRATION", "%s model: colour 2 (T%d) is %.3f mm off colour 1 (T%d) (allowed %.3f)"
                    % (side, b, r, a, registration_limit))
    res["dual_registration_mm"] = reg

    # ---- every layer: each nozzle prints exactly its own colour region ---------------------------
    worst = dict(value=0.0, where=None)
    bad = []
    layers_ok = {t: 0 for t in range(4)}
    for t in range(4):
        if t not in shift:
            continue
        for L in range(1, nL + 1):
            A = _cells_of_segments(_model_segs(models[t], L), shift[t][0], shift[t][1])
            B = _cells_of_segments(sim.deposits.get((t, L), []))
            if not A and not B:
                layers_ok[t] += 1
                continue
            miss = len(A - _dilate(B)) / max(1, len(A))
            extra = len(B - _dilate(A)) / max(1, len(B))
            w = max(miss, extra)
            if w > worst["value"]:
                worst = dict(value=round(w, 5), where=dict(head="T%d" % t, layer=L))
            if (A and not B) or miss > region_tol or extra > region_tol:
                bad.append(dict(head="T%d" % t, layer=L, region_missing=round(miss, 4), extra_outside_region=round(extra, 4)))
            else:
                layers_ok[t] += 1
    res["dual_region_layers_ok"] = {("T%d" % t): "%d/%d" % (layers_ok[t], nL) for t in range(4)}
    res["dual_region_worst_mismatch"] = worst
    res["dual_region_problems"] = bad[:20]
    if bad:
        err("DUAL_REGION", "%d head-layers do not reproduce their own colour region (first: %s layer %d, missing %.1f%%, outside %.1f%%)"
            % (len(bad), bad[0]["head"], bad[0]["layer"], bad[0]["region_missing"] * 100, bad[0]["extra_outside_region"] * 100))

    # ---- pair membership from machine state -------------------------------------------------------
    per_layer = {}
    pair_bad = []
    for vs in sim.visit_states:
        per_layer.setdefault(vs["layer"], []).append(vs)
        if vs["moving"] != vs["extruding"]:
            pair_bad.append("layer %d line %d: carriages %s but extruders %s move" % (vs["layer"], vs["line"], vs["moving"], vs["extruding"]))
    for L in range(1, nL + 1):
        got = [vs["moving"] for vs in per_layer.get(L, [])]
        want = [[0], [1], [2], [3]] if L <= F else [[0, 1], [2, 3]]
        if got != want:
            pair_bad.append("layer %d printed by %s (expected %s)" % (L, got, want))
    first_pair = min((vs["layer"] for vs in sim.visit_states if len(vs["moving"]) > 1), default=None)
    res["synchronized_from_layer"] = first_pair
    res["dual_pair_problems"] = pair_bad[:10]
    if pair_bad:
        err("DUAL_PAIR", "head pairs differ from T0+T1 / T2+T3: %s" % "; ".join(pair_bad[:2]))

    # ---- towers ---------------------------------------------------------------------------------------
    tw_bad = []
    sq = sim.tower_size + 0.5 + 1e-6
    boxes = {}
    first_model_line = {}
    for vs in sim.visit_states:
        for t in vs["moving"]:
            first_model_line.setdefault((t, vs["layer"]), vs["line"])
    wall_f, fill_f = sim.tower_speeds[0] * 60.0, sim.tower_speeds[1] * 60.0
    for t in range(4):
        for L in range(1, nL + 1):
            tw = sim.towers.get((t, L))
            if tw is None:
                tw_bad.append("T%d layer %d has no tower" % (t, L))
                continue
            xs = [p for s in tw["segs"] for p in (s[0], s[2])]
            ys = [p for s in tw["segs"] for p in (s[1], s[3])]
            bb = (min(xs), min(ys), max(xs), max(ys))
            if bb[2] - bb[0] > sq or bb[3] - bb[1] > sq:
                tw_bad.append("T%d layer %d tower %.2f x %.2f mm" % (t, L, bb[2] - bb[0], bb[3] - bb[1]))
            if t in boxes and (abs(bb[0] - boxes[t][0]) > 0.5 or abs(bb[1] - boxes[t][1]) > 0.5):
                tw_bad.append("T%d tower moved on layer %d" % (t, L))
            boxes.setdefault(t, bb)
            if not tw["feeds"] <= {round(wall_f, 3), round(fill_f, 3)} or round(wall_f, 3) not in tw["feeds"]:
                tw_bad.append("T%d layer %d tower speeds %s mm/min (expected wall %.0f, infill %.0f)" % (t, L, sorted(tw["feeds"]), wall_f, fill_f))
            ml = first_model_line.get((t, L))
            if ml is not None and tw["line"] > ml:
                tw_bad.append("T%d layer %d tower printed after the model region" % (t, L))
    # tower distance to every model region (deposited cells)
    model_cells = {}
    for (t, L), segs in sim.deposits.items():
        model_cells.setdefault(SIDE[t], set()).update(_cells_of_segments(segs[::4] if len(segs) > 400 else segs, res=1.0))
    tower_gap = 1e9
    for t, bb in boxes.items():
        for side, cells in model_cells.items():
            for (cx, cy) in cells:
                dx = max(bb[0] - cx - 0.5, 0.0, cx - 0.5 - bb[2])
                dy = max(bb[1] - cy - 0.5, 0.0, cy - 0.5 - bb[3])
                tower_gap = min(tower_gap, math.hypot(dx, dy))
    res["towers"] = {("T%d" % t): dict(bbox=[round(v, 3) for v in bb]) for t, bb in boxes.items()}
    res["tower_min_gap_to_models_mm"] = round(tower_gap, 2)
    res["tower_thickness"] = dict(sim.tower_thick, min=round(sim.tower_thick["min"], 4), max=round(sim.tower_thick["max"], 4))
    if tower_gap < min_gap:
        tw_bad.append("tower only %.2f mm from a model" % tower_gap)
    res["tower_problems"] = tw_bad[:10]
    if tw_bad:
        err("DUAL_TOWER", "; ".join(tw_bad[:3]))

    # ---- model gap (deposited cells, 1 mm) -----------------------------------------------------------
    if "left" in model_cells and "right" in model_cells:
        rmin = min(c[0] for c in model_cells["right"])
        lmax = max(c[0] for c in model_cells["left"])
        res["min_gap_between_models_mm"] = round(rmin - lmax - 1.0, 2)
        if rmin - lmax - 1.0 < min_gap:
            err("COPY_OVERLAP", "the two models are only %.2f mm apart" % (rmin - lmax - 1.0))

    # ---- prime lines: one per head, directly before that head's own first print ------------------------
    pr_bad = []
    for t in range(4):
        n = sum(1 for p in sim.purges if p["nozzle"] == t)
        if n != 1:
            pr_bad.append("T%d has %d prime lines" % (t, n))
    ev = sim.ext_events
    for i, (ln, t, ctx) in enumerate(ev):
        if ctx != "purge":
            continue
        nxt = ev[i + 1] if i + 1 < len(ev) else None
        if nxt is None or nxt[1] != t or nxt[2] == "purge":
            pr_bad.append("T%d prime line (line %d) is not followed by its own first print" % (t, ln))
        before = [e for e in ev[:i] if e[1] == t]
        if before:
            pr_bad.append("T%d prints before its prime line" % t)
    if sim.purges:
        top = max(p["start"][1] for p in sim.purges)
        lowest = min(min(c[1] for c in cells) for cells in model_cells.values()) if model_cells else 1e9
        lowest = min([lowest] + [bb[1] for bb in boxes.values()])
        if top >= lowest:
            pr_bad.append("prime lines are not in front of the models/towers")
    res["prime_line_problems"] = pr_bad
    if pr_bad:
        err("DUAL_PRIME", "; ".join(pr_bad[:3]))

    # ---- carriage moves of idle heads: only away from the printing head(s) ---------------------------
    # A head that is not printing next must stay where it is or move AWAY from the printing heads
    # (left of them: left; right of them: right). Moving towards them first and back later is
    # the right-then-left detour the planner must avoid.
    tstart = {}
    for (t, L), tw in sim.towers.items():
        tstart.setdefault(tw["line"], set()).add(t)
    starts = sorted([(vs["line"], vs["moving"]) for vs in sim.visit_states]
                    + [(ln, sorted(ts)) for ln, ts in tstart.items()])
    toward = []
    for q in sim.qpms:
        nxt = next((m for (ln, m) in starts if ln > q["line"]), None)
        if not nxt or any(q["start"][t] is None for t in range(4)):
            continue
        lo, hi = min(nxt), max(nxt)
        for t in range(4):
            if t in nxt:
                continue
            mv = q["target"][t] - q["start"][t]
            if (t < lo and mv > 1e-6) or (t > hi and mv < -1e-6):
                toward.append(dict(head="T%d" % t, line=q["line"], printing_next=["T%d" % u for u in nxt],
                                   start=round(q["start"][t], 3), target=round(q["target"][t], 3)))
    res["idle_moves_toward_printing_heads"] = toward[:20]
    if toward:
        d0 = toward[0]
        err("CARRIAGE_DETOUR", "%d idle-head moves towards the printing heads (first: %s %.3f -> %.3f at line %d while %s print)"
            % (len(toward), d0["head"], d0["start"], d0["target"], d0["line"], d0["printing_next"]))

    errc = sum(c for code, c in sim.issue_counts.items() if any(i["code"] == code and i["severity"] == "error" for i in sim.issues))
    res["issues"] = sim.issues
    res["issue_counts"] = sim.issue_counts
    res["error_count"] = errc
    res["passed"] = errc == 0
    res["mode"] = "dual"
    return res


def verify_dual(prof, models, gcode_path, foundation_layers=3, label="candidate", registration_limit=0.10):
    nozzle = prof.extruders[0].nozzle_diameter
    sim = DualSim(prof, models, label, h_limits=(0.2 * nozzle, 0.75 * nozzle)).run(gcode_path, foundation_layers)
    return analyse_dual(sim, registration_limit=registration_limit), sim
