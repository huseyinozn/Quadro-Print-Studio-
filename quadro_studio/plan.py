"""Farm Mode planning: layout, compensation model and feasibility checks.

Coordinate conventions (established from measured data, see REPORT.md):

* *T0 frame*: commanded XY when T0 is primary with zero G-code offset; equal
  to T0's physical nozzle XY (T0 is the calibration reference).
* Tool offsets ``(ox, oy, oz)`` from ``bed_pin_offsets.json`` follow the sign
  convention ``commanded = physical + offset`` for X and Y and
  ``commanded Z = physical nozzle height + oz``. Therefore a tool's physical
  nozzle is at ``carriage_x - ox``, ``Y_cmd - oy`` and ``Z_cmd - oz``.
* In COPY mode all heads share Y and Z. Head ``t`` sits at physical
  ``(x + sync_pitch*t, y - oy_t)`` when T0 is at ``(x, y)``; its carriage is
  at ``x + sync_pitch*t + ox_t``.

Compensation ("independent foundation"):

* ``S_t(p) = mesh(physical XY of head t) + oz_t`` is the commanded Z at
  which head t touches the bed above T0-frame point p.
* Layer 1 of head t is printed at ``z1 + S_t(p)`` (true first-layer
  thickness everywhere).
* Common surface: ``Zc(p) = zF + (max_t S_t(p) + min_t S_t(p)) / 2``.
* Layers 2..F blend linearly in nominal Z from ``z1 + S_t`` to ``Zc``;
  local thickness ``h = Z_L - Z_{L-1}`` varies smoothly; E is scaled by the
  Slic3r flow cross-section ``A(h, w) / A(h_nominal, w)`` at constant width.
* Layers > F are printed in COPY mode at ``z_L + (Zc - zF)``: one commanded Z
  for all four heads, exactly nominal thickness on every head.
* The compensation is embedded in the G-code; the runtime mesh is cleared
  (``BED_MESH_CLEAR``) so the two are never stacked.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from .diagnostics import Diagnostics
from .geometry import poly_distance, translate, bbox, bbox_gap, flow_area
from .mesh import BedMesh

PRESETS = {
    # 300x300 Quadro reference layout: 8 columns x 5 rows for 40 copies.
    "reference-300": dict(sync_pitch=72.0, cols_per_zone=2, col_pitch=35.0, row_pitch=58.0),
}


MODES = {"farm": dict(heads=4), "copy": dict(heads=2)}


@dataclass
class LayoutOptions:
    count: int = 40
    mode: str = "farm"                    # farm (T0-T3, identical copies) | copy (T0+T1, two copies)
    preset: str = "reference-300"         # reference-300 | auto
    sync_pitch: float | None = None
    cols_per_zone: int | None = None
    col_pitch: float | None = None
    row_pitch: float | None = None
    center_x: float | None = None        # default: bed centre
    center_y: float | None = None
    min_gap: float = 2.0                  # minimum edge gap between deposited footprints (mm)
    foundation_layers: int = 5
    standby_temp: float = 140.0
    travel_clearance: float = 1.0         # nozzle tip above highest deposit during inter-part transfers
    park_clearance: float = 5.0           # nozzle tip above highest deposit during carriage parking
    start_z: float = 8.0
    max_segment: float = 4.0              # mesh-following segment length (mm)
    lin_tol: float = 0.001                # max deviation of a straight piece from the compensation surface (mm)
    min_segment: float = 0.25             # never split pieces shorter than this (mm)
    h_min_ratio: float = 0.2              # foundation layer thickness limits as fraction of nozzle
    h_max_ratio: float = 0.75
    purge_length: float = 100.0           # straight front prime line per head
    purge_e_per_mm: float = 0.15          # same density as the profile's start_print purge (E15 / 100 mm)
    purge_feed: float = 900.0
    purge_step: float = 2.0
    purge_edge_margin: float = 10.0
    purge_lane_gap: float = 0.7
    head_margin: float = 5.0              # auto layout: synchronized heads keep min_clearance + this margin
    park_margin: float = 0.0              # extra gap (mm) above min clearance for stacked (non-endstop) parks
    parallel_speed: float = 350.0
    park_accel: float = 7000.0
    lift_feed: float = 1000.0
    descend_feed: float = 600.0
    outlier_threshold: float = 0.25

    def resolved(self):
        d = asdict(self)
        d["heads"] = list(range(MODES.get(self.mode, MODES["farm"])["heads"]))

        if self.preset in PRESETS:
            for k, v in PRESETS[self.preset].items():
                if d.get(k) is None:
                    d[k] = v
        return d


@dataclass
class Slot:
    index: int
    col: int
    row: int
    cx: float      # T0-frame centre of the deposited footprint
    cy: float
    dx: float      # translation slicer -> T0 frame
    dy: float


@dataclass
class Plan:
    options: dict
    profile_summary: dict
    slice_summary: dict
    slots: list
    sync_pitch: float
    offsets: dict
    layer_z: list               # nominal z per layer (index 0 -> layer 1)
    foundation_layers: int
    park_home: dict
    left_stack: dict
    right_stack: dict
    purge: dict                 # tool -> {"x0","x1","y"} physical
    copy_carriage_delta: dict
    tool_ranges: dict
    copy_range: tuple
    checks: list = field(default_factory=list)
    affected_by_outliers: list = field(default_factory=list)
    mesh_deviating_points: list = field(default_factory=list)
    foundation_stats: dict = field(default_factory=dict)
    footprints: dict = field(default_factory=dict)
    validated_scope: str = ""
    heads: list = field(default_factory=lambda: [0, 1, 2, 3])
    mode: str = "farm"
    mesh: BedMesh = None

    # ------------------------------------------------------------------ model
    def S(self, t, x, y, mesh=None):
        o = self.offsets[t]
        return (mesh or self.mesh).z(x + self.sync_pitch * t, y - o[1]) + o[2]

    def mid(self, x, y, mesh=None):
        v = [self.S(t, x, y, mesh) for t in self.heads]
        return 0.5 * (max(v) + min(v))

    def zbase(self, L, t, x, y, mesh=None):
        """Commanded Z of the nominal layer-L surface for head t (t=None in COPY)."""
        F = self.foundation_layers
        zL = self.layer_z[L - 1] if L >= 1 else 0.0
        if L <= 0:
            return self.S(t, x, y, mesh)
        if L > F or t is None:
            return zL + self.mid(x, y, mesh)
        z1 = self.layer_z[0]
        zF = self.layer_z[F - 1]
        s = self.S(t, x, y, mesh)
        if F == 1:
            return zF + self.mid(x, y, mesh)
        frac = (zL - z1) / (zF - z1)
        return z1 + s + frac * ((zF + self.mid(x, y, mesh)) - (z1 + s))

    def thickness(self, L, t, x, y, mesh=None):
        if L > self.foundation_layers:
            return self.layer_z[L - 1] - (self.layer_z[L - 2] if L >= 2 else 0.0)
        return self.zbase(L, t, x, y, mesh) - self.zbase(L - 1, t, x, y, mesh)

    def t0_point(self, slot: Slot, sx, sy):
        return sx + slot.dx, sy + slot.dy

    def cmd_xy(self, t, slot: Slot, sx, sy, copy=False):
        x, y = sx + slot.dx, sy + slot.dy
        if copy:
            return x, y
        return x + self.sync_pitch * t + self.offsets[t][0], y

    def phys_xy(self, t, slot: Slot, sx, sy):
        x, y = sx + slot.dx, sy + slot.dy
        return x + self.sync_pitch * t, y - self.offsets[t][1]

    def as_json(self):
        d = {k: v for k, v in self.__dict__.items() if k not in ("mesh", "slots")}
        d["slots"] = [asdict(s) for s in self.slots]
        return d


def _snake(k, cols):
    order = []
    rows = int(math.ceil(k / float(cols)))
    for r in range(rows):
        cs = list(range(cols)) if r % 2 == 0 else list(range(cols - 1, -1, -1))
        for c in cs:
            if len(order) < k:
                order.append((c, r))
    return order


def _stacks(prof, margin=0.0):
    """Left/right parking stacks. Endstop positions are fixed by the machine; a
    stacked (non-endstop) park keeps ``min_clearance + margin`` to its neighbour."""
    c = prof.min_clearance
    car = prof.carriages
    home = {t: car[t].position_endstop for t in range(4)}
    left, right = {}, {}
    for t in range(4):
        if t == 0:
            left[t] = car[t].position_min
            continue
        v = max(car[t].position_min, left[t - 1] + c)
        if abs(v - home[t]) > 1e-9:
            v = max(car[t].position_min, left[t - 1] + c + margin)
        left[t] = v
    for t in range(3, -1, -1):
        if t == 3:
            right[t] = car[t].position_max
            continue
        v = min(car[t].position_max, right[t + 1] - c)
        if abs(v - home[t]) > 1e-9:
            v = min(car[t].position_max, right[t + 1] - c - margin)
        right[t] = v
    return home, left, right


def purge_band_top(model, o, first_layer=None):
    """Highest y (mm) occupied by the two front prime-line lanes."""
    h = first_layer or model.blocks[0].z
    w = o["purge_e_per_mm"] * math.pi * (model.config["filament_diameter"] / 2) ** 2 / h
    laneA = w / 2.0 + 0.1
    laneB = laneA + w + o["purge_lane_gap"]
    return laneB + w / 2.0


def auto_layout(model, prof, o, k):
    """Model-size-driven layout search (no fixed reference grid).

    Pitches come from the model's whole motion envelope plus a uniform gap g.
    g is searched from 5 mm down to ``min_gap``; for each g the column count per
    head zone is tried from 1 upwards. The first arrangement that fits the
    measured mesh area (minus the prime-line band) with the four COPY carriages
    at least ``min_clearance`` apart wins, i.e. the largest gap is preferred.
    Returns (cols, sync, col_pitch, row_pitch, centre_x, centre_y) or None.
    The full plan checks still run on the result; this is not optimal packing."""
    mesh = prof.mesh
    bed = [float(v) for p in model.config["printable_area"].split(",") for v in p.split("x")] if model.config["printable_area"] else [0, 0, 300, 0, 300, 300, 0, 300]
    bx0, by0, bx1, by1 = min(bed[0::2]), min(bed[1::2]), max(bed[0::2]), max(bed[1::2])
    ux0, ux1 = max(bx0, mesh.min_x), min(bx1, mesh.max_x)
    uy0 = max(by0, mesh.min_y, purge_band_top(model, o) + 1.0)
    uy1 = min(by1, mesh.max_y)
    ex0, ey0, ex1, ey1 = model.bbox_extrude
    mx0, my0, mx1, my1 = model.bbox_motion
    W = max(ex1 - ex0, mx1 - mx0)
    H = max(ey1 - ey0, my1 - my0)
    heads = o.get("heads", [0, 1, 2, 3])
    nh = len(heads)
    oxs = [prof.offsets[t].x for t in heads]
    oys = [prof.offsets[t].y for t in heads]
    need_sync = prof.min_clearance + o.get("head_margin", 0.0) + max([0.0] + [oxs[i] - oxs[i + 1] for i in range(nh - 1)])
    dy = max(oys) - min(oys)          # copies of different heads are shifted in Y by their offsets
    g = 5.0
    while g >= o["min_gap"] - 1e-9:
        for cols in range(1, k + 1):
            rows = int(math.ceil(k / float(cols)))
            cp, rp = W + g, H + g
            sync = max(need_sync, (cols - 1) * cp + W + g)
            span_x = (nh - 1) * sync + (cols - 1) * cp + W
            span_y = (rows - 1) * rp + H + dy
            if span_x <= (ux1 - ux0) - 1e-9 and span_y <= (uy1 - uy0) - 1e-9:
                # the T0 block sits at the returned centre; head t's copies are shifted by -oy_t, so
                # centre the union of all heads' blocks (not the T0 block) on the usable area
                return (cols, sync, cp, rp, 0.5 * (ux0 + ux1), 0.5 * (uy0 + uy1) + 0.5 * (max(oys) + min(oys)))
        g -= 0.25
    return None


def suggest_count(model, prof, opts, requested):
    """Largest multiple of 4 below ``requested`` for which a full plan passes (Farm Mode)."""
    if opts.mode != "farm":
        return None, None
    from dataclasses import replace
    c = (requested // 4) * 4
    if c == requested:
        c -= 4
    while c >= 4:
        o2 = replace(opts, count=c)
        if opts.preset != "auto" or auto_layout(model, prof, o2.resolved(), c // 4) is not None:
            plan, d = make_plan(model, prof, o2)
            if plan is not None and d.ok():
                return c, plan
        c -= 4
    return None, None


def make_plan(model, prof, opts: LayoutOptions):
    diags = Diagnostics()
    o = opts.resolved()
    checks = []

    def check(name, ok, detail, severity="error", **kw):
        checks.append(dict(name=name, ok=bool(ok), detail=detail, **kw))
        if not ok:
            (diags.error if severity == "error" else diags.warning)("PLAN_" + name.upper(), detail)

    # ---- basic request validation -------------------------------------------------
    n = o["count"]
    heads = o["heads"]
    nh = len(heads)
    if o["mode"] not in MODES:
        diags.error("PLAN_MODE", "unknown mode %r" % o["mode"])
        return None, diags
    if o["mode"] == "copy" and n != 2:
        diags.error("PLAN_COUNT", "Copy Mode prints exactly 2 copies (T0 left, T1 right); %d requested" % n,
                    "Choose 2 copies, or Farm Mode for more identical copies.")
        return None, diags
    if o["mode"] == "farm" and (n < 4 or n % 4):
        diags.error("PLAN_COUNT", "requested count %d is not a positive multiple of 4" % n,
                    "Farm Mode prints identical copies on four synchronized heads; choose 4, 8, 12, ...")
        return None, diags
    F = o["foundation_layers"]
    if not (1 <= F < model.layer_count):
        diags.error("PLAN_FOUNDATION", "foundation_layers=%d must be between 1 and %d" % (F, model.layer_count - 1))
        return None, diags
    for key in ("sync_pitch", "cols_per_zone", "col_pitch", "row_pitch"):
        if o.get(key) is None and o["preset"] != "auto":
            diags.error("PLAN_PRESET", "unknown layout preset %r and %s not given" % (o["preset"], key))
            return None, diags
    nozzle = prof.extruders[0].nozzle_diameter
    for t, e in prof.extruders.items():
        if abs(e.nozzle_diameter - model.config["nozzle_diameter"]) > 1e-6:
            diags.error("PLAN_NOZZLE", "T%d nozzle %.2f differs from sliced nozzle %.2f" % (t, e.nozzle_diameter, model.config["nozzle_diameter"]),
                        "Re-slice for the installed nozzle size.")
        if abs(e.filament_diameter - model.config["filament_diameter"]) > 1e-6:
            diags.error("PLAN_FILAMENT", "T%d filament diameter differs from slice" % t)
        if model.nozzle_temp > e.max_temp or model.nozzle_temp < e.min_extrude_temp:
            diags.error("PLAN_TEMP", "nozzle temperature %.0f outside T%d limits" % (model.nozzle_temp, t))
    if model.bed_temp > prof.bed_max_temp:
        diags.error("PLAN_TEMP", "bed temperature %.0f above limit %.0f" % (model.bed_temp, prof.bed_max_temp))
    if abs(prof.mesh.z(150.0, 150.0)) > 0.005:
        diags.warning("PLAN_MESH_DATUM", "mesh is not zero at the Z-home point (150,150): %.4f" % prof.mesh.z(150, 150))
    if not diags.ok():
        return None, diags

    # ---- footprint ------------------------------------------------------------------
    fx0, fy0, fx1, fy1 = model.bbox_extrude
    W, H = fx1 - fx0, fy1 - fy0
    fcx, fcy = 0.5 * (fx0 + fx1), 0.5 * (fy0 + fy1)
    mx0, my0, mx1, my1 = model.bbox_motion
    hull = model.hull
    k = n // nh
    offs = {t: (prof.offsets[t].x, prof.offsets[t].y, prof.offsets[t].z) for t in range(4)}
    bed = [float(v) for p in model.config["printable_area"].split(",") for v in p.split("x")] if model.config["printable_area"] else [0, 0, 300, 0, 300, 300, 0, 300]
    bx0, by0, bx1, by1 = min(bed[0::2]), min(bed[1::2]), max(bed[0::2]), max(bed[1::2])
    mesh = prof.mesh
    ux0, uy0, ux1, uy1 = max(bx0, mesh.min_x), max(by0, mesh.min_y), min(bx1, mesh.max_x), min(by1, mesh.max_y)
    cxb = o["center_x"] if o["center_x"] is not None else 0.5 * (bx0 + bx1)
    cyb = o["center_y"] if o["center_y"] is not None else 0.5 * (by0 + by1)

    if o["preset"] == "auto":
        choice = auto_layout(model, prof, o, k)
        if choice is None:
            diags.error("PLAN_DOES_NOT_FIT",
                        "%d copies of a %.1f x %.1f mm model do not fit the measured bed area with at least %.1f mm between copies"
                        % (n, W, H, o["min_gap"]),
                        "Choose fewer copies (a multiple of 4) or re-slice a smaller model; G-code is never scaled.")
            return None, diags
        o["cols_per_zone"], o["sync_pitch"], o["col_pitch"], o["row_pitch"], cxb, cyb = choice
        o["center_x"], o["center_y"] = cxb, cyb
    cols = min(int(o["cols_per_zone"]), k)
    sync = float(o["sync_pitch"])
    cp = float(o["col_pitch"])
    rp = float(o["row_pitch"])
    rows = int(math.ceil(k / float(cols)))
    span_x = (nh - 1) * sync + (cols - 1) * cp
    x0 = cxb - span_x / 2.0
    y0 = cyb - (rows - 1) * rp / 2.0
    slots = []
    for i, (c, r) in enumerate(_snake(k, cols)):
        cx, cy = x0 + c * cp, y0 + r * rp
        slots.append(Slot(i, c, r, round(cx, 6), round(cy, 6), round(cx - fcx, 6), round(cy - fcy, 6)))

    home, left, right = _stacks(prof, o["park_margin"])
    if o["park_margin"] < 0:
        diags.error("PLAN_PARK_MARGIN", "park margin cannot be negative")
        return None, diags
    plan = Plan(options=o, profile_summary=prof.summary(), slice_summary=dict(model.stats, footprint=[W, H],
                bbox_extrude=model.bbox_extrude, generator=model.generator), slots=slots, sync_pitch=sync,
                offsets=offs, layer_z=[b.z for b in model.blocks], foundation_layers=F, park_home=home,
                left_stack=left, right_stack=right, purge={}, copy_carriage_delta={}, tool_ranges={},
                copy_range=(0, 0), mesh=mesh, heads=list(heads), mode=o["mode"])

    # ---- per-copy physical footprints ----------------------------------------------
    copies = {}
    for t in heads:
        for s in slots:
            dxp, dyp = s.dx + sync * t, s.dy - offs[t][1]
            copies[(t, s.index)] = dict(hull=translate(hull, dxp, dyp),
                                        bbox=(fx0 + dxp, fy0 + dyp, fx1 + dxp, fy1 + dyp),
                                        motion=(mx0 + dxp, my0 + dyp, mx1 + dxp, my1 + dyp))
    plan.footprints = {"%d/%d" % key: [[round(a, 3), round(b, 3)] for a, b in v["hull"]] for key, v in copies.items()}

    # bed / mesh containment
    worst_in = min(min(v["motion"][0] - bx0, v["motion"][1] - by0, bx1 - v["motion"][2], by1 - v["motion"][3]) for v in copies.values())
    check("bed_bounds", worst_in >= 0, "all nozzle paths of all copies inside the printable area (min margin %.2f mm)" % worst_in)
    worst_mesh = min(min(v["bbox"][0] - mesh.min_x, v["bbox"][1] - mesh.min_y, mesh.max_x - v["bbox"][2], mesh.max_y - v["bbox"][3]) for v in copies.values())
    check("mesh_coverage", worst_mesh >= 0, "all deposited footprints inside the measured mesh area (min margin %.2f mm)" % worst_mesh)

    # gaps between deposited footprints (convex hulls)
    keys = list(copies)
    min_gap = float("inf")
    worst_pair = None
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = copies[keys[i]], copies[keys[j]]
            if bbox_gap(a["bbox"], b["bbox"]) > 60.0:
                continue
            d = poly_distance(a["hull"], b["hull"])
            if d < min_gap:
                min_gap, worst_pair = d, (keys[i], keys[j])
    if min_gap == float("inf"):
        min_gap = 1e9
    check("copy_gap", min_gap >= o["min_gap"], "minimum edge gap between deposited footprints %.3f mm (required >= %.2f) between %s"
          % (min_gap, o["min_gap"], ["T%d/slot%d" % p for p in worst_pair] if worst_pair else "-"), value=min_gap)
    # nozzle motion envelope must not enter another copy's footprint
    min_env = float("inf")
    for i in keys:
        for j in keys:
            if i == j:
                continue
            g = bbox_gap(copies[i]["motion"], copies[j]["bbox"])
            if g < min_env:
                min_env = g
    check("motion_envelope", min_env >= o["min_gap"] / 2.0 or min_env >= 1e8,
          "in-part nozzle motion stays %.3f mm (bbox) away from other copies" % min_env, value=min_env)

    # ---- purge lanes -----------------------------------------------------------------
    w_purge = o["purge_e_per_mm"] * math.pi * (model.config["filament_diameter"] / 2) ** 2 / plan.layer_z[0]
    parts_min_y = min(v["bbox"][1] for v in copies.values())
    laneA = by0 + w_purge / 2.0 + 0.1
    laneB = laneA + w_purge + o["purge_lane_gap"]
    gap_to_parts = parts_min_y - (laneB + w_purge / 2.0)
    lx0 = bx0 + o["purge_edge_margin"]
    rx0 = bx1 - o["purge_edge_margin"] - o["purge_length"]
    lanes_ok = gap_to_parts >= 1.0 and rx0 - (lx0 + o["purge_length"]) >= 5.0
    check("purge_lanes", lanes_ok, "four straight front prime lines (est. width %.2f mm) in two lanes y=%.2f/%.2f; gap to parts %.2f mm"
          % (w_purge, laneA, laneB, gap_to_parts))
    plan.purge = {0: dict(x0=lx0, x1=lx0 + o["purge_length"], y=laneA), 1: dict(x0=rx0, x1=rx0 + o["purge_length"], y=laneA),
                  2: dict(x0=lx0, x1=lx0 + o["purge_length"], y=laneB), 3: dict(x0=rx0, x1=rx0 + o["purge_length"], y=laneB),
                  "width_est": w_purge}
    if laneA < mesh.min_y or laneB < mesh.min_y:
        diags.warning("PLAN_PURGE_UNMEASURED", "prime lines at y<%.1f lie outside the measured mesh (Klipper clamps to the edge row)" % mesh.min_y,
                      "The prime line is not part of the model; verify the front edge is flat.")

    # ---- carriage ranges ----------------------------------------------------------------
    c = prof.min_clearance
    for t in heads:
        xs = []
        for s in slots:
            xs += [mx0 + s.dx + sync * t + offs[t][0], mx1 + s.dx + sync * t + offs[t][0]]
        p = plan.purge[t]
        xs += [p["x0"] + offs[t][0], p["x1"] + offs[t][0]]
        lo, hi = min(xs), max(xs)
        allow_lo = max(prof.carriages[t].position_min, left[t - 1] + c if t > 0 else -1e9)
        allow_hi = min(prof.carriages[t].position_max, right[t + 1] - c if t < 3 else 1e9)
        plan.tool_ranges[t] = dict(lo=lo, hi=hi, allowed=[allow_lo, allow_hi])
        check("tool_reach_T%d" % t, allow_lo - 1e-6 <= lo and hi <= allow_hi + 1e-6,
              "T%d independent work range carriage [%.2f, %.2f] within allowed [%.2f, %.2f] (others parked at stacks)" % (t, lo, hi, allow_lo, allow_hi))
    delta = {t: sync * t + offs[t][0] for t in heads}
    plan.copy_carriage_delta = delta
    xs = [v for s in slots for v in (mx0 + s.dx, mx1 + s.dx)]
    lo, hi = min(xs), max(xs)
    plan.copy_range = (lo, hi)
    ok = True
    for t in heads:
        if lo + delta[t] < prof.carriages[t].position_min - 1e-6 or hi + delta[t] > prof.carriages[t].position_max + 1e-6:
            ok = False
    check("copy_limits", ok, "COPY mode carriage ranges within limits (T0 X %.2f..%.2f)" % (lo, hi))
    if len(heads) > 1:
        spacing = [delta[heads[i + 1]] - delta[heads[i]] for i in range(len(heads) - 1)]
        check("copy_clearance", min(spacing) >= c - 1e-9, "COPY carriage spacing %s >= %.2f" % (["%.4f" % v for v in spacing], c))
    unused = [t for t in range(4) if t not in heads]
    if unused:
        # heads that do not print stay parked at their endstop positions for the whole print
        last, nxt = heads[-1], unused[0]
        margin = home[nxt] - (hi + delta[last])
        check("parked_heads_clear", margin >= c - 1e-9,
              "COPY phase: T%d reaches carriage %.2f, parked T%d at %.2f -> gap %.2f >= %.2f"
              % (last, hi + delta[last], nxt, home[nxt], margin, c))
    ys = [my0 + s.dy for s in slots] + [my1 + s.dy for s in slots] + [plan.purge[t]["y"] + offs[t][1] for t in heads]
    check("y_limits", prof.y_range[0] <= min(ys) and max(ys) <= prof.y_range[1], "Y range %.2f..%.2f within %s" % (min(ys), max(ys), prof.y_range))

    # ---- foundation thickness + outlier influence -----------------------------------------
    hmin, hmax = o["h_min_ratio"] * nozzle, o["h_max_ratio"] * nozzle
    stats = {}
    glob = [1e9, -1e9]
    for t in heads:
        for s in slots:
            lo_h, hi_h = 1e9, -1e9
            for L in range(2, F + 1):
                for m in model.blocks[L - 1].moves():
                    if m.kind != "extrude":
                        continue
                    x, y = m.x + s.dx, m.y + s.dy
                    h = plan.thickness(L, t, x, y)
                    lo_h, hi_h = min(lo_h, h), max(hi_h, h)
            stats["T%d/slot%d" % (t, s.index)] = [round(lo_h, 4), round(hi_h, 4)]
            glob = [min(glob[0], lo_h), max(glob[1], hi_h)]
    plan.foundation_stats = dict(per_copy=stats, global_range=[round(v, 4) for v in glob], limits=[hmin, hmax])
    if F > 1:
        check("foundation_thickness", hmin <= glob[0] and glob[1] <= hmax,
              "foundation layer 2..%d local thickness %.4f..%.4f mm within [%.3f, %.3f]" % (F, glob[0], glob[1], hmin, hmax))

    # Points that deviate markedly from their neighbours are evaluated on THIS
    # printer's mesh (none, one or several may exist). Measured values are kept
    # unchanged in the output; the comparison surface below (point replaced by its
    # neighbour median) is used only to quantify which copies depend on the point.
    outs = mesh.outliers(o["outlier_threshold"])
    affected = []
    plan.mesh_deviating_points = outs
    l1 = [m for m in model.blocks[0].moves() if m.kind == "extrude"]
    lF = [m for m in model.blocks[F - 1].moves() if m.kind == "extrude"]
    for ol in outs:
        pts = [list(r) for r in mesh.points]
        pts[ol["iy"]][ol["ix"]] = ol["neighbour_median"]
        alt = BedMesh(pts, mesh.min_x, mesh.max_x, mesh.min_y, mesh.max_y, mesh.x_count, mesh.y_count,
                      mesh.x_pps, mesh.y_pps, mesh.algo, mesh.tension, "comparison only")
        for t in heads:
            for s in slots:
                d1 = max(abs(plan.S(t, m.x + s.dx, m.y + s.dy) - plan.S(t, m.x + s.dx, m.y + s.dy, alt)) for m in l1)
                dF = max(abs(plan.zbase(F, t, m.x + s.dx, m.y + s.dy) - plan.zbase(F, t, m.x + s.dx, m.y + s.dy, alt)) for m in lF)
                if d1 > 0.01 or dF > 0.01:
                    if d1 > plan.layer_z[0] * 0.5:
                        txt = ("first-layer nozzle height depends on this point by up to %.3f mm (nominal first layer %.2f mm); "
                               "if the bed there is not as low as measured, the nozzle would touch the bed" % (d1, plan.layer_z[0]))
                    else:
                        txt = "first-layer height depends on this point by up to %.3f mm" % d1
                    affected.append(dict(point=[ol["x"], ol["y"]], point_z=ol["z"], deviation=ol["deviation"],
                                         copy="T%d/slot%d" % (t, s.index), tool=t, slot=s.index,
                                         first_layer_dz_max=round(d1, 4), foundation_top_dz_max=round(dF, 4),
                                         effect=txt))
    plan.affected_by_outliers = affected
    if affected:
        diags.warning("PLAN_DEVIATING_POINT_AFFECTS_COPIES",
                      "%d copy/point combinations depend (>0.01 mm) on mesh point(s) that deviate markedly from their neighbours"
                      % len(affected),
                      "Measured values are used unchanged. Check the bed at the listed point(s) before printing; "
                      "regenerate after any new mesh.")

    plan.checks = checks
    plan.validated_scope = ("reference layout preset (reference-300, 40 copies) compared against the reviewed oracle; "
                            "other counts/models are verified by the same software checks but have no physical print evidence")
    return plan, diags
