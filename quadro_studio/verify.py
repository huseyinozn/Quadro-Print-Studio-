"""Independent verification of Farm Mode G-code.

The verifier does NOT import the generator or the plan. It re-reads the
output G-code with its own interpreter of the output dialect, tracking all
four carriages, their modes, extruder synchronisation, temperatures, fans,
mesh state and the filament retraction state of every extruder. From that it
derives the *physical* motion of every nozzle (carriage - X offset, shared Y
- Y offset, shared Z - Z offset) and checks it against:

* the slicer input (every model move of every layer must be reproduced for
  every copy, translated, with preserved F and E semantics),
* the printer profile (limits, carriage clearance, mesh, offsets),
* physical rules (first-layer height above the measured bed, local layer
  thickness, travel clearance above deposited material, heating before
  extrusion, retraction balance, final machine state).

Segment markers in comments (``; FARM_PART`` / ``; SHARK``) are used only to
segment the stream; every claim they make is re-derived geometrically.
The same interpreter reads the golden oracle when ``--oracle`` is given; the
oracle is never read by any other stage.
"""
from __future__ import annotations

import math
import re
from array import array

from .geometry import flow_area

EXTR_IDX = {"extruder": 0, "extruder1": 1, "extruder2": 2, "extruder3": 3}

# Tolerances fixed before comparing candidate and oracle (see REPORT.md, "Tolerances").
TOL = dict(
    xy_align=0.002,          # mm, output XY vs translated slicer XY (4-decimal output)
    first_layer=0.0015,      # mm, layer-1 nozzle height above measured bed vs nominal
    copy_common_z=0.0002,    # mm, commanded Z agreement of the four heads on the foundation top
    thickness_nominal=0.004, # mm, measured local thickness in COPY layers vs nominal (plane-fit estimate)
    e_exact_rel=2e-3,        # relative, E per slicer move where no flow compensation applies
    e_exact_abs=2e-6,
    e_found_rel=0.03,        # relative, foundation E vs A(h_meas,w)/A(h0,w)
    retract=1e-4,            # mm filament, retraction balance at each extrusion
    transfer_clearance=0.5,  # mm, minimum nozzle tip clearance above other deposits during transfers
    inpart_sink=0.03,        # mm, nozzle may not sink below own completed material during in-part travel
    descend_margin=0.0,      # mm, approach descent may not end below local material (1 mm cell)
    oracle_xy=0.002, oracle_z=0.001, oracle_e_exact=2e-3, oracle_e_found=0.03, oracle_f=0.05,
)

WORD = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")


class Grid:
    """Height field of deposited material (physical tip heights), 1 mm cells.

    Each cell keeps the highest deposit, its owner (copy key), the layer of that
    deposit and the owner's previous-layer top, so in-part travel can be checked
    against *completed* layers while transfers are checked against everything.
    """

    def __init__(self, cell=1.0):
        self.c = cell
        self.cells = {}   # key -> [top, owner, layer, prev_top]

    def key(self, x, y):
        return (int(math.floor(x / self.c)), int(math.floor(y / self.c)))

    def deposit(self, x0, y0, x1, y1, z, owner, layer):
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L / (self.c * 0.5)))
        cells = self.cells
        for i in range(n + 1):
            a = i / n
            k = self.key(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a)
            c = cells.get(k)
            if c is None:
                cells[k] = [z, owner, layer, -1e9]
            elif c[1] == owner:
                if layer > c[2]:
                    c[3] = c[0]
                    c[0], c[2] = z, layer
                elif z > c[0]:
                    c[0] = z
            elif z >= c[0]:
                cells[k] = [z, owner, layer, c[0]]

    def local(self, x, y, rad=1):
        kx, ky = self.key(x, y)
        best = -1e9
        for i in range(-rad, rad + 1):
            for j in range(-rad, rad + 1):
                c = self.cells.get((kx + i, ky + j))
                if c is not None and c[0] > best:
                    best = c[0]
        return best, None

    def local_split(self, x, y, me, cur_layer, rad=1):
        """(foreign top, own completed-layer top) around x,y."""
        kx, ky = self.key(x, y)
        fo, ow = -1e9, -1e9
        for i in range(-rad, rad + 1):
            for j in range(-rad, rad + 1):
                c = self.cells.get((kx + i, ky + j))
                if c is None:
                    continue
                if c[1] == me:
                    v = c[3] if c[2] >= cur_layer else c[0]
                    ow = max(ow, v)
                else:
                    fo = max(fo, c[0])
        return fo, ow


class PlaneCells:
    """Per-copy, per-layer point cloud (0.5 mm cells) for local plane fits."""
    CELL = 0.5

    def __init__(self):
        self.cells = {}

    def add(self, x, y, z):
        k = (int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL)))
        c = self.cells.get(k)
        if c is None:
            c = self.cells[k] = [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        c[0] += 1
        c[1] += x; c[2] += y; c[3] += z
        c[4] += x * x; c[5] += x * y; c[6] += y * y
        c[7] += x * z; c[8] += y * z

    def height(self, x, y):
        """Previous-layer surface height at (x, y): least-squares plane through the
        deposited points of the 3x3 neighbouring 0.5 mm cells (5x5 if those are
        nearly collinear). The plane removes the mesh slope; the remaining
        estimator error is curvature x radius^2 (about 1 um at 0.75 mm)."""
        for rad in (1, 2):
            v = self._fit(x, y, rad)
            if v is not None:
                return v
        return None

    def _fit(self, x, y, rad):
        kx, ky = int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL))
        s = [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        rng = range(-rad, rad + 1)
        for i in rng:
            for j in rng:
                c = self.cells.get((kx + i, ky + j))
                if c:
                    for q in range(9):
                        s[q] += c[q]
        n = s[0]
        if n < 6:
            return None
        mx, my, mz = s[1] / n, s[2] / n, s[3] / n
        sxx = s[4] - n * mx * mx
        sxy = s[5] - n * mx * my
        syy = s[6] - n * my * my
        sxz = s[7] - n * mx * mz
        syz = s[8] - n * my * mz
        det = sxx * syy - sxy * sxy
        if det < 0.15 * n * n * (rad * rad) * self.CELL ** 4:   # nearly collinear -> ill-conditioned
            return None
        a = (sxz * syy - syz * sxy) / det
        b = (syz * sxx - sxz * sxy) / det
        return mz + a * (x - mx) + b * (y - my)


class Sim:
    def __init__(self, prof, model, label, keep_arrays=True, h_limits=(0.08, 0.30), sample_every=4):
        self.prof = prof
        self.model = model
        self.label = label
        self.keep = keep_arrays
        self.h_limits = h_limits
        self.sample_every = sample_every
        self.off = {t: (prof.offsets[t].x, prof.offsets[t].y, prof.offsets[t].z) for t in range(4)}
        self.lim = {t: (prof.carriages[t].position_min, prof.carriages[t].position_max) for t in range(4)}
        self.home = {t: prof.carriages[t].position_endstop for t in range(4)}
        self.c = prof.min_clearance
        self.mesh = prof.mesh
        self.fans_name = {prof.extruders[t].fan: t for t in range(4)}
        self.min_ext_temp = {t: prof.extruders[t].min_extrude_temp for t in range(4)}
        # machine state (before the file runs: unknown)
        self.cx = {t: None for t in range(4)}
        self.mode = {t: "INACTIVE" for t in range(4)}
        self.primary = None
        self.active = None
        self.queue = {t: t for t in range(4)}
        self.y = self.z = None
        self.f = 0.0
        self.goff = (0.0, 0.0, 0.0)
        self.target = {t: 0.0 for t in range(4)}
        self.waited = {t: False for t in range(4)}
        self.bed = 0.0
        self.bed_waited = False
        self.fan = {t: 0.0 for t in range(4)}
        self.pa = {t: None for t in range(4)}
        self.mesh_state = "unknown"
        self.level = {t: 0.0 for t in range(4)}
        self.e_total = {t: 0.0 for t in range(4)}
        self.homed = False
        self.rel_e = None
        # bookkeeping
        self.issues = []          # (severity, code, message, line)
        self.issue_counts = {}
        self.grid = Grid()
        self.visit = None
        self.visits = []          # summaries
        self.arrays = {}          # (nozzle, layer, dkey) -> dict of arrays
        self.copy_layers = {}     # copy key -> {layer: PlaneCells}
        self.qpms = []
        self.purges = []          # runs of extrusion outside visits
        self.cur_purge = None
        self.first_after = []     # first extrusions after purge / copy entry
        self.expect_first = {}
        self.stats = dict(lines=0, g1=0, ext_moves=0, travel_samples=0, travel_checked=0,
                          min_transfer_clear=1e9, min_transfer_where=None, min_qpm_clear=1e9,
                          min_inpart_margin=1e9, max_carriage_gap_violation=0.0, min_carriage_gap=1e9,
                          est_time_s=0.0, thickness_checked=0, thickness_skipped=0,
                          e_found_checked=0, e_found_max_dev=0.0, e_found_max_where=None,
                          h_meas_min=1e9, h_meas_max=-1e9, copy_thick_max_dev=0.0, copy_thick_where=None,
                          first_layer_max_dev=0.0, first_layer_where=None, first_layer_points=0)
        self.unknown = {}
        self.layer_events = []

    # ------------------------------------------------------------------ issues
    def issue(self, sev, code, msg, line):
        self.issue_counts[code] = self.issue_counts.get(code, 0) + 1
        if self.issue_counts[code] <= 20:
            self.issues.append(dict(severity=sev, code=code, message=msg, line=line))

    # ------------------------------------------------------------------ helpers
    def phys(self, t):
        return self.cx[t] - self.off[t][0], self.y - self.off[t][1], self.z - self.off[t][2]

    def over_bed(self, x):
        return -5.0 <= x <= 305.0

    def check_carriages(self, line):
        if any(self.cx[t] is None for t in range(4)):
            return
        for t in range(4):
            lo, hi = self.lim[t]
            if self.cx[t] < lo - 1e-6 or self.cx[t] > hi + 1e-6:
                self.issue("error", "CARRIAGE_LIMIT", "T%d carriage at %.4f outside [%.2f, %.2f]" % (t, self.cx[t], lo, hi), line)
        for t in range(3):
            g = self.cx[t + 1] - self.cx[t]
            if g < self.stats["min_carriage_gap"]:
                self.stats["min_carriage_gap"] = g
            if g < self.c - 1e-6:
                self.issue("error", "CARRIAGE_CLEARANCE", "carriage gap T%d/T%d %.4f < %.2f" % (t, t + 1, g, self.c), line)

    def moving_extruders(self):
        if self.active is None:
            return []
        return [t for t in range(4) if t == self.active or (self.queue[t] == self.active)]

    # ------------------------------------------------------------------ visit handling
    def start_visit(self, layer, slot, tools, line):
        self.end_visit()
        self.visit = dict(layer=layer, slot=slot, tools=tools, line=line, rec={t: [] for t in range(4)},
                          d={}, first_done={})

    def end_visit(self):
        v = self.visit
        if v is None:
            return
        self.visit = None
        for t, recs in v["rec"].items():
            if not recs:
                continue
            self.align(v, t, recs)

    def align(self, v, t, recs):
        """Align one nozzle's moves in a visit with the slicer block of that layer."""
        L = v["layer"]
        blk = self.model.blocks[L - 1] if 1 <= L <= self.model.layer_count else None
        summ = dict(nozzle=t, layer=L, slot_hint=v["slot"], line=v["line"], tools=tuple(v["tools"]), ok=False, issues=[])
        if blk is None:
            summ["issues"].append("layer outside slice")
            self.visits.append(summ)
            return
        smoves = blk.moves()
        trailer_from = None
        if blk.trailer_index >= 0:
            cnt = 0
            for idx, it in enumerate(blk.items):
                if idx == blk.trailer_index:
                    trailer_from = cnt
                    break
                if hasattr(it, "kind") and it.__class__.__name__ == "Move":
                    cnt += 1
        last_ext = max((k for k, m_ in enumerate(smoves) if m_.kind == "extrude"), default=len(smoves) - 1)
        if trailer_from is None or trailer_from > last_ext + 1:
            trailer_from = last_ext + 1
        # find first output extrusion
        j = 0
        while j < len(recs) and recs[j][9] != "ext":
            j += 1
        if j == len(recs):
            summ["issues"].append("no extrusion in visit")
            self.visits.append(summ)
            return
        s0 = smoves[0]
        d = (recs[j][1] - s0.x0, recs[j][2] - s0.y0)
        summ["d"] = (round(d[0], 4), round(d[1], 4))
        za = array("d")
        ea = array("d")
        fa = array("d")
        xy_err = 0.0
        tol = TOL["xy_align"]
        n_s = len(smoves)
        i = 0
        status = "complete"
        while i < n_s:
            sm = smoves[i]
            if sm.kind in ("extrude", "wipe", "travel"):
                tx, ty = sm.x + d[0], sm.y + d[1]
                esum = 0.0
                zt = None
                f = None
                matched = False
                pcs = []
                while j < len(recs):
                    r = recs[j]
                    j += 1
                    if r[9] not in ("ext", "wipe", "travel"):
                        j -= 1
                        break
                    esum += r[7]
                    zt = r[6]
                    f = r[8]
                    pcs.append(r)
                    # piece endpoint must lie on the slicer segment
                    ex = seg_err(r[3], r[4], sm.x0 + d[0], sm.y0 + d[1], tx, ty)
                    if ex > xy_err:
                        xy_err = ex
                    if abs(r[3] - tx) <= tol and abs(r[4] - ty) <= tol:
                        matched = True
                        break
                if not matched:
                    status = "mismatch at slicer line %d (%s)" % (sm.line, sm.kind)
                    break
                za.append(zt); ea.append(esum); fa.append(f)
                if sm.kind == "extrude" and 1 < L <= self.found_layers:
                    self.check_found_e(t, L, sm, pcs, esum, blk.height)
            elif sm.kind == "zmove":
                if j < len(recs) and recs[j][9] == "z":
                    za.append(recs[j][6]); ea.append(0.0); fa.append(recs[j][8])
                    j += 1
                else:
                    status = "mismatch at slicer line %d (zmove)" % sm.line
                    break
            else:  # retract / deretract
                if j < len(recs) and recs[j][9] == "eonly":
                    za.append(recs[j][6]); ea.append(recs[j][7]); fa.append(recs[j][8])
                    j += 1
                else:
                    status = "mismatch at slicer line %d (%s)" % (sm.line, sm.kind)
                    break
            i += 1
        summ["aligned_moves"] = i
        summ["slicer_moves"] = n_s
        summ["xy_err"] = xy_err
        summ["status"] = status
        if status != "complete":
            if trailer_from is not None and i >= trailer_from:
                summ["trailer_differs"] = True
                summ["ok"] = True
            else:
                summ["issues"].append(status)
        else:
            summ["ok"] = True
        summ["extra_out_moves"] = len(recs) - j
        if xy_err > tol:
            summ["issues"].append("piece off slicer path by %.4f" % xy_err)
            summ["ok"] = False
        # E / F per aligned move
        nonfound = (L == 1) or (L > self.found_layers)
        e_bad = 0
        f_bad = 0
        e_max = 0.0
        for k in range(len(ea)):
            sm = smoves[k]
            if abs(fa[k] - sm.f) > 0.05:
                f_bad += 1
                summ.setdefault("f_examples", []).append((sm.line, sm.kind, sm.f, fa[k]))
            if sm.kind == "extrude":
                if nonfound:
                    dev = abs(ea[k] - sm.e)
                    if dev > TOL["e_exact_abs"] + TOL["e_exact_rel"] * abs(sm.e):
                        e_bad += 1
                    e_max = max(e_max, dev / abs(sm.e))
            elif sm.kind in ("wipe", "retract", "deretract"):
                if abs(ea[k] - sm.e) > 1e-5:
                    if summ.get("trailer_differs") and k >= trailer_from:
                        summ["trailer_e_differs"] = summ.get("trailer_e_differs", 0) + 1
                    else:
                        e_bad += 1
        summ["e_exact_bad"] = e_bad
        summ["f_bad"] = f_bad
        summ["e_rel_max"] = e_max
        if e_bad:
            summ["issues"].append("%d moves with E differing from slice" % e_bad)
            summ["ok"] = False
        if f_bad:
            summ["issues"].append("%d moves with feedrate differing from slice" % f_bad)
            summ["ok"] = False
        self.visits.append(summ)
        if self.keep:
            self.arrays[(t, L, summ["d"])] = dict(z=za, e=ea, f=fa)

    def check_found_e(self, t, L, sm, pcs, esum, h0):
        slen = math.hypot(sm.x - sm.x0, sm.y - sm.y0)
        if slen <= 0:
            return
        exp = 0.0
        for r in pcs:
            if r[10] is None:
                return
            plen = math.hypot(r[3] - r[1], r[4] - r[2])
            exp += sm.e * plen / slen * flow_area(r[10], sm.width) / flow_area(h0, sm.width)
        if exp <= 0:
            return
        dev = esum / exp - 1.0
        self.stats["e_found_checked"] += 1
        if abs(dev) > abs(self.stats["e_found_max_dev"]):
            self.stats["e_found_max_dev"] = dev
            self.stats["e_found_max_where"] = dict(nozzle=t, layer=L, slicer_line=sm.line)
        if abs(dev) > TOL["e_found_rel"]:
            self.issue("error", "FOUNDATION_FLOW", "T%d layer %d E deviates %.2f%% from A(h,w) at measured thickness (slicer line %d)"
                       % (t, L, dev * 100, sm.line), 0)

    # ------------------------------------------------------------------ main loop
    def run(self, path, found_layers):
        self.found_layers = found_layers
        nL = self.model.layer_count
        model_blocks = self.model.blocks
        with open(path, "r", encoding="utf-8") as fh:
            for ln, raw in enumerate(fh, 1):
                s = raw.strip()
                if not s:
                    continue
                self.stats["lines"] += 1
                if s[0] == ";":
                    self.comment(s, ln)
                    continue
                code = s.split(";", 1)[0].strip()
                if not code:
                    continue
                if code.startswith("G1 ") or code == "G1" or code.startswith("G0 "):
                    self.g1(code, ln)
                else:
                    self.command(code, ln)
        self.end_visit()
        self.close_purge()
        self.final_state()
        return self

    def comment(self, s, ln):
        m = re.match(r";\s*(FARM_PART|SHARK)\s+layer=(\d+)\s+slot=(\d+)\s+tools=([\d,]+)", s)
        if m:
            self.close_purge()
            self.start_visit(int(m.group(2)), int(m.group(3)), [int(v) for v in m.group(4).split(",")], ln)
            return
        if s.startswith("; FARM_TRANSFER") or s.startswith("; FARM_PARK"):
            self.end_visit()
        if s.startswith("; FARM_PURGE"):
            self.end_visit()

    # ------------------------------------------------------------------ G1
    def g1(self, code, ln):
        self.stats["g1"] += 1
        w = {k: float(v) for k, v in WORD.findall(code[2:])}
        if set(w) - set("XYZEF"):
            self.issue("error", "OUT_UNSUPPORTED", "unsupported G1 words in %s" % code, ln)
        if not self.homed:
            self.issue("error", "OUT_NOT_HOMED", "motion before homing", ln)
            return
        if "F" in w:
            self.f = w["F"]
        if any(v != 0.0 for v in self.goff):
            self.issue("error", "OUT_GCODE_OFFSET", "non-zero G-code offset during motion", ln)
        p = self.primary
        moving = [t for t in range(4) if self.mode[t] in ("PRIMARY", "COPY")]
        old = {t: self.phys(t) for t in moving} if self.y is not None and self.z is not None else {}
        dx = 0.0
        if "X" in w:
            if p is None:
                self.issue("error", "OUT_NO_PRIMARY", "X move without primary carriage", ln)
                return
            dx = w["X"] - self.cx[p]
            for t in moving:
                self.cx[t] += dx
        if "Y" in w:
            self.y = w["Y"]
        if "Z" in w:
            self.z = w["Z"]
        if abs(dx) > 0:
            self.check_carriages(ln)
        if self.y is not None and not (self.prof.y_range[0] - 1e-6 <= self.y <= self.prof.y_range[1] + 1e-6):
            self.issue("error", "Y_LIMIT", "Y %.3f outside machine range" % self.y, ln)
        if self.z is not None and not (self.prof.z_range[0] <= self.z <= self.prof.z_range[1]):
            self.issue("error", "Z_LIMIT", "Z %.3f outside machine range" % self.z, ln)
        e = w.get("E", 0.0)
        if not old:
            return
        exts = self.moving_extruders() if "E" in w else []
        # extruders whose carriage is parked must not move
        for t in exts:
            if self.mode[t] == "INACTIVE":
                self.issue("error", "OUT_PARKED_EXTRUDE", "extruder %d moves while its carriage is parked" % t, ln)
        if self.f > 0:
            pt = self.phys(moving[0])
            o0 = old[moving[0]]
            dist = math.sqrt((pt[0] - o0[0]) ** 2 + (pt[1] - o0[1]) ** 2 + (pt[2] - o0[2]) ** 2)
            if dist == 0 and e:
                dist = abs(e)
            self.stats["est_time_s"] += dist / (self.f / 60.0)
        for t in moving:
            x0, y0, z0 = old[t]
            x1, y1, z1 = self.phys(t)
            xy = (abs(x1 - x0) > 1e-9) or (abs(y1 - y0) > 1e-9)
            et = e if t in exts else 0.0
            if "E" in w and t not in exts and e != 0:
                self.issue("error", "OUT_COPY_NOT_SYNCED", "head T%d moves with others but its extruder is not synchronized" % t, ln)
            if xy and et > 0:
                kind = "ext"
            elif xy and et < 0:
                kind = "wipe"
            elif xy:
                kind = "travel"
            elif abs(z1 - z0) > 1e-12:
                kind = "z"
            else:
                kind = "eonly"
            # retraction bookkeeping and extrusion preconditions
            if et:
                if kind == "ext":
                    if abs(self.level[t]) > TOL["retract"]:
                        self.issue("error", "RETRACT_STATE", "T%d extrudes while retracted %.5f mm" % (t, self.level[t]), ln)
                    self.level[t] = 0.0
                    if self.target[t] < self.min_ext_temp[t] or not self.waited[t]:
                        self.issue("error", "COLD_EXTRUDE", "T%d extrudes without reaching print temperature" % t, ln)
                    if self.mesh_state != "cleared":
                        self.issue("error", "MESH_STACKED", "extrusion with runtime bed mesh %s (compensation is embedded)" % self.mesh_state, ln)
                elif kind in ("wipe",) or et < 0:
                    self.level[t] -= et
                else:
                    self.level[t] -= et
                    if self.level[t] < -TOL["retract"]:
                        self.issue("error", "OVER_PRIME", "T%d primed %.5f mm more than retracted" % (t, -self.level[t]), ln)
                self.e_total[t] += et
            rec = (ln, x0, y0, x1, y1, z0, z1, et, self.f, kind, None)
            if kind == "ext":
                self.stats["ext_moves"] += 1
                h = self.on_extrude(t, rec, ln)
                if h is not None:
                    rec = rec[:10] + (h,)
            else:
                self.on_travel(t, rec, ln)
            if self.visit is not None and t in self.visit["rec"]:
                self.visit["rec"][t].append(rec)
            elif kind == "ext":
                self.purge_piece(t, rec)

    # ------------------------------------------------------------------ physical checks
    def copy_key(self, t):
        v = self.visit
        if v is None or t not in v["d"]:
            return None
        return (t, v["d"][t])

    def on_extrude(self, t, rec, ln):
        ln_, x0, y0, x1, y1, z0, z1, e, f, kind, _h = rec
        v = self.visit
        owner = ("purge", t)
        hret = None
        if v is not None:
            if t not in v["d"]:
                blk = self.model.blocks[v["layer"] - 1]
                s0 = blk.moves()[0]
                v["d"][t] = (round(x0 - s0.x0, 2), round(y0 - s0.y0, 2))
                self.first_extrusion(t, rec, v)
            owner = (t, v["d"][t])
            L = v["layer"]
            mz = self.mesh.z(x1, y1)
            if L == 1:
                dev = (z1 - mz) - self.model.blocks[0].z
                self.stats["first_layer_points"] += 1
                if abs(dev) > abs(self.stats["first_layer_max_dev"]):
                    self.stats["first_layer_max_dev"] = dev
                    self.stats["first_layer_where"] = dict(line=ln, nozzle=t, x=round(x1, 3), y=round(y1, 3))
                if abs(dev) > TOL["first_layer"]:
                    self.issue("error", "FIRST_LAYER_HEIGHT", "T%d layer-1 nozzle %.4f mm above bed (nominal %.3f)" % (t, z1 - mz, self.model.blocks[0].z), ln)
            # local thickness via plane fit on previous layer of the same copy
            layers = self.copy_layers.setdefault(owner, {})
            cur = layers.get(L)
            if cur is None:
                cur = layers[L] = PlaneCells()
                layers.pop(L - 3, None)
            prev = layers.get(L - 1)
            if L >= 2 and prev is not None:
                full = L <= self.found_layers + 1
                if full or (self.stats["ext_moves"] % self.sample_every == 0):
                    xm, ym, zm = 0.5 * (x0 + x1), 0.5 * (y0 + y1), 0.5 * (z0 + z1)
                    hp = prev.height(xm, ym)
                    if hp is None:
                        self.stats["thickness_skipped"] += 1
                    else:
                        h = zm - hp
                        hret = h
                        self.stats["thickness_checked"] += 1
                        self.stats["h_meas_min"] = min(self.stats["h_meas_min"], h)
                        self.stats["h_meas_max"] = max(self.stats["h_meas_max"], h)
                        nominal = self.model.blocks[L - 1].height
                        if L > self.found_layers:
                            dev = h - nominal
                            if abs(dev) > abs(self.stats["copy_thick_max_dev"]):
                                self.stats["copy_thick_max_dev"] = dev
                                self.stats["copy_thick_where"] = dict(line=ln, nozzle=t, layer=L, x=round(x1, 3), y=round(y1, 3))
                            if abs(dev) > TOL["thickness_nominal"]:
                                self.issue("error", "COPY_THICKNESS", "T%d layer %d local thickness %.4f (nominal %.3f)" % (t, L, h, nominal), ln)
                        else:
                            if not (self.h_limits[0] <= h <= self.h_limits[1]):
                                self.issue("error", "FOUNDATION_THICKNESS", "T%d layer %d local thickness %.4f outside limits" % (t, L, h), ln)
            cur.add(x1, y1, z1)
        self.grid.deposit(x0, y0, x1, y1, z1, owner, self.visit['layer'] if self.visit is not None else 0)
        return hret

    def first_extrusion(self, t, rec, v):
        key = self.expect_first.pop(t, None)
        if key is None:
            return
        ln, x0, y0, x1, y1, z0, z1, e, f, kind, _h = rec
        self.first_after.append(dict(event=key, nozzle=t, line=ln, layer=v["layer"], tip_above_bed=round(z0 - self.mesh.z(x0, y0), 5),
                                     retract_level_before=round(self.level[t], 6), temp_target=self.target[t], waited=self.waited[t]))

    def on_travel(self, t, rec, ln):
        ln_, x0, y0, x1, y1, z0, z1, e, f, kind, _h = rec
        if kind == "eonly" or not (self.over_bed(x0) or self.over_bed(x1)):
            return
        me = self.copy_key(t)
        cur_layer = self.visit["layer"] if self.visit is not None else 0
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L / 1.0))
        g = self.grid
        for i in range(n + 1):
            if i == 0:
                continue   # start point: where the nozzle already is
            a = i / n
            x, y, z = x0 + (x1 - x0) * a, y0 + (y1 - y0) * a, z0 + (z1 - z0) * a
            self.stats["travel_samples"] += 1
            if me is None:
                if kind == "z" and z1 < z0:
                    # approach descent onto the part: must not end inside material (centre cell)
                    top, _ = g.local(x, y, rad=0)
                    if top > -1e8 and z - top < -TOL["descend_margin"]:
                        self.issue("error", "DESCENT_INTO_MATERIAL", "T%d descends %.4f mm into material" % (t, top - z), ln)
                    continue
                top, _ = g.local(x, y, rad=1)
                if top > -1e8:
                    m = z - top
                    if m < self.stats["min_transfer_clear"]:
                        self.stats["min_transfer_clear"] = m
                        self.stats["min_transfer_where"] = dict(line=ln, nozzle=t, x=round(x, 2), y=round(y, 2))
                    if m < TOL["transfer_clearance"]:
                        self.issue("error", "TRANSFER_CLEARANCE", "T%d travels %.3f mm above deposited material outside its part" % (t, m), ln)
            else:
                fo, ow = g.local_split(x, y, me, cur_layer, rad=0)
                if fo > -1e8:
                    m = z - fo
                    if m < self.stats["min_transfer_clear"]:
                        self.stats["min_transfer_clear"] = m
                        self.stats["min_transfer_where"] = dict(line=ln, nozzle=t, x=round(x, 2), y=round(y, 2), inpart=True)
                    if m < TOL["transfer_clearance"]:
                        self.issue("error", "TRANSFER_CLEARANCE", "T%d passes %.3f mm above another copy" % (t, m), ln)
                if ow > -1e8:
                    m = z - ow
                    if m < self.stats["min_inpart_margin"]:
                        self.stats["min_inpart_margin"] = m
                    if m < -TOL["inpart_sink"]:
                        self.issue("error", "INPART_SINK", "T%d moves %.3f mm below its own completed layers" % (t, -m), ln)

    # ------------------------------------------------------------------ purge lines
    def purge_piece(self, t, rec):
        cp = self.cur_purge
        if cp is None or cp["t"] != t or abs(cp["pts"][-1][0] - rec[1]) > 1e-6 or abs(cp["pts"][-1][1] - rec[2]) > 1e-6:
            self.close_purge()
            cp = self.cur_purge = dict(t=t, pts=[(rec[1], rec[2])], e=0.0, z=[], line=rec[0])
        cp["pts"].append((rec[3], rec[4]))
        cp["e"] += rec[7]
        cp["z"].append(rec[6] - self.mesh.z(rec[3], rec[4]))

    def close_purge(self):
        cp = self.cur_purge
        if cp is None:
            return
        self.cur_purge = None
        (xa, ya), (xb, yb) = cp["pts"][0], cp["pts"][-1]
        dev = max(seg_err(x, y, xa, ya, xb, yb) for x, y in cp["pts"])
        length = math.hypot(xb - xa, yb - ya)
        fil_area = math.pi * (self.prof.extruders[cp["t"]].filament_diameter / 2) ** 2
        h = sum(cp["z"]) / len(cp["z"])
        width = cp["e"] * fil_area / max(1e-9, length) / max(1e-3, h)
        self.purges.append(dict(nozzle=cp["t"], line=cp["line"], start=(round(xa, 3), round(ya, 3)), end=(round(xb, 3), round(yb, 3)),
                                length=round(length, 3), straightness=round(dev, 5), e=round(cp["e"], 4),
                                height_above_bed=round(h, 4), width_est=round(width, 3)))
        self.expect_first[cp["t"]] = "after_purge"
        if dev > 0.01 or length < 20:
            self.issue("error", "PURGE_SHAPE", "extrusion outside any model block that is not a straight prime line (T%d line %d)" % (cp["t"], cp["line"]), cp["line"])

    # ------------------------------------------------------------------ commands
    def command(self, code, ln):
        parts = code.split()
        head = parts[0].upper()
        params = dict(re.findall(r"([A-Za-z_0-9]+)=(\S+)", code))
        P = {k.upper(): v for k, v in params.items()}
        if head == "G90":
            return
        if head == "M83":
            self.rel_e = True
            return
        if head == "M82":
            self.rel_e = False
            return
        if head in ("M220", "M221", "M117", "SET_PRINT_STATS_INFO", "G21"):
            return
        if head == "M400":
            return
        if head == "_PRINT_HOME":
            # ASSUMED (profile macro, not executed): homes all axes, leaves T0 primary/active at
            # X150 Y150 with zero G-code offset and runtime mesh cleared; other carriages at
            # their endstops; Z lifted 10 mm after probing.
            self.homed = True
            for t in range(4):
                self.cx[t] = self.home[t]
                self.mode[t] = "INACTIVE"
            # QUADRO_DISTRIBUTE_X (called by homing) leaves T0=150, T1=356.5, T2/T3 at endstops
            self.cx[0] = 150.0
            self.cx[1] = 356.5
            self.mode[0] = "PRIMARY"
            self.primary = 0
            self.active = 0
            self.y = 150.0
            self.z = 10.0
            self.mesh_state = "cleared"
            self.goff = (0.0, 0.0, 0.0)
            self.target[0] = 140.0
            self.bed = float(P.get("BED_TEMP", 60)) - 5
            self.assumed_macros = True
            return
        if head == "BED_MESH_CLEAR":
            self.mesh_state = "cleared"
            return
        if head == "BED_MESH_PROFILE":
            self.mesh_state = "loaded:" + P.get("LOAD", "?")
            return
        if head == "SET_GCODE_OFFSET":
            self.goff = (float(P.get("X", 0)), float(P.get("Y", 0)), float(P.get("Z", 0)))
            return
        if head == "SYNC_EXTRUDER_MOTION":
            e = EXTR_IDX.get(P.get("EXTRUDER"))
            q = EXTR_IDX.get(P.get("MOTION_QUEUE"))
            if e is None or q is None:
                self.issue("error", "OUT_SYNTAX", "bad SYNC_EXTRUDER_MOTION", ln)
                return
            if self.level[e] != self.level[q] and e != q and abs(self.level[e] - self.level[q]) > TOL["retract"]:
                self.issue("error", "SYNC_RETRACT_MISMATCH", "extruder %d synced with different retraction state (%.4f vs %.4f)"
                           % (e, self.level[e], self.level[q]), ln)
            self.queue[e] = q
            return
        if head == "SET_DUAL_CARRIAGE":
            name = P.get("CARRIAGE", "")
            m = re.match(r"carriage_t(\d)$", name)
            if not m:
                self.issue("error", "OUT_SYNTAX", "bad carriage %s" % name, ln)
                return
            t = int(m.group(1))
            mode = P.get("MODE", "PRIMARY").upper()
            self.end_visit()
            if mode == "PRIMARY":
                for u in range(4):
                    if self.mode[u] == "PRIMARY" and u != t:
                        self.mode[u] = "INACTIVE"
                self.mode[t] = "PRIMARY"
                self.primary = t
            elif mode in ("COPY", "INACTIVE"):
                if t == self.primary:
                    self.issue("error", "OUT_MODE", "primary carriage set to %s" % mode, ln)
                self.mode[t] = mode
                if mode == "COPY":
                    self.expect_first[t] = "after_copy_entry"
                    self.expect_first[self.primary] = "after_copy_entry"
            else:
                self.issue("error", "OUT_SYNTAX", "unknown carriage mode %s" % mode, ln)
            return
        if head == "ACTIVATE_EXTRUDER":
            e = EXTR_IDX.get(P.get("EXTRUDER"))
            if e is None:
                self.issue("error", "OUT_SYNTAX", "bad extruder", ln)
                return
            if self.primary is not None and e != self.primary:
                self.issue("error", "OUT_EXTRUDER_CARRIAGE", "extruder %d activated while carriage T%d is primary" % (e, self.primary), ln)
            self.active = e
            return
        if head == "QUADRO_PARALLEL_MOVE":
            self.end_visit()
            self.close_purge()
            start = dict(self.cx)
            tgt = dict(self.cx)
            for t in range(4):
                if "T%d" % t in P:
                    tgt[t] = float(P["T%d" % t])
            mc = float(P.get("MIN_CLEARANCE", self.c))
            if mc < self.c - 1e-9:
                self.issue("error", "QPM_CLEARANCE_PARAM", "MIN_CLEARANCE %.2f below profile %.2f" % (mc, self.c), ln)
            for label, pos in (("start", start), ("target", tgt)):
                for t in range(4):
                    if pos[t] is None:
                        continue
                    if not (self.lim[t][0] - 1e-6 <= pos[t] <= self.lim[t][1] + 1e-6):
                        self.issue("error", "QPM_LIMIT", "%s T%d %.4f outside limits" % (label, t, pos[t]), ln)
                for t in range(3):
                    if pos[t] is not None and pos[t + 1] is not None and pos[t + 1] - pos[t] < self.c - 1e-6:
                        self.issue("error", "QPM_CLEARANCE", "%s gap T%d/T%d %.4f" % (label, t, t + 1, pos[t + 1] - pos[t]), ln)
            # sweep clearance above deposits (all nozzles move along X at fixed Y/Z)
            for t in range(4):
                if start[t] is None:
                    continue
                xa, xb = sorted((start[t] - self.off[t][0], tgt[t] - self.off[t][0]))
                xa, xb = max(xa, -5.0), min(xb, 305.0)
                if xa > xb:
                    continue
                yy = self.y - self.off[t][1]
                zz = self.z - self.off[t][2]
                x = xa
                while x <= xb + 1e-9:
                    top, _ = self.grid.local(x, yy)
                    if top > -1e8:
                        m = zz - top
                        self.stats["min_qpm_clear"] = min(self.stats["min_qpm_clear"], m)
                        if m < TOL["transfer_clearance"]:
                            self.issue("error", "QPM_SWEEP_CLEARANCE", "T%d sweeps %.3f mm above deposited material" % (t, m), ln)
                    x += 2.0
            dist = max(abs(tgt[t] - start[t]) for t in range(4) if start[t] is not None)
            sp = float(P.get("SPEED", 50))
            self.stats["est_time_s"] += dist / sp
            self.qpms.append(dict(line=ln, start={t: start[t] for t in range(4)}, target={t: tgt[t] for t in range(4)},
                                  z=self.z, y=self.y, modes=dict(self.mode)))
            self.cx = tgt
            self.check_carriages(ln)
            return
        if head in ("M104", "M109"):
            t = int(float(P.get("T", re.search(r"T(\d)", code).group(1) if re.search(r"\bT(\d)", code) else 0)))
            sm = re.search(r"\bS([-\d.]+)", code)
            v = float(sm.group(1)) if sm else 0.0
            if abs(v - self.target[t]) > 1e-9:
                self.waited[t] = False
            self.target[t] = v
            if head == "M109":
                self.waited[t] = True
            return
        if head in ("M140", "M190"):
            sm = re.search(r"\bS([-\d.]+)", code)
            self.bed = float(sm.group(1)) if sm else 0.0
            self.bed_waited = head == "M190"
            return
        if head == "TURN_OFF_HEATERS":
            for t in range(4):
                self.target[t] = 0.0
                self.waited[t] = False
            self.bed = 0.0
            return
        if head == "SET_FAN_SPEED":
            t = self.fans_name.get(P.get("FAN"))
            if t is None:
                self.issue("error", "OUT_SYNTAX", "unknown fan %s" % P.get("FAN"), ln)
                return
            self.fan[t] = float(P.get("SPEED", 0))
            return
        if head == "SET_PRESSURE_ADVANCE":
            e = EXTR_IDX.get(P.get("EXTRUDER", "extruder"))
            self.pa[e] = float(P.get("ADVANCE", 0))
            return
        if head == "SET_VELOCITY_LIMIT":
            a = float(P.get("ACCEL", 0))
            if a > self.prof.max_accel + 1e-9:
                self.issue("warning", "ACCEL_CAPPED", "ACCEL %.0f above printer max %.0f" % (a, self.prof.max_accel), ln)
            return
        self.unknown[head] = self.unknown.get(head, 0) + 1
        self.issue("error", "OUT_UNKNOWN_COMMAND", "command not in the verified output dialect: %s" % code[:60], ln)

    # ------------------------------------------------------------------ end state
    def final_state(self):
        fs = dict(
            heaters_off=all(self.target[t] == 0 for t in range(4)) and self.bed == 0,
            fans_off=all(self.fan[t] == 0 for t in range(4)),
            carriages_home=all(self.cx[t] is not None and abs(self.cx[t] - self.home[t]) < 1e-6 for t in range(4)),
            modes=dict(self.mode), primary=self.primary,
            extruders_unsynced=all(self.queue[t] == t for t in range(4)),
            mesh_state=self.mesh_state, gcode_offset_zero=all(v == 0 for v in self.goff),
            final_z=self.z, retract_levels={t: round(self.level[t], 5) for t in range(4)},
            e_total={t: round(self.e_total[t], 3) for t in range(4)},
        )
        fs["modes_ok"] = self.mode[0] == "PRIMARY" and all(self.mode[t] == "INACTIVE" for t in (1, 2, 3))
        self.final = fs
        for k in ("heaters_off", "fans_off", "carriages_home", "extruders_unsynced", "modes_ok", "gcode_offset_zero"):
            if not fs[k]:
                self.issue("error", "FINAL_STATE", "final state check failed: %s" % k, 0)


def seg_err(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


# ======================================================================================
# Analysis on top of a simulation
# ======================================================================================
def analyse(sim: Sim, expected_count=None, min_gap=2.0):
    model = sim.model
    nL = model.layer_count
    F = sim.found_layers
    res = dict(label=sim.label)
    # placements per nozzle
    copies = {}
    order = {t: [] for t in range(4)}
    bad_visits = []
    trailer_diff = 0
    for v in sim.visits:
        if "d" not in v:
            bad_visits.append(v)
            continue
        key = (v["nozzle"], v["d"])
        copies.setdefault(key, []).append(v["layer"])
        order[v["nozzle"]].append((v["layer"], v["d"]))
        if not v["ok"]:
            bad_visits.append(v)
        if v.get("trailer_differs"):
            trailer_diff += 1
    # group near-identical d (rounding)
    per_copy_layers = {}
    for (t, d), layers in copies.items():
        per_copy_layers.setdefault((t, (round(d[0], 2), round(d[1], 2))), []).extend(layers)
    complete = 0
    problems = []
    for key, layers in per_copy_layers.items():
        cnt = {}
        for L in layers:
            cnt[L] = cnt.get(L, 0) + 1
        dup = [L for L, c in cnt.items() if c > 1]
        missing = [L for L in range(1, nL + 1) if L not in cnt]
        if dup:
            problems.append("copy %s printed twice on layers %s" % (key, dup[:5]))
        if missing:
            problems.append("copy %s missing layers %s" % (key, missing[:5]))
        if not dup and not missing:
            complete += 1
    res["copies_found"] = len(per_copy_layers)
    res["copies_complete"] = complete
    res["copy_problems"] = problems[:20]
    res["visits"] = len(sim.visits)
    res["visits_failed"] = len(bad_visits)
    res["visits_failed_examples"] = [dict(nozzle=v["nozzle"], layer=v["layer"], line=v["line"], issues=v.get("issues")) for v in bad_visits[:10]]
    res["trailer_differs"] = trailer_diff
    if expected_count is not None and (len(per_copy_layers) != expected_count or complete != expected_count):
        sim.issue("error", "COPY_COUNT", "expected %d complete copies, found %d (%d complete)" % (expected_count, len(per_copy_layers), complete), 0)
    if bad_visits:
        sim.issue("error", "VISIT_MISMATCH", "%d part visits do not reproduce the slice" % len(bad_visits), 0)
    if problems:
        sim.issue("error", "COPY_INCOMPLETE", "; ".join(problems[:3]), 0)
    # placement geometry: distinct copies, gaps between physical footprints (bbox + hull)
    from .geometry import poly_distance, translate
    hull = model.hull
    placed = sorted(per_copy_layers)
    hulls = {k: translate(hull, k[1][0], k[1][1]) for k in placed}
    gmin, gpair = 1e9, None
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            a, b = hulls[placed[i]], hulls[placed[j]]
            if abs(placed[i][1][0] - placed[j][1][0]) > 80 or abs(placed[i][1][1] - placed[j][1][1]) > 80:
                continue
            dd = poly_distance(a, b)
            if dd < gmin:
                gmin, gpair = dd, (placed[i], placed[j])
    res["min_gap_between_copies"] = round(gmin, 4)
    res["min_gap_pair"] = [("T%d" % k[0], k[1]) for k in gpair] if gpair else None
    if gmin < min_gap:
        sim.issue("error", "COPY_OVERLAP", "deposited footprints of two copies only %.3f mm apart" % gmin, 0)
    res["placements"] = [dict(nozzle=k[0], d=k[1], center=(round(k[1][0] + 0.5 * (model.bbox_extrude[0] + model.bbox_extrude[2]), 3),
                                                             round(k[1][1] + 0.5 * (model.bbox_extrude[1] + model.bbox_extrude[3]), 3)))
                         for k in placed]
    # ordering: foundation per tool layer-major, tool order, copy afterwards
    seq = []
    for v in sim.visits:
        seq.append((v["nozzle"], v["layer"], v["line"]))
    ord_ok = True
    seen_layer = {}
    for t, L, line in seq:
        if seen_layer.get(t, 0) > L:
            ord_ok = False
        seen_layer[t] = max(seen_layer.get(t, 0), L)
    res["layer_order_monotonic_per_head"] = ord_ok
    # foundation sequencing: all T0 foundation visits before T1 etc.
    fseq = [t for (t, L, line) in sorted(seq, key=lambda s: s[2]) if L <= F]
    blocks = []
    for t in fseq:
        if not blocks or blocks[-1] != t:
            blocks.append(t)
    res["foundation_head_sequence"] = blocks
    res["foundation_independent"] = blocks == [0, 1, 2, 3]
    if not ord_ok:
        sim.issue("error", "ORDER", "a head returns to an earlier layer", 0)
    # transitions & detours
    trans = []
    last = None
    for q in sim.qpms:
        trans.append(dict(line=q["line"], start={("T%d" % t): q["start"][t] for t in range(4)},
                          target={("T%d" % t): q["target"][t] for t in range(4)}, z=q["z"]))
    detours = []
    for i in range(1, len(sim.qpms)):
        a, b = sim.qpms[i - 1], sim.qpms[i]
        for t in range(4):
            p0, p1, p2 = a["start"][t], a["target"][t], b["target"][t]
            if p0 is None or p1 != b["start"][t]:
                continue
            if (p1 - p0) * (p2 - p1) < 0 and abs(p1 - p0) > 1 and abs(p2 - p1) > 1:
                extra = abs(p1 - p0) + abs(p2 - p1) - abs(p2 - p0)
                detours.append(dict(head="T%d" % t, lines=[a["line"], b["line"]], path=[round(p0, 3), round(p1, 3), round(p2, 3)],
                                    extra_travel_mm=round(extra, 2)))
    res["parallel_moves"] = trans
    res["carriage_detours"] = detours
    res["purges"] = sim.purges
    # purge overlap with each other
    pov = []
    for i in range(len(sim.purges)):
        for j in range(i + 1, len(sim.purges)):
            a, b = sim.purges[i], sim.purges[j]
            dy = abs(a["start"][1] - b["start"][1])
            ox = min(max(a["start"][0], a["end"][0]), max(b["start"][0], b["end"][0])) - max(min(a["start"][0], a["end"][0]), min(b["start"][0], b["end"][0]))
            need = 0.5 * (a["width_est"] + b["width_est"])
            if ox > 0 and dy < need:
                pov.append(dict(a="T%d" % a["nozzle"], b="T%d" % b["nozzle"], y_spacing=round(dy, 3), est_combined_half_widths=round(need, 3),
                                overlap_mm=round(need - dy, 3), x_overlap=round(ox, 2)))
    res["purge_overlaps"] = pov
    if pov:
        sim.issue("error", "PURGE_OVERLAP", "%d pairs of prime lines overlap (nozzle crosses an existing prime line)" % len(pov), 0)
    res["first_prints"] = sim.first_after
    for fp in sim.first_after:
        if abs(fp["retract_level_before"]) > TOL["retract"] or not fp["waited"]:
            sim.issue("error", "FIRST_PRINT_STATE", "first print %s on T%d not primed/heated" % (fp["event"], fp["nozzle"]), fp["line"])
    # foundation top must be one commanded Z for the four heads of each COPY group
    groups = {}
    for v in sim.visits:
        if v["layer"] == F + 1 and len(v.get("tools", ())) == 4 and "d" in v:
            groups.setdefault(v["line"], {})[v["nozzle"]] = v["d"]
    cz_max = 0.0
    cz_where = None
    for line, g in groups.items():
        if len(g) != 4:
            continue
        arrs = {t: sim.arrays.get((t, F, g[t])) for t in g}
        if any(a is None for a in arrs.values()):
            sim.issue("error", "COPY_GROUP", "foundation of a copy group not found for COPY visit at line %d" % line, line)
            continue
        n = min(len(a["z"]) for a in arrs.values())
        for k in range(n):
            vals = [arrs[t]["z"][k] + sim.off[t][2] for t in range(4)]
            dv = max(vals) - min(vals)
            if dv > cz_max:
                cz_max, cz_where = dv, dict(copy_visit_line=line, slicer_line=model.blocks[F - 1].moves()[k].line)
    res["foundation_common_z_max_spread"] = round(cz_max, 6)
    res["foundation_common_z_where"] = cz_where
    res["copy_groups"] = len(groups)
    if cz_max > TOL["copy_common_z"]:
        sim.issue("error", "COMMON_Z", "foundation tops of a copy group differ by %.4f mm in commanded Z" % cz_max, 0)
    res["final_state"] = sim.final
    st = dict(sim.stats)
    res["stats"] = st
    res["issues"] = sim.issues
    res["issue_counts"] = sim.issue_counts
    res["unknown_commands"] = sim.unknown
    errs = sum(c for code, c in sim.issue_counts.items() if any(i["code"] == code and i["severity"] == "error" for i in sim.issues))
    res["error_count"] = errs
    res["passed"] = errs == 0
    return res


def compare(cand: Sim, orc: Sim, F):
    """Quantify candidate vs oracle differences at aligned slicer moves."""
    out = dict()
    # placements: map nozzle+d
    cd = {}
    for (t, L, d), arr in cand.arrays.items():
        cd.setdefault(t, set()).add(d)
    od = {}
    for (t, L, d), arr in orc.arrays.items():
        od.setdefault(t, set()).add(d)
    mapping = {}
    pmax = 0.0
    for t in range(4):
        for d in cd.get(t, []):
            best = min(od.get(t, []), key=lambda e: math.hypot(e[0] - d[0], e[1] - d[1]), default=None)
            if best is None:
                continue
            dist = math.hypot(best[0] - d[0], best[1] - d[1])
            pmax = max(pmax, dist)
            mapping[(t, d)] = best
    out["placement_max_delta_mm"] = round(pmax, 5)
    out["placements_matched"] = len(mapping)
    # per-move Z/E/F
    zstat = {}
    estat = {}
    fbad = 0
    worst = dict(z=(0, None), e_exact=(0, None), e_found=(0, None))
    nmoves = 0
    for (t, L, d), ca in cand.arrays.items():
        od_ = mapping.get((t, d))
        if od_ is None:
            continue
        oa = orc.arrays.get((t, L, od_))
        if oa is None:
            continue
        blk = cand.model.blocks[L - 1]
        sm = blk.moves()
        n = min(len(ca["z"]), len(oa["z"]))
        zk = "L1" if L == 1 else ("L2-%d" % F if L <= F else "L%d+" % (F + 1))
        for k in range(n):
            nmoves += 1
            dz = ca["z"][k] - oa["z"][k]
            a = zstat.setdefault((zk, t), [1e9, -1e9, 0])
            a[0] = min(a[0], dz); a[1] = max(a[1], dz); a[2] += 1
            if abs(dz) > abs(worst["z"][0]):
                worst["z"] = (dz, dict(nozzle=t, layer=L, d=d, slicer_line=sm[k].line))
            if abs(ca["f"][k] - oa["f"][k]) > TOL["oracle_f"]:
                fbad += 1
            if sm[k].kind == "extrude" and oa["e"][k] > 0:
                r = ca["e"][k] / oa["e"][k] - 1.0
                b = estat.setdefault((zk, t), [1e9, -1e9, 0, 0])
                b[0] = min(b[0], r); b[1] = max(b[1], r); b[2] += 1
                lim = TOL["oracle_e_found"] if (1 < L <= F) else TOL["oracle_e_exact"]
                if abs(r) > lim:
                    b[3] += 1
                key = "e_found" if 1 < L <= F else "e_exact"
                if abs(r) > abs(worst[key][0]):
                    worst[key] = (r, dict(nozzle=t, layer=L, d=d, slicer_line=sm[k].line))
    out["aligned_moves_compared"] = nmoves
    out["z_delta"] = {"%s/T%d" % k: [round(v[0], 6), round(v[1], 6), v[2]] for k, v in sorted(zstat.items())}
    out["e_ratio_minus_1"] = {"%s/T%d" % k: [round(v[0], 5), round(v[1], 5), v[2], v[3]] for k, v in sorted(estat.items())}
    out["f_mismatches"] = fbad
    out["worst"] = {k: dict(value=round(v[0], 6), where=v[1]) for k, v in worst.items()}
    zmax = max((max(abs(v[0]), abs(v[1])) for v in zstat.values()), default=0)
    out["z_max_abs_delta"] = round(zmax, 6)
    out["z_within_tol"] = zmax <= TOL["oracle_z"]
    out["e_outside_tol"] = sum(v[3] for v in estat.values())
    # order of visits per nozzle
    def seq(sim):
        s = {}
        for v in sim.visits:
            if "d" in v:
                s.setdefault(v["nozzle"], []).append((v["layer"], v["d"]))
        return s
    cs, os_ = seq(cand), seq(orc)
    same_order = True
    for t in range(4):
        a = [(L, mapping.get((t, d))) for L, d in cs.get(t, [])]
        b = os_.get(t, [])
        if a != b:
            same_order = False
    out["visit_order_identical"] = same_order
    return out
