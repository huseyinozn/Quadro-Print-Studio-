"""Dual Color Copy Mode planning.

Two copies of a two-colour model, side by side. Fixed head mapping:
    left model:  colour 1 = T0, colour 2 = T2
    right model: colour 1 = T1, colour 2 = T3
Pair A = T0 (primary) + T1 (COPY); pair B = T2 (primary) + T3 (COPY).
Layers 1..F: every head prints its own region alone (T0, T1, T2, T3), each on
its own measured mesh + offsets. From layer F+1 pair A prints the colour-1
regions of both models together, parks, then pair B prints the colour-2 regions.

Why a profile can be refused
Both pairs print on the SAME two models with ONE shared Z and ONE shared Y per
pair. Head t's nozzle sits at commanded - offset. On the left model the T0 and
T2 regions of a layer must meet at one height; on the right model the T1 and T3
regions too. With commanded heights Z_A (pair A) and Z_B (pair B):
    left  needs Z_B - Z_A = oz2 - oz0,   right needs Z_B - Z_A = oz3 - oz1.
If (oz2 - oz0) != (oz3 - oz1) both cannot hold; the best choice splits the
difference and leaves a residual r_z = |(oz2-oz0) - (oz3-oz1)| / 2 on every
colour boundary (one colour sits r_z higher than the other, and a layer that
changes colour is h +/- r_z thick). The same holds in Y with the Y offsets
(registration error r_y between the two colours of each model). X is exact
(each pair has its own carriage spacing). The plan refuses when the boundary
thickness leaves [h_min, h_max] or r_y exceeds the registration limit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .diagnostics import Diagnostics
from .geometry import translate, bbox_gap, poly_distance
from .mesh import BedMesh
from .plan import LayoutOptions, Slot, _stacks, purge_band_top
from .dualtol import DUAL_LIMITS as _DL

DUAL_LIMITS_INTO = _DL["into_material"]

PAIR = {"A": (0, 1), "B": (2, 3)}
PAIR_OF = {0: "A", 1: "A", 2: "B", 3: "B"}
RIGHT = {1, 3}


@dataclass
class DualOptions:
    foundation_layers: int = 3          # default number of independent layers (start of the search)
    foundation_auto: bool = True        # use more only if 3 cannot even out bed + head heights
    foundation_max: int = 10            # never more than this (and never more than a quarter of the model)
    head_margin: float = 5.0
    min_gap: float = 5.0
    tower_size: float = 10.0
    tower_wall_speed: float = 16.7      # mm/s
    tower_infill_speed: float = 100.0   # mm/s
    tower_gap: float = 6.0              # tower to model / tower to tower (mm)
    registration_limit: float = 0.30    # mm, max Y offset between the two colours of one model (dualtol)
    z_deviation_limit: float = 0.30     # mm, max |local thickness - nominal| where colours stack (dualtol)
    travel_lift_clearance: float = 0.05 # mm, raised travel ends this far above the other colour's top
    z_split: str = "auto"               # auto | symmetric | least_into_material (pair-B height choice)
    sync_flow_tolerance: float = 0.017  # relative; one E for a synchronised pair must fit both models (verifier e_found_rel)
    h_min_ratio: float = 0.2
    h_max_ratio: float = 0.75
    standby_temp: float | None = None   # default: print temp + slice standby_temperature_delta
    travel_clearance: float = 1.0
    park_clearance: float = 5.0
    start_z: float = 8.0
    max_segment: float = 4.0
    lin_tol: float = 0.001
    min_segment: float = 0.25
    purge_length: float = 100.0
    purge_e_per_mm: float = 0.15
    purge_feed: float = 900.0
    purge_step: float = 2.0
    purge_edge_margin: float = 10.0
    purge_lane_gap: float = 0.7
    parallel_speed: float = 350.0
    park_accel: float = 7000.0
    lift_feed: float = 1000.0
    descend_feed: float = 600.0
    outlier_threshold: float = 0.25
    sink_tolerance: float = 0.03        # mm, nozzle below the other colour's material (dualtol into_material)
    preset: str = "auto"


class DualPlan:
    mode = "dual"

    def __init__(self):
        self.checks = []

    # ------------------------------------------------------------- geometry
    def phys_xy(self, t, x, y):
        """Physical nozzle XY of head t when the pair frame point is (x, y)."""
        s = self.y_shift[PAIR_OF[t]]
        h0 = PAIR[PAIR_OF[t]][0]
        dx = self.spacing if t in RIGHT else 0.0
        return x + dx, y + s + self.offsets[h0][1] - self.offsets[t][1]

    def S(self, t, x, y, mesh=None):
        px, py = self.phys_xy(t, x, y)
        return (mesh or self.mesh).z(px, py) + self.offsets[t][2]

    def mid(self, x, y, mesh=None):
        v = [self.S(t, x, y, mesh) - (self.dstar if PAIR_OF[t] == "B" else 0.0) for t in range(4)]
        return 0.5 * (max(v) + min(v))

    def C(self, pair, x, y, mesh=None):
        return self.layer_z[self.foundation_layers - 1] + self.mid(x, y, mesh) + (self.dstar if pair == "B" else 0.0)

    def zbase(self, L, t_ref, x, y, mesh=None):
        """Commanded Z of the nominal layer-L surface; t_ref = head (foundation) or 'A'/'B' (pairs)."""
        F = self.foundation_layers
        if L <= 0:
            return self.S(t_ref, x, y, mesh)
        zL = self.layer_z[L - 1]
        zF = self.layer_z[F - 1]
        if isinstance(t_ref, str):
            return zL - zF + self.C(t_ref, x, y, mesh)
        z1 = self.layer_z[0]
        s = self.S(t_ref, x, y, mesh)
        c = self.C(PAIR_OF[t_ref], x, y, mesh)
        if L > F:
            return zL - zF + c
        if F == 1:
            return c
        frac = (zL - z1) / (zF - z1)
        return z1 + s + frac * (c - z1 - s)

    def thickness(self, L, t_ref, x, y, mesh=None):
        if L > self.foundation_layers or isinstance(t_ref, str):
            return self.layer_z[L - 1] - (self.layer_z[L - 2] if L >= 2 else 0.0)
        return self.zbase(L, t_ref, x, y, mesh) - self.zbase(L - 1, t_ref, x, y, mesh)

    def t0_point(self, slot, sx, sy):
        return sx + slot.dx, sy + slot.dy

    def cmd_xy(self, t, slot, sx, sy, copy=False):
        """Commanded XY of the primary of the pair (copy=True) or of head t alone."""
        x, y = sx + slot.dx, sy + slot.dy
        h0 = PAIR[PAIR_OF[t]][0]
        ycmd = y + self.y_shift[PAIR_OF[t]] + self.offsets[h0][1]
        if copy:
            return x + self.offsets[h0][0], ycmd
        px = x + (self.spacing if t in RIGHT else 0.0)
        return px + self.offsets[t][0], ycmd

    def pair_of(self, tools):
        return PAIR_OF[tools[0]]

    def as_json(self):
        d = {k: v for k, v in self.__dict__.items() if k not in ("mesh", "slots")}
        d["slots"] = [s.__dict__ for s in self.slots]
        return d


def make_dual_plan(dual, prof, opts: DualOptions | None = None):
    """Returns (plan, diags). The plan is returned even when checks fail so the
    preview can show the mapping; generation requires diags.ok()."""
    from dataclasses import asdict
    opts = opts or DualOptions()
    o = asdict(opts)
    diags = Diagnostics()
    plan = DualPlan()
    checks = plan.checks

    def check(name, ok, detail, severity="error", **kw):
        checks.append(dict(name=name, ok=bool(ok), detail=detail, **kw))
        if not ok:
            (diags.error if severity == "error" else diags.warning)("PLAN_" + name.upper(), detail)

    m0, m2 = dual.models[0], dual.models[2]
    F = o["foundation_layers"]
    if not (1 <= F < dual.layer_count):
        diags.error("PLAN_FOUNDATION", "foundation_layers=%d invalid" % F)
        return None, diags
    nozzle = prof.extruders[0].nozzle_diameter
    for t, e in prof.extruders.items():
        if abs(e.nozzle_diameter - m0.config["nozzle_diameter"]) > 1e-6:
            diags.error("PLAN_NOZZLE", "T%d nozzle %.2f differs from sliced nozzle %.2f" % (t, e.nozzle_diameter, m0.config["nozzle_diameter"]))
    offs = {t: (prof.offsets[t].x, prof.offsets[t].y, prof.offsets[t].z) for t in range(4)}
    mesh = prof.mesh
    plan.offsets = offs
    plan.mesh = mesh
    plan.options = o
    plan.foundation_layers = F
    plan.layer_z = [b.z for b in m0.blocks]
    plan.heads = [0, 1, 2, 3]
    plan.colours = dual.colours

    # ---- colour mapping ------------------------------------------------------------------------
    rr = getattr(dual, "right_replaced", None)
    if getattr(dual, "right_source", "slice") == "left":
        lay = sorted({x["layer"] for k in ("colour1", "colour2") for x in (rr or {}).get(k, [])})
        plan.colour_mapping = dict(source="left model T0 (colour 1) and T2 (colour 2)", right="copy of the left regions",
                                   slice_right_differed_on_layers=lay)
        check("colour_mapping", True,
              "Colour mapping: left T0 = colour 1, T2 = colour 2; right T1 = copy of T0's regions, T3 = copy of T2's regions "
              "(built-in example: the slice's own right-model painting differed on %d layers and is not used)." % len(lay))
    else:
        tm = getattr(dual, "tool_map", {0: 0, 1: 1, 2: 2, 3: 3})
        plan.colour_mapping = dict(source="slice", right="the slice's right model (verified equal to the left model's regions)",
                                   slice_tool_for_head={"T%d" % h: "T%d" % tm[h] for h in range(4)},
                                   slice_colours={"T%d" % h: dual.colours[h] for h in range(4)})
        renum = "" if tm == {0: 0, 1: 1, 2: 2, 3: 3} else (
            " Slice tools mapped by geometry: head T0 = slice T%d (left), T1 = slice T%d (right), T2 = slice T%d (left), "
            "T3 = slice T%d (right)." % (tm[0], tm[1], tm[2], tm[3]))
        check("colour_mapping", True, "Colour mapping: left T0 = colour 1, T2 = colour 2; right T1 = colour 1, T3 = colour 2; "
                                      "the right model's regions match the left model's on every layer (same area, one "
                                      "translation %.3f, %.3f mm)." % tuple(dual.translation) + renum)

    # ---- pair consistency (the reason a profile may be refused) ---------------------------
    a_z, b_z = offs[2][2] - offs[0][2], offs[3][2] - offs[1][2]
    e_z = a_z - b_z
    r_z = abs(e_z) / 2.0
    hL = m0.blocks[1].height if len(m0.blocks) > 1 else m0.blocks[0].z

    # Shared Z per pair: dstar = Z_B - Z_A. Colour 2 sits delta = dstar - a_z (left) and dstar - b_z
    # (right) above colour 1. Colour 2 prints after colour 1 in every layer, so a nozzle ends up
    # below the other colour by max(0, -delta) (colour 2 beside a higher colour 1) or delta - h
    # (colour 1 of the next layer on a higher colour 2). Two splits are evaluated:
    def into_depth(dl):
        return max(0.0, -dl, dl - hL)
    sym = 0.5 * (a_z + b_z)
    lo_, hi_ = min(a_z, b_z) - hL, max(a_z, b_z) + hL
    best = None
    n = 4000
    for i in range(n + 1):
        d = lo_ + (hi_ - lo_) * i / n
        key = (round(max(into_depth(d - a_z), into_depth(d - b_z)), 9), max(abs(d - a_z), abs(d - b_z)))
        if best is None or key < best[0]:
            best = (key, d)
    least = best[1]
    plan.z_splits = {
        "symmetric": dict(dstar=round(sym, 5), left_delta=round(sym - a_z, 5), right_delta=round(sym - b_z, 5),
                          max_into_material=round(max(into_depth(sym - a_z), into_depth(sym - b_z)), 5)),
        "least_into_material": dict(dstar=round(least, 5), left_delta=round(least - a_z, 5), right_delta=round(least - b_z, 5),
                                    max_into_material=round(max(into_depth(least - a_z), into_depth(least - b_z)), 5)),
    }
    plan.z_split = "symmetric"
    plan.dstar = sym
    h = m0.blocks[1].height if len(m0.blocks) > 1 else m0.blocks[0].z
    hmin, hmax = o["h_min_ratio"] * nozzle, o["h_max_ratio"] * nozzle
    plan.pair_z = dict(T2_minus_T0=round(a_z, 5), T3_minus_T1=round(b_z, 5), mismatch=round(e_z, 5), residual=round(r_z, 5),
                       boundary_thickness=[round(h - r_z, 4), round(h + r_z, 4)], limits=[hmin, hmax])
    plan.z_analysis = None          # filled after placement (needs the layout)
    plan._z_text = ("Z offsets: T2-T0 = %+.4f mm but T3-T1 = %+.4f mm (difference %.4f mm). With one shared Z per pair the two "
                    "colours of each model end up %.4f mm apart (left: colour 2 %s, right: colour 2 %s), so a layer that changes "
                    "colour could be %.3f-%.3f mm thick (allowed %.3f-%.3f mm)."
                    % (a_z, b_z, abs(e_z), r_z, "lower" if plan.dstar - a_z < 0 else "higher",
                       "lower" if plan.dstar - b_z < 0 else "higher", h - r_z, h + r_z, hmin, hmax))
    a_y, b_y = offs[2][1] - offs[0][1], offs[3][1] - offs[1][1]
    k = a_y - b_y
    r_y = abs(k) / 2.0
    # pair B Y shift u splits the error: left model off by |u|, right by |u + k|; u = -k/2 -> both |k|/2
    plan.y_shift = {"A": 0.0, "B": -0.5 * k}
    plan.pair_y = dict(T2_minus_T0=round(a_y, 5), T3_minus_T1=round(b_y, 5), mismatch=round(k, 5), registration_error=round(r_y, 5),
                       limit=o["registration_limit"])
    from .dualtol import within
    check("pair_y_registration", within(r_y, o["registration_limit"]),
          "Y offsets: T2-T0 = %+.4f mm but T3-T1 = %+.4f mm (difference %.4f mm). Both pairs share Y, so the two colours of each model "
          "are shifted %.3f mm against each other (allowed %.3f mm absolute). Separate from Z."
          % (a_y, b_y, abs(k), r_y, o["registration_limit"]), value=round(r_y, 5), limit=o["registration_limit"])

    # ---- placement -----------------------------------------------------------------------------
    fx0, fy0, fx1, fy1 = [min(a, b) for a, b in zip(m0.bbox_extrude[:2], m2.bbox_extrude[:2])] + \
                         [max(a, b) for a, b in zip(m0.bbox_extrude[2:], m2.bbox_extrude[2:])]
    W, H = fx1 - fx0, fy1 - fy0
    oxs = [offs[t][0] for t in range(4)]
    need = prof.min_clearance + o["head_margin"] + max(0.0, oxs[0] - oxs[1], oxs[2] - oxs[3])
    spacing = max(need, W + o["min_gap"])
    plan.spacing = spacing
    bed = [0.0, 0.0, 300.0, 300.0]
    pa = m0.config.get("printable_area", "")
    if pa:
        v = [float(q) for p in pa.split(",") for q in p.split("x")]
        bed = [min(v[0::2]), min(v[1::2]), max(v[0::2]), max(v[1::2])]
    cx = 0.5 * (bed[0] + bed[2]) - spacing / 2.0
    tw, tg = o["tower_size"], o["tower_gap"]
    cy = 0.5 * (bed[1] + bed[3]) + (tw + tg) / 2.0       # leave room for the towers in front
    fcx, fcy = 0.5 * (fx0 + fx1), 0.5 * (fy0 + fy1)
    slot = Slot(0, 0, 0, round(cx, 6), round(cy, 6), round(cx - fcx, 6), round(cy - fcy, 6))
    plan.slots = [slot]
    # towers (pair frame, i.e. physical position for the primary head of each pair)
    tx0 = cx - W / 2.0
    ty0 = cy - H / 2.0 - tg - tw
    plan.towers = {"A": (tx0, ty0), "B": (tx0 + tw + tg, ty0)}
    plan.tower_size = tw

    # footprints (physical) per head
    plan.footprints = {}
    hulls = {}
    for t in range(4):
        mod = dual.models[PAIR[PAIR_OF[t]][0]]
        pts = [translate([p], 0, 0)[0] for p in mod.hull]
        hull = [plan.phys_xy(t, x + slot.dx, y + slot.dy) for x, y in pts]
        hulls[t] = hull
        plan.footprints["%d/0" % t] = [[round(a, 3), round(b, 3)] for a, b in hull]
    plan.tower_squares = {}
    for t in range(4):
        P = PAIR_OF[t]
        x0, y0 = plan.towers[P]
        x0, y0 = plan.phys_xy(t, x0, y0)
        plan.tower_squares[t] = [(x0, y0), (x0 + tw, y0), (x0 + tw, y0 + tw), (x0, y0 + tw)]

    # ---- containment and gaps -----------------------------------------------------------------
    allpts = [p for t in range(4) for p in hulls[t] + plan.tower_squares[t]]
    xs, ys = [p[0] for p in allpts], [p[1] for p in allpts]
    check("bed_bounds", min(xs) >= bed[0] and min(ys) >= bed[1] and max(xs) <= bed[2] and max(ys) <= bed[3],
          "models and towers inside the printable area (x %.1f-%.1f, y %.1f-%.1f)" % (min(xs), max(xs), min(ys), max(ys)))
    check("mesh_coverage", min(xs) >= mesh.min_x and min(ys) >= mesh.min_y and max(xs) <= mesh.max_x and max(ys) <= mesh.max_y,
          "models and towers inside the measured mesh area")
    gap_models = poly_distance(hulls[0], hulls[1])
    check("copy_gap", gap_models >= o["min_gap"] - 1e-6, "gap between the two models %.2f mm (>= %.1f)" % (gap_models, o["min_gap"]), value=gap_models)
    tgap = min(poly_distance(plan.tower_squares[t], hulls[u]) for t in range(4) for u in range(4))
    ttgap = min(poly_distance(plan.tower_squares[t], plan.tower_squares[u]) for t in range(4) for u in range(4) if t < u)
    check("tower_clearance", tgap >= 2.0 and ttgap >= 2.0,
          "towers keep %.2f mm from the models and %.2f mm from each other (>= 2)" % (tgap, ttgap))

    # ---- purge lanes (same front lanes as Farm) --------------------------------------------------
    w_purge = o["purge_e_per_mm"] * math.pi * (m0.config["filament_diameter"] / 2) ** 2 / plan.layer_z[0]
    laneA = bed[1] + w_purge / 2.0 + 0.1
    laneB = laneA + w_purge + o["purge_lane_gap"]
    lx0 = bed[0] + o["purge_edge_margin"]
    rx0 = bed[2] - o["purge_edge_margin"] - o["purge_length"]
    plan.purge = {0: dict(x0=lx0, x1=lx0 + o["purge_length"], y=laneA), 1: dict(x0=rx0, x1=rx0 + o["purge_length"], y=laneA),
                  2: dict(x0=lx0, x1=lx0 + o["purge_length"], y=laneB), 3: dict(x0=rx0, x1=rx0 + o["purge_length"], y=laneB),
                  "width_est": w_purge}
    check("purge_lanes", min(ys) - (laneB + w_purge / 2) >= 1.0, "prime lines clear of models/towers by %.1f mm" % (min(ys) - laneB - w_purge / 2))

    # ---- carriages ---------------------------------------------------------------------------------
    home, left, right = _stacks(prof, 0.0)
    plan.park_home, plan.left_stack, plan.right_stack = home, left, right
    c = prof.min_clearance
    mx0, my0, mx1, my1 = m0.bbox_motion
    plan.tool_ranges = {}
    for t in range(4):
        xs_t = [p[0] + offs[t][0] for p in hulls[t] + plan.tower_squares[t]] + [plan.purge[t]["x0"] + offs[t][0], plan.purge[t]["x1"] + offs[t][0]]
        lo, hi = min(xs_t), max(xs_t)
        allow_lo = max(prof.carriages[t].position_min, left[t - 1] + c if t > 0 else -1e9)
        allow_hi = min(prof.carriages[t].position_max, right[t + 1] - c if t < 3 else 1e9)
        plan.tool_ranges[t] = dict(lo=lo, hi=hi, allowed=[allow_lo, allow_hi])
        check("tool_reach_T%d" % t, allow_lo - 1e-6 <= lo and hi <= allow_hi + 1e-6,
              "T%d alone: carriage %.2f..%.2f within %.2f..%.2f" % (t, lo, hi, allow_lo, allow_hi))
    plan.copy_carriage_delta = {}
    for P, (h0, h1) in PAIR.items():
        d = spacing + offs[h1][0] - offs[h0][0]
        plan.copy_carriage_delta[P] = d
        check("pair_%s_spacing" % P.lower(), d >= c - 1e-9, "pair %s (T%d+T%d) carriage spacing %.3f >= %.2f" % (P, h0, h1, d, c))
    # pair A works while T2/T3 sit at their right endstops; pair B while T0/T1 sit left
    hiA = max(p[0] + offs[1][0] for p in hulls[1] + plan.tower_squares[1])
    loB = min(p[0] + offs[2][0] for p in hulls[2] + plan.tower_squares[2])
    check("pair_parks", home[2] - hiA >= c and loB - home[1] >= c,
          "pair A keeps %.1f mm to parked T2, pair B keeps %.1f mm to parked T1" % (home[2] - hiA, loB - home[1]))

    # ---- independent layers: default 3, more only if 3 cannot even out bed + head heights -------------
    def found_range(Fc):
        plan.foundation_layers = Fc
        g = [1e9, -1e9]
        for t in range(4):
            mod = dual.models[PAIR[PAIR_OF[t]][0]]
            for L in range(2, Fc + 1):
                for mv in mod.blocks[L - 1].moves():
                    if mv.kind != "extrude":
                        continue
                    hh = plan.thickness(L, t, mv.x + slot.dx, mv.y + slot.dy)
                    g = [min(g[0], hh), max(g[1], hh)]
        return g
    Fmax = max(F, min(o["foundation_max"], dual.layer_count // 4)) if o["foundation_auto"] else F
    tried = []
    chosen = None
    for Fc in range(F, Fmax + 1):
        g = found_range(Fc)
        ok = Fc == 1 or (hmin - 1e-9 <= g[0] and g[1] <= hmax + 1e-9)
        tried.append(dict(layers=Fc, thickness=[round(g[0], 4), round(g[1], 4)] if Fc > 1 else None, ok=ok))
        if ok:
            chosen = Fc
            break
    F = chosen if chosen is not None else F
    plan.foundation_layers = F
    plan.foundation_choice = dict(default=o["foundation_layers"], chosen=F, auto=o["foundation_auto"], tried=tried,
                                  pairs_from_layer=F + 1, max_allowed=Fmax, found=chosen is not None)
    check("foundation_layers", chosen is not None,
          ("%d independent layers (default %d) even out every head's own bed and offset height; pairs print together from "
           "layer %d." % (F, o["foundation_layers"], F + 1)) if chosen is not None else
          ("even %d independent layers cannot even out the bed and head heights (layer thickness %.3f-%.3f mm, allowed %.2f-%.2f)."
           % (Fmax, tried[-1]["thickness"][0], tried[-1]["thickness"][1], hmin, hmax)), value=F)

    # ---- Z: deviation limit (owner: 0.30 mm) and the SEPARATE into-material / travel checks -------
    from .dualz import analyse_z
    from .dualtol import within
    zl, sk = o["z_deviation_limit"], o["sink_tolerance"]
    plan.travel_lift = {}
    plan.z_analysis = plan.z_analysis_symmetric = None
    if r_z > 1e-6:
        za = analyse_z(dual, plan, sink_tol=sk, z_dev=zl)
        plan.z_analysis_symmetric = za
        alt = plan.z_splits["least_into_material"]
        want = o["z_split"]
        if want == "least_into_material" or (want == "auto" and (
                "nozzle_into_material" in za["blocking"] or "travel_drag" in za["blocking"]) and alt["max_into_material"] <= sk + 1e-9
                and within(alt["left_delta"], zl) and within(alt["right_delta"], zl)):
            # fix evaluated and applied: choose the pair-B height that keeps every nozzle out of the
            # other colour (offsets and mesh unchanged); re-check the real geometry with it
            plan.dstar, plan.z_split = least, "least_into_material"
            za = analyse_z(dual, plan, sink_tol=sk, z_dev=zl)
        plan.z_analysis = za
    sp = plan.z_splits[plan.z_split]
    devs = {side: max(v["stats"]["colour_change_interior"]["max_abs_deviation"], v["stats"]["colour_change_edge"]["max_abs_deviation"])
            for side, v in (plan.z_analysis or {"sides": {}})["sides"].items()}
    plan.pair_z.update(split=plan.z_split, dstar=round(plan.dstar, 5), left_delta=sp["left_delta"], right_delta=sp["right_delta"],
                       measured_max_deviation=({k: round(v, 4) for k, v in devs.items()} if devs else None), limit=zl)
    zbad = plan.z_analysis is not None and "z_deviation" in plan.z_analysis["blocking"]
    check("pair_z_consistency", not zbad and within(sp["left_delta"], zl) and within(sp["right_delta"], zl),
          "Z offsets: T2-T0 = %+.4f mm, T3-T1 = %+.4f mm. With one shared Z per pair colour 2 sits %+.4f mm (left) and %+.4f mm "
          "(right) against colour 1 (%s split). Where one colour is printed on the other the layer is up to %s mm off its "
          "nominal height (allowed %.2f mm absolute)." % (
              a_z, b_z, sp["left_delta"], sp["right_delta"], plan.z_split.replace("_", " "),
              "/".join("%.3f" % v for v in devs.values()) if devs else "0.000", zl),
          value=round(max([abs(sp["left_delta"]), abs(sp["right_delta"])] + list(devs.values())), 5), limit=zl)
    # nozzle into the other colour's material (extruding / descending): separate, never relaxed
    if plan.z_analysis:
        pts = {side: v["stats"]["sink_extrude"]["points"] + v["stats"]["into_lower_layer"]["points"] for side, v in plan.z_analysis["sides"].items()}
        mx = max(v["stats"]["deepest"]["extrude"] for v in plan.z_analysis["sides"].values())
    else:
        pts, mx = {}, 0.0
    alt_txt = ""
    if plan.z_analysis_symmetric is not None and plan.z_split != "symmetric":
        s0 = plan.z_analysis_symmetric["sides"]
        alt_txt = (" Fix applied: the symmetric split (colour 2 %+.3f / %+.3f mm) put the nozzle up to %.3f mm into colour 1 at %d "
                   "points and dragged %d travels through it; pair B is printed %+.4f mm higher instead (offsets and mesh unchanged)."
                   % (plan.z_splits["symmetric"]["left_delta"], plan.z_splits["symmetric"]["right_delta"],
                      max(max(v["stats"]["sink_extrude"]["max_depth"], v["stats"]["into_lower_layer"]["max_depth"]) for v in s0.values()),
                      sum(v["stats"]["sink_extrude"]["points"] + v["stats"]["into_lower_layer"]["points"] for v in s0.values()),
                      sum(v["stats"]["sink_travel"]["moves"] for v in s0.values()), plan.dstar - plan.z_splits["symmetric"]["dstar"]))
    check("into_material", not any(pts.values()),
          "Nozzle into the other colour while printing: %s points (deepest %.3f mm; allowed %.2f mm).%s"
          % (", ".join("%s %d" % kv for kv in pts.items()) or "0", mx, sk, alt_txt), value=round(mx, 5), limit=sk)
    # travels / wipes through the other colour: raised travel where the slice path would drag
    drags = [dict(dm, side=side) for side, v in (plan.z_analysis or {"sides": {}})["sides"].items() for dm in v["drag_moves"]]
    lifts = [dm for v in (plan.z_analysis or {"sides": {}})["sides"].values() for dm in v["lift_moves"]] if drags else []
    for dm in lifts:
        key = (dm["layer"], PAIR_OF[dm["head"]], dm["slicer_line"])
        plan.travel_lift[key] = max(plan.travel_lift.get(key, 0.0), round(dm["depth"] + o["travel_lift_clearance"], 4))
    plan.travel_lift_report = dict(moves=len(plan.travel_lift), max_lift=max(plan.travel_lift.values(), default=0.0),
                                   examples=drags[:20])
    tmx = max((v["stats"]["deepest"]["travel"] for v in (plan.z_analysis or {"sides": {}})["sides"].values()), default=0.0)
    check("travel_drag", True,
          ("No travel or wipe passes through the other colour." if not drags else
           "%d travel/wipe moves would pass up to %.3f mm through the other colour; they are raised by up to %.3f mm "
           "(raised travel, %.2f mm above it)." % (len(plan.travel_lift), max(d["depth"] for d in drags),
                                                    plan.travel_lift_report["max_lift"], o["travel_lift_clearance"]))
          + (" Deepest travel/wipe below the other colour: %.3f mm (allowed %.2f)." % (max(tmx, 0.0), sk)),
          value=round(max(tmx, 0.0), 5) if not drags else 0.0, limit=sk)

    # ---- room for the filament where one colour is printed on the other (both models) -------------
    from .dualroom import analyse_room
    room_min = o["h_min_ratio"] * nozzle
    plan.room = None
    if plan.z_analysis is not None:
        rm = analyse_room(plan, plan.z_analysis, room_min, t_layer=(o["h_min_ratio"] * nozzle, o["h_max_ratio"] * nozzle),
                          flow_tol=o["sync_flow_tolerance"], edge_goal=False, dual=dual)
        plan.room = rm
        b, a_ = rm["before"], rm["after"]
        if rm["feasible"] and not rm["layers_changed"]:
            txt = "room under every bead printed on the other colour: left %.3f mm, right %.3f mm (min %.2f)" % (
                b["left"]["centre_min"], b["right"]["centre_min"], room_min)
        else:
            fails = rm["sync_flow_failures"]
            ex = fails[0] if fails else None
            txt = ("as planned the smallest room under a bead printed on the other colour is %.3f mm (left) and %.3f mm (right), "
                   "at colour borders %.3f / %.3f mm (needed: %.2f mm under the bead, >= 0 at its edge). Local layer compensation "
                   "(pair heights per layer, back on target from layer %d) reaches %.3f / %.3f mm; " % (
                       b["left"]["centre_min"], b["right"]["centre_min"], b["left"]["edge_min"], b["right"]["edge_min"], room_min,
                       rm["back_on_target_from"], a_["left"]["centre_min"], a_["right"]["centre_min"]))
            why = []
            if not rm["conditions"]["centre_room"]:
                why.append("raises needed on consecutive layers add up (max %.2f mm) and still miss %d layer(s) from layer %d"
                           % (rm["max_raise"], len(rm["goals_unmet"]), rm["goals_unmet"][0]["layer"]) if rm["goals_unmet"] else
                           "the room under the bead stays below the minimum")
            if not rm["conditions"]["synchronised_flow"]:
                fc = rm["sync_with_compensation"]["first_conflict"]
                st = rm["sync_with_compensation"]["stats"]
                why.append("one synchronised E cannot fit both models on the same move: first at layer %d, %s, slicer line %d (%s): "
                           "left (%.2f, %.2f) room %.3f mm on %s needs %.0f %% of the slice E, right (%.2f, %.2f) room %.3f mm on %s "
                           "needs %.0f %% (accepted +/-%.1f %% each, no common E); %d moves in %d layer/pair turns" % (
                               fc["layer"], fc["pair"], fc["slicer_line"], fc["feature"], fc["left_xy"][0], fc["left_xy"][1],
                               fc["room_left"], fc["below_left"], fc["e_left_pct"], fc["right_xy"][0], fc["right_xy"][1],
                               fc["room_right"], fc["below_right"], fc["e_right_pct"], o["sync_flow_tolerance"] * 100,
                               st["conflict_moves"], len(st["conflict_layers"])) if fc else "")
            if not rm["conditions"]["edge_room"]:
                why.append("at continuing colour borders (layers %d-%d) the right model's colour-1 edge cannot get room >= 0 while "
                           "colour 2 on the left stays within %.2f mm of colour 1: the best average is %.4f mm" % (
                               rm["edge_layers"][0], rm["edge_layers"][-1], DUAL_LIMITS_INTO, rm["edge_room_bound"]) if rm["edge_layers"] else "")
            txt += "; ".join(w for w in why if w) + "."
        check("colour_stack_room", rm["feasible"], "Room for the filament: " + txt,
              value=round(min(v for v in (b["left"]["centre_min"], b["right"]["centre_min"]) if v is not None), 4)
              if b["left"]["centre_min"] is not None else None, limit=room_min)
    else:
        check("colour_stack_room", True, "Room for the filament: no colour offset between the pairs, every layer at its nominal height.",
              limit=room_min)

    # ---- foundation thickness over every head's region and tower -----------------------------------
    glob = found_range(F)
    plan.foundation_stats = dict(global_range=[round(v, 4) for v in glob], limits=[hmin, hmax])
    if F > 1:
        check("foundation_thickness", hmin <= glob[0] and glob[1] <= hmax,
              "layers 2..%d local thickness %.4f..%.4f mm within [%.3f, %.3f]" % (F, glob[0], glob[1], hmin, hmax))

    # ---- deviating mesh points (as in Farm) ------------------------------------------------------------
    outs = mesh.outliers(o["outlier_threshold"])
    plan.mesh_deviating_points = outs
    plan.affected_by_outliers = []
    for ol in outs:
        pts = [list(r) for r in mesh.points]
        pts[ol["iy"]][ol["ix"]] = ol["neighbour_median"]
        alt = BedMesh(pts, mesh.min_x, mesh.max_x, mesh.min_y, mesh.max_y, mesh.x_count, mesh.y_count,
                      mesh.x_pps, mesh.y_pps, mesh.algo, mesh.tension, "comparison only")
        for t in range(4):
            mod = dual.models[PAIR[PAIR_OF[t]][0]]
            l1 = [mv for mv in mod.blocks[0].moves() if mv.kind == "extrude"]
            d1 = max(abs(plan.S(t, mv.x + slot.dx, mv.y + slot.dy) - plan.S(t, mv.x + slot.dx, mv.y + slot.dy, alt)) for mv in l1)
            dF = max(abs(plan.zbase(F, t, mv.x + slot.dx, mv.y + slot.dy) - plan.zbase(F, t, mv.x + slot.dx, mv.y + slot.dy, alt)) for mv in l1)
            if d1 > 0.01 or dF > 0.01:
                plan.affected_by_outliers.append(dict(point=[ol["x"], ol["y"]], point_z=ol["z"], deviation=ol["deviation"],
                                                      copy="T%d/slot0" % t, tool=t, slot=0, first_layer_dz_max=round(d1, 4),
                                                      foundation_top_dz_max=round(dF, 4), effect="calculated effect"))
    plan.validated_scope = "Dual Color Copy Mode: software checks only, no physical print evidence for this file"
    plan.sync_pitch = spacing
    plan.profile_summary = prof.summary()
    plan.slice_summary = dict(dual.stats, footprint=[round(W, 2), round(H, 2)])
    return plan, diags
