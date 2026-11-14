"""Dual Color Copy Mode: where does the pair Z residual really matter in THIS model?

Both pairs print both models at one shared Z each, so on every model one colour
sits r_z above or below the other (left and right with opposite signs). Whether
that becomes a printing problem depends on the model's geometry. This module
replays the planned nozzle heights over the slice geometry, layer by layer and
head by head in print order, on a 0.1 mm height raster per model, and reports:

* colour_change: a bead laid on the OTHER colour of an earlier layer. The local
  thickness (nozzle tip minus top of the material below) must be within the
  layer-thickness limits [h_min, h_max] (same limits as everywhere else).
  "interior" = the colour really changes vertically; "edge" = the bead's edge
  (half line width) overlaps the other colour at a lateral colour boundary.
* sink_extrude: the nozzle prints next to the other colour of the SAME layer,
  printed higher a moment earlier, and its bead footprint (half line width)
  lies more than ``sink_tol`` below that material's top.
* sink_travel: a slicer travel or wipe at layer height (no z-hop) passes over
  the other colour's higher material with the nozzle opening (radius d/2) more
  than ``sink_tol`` below its top.

Every finding has a layer, head, physical XY and the colour below/beside.
The mesh and offsets are the uploaded profile's, exactly as measured.
"""
from __future__ import annotations

import math

import numpy as np

from .dualplan import PAIR, PAIR_OF

RES = 0.1


def _kernel(radius):
    r = int(math.ceil(radius / RES))
    pts = [(i, j) for i in range(-r, r + 1) for j in range(-r, r + 1) if math.hypot(i, j) * RES <= radius + 1e-9]
    return np.array(pts, dtype=np.int32)


class _Lattice:
    """Planned tip height per head on a 0.5 mm lattice of slice coordinates (bilinear)."""

    def __init__(self, plan, x0, y0, x1, y1, step=0.5):
        self.plan, self.step = plan, step
        self.x0, self.y0 = x0, y0
        nx, ny = int(math.ceil((x1 - x0) / step)) + 2, int(math.ceil((y1 - y0) / step)) + 2
        slot = plan.slots[0]
        xs = x0 + step * np.arange(nx)
        ys = y0 + step * np.arange(ny)
        self.S = {t: np.empty((nx, ny)) for t in range(4)}
        self.C = {P: np.empty((nx, ny)) for P in PAIR}
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                X, Y = x + slot.dx, y + slot.dy
                for t in range(4):
                    self.S[t][i, j] = plan.S(t, X, Y)
                self.C["A"][i, j] = plan.C("A", X, Y)
                self.C["B"][i, j] = self.C["A"][i, j] + 0.0   # C_B = C_A + dstar (added below)
        self.C["B"] += plan.dstar
        self.nx, self.ny = nx, ny

    def _bil(self, arr, x, y):
        fx = (x - self.x0) / self.step
        fy = (y - self.y0) / self.step
        i = np.clip(np.floor(fx).astype(int), 0, self.nx - 2)
        j = np.clip(np.floor(fy).astype(int), 0, self.ny - 2)
        ax, ay = fx - i, fy - j
        return (arr[i, j] * (1 - ax) * (1 - ay) + arr[i + 1, j] * ax * (1 - ay)
                + arr[i, j + 1] * (1 - ax) * ay + arr[i + 1, j + 1] * ax * ay)

    def tip(self, t, L, x, y):
        """Nozzle tip height (physical) of head t on layer L at slice point(s) x, y."""
        p = self.plan
        F = p.foundation_layers
        zL, zF, z1 = p.layer_z[L - 1], p.layer_z[F - 1], p.layer_z[0]
        c = self._bil(self.C[PAIR_OF[t]], x, y)
        if L > F:
            cmd = zL - zF + c
        else:
            s = self._bil(self.S[t], x, y)
            cmd = c if F == 1 else z1 + s + (zL - z1) / (zF - z1) * (c - z1 - s)
        return cmd - p.offsets[t][2]


def _samples(segs, spacing):
    """Sample points along segments: arrays x, y, segment index."""
    xs, ys, idx = [], [], []
    for k, (x0, y0, x1, y1) in enumerate(segs):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / spacing))
        a = np.linspace(0.0, 1.0, n + 1)
        xs.append(x0 + (x1 - x0) * a)
        ys.append(y0 + (y1 - y0) * a)
        idx.append(np.full(n + 1, k, dtype=np.int32))
    if not xs:
        return np.empty(0), np.empty(0), np.empty(0, dtype=np.int32)
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(idx)


def analyse_z(dual, plan, h_limits, sink_tol=0.03, max_items=40):
    """Replays both models. Returns a JSON-able report; ``blocking`` lists the
    finding kinds that violate a criterion."""
    src = {t: dual.models[PAIR[PAIR_OF[t]][0]] for t in range(4)}     # T1 copies T0's paths, T3 copies T2's
    m0, m2 = dual.models[0], dual.models[2]
    bx0 = min(m0.bbox_motion[0], m2.bbox_motion[0]) - 2
    by0 = min(m0.bbox_motion[1], m2.bbox_motion[1]) - 2
    bx1 = max(m0.bbox_motion[2], m2.bbox_motion[2]) + 2
    by1 = max(m0.bbox_motion[3], m2.bbox_motion[3]) + 2
    lat = _Lattice(plan, bx0, by0, bx1, by1)
    slot = plan.slots[0]
    nozzle_r = plan.options.get("nozzle_radius", 0.2)
    sides = {"left": (0, 2), "right": (1, 3)}
    hmin, hmax = h_limits
    nL = len(plan.layer_z)
    F = plan.foundation_layers
    pad = 1.0
    gx = int(math.ceil((bx1 - bx0 + 2 * pad) / RES)) + 1
    gy = int(math.ceil((by1 - by0 + 2 * pad) / RES)) + 1
    report = dict(raster_mm=RES, sink_tolerance_mm=sink_tol, thickness_limits_mm=[hmin, hmax],
                  nozzle_opening_radius_mm=nozzle_r, sides={})
    kernels = {}
    blocking = set()
    for side, heads in sides.items():
        ref = heads[0]
        o_ref = plan.phys_xy(ref, 0.0, 0.0)
        top = np.full((gx, gy), -1e9)
        topc = np.zeros((gx, gy), dtype=np.int8)       # colour of the top material
        topl = np.zeros((gx, gy), dtype=np.int16)      # layer of the top material
        stats = dict(colour_change_interior=dict(cells=0, out_of_limits=0, min=None, max=None, layers=set()),
                     colour_change_edge=dict(cells=0, out_of_limits=0, min=None, max=None, layers=set()),
                     sink_extrude=dict(points=0, max_depth=0.0, layers=set()),
                     sink_travel=dict(moves=0, max_depth=0.0, layers=set()),
                     colour_change_over_gap=dict(cells=0, min=None, max=None),
                     same_colour=dict(min=None, max=None))
        items = dict(colour_change_interior=[], colour_change_edge=[], sink_extrude=[], sink_travel=[])

        def upd(d, v):
            d["min"] = v if d["min"] is None else min(d["min"], v)
            d["max"] = v if d["max"] is None else max(d["max"], v)

        def footprint(t, L, shift):
            ext = [m for m in src[t].blocks[L - 1].moves() if m.kind == "extrude"]
            if not ext:
                return None
            widths = np.array([m.width for m in ext])
            xs, ys, k = _samples([(m.x0, m.y0, m.x, m.y) for m in ext], 0.05)
            tips = lat.tip(t, L, xs, ys)
            ci = np.rint((xs + shift[0] - bx0 + pad) / RES).astype(np.int32)
            cj = np.rint((ys + shift[1] - by0 + pad) / RES).astype(np.int32)
            wk = np.round(widths[k] / 2.0, 3)
            I, J, T, S = [], [], [], []
            for r in np.unique(wk):
                if r not in kernels:
                    kernels[r] = _kernel(r)
                K = kernels[r]
                sel = np.nonzero(wk == r)[0]
                I.append((ci[sel][:, None] + K[None, :, 0]).ravel())
                J.append((cj[sel][:, None] + K[None, :, 1]).ravel())
                T.append(np.repeat(tips[sel], len(K)))
                S.append(np.repeat(sel, len(K)))
            lin = np.concatenate(I) * gy + np.concatenate(J)
            tp, sk = np.concatenate(T), np.concatenate(S)
            order = np.lexsort((tp, lin))              # per cell: the lowest tip over it (bead top)
            lin, tp, sk = lin[order], tp[order], sk[order]
            first = np.ones(len(lin), dtype=bool)
            first[1:] = lin[1:] != lin[:-1]
            cl, ct, cs = lin[first], tp[first], sk[first]
            return cl // gy, cl % gy, ct, cs, xs, ys

        shifts = {t: np.subtract(plan.phys_xy(t, 0.0, 0.0), o_ref) for t in heads}
        for L in range(1, nL + 1):
            fps = {t: footprint(t, L, shifts[t]) for t in heads}
            masks = {}
            for t in heads:
                mk = np.zeros((gx, gy), dtype=bool)
                if fps[t] is not None:
                    mk[fps[t][0], fps[t][1]] = True
                masks[t] = mk
            for t in heads:                              # colour 1 first, then colour 2 (print order)
                col = 1 if t in (0, 1) else 2
                oth = other_head(t)
                fp = fps[t]
                if fp is not None:
                    ui, uj, ct, cs, xs, ys = fp
                    below, bcol, blay = top[ui, uj], topc[ui, uj], topl[ui, uj]
                    has = below > -1e8
                    other = has & (bcol != col)
                    same = other & (blay == L)
                    depth = below - ct
                    sk_mask = same & (depth > sink_tol)
                    if sk_mask.any():
                        d = stats["sink_extrude"]
                        d["points"] += int(sk_mask.sum())
                        d["layers"].add(L)
                        q = int(np.argmax(np.where(sk_mask, depth, -1)))
                        d["max_depth"] = max(d["max_depth"], float(depth[q]))
                        if len(items["sink_extrude"]) < max_items:
                            items["sink_extrude"].append(self_item(plan, slot, t, L, xs[cs[q]], ys[cs[q]], depth=float(depth[q]),
                                                                   beside="colour %d (T%d), same layer" % (3 - col, oth),
                                                                   cells=int(sk_mask.sum())))
                    # a bead over a gap of more than one layer (bridge, sparse infill below): not a
                    # layer thickness; reported as deviation from the slice's own gap, not judged
                    gap = other & (blay < L - 1)
                    if gap.any():
                        nom = plan.layer_z[L - 1] - np.array(plan.layer_z)[np.maximum(blay[gap], 1) - 1]
                        dv = (ct - below)[gap] - nom
                        upd(stats["colour_change_over_gap"], float(dv.min())); upd(stats["colour_change_over_gap"], float(dv.max()))
                        stats["colour_change_over_gap"]["cells"] += int(gap.sum())
                    chg = other & (blay == L - 1)
                    if chg.any():
                        th = ct - below
                        edge = chg & masks[oth][ui, uj]
                        for name, msk in (("colour_change_edge", edge), ("colour_change_interior", chg & ~edge)):
                            if not msk.any():
                                continue
                            d = stats[name]
                            v = th[msk]
                            d["cells"] += int(msk.sum())
                            upd(d, float(v.min())); upd(d, float(v.max()))
                            bad = msk & ((th < hmin - 1e-9) | (th > hmax + 1e-9))
                            if bad.any():
                                d["out_of_limits"] += int(bad.sum())
                                d["layers"].add(L)
                                q = int(np.argmax(np.where(bad, np.abs(th - 0.5 * (hmin + hmax)), -1)))
                                if len(items[name]) < max_items:
                                    items[name].append(self_item(plan, slot, t, L, xs[cs[q]], ys[cs[q]], thickness=float(th[q]),
                                                                 below="colour %d (T%d), layer %d" % (3 - col, oth, int(blay[q])),
                                                                 cells=int(bad.sum())))
                    sc = has & ~other & (blay == L - 1)
                    if sc.any():
                        v = (ct - below)[sc]
                        upd(stats["same_colour"], float(v.min())); upd(stats["same_colour"], float(v.max()))
                # travels / wipes at layer height
                bz = src[t].blocks[L - 1].z
                trav = [m for m in src[t].blocks[L - 1].moves()
                        if m.kind in ("travel", "wipe") and abs(m.z - bz) < 1e-6 and abs(m.z0 - bz) < 1e-6]
                if trav:
                    rk = r_key(nozzle_r)
                    if rk not in kernels:
                        kernels[rk] = _kernel(nozzle_r)
                    K = kernels[rk]
                    sh = shifts[t]
                    for m in trav:
                        xs_, ys_, _ = _samples([(m.x0, m.y0, m.x, m.y)], 0.05)
                        tips = lat.tip(t, L, xs_, ys_)
                        ci = np.rint((xs_ + sh[0] - bx0 + pad) / RES).astype(np.int32)
                        cj = np.rint((ys_ + sh[1] - by0 + pad) / RES).astype(np.int32)
                        ii = ci[:, None] + K[None, :, 0]
                        jj = cj[:, None] + K[None, :, 1]
                        tb = top[ii, jj]
                        oc = (topc[ii, jj] != col) & (tb > -1e8)
                        dep = np.where(oc, tb - tips[:, None], -1e9)
                        mx = float(dep.max())
                        if mx > sink_tol:
                            d = stats["sink_travel"]
                            d["moves"] += 1
                            d["layers"].add(L)
                            d["max_depth"] = max(d["max_depth"], mx)
                            q = int(np.unravel_index(int(np.argmax(dep)), dep.shape)[0])
                            if len(items["sink_travel"]) < max_items:
                                items["sink_travel"].append(self_item(plan, slot, t, L, xs_[q], ys_[q], depth=mx, kind=m.kind,
                                                                      over="colour %d (T%d)" % (3 - col, oth), slicer_line=m.line))
                if fp is not None:
                    top[ui, uj] = ct
                    topc[ui, uj] = col
                    topl[ui, uj] = L
        for k, d in stats.items():
            if "layers" in d:
                d["layers"] = _ranges(sorted(d["layers"]))
        report["sides"][side] = dict(heads=["T%d" % h for h in heads], stats=stats, examples=items)
        if stats["colour_change_interior"]["out_of_limits"] or stats["colour_change_edge"]["out_of_limits"]:
            blocking.add("colour_change_thickness")
        if stats["sink_extrude"]["points"]:
            blocking.add("nozzle_sinks_into_other_colour")
        if stats["sink_travel"]["moves"]:
            blocking.add("travel_through_other_colour")
    report["blocking"] = sorted(blocking)
    report["possible_boundary_thickness"] = plan.pair_z["boundary_thickness"]
    return report


def r_key(r):
    return round(r, 3)


def other_head(t):
    return {0: 2, 2: 0, 1: 3, 3: 1}[t]


def self_item(plan, slot, t, L, sx, sy, **kw):
    px, py = plan.phys_xy(t, float(sx) + slot.dx, float(sy) + slot.dy)
    d = dict(layer=int(L), head="T%d" % t, model="left" if t in (0, 2) else "right", colour=1 if t in (0, 1) else 2,
             x=round(px, 2), y=round(py, 2))
    for k, v in kw.items():
        d[k] = round(v, 4) if isinstance(v, float) else v
    return d


def _ranges(v):
    out = []
    for x in v:
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return ["%d" % a if a == b else "%d-%d" % (a, b) for a, b in out]
