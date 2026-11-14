"""Farm Mode G-code generation.

Reads ONLY: the parsed slice, the explicit printer profile and the plan
(layout options). It never opens any other G-code file.

Output dialect (Klipper + Quadro module commands from the profile):
  G1 G90 M83 M82 M220 M221 M400 M104 M109 M190 M117 SET_VELOCITY_LIMIT
  SET_PRESSURE_ADVANCE SET_FAN_SPEED SET_GCODE_OFFSET SET_DUAL_CARRIAGE
  ACTIVATE_EXTRUDER SYNC_EXTRUDER_MOTION QUADRO_PARALLEL_MOVE BED_MESH_CLEAR
  BED_MESH_PROFILE TURN_OFF_HEATERS SET_PRINT_STATS_INFO _PRINT_HOME
Assumed macro behaviour is documented in README.md (only _PRINT_HOME, M109
and M190 macros are relied upon).
"""
from __future__ import annotations

import hashlib
import math

from . import __version__
from .geometry import flow_area
from .slicer import Move, Cmd

EXTR = {0: "extruder", 1: "extruder1", 2: "extruder2", 3: "extruder3"}


def _fmt(v, nd):
    s = ("%." + str(nd) + "f") % v
    if s.startswith("-") and float(s) == 0.0:
        s = s[1:]
    return s


class Emitter:
    def __init__(self):
        self.lines = []

    def __call__(self, s):
        self.lines.append(s)

    def text(self):
        return "\n".join(self.lines) + "\n"


class FarmGenerator:
    def __init__(self, model, prof, plan, input_sha256="", profile_fp=""):
        self.m = model
        self.p = prof
        self.plan = plan
        self.o = plan.options
        self.out = Emitter()
        self.level = {t: 0.0 for t in range(4)}       # retracted filament per extruder
        self.fan = {t: None for t in range(4)}
        self.z = None
        self.top_phys = 0.0                            # highest deposited physical tip height
        self.input_sha = input_sha256
        self.profile_fp = profile_fp
        self.counts = dict(part_visits=0, moves=0, pieces=0)
        cfg = model.config
        self.retract_len = cfg["retraction_length"]
        self.retract_f = cfg["retraction_speed"] * 60.0
        self.toolchange_retract = max(cfg["retract_length_toolchange"], self.retract_len)
        self.travel_f = min(cfg["travel_speed"], prof.max_velocity) * 60.0
        self.travel_acc = min(cfg["travel_acceleration"] or cfg["default_acceleration"], prof.max_accel)
        self.first_travel_acc = min(cfg["initial_layer_travel_acceleration"] or self.travel_acc, prof.max_accel)
        zmax_f = prof.max_z_velocity * 60.0
        self.lift_f = min(self.o["lift_feed"], zmax_f)
        self.descend_f = min(self.o["descend_feed"], zmax_f)

    # ------------------------------------------------------------- helpers
    def g1(self, x=None, y=None, z=None, e=None, f=None):
        parts = ["G1"]
        if x is not None:
            parts.append("X" + _fmt(x, 4))
        if y is not None:
            parts.append("Y" + _fmt(y, 4))
        if z is not None:
            parts.append("Z" + _fmt(z, 5))
            self.z = z
        if e is not None:
            parts.append("E" + _fmt(e, 6))
        if f is not None:
            fs = ("%.3f" % f).rstrip("0").rstrip(".")
            parts.append("F" + fs)
        self.out(" ".join(parts))

    def qpm(self, pos, label):
        self.out("M400")
        self.out("SET_VELOCITY_LIMIT ACCEL=%s ACCEL_TO_DECEL=%s" % (_fmt(self.o["park_accel"], 0), _fmt(self.o["park_accel"] / 2, 0)))
        self.out("; FARM_PARK %s" % label)
        self.out("QUADRO_PARALLEL_MOVE " + " ".join("T%d=%s" % (t, _fmt(pos[t], 5)) for t in range(4))
                 + " SPEED=%s MIN_CLEARANCE=%s" % (_fmt(self.o["parallel_speed"], 0), _fmt(self.p.min_clearance, 2)))
        self.out("M400")
        self.carriage = dict(pos)

    def set_fans(self, tools, s255):
        for t in tools:
            v = round(s255 / 255.0, 4)
            if self.fan[t] != v:
                self.out("SET_FAN_SPEED FAN=%s SPEED=%s" % (self.p.extruders[t].fan, _fmt(v, 4).rstrip("0").rstrip(".") or "0"))
                self.fan[t] = v

    def lift_to(self, z):
        if self.z is None or z > self.z + 1e-9:
            self.g1(z=z, f=self.lift_f)

    def safe_z(self, tools, clearance):
        need = self.top_phys + max(self.plan.offsets[t][2] for t in tools) + clearance
        return math.ceil(need * 1000.0 - 1e-6) / 1000.0

    def retract(self, tools, amount):
        if amount > 1e-9:
            self.g1(e=-amount, f=self.retract_f)
            for t in tools:
                self.level[t] += amount

    # ------------------------------------------------------------- part block
    def block(self, L, slot, tools, copy):
        """Emit one model block (layer L) for one slot on the given head(s)."""
        b = self.m.blocks[L - 1]
        plan = self.plan
        t_ref = None if copy else tools[0]
        F = plan.foundation_layers
        mv0 = b.moves()[0]
        # approach -------------------------------------------------------------
        sx, sy = mv0.x0, mv0.y0
        X, Y = plan.cmd_xy(tools[0], slot, sx, sy, copy)
        px, py = plan.t0_point(slot, sx, sy)
        zt = self.safe_z(tools, self.o["travel_clearance"])
        self.lift_to(zt)
        acc = self.first_travel_acc if L == 1 else self.travel_acc
        self.out("SET_VELOCITY_LIMIT ACCEL=%s ACCEL_TO_DECEL=%s" % (_fmt(acc, 0), _fmt(acc / 2, 0)))
        self.out("; FARM_TRANSFER")
        self.g1(x=X, y=Y, f=self.travel_f)
        z0 = plan.zbase(L, t_ref, px, py) + (mv0.z0 - b.z)
        self.g1(z=z0, f=self.descend_f)
        self.out("; FARM_PART layer=%d slot=%d tools=%s" % (L, slot.index, ",".join(str(t) for t in tools)))
        lv = [self.level[t] for t in tools]
        if max(lv) - min(lv) > 1e-6:
            raise RuntimeError("synchronized extruders have unequal retraction state %s" % lv)
        if lv[0] > 1e-9:
            self.g1(e=round(lv[0], 5), f=b.deretract_f or self.retract_f)
            for t in tools:
                self.level[t] = 0.0
        if b.accel_at_start:
            self.out(b.accel_at_start)
        self.set_fans(tools, b.fan_at_start)
        self.counts["part_visits"] += 1
        # moves ----------------------------------------------------------------
        maxseg = self.o["max_segment"]
        for it in b.items:
            if isinstance(it, Cmd):
                if it.kind == "accel":
                    self.out(it.text)
                elif it.kind == "fan":
                    self.set_fans(tools, float(it.text.split()[1]))
                elif it.kind == "comment" and it.text.startswith(";TYPE:"):
                    self.out(it.text)
                continue
            m: Move = it
            self.counts["moves"] += 1
            if m.kind in ("retract", "deretract"):
                self.g1(e=m.e, f=m.f)
                for t in tools:
                    self.level[t] -= m.e
                    if abs(self.level[t]) < 5e-5:
                        self.level[t] = 0.0
                continue
            if m.kind == "zmove":
                cx, cy = plan.t0_point(slot, m.x, m.y)
                self.g1(z=plan.zbase(L, t_ref, cx, cy) + (m.z - b.z), f=m.f)
                continue
            fr = self.breakpoints(L, t_ref, slot, m, b.z)
            n = len(fr) - 1
            for k in range(1, n + 1):
                a0, a1 = fr[k - 1], fr[k]
                ex, ey = m.x0 + (m.x - m.x0) * a1, m.y0 + (m.y - m.y0) * a1
                ez = m.z0 + (m.z - m.z0) * a1
                cx, cy = plan.t0_point(slot, ex, ey)
                X, Y = plan.cmd_xy(tools[0], slot, ex, ey, copy)
                Z = self._zs[k] + (ez - b.z)
                e = None
                if m.kind == "extrude":
                    e = m.e * (a1 - a0)
                    if 1 < L <= F:
                        mx_, my_ = plan.t0_point(slot, m.x0 + (m.x - m.x0) * (a0 + a1) / 2, m.y0 + (m.y - m.y0) * (a0 + a1) / 2)
                        h = plan.thickness(L, t_ref, mx_, my_)
                        e *= flow_area(h, m.width) / flow_area(b.height, m.width)
                    for t in tools:
                        tip = Z - plan.offsets[t][2]
                        if tip > self.top_phys:
                            self.top_phys = tip
                elif m.kind == "wipe":
                    e = m.e * (a1 - a0)
                    for t in tools:
                        self.level[t] -= e
                self.g1(x=X, y=Y, z=Z, e=e, f=m.f)
                self.counts["pieces"] += 1
        for t in tools:
            if abs(self.level[t] - self.retract_len) < 5e-5:
                self.level[t] = self.retract_len

    def breakpoints(self, L, t_ref, slot, m, layer_z):
        """Split a move so the commanded Z follows the compensation surface.

        Pieces are at most ``max_segment`` long and are bisected until the
        surface deviates from the straight piece by at most ``lin_tol`` at the
        piece's midpoint and quarter points (minimum piece ``min_segment``).
        Leaves ``self._zs``: surface Z at each breakpoint."""
        plan = self.plan
        maxseg = self.o["max_segment"]
        tol = self.o.get("lin_tol", 0.001)
        minseg = self.o.get("min_segment", 0.25)
        Lxy = m.xy_len

        def surf(a):
            x, y = plan.t0_point(slot, m.x0 + (m.x - m.x0) * a, m.y0 + (m.y - m.y0) * a)
            return plan.zbase(L, t_ref, x, y)
        n = max(1, int(math.ceil(Lxy / maxseg - 1e-9)))
        pts = [(k / n, surf(k / n)) for k in range(n + 1)]
        out = [pts[0]]
        stack = list(reversed(list(zip(pts[:-1], pts[1:]))))
        while stack:
            (a0, z0), (a1, z1) = stack.pop()
            if (a1 - a0) * Lxy > 2 * minseg:
                am = 0.5 * (a0 + a1)
                zm = surf(am)
                err = abs(zm - 0.5 * (z0 + z1))
                if err <= tol and (a1 - a0) * Lxy > 1.0:
                    for q in (0.25, 0.75):
                        aq = a0 + (a1 - a0) * q
                        err = max(err, abs(surf(aq) - (z0 + (z1 - z0) * q)))
                if err > tol:
                    stack.append(((am, zm), (a1, z1)))
                    stack.append(((a0, z0), (am, zm)))
                    continue
            out.append((a1, z1))
        self._zs = [z for _, z in out]
        return [a for a, _ in out]

    # ------------------------------------------------------------- sequence
    def run(self):
        o, p, plan, m, out = self.o, self.p, self.plan, self.m, self.out
        F = plan.foundation_layers
        temp, bed = m.nozzle_temp, m.bed_temp
        nL = m.layer_count
        slots = plan.slots
        order_fwd = list(slots)
        order_rev = list(reversed(slots))
        H = list(plan.heads)
        n = len(slots) * len(H)
        title = "Farm Mode" if plan.mode == "farm" else "Copy Mode"

        out("; Quadro Print Studio %s - %s" % (__version__, title))
        out("; CANDIDATE OUTPUT - software-verified only, NOT validated on hardware.")
        out("; copies=%d (%d heads x %d) layers=%d foundation_layers=%d preset=%s" % (n, len(H), len(slots), nL, F, o["preset"]))
        if len(H) < 4:
            out("; printing heads: %s; parked for the whole print: %s" % (",".join("T%d" % t for t in H),
                                                                         ",".join("T%d" % t for t in range(4) if t not in H)))
        out("; slice_sha256=%s profile_fingerprint=%s mesh=%s" % (self.input_sha, self.profile_fp, p.mesh_name))
        out("; tool offsets (x,y,z) applied in coordinates: " + " ".join(
            "T%d=(%.4f,%.4f,%.4f)" % (t, *plan.offsets[t]) for t in range(4)))
        out("; bed mesh embedded in Z (runtime mesh cleared; restored at end). Do not enable runtime bed mesh.")
        out("G90")
        out("M83")
        out("M220 S100")
        out("M221 S100")
        for t in range(4):
            out("SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE=%s" % (EXTR[t], EXTR[t]))
        out("_PRINT_HOME TEMP=%s BED_TEMP=%s" % (_fmt(temp, 0), _fmt(bed, 0)))
        out("G90")
        out("M83")
        out("BED_MESH_CLEAR")
        out("SET_GCODE_OFFSET X=0 Y=0 Z=0 MOVE=0")
        self.g1(z=o["start_z"], f=900)
        out("M190 S%s" % _fmt(bed, 0))
        self.qpm(plan.park_home, "park-at-endstop-positions")
        for t in range(4):
            out("M104 T%d S%s" % (t, _fmt(o["standby_temp"] if t in H else 0, 0)))
            out("SET_PRESSURE_ADVANCE EXTRUDER=%s ADVANCE=%s" % (EXTR[t], repr(m.pressure_advance)))
            self.set_fans([t], 0.0)
        out("SET_PRINT_STATS_INFO TOTAL_LAYER=%d CURRENT_LAYER=1" % nL)

        # ---------------- independent foundation, one head after another -------------
        lead = int(o.get("preheat_lead_layers") or 0)
        preheated = set()
        for ai, a in enumerate(H):
            out("; ===== FOUNDATION T%d: layers 1..%d, %d slots =====" % (a, F, len(slots)))
            if a not in preheated:
                out("M104 T%d S%s" % (a, _fmt(temp, 0)))
            zp = max(self.safe_z([0, 1, 2, 3], o["park_clearance"]), o["start_z"])
            self.lift_to(zp)
            pos = {}
            for t in range(4):
                if t < a:
                    pos[t] = plan.left_stack[t] if t > 0 or a > 0 else plan.park_home[t]
                elif t > a:
                    pos[t] = plan.right_stack[t]
                else:
                    pos[t] = plan.purge[a]["x0"] + plan.offsets[a][0]
            # keep tools at their home park when home is already on the required side
            for t in range(4):
                if t < a and plan.park_home[t] <= pos[t]:
                    pos[t] = plan.park_home[t]
                if t > a and plan.park_home[t] >= pos[t]:
                    pos[t] = plan.park_home[t]
            self.qpm(pos, "arrange-T%d" % a)
            out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t%d MODE=PRIMARY" % a)
            out("ACTIVATE_EXTRUDER EXTRUDER=%s" % EXTR[a])
            out("SET_GCODE_OFFSET X=0 Y=0 Z=0 MOVE=0")
            out("G90")
            out("M83")
            pg = plan.purge[a]
            ycmd = pg["y"] + plan.offsets[a][1]
            self.g1(y=ycmd, f=12000)
            out("M109 T%d S%s" % (a, _fmt(temp, 0)))
            # straight front prime line
            out("; FARM_PURGE tool=%d" % a)
            t0y = ycmd       # T0-frame y for mesh lookup: physical = ycmd - oy
            x0p = pg["x0"]
            z_at = lambda xp: plan.layer_z[0] + p.mesh.z(xp, pg["y"]) + plan.offsets[a][2]
            self.g1(z=z_at(x0p), f=450)
            steps = int(round(o["purge_length"] / o["purge_step"]))
            for k in range(1, steps + 1):
                xp = x0p + k * o["purge_length"] / steps
                self.g1(x=xp + plan.offsets[a][0], z=z_at(xp), e=o["purge_e_per_mm"] * o["purge_length"] / steps, f=o["purge_feed"])
            self.top_phys = max(self.top_phys, plan.layer_z[0] + max(p.mesh.z(x0p, pg["y"]), p.mesh.z(pg["x1"], pg["y"])))
            self.level[a] = 0.0
            self.retract([a], self.retract_len)
            out("; FARM_PURGE_END")
            fan_state = None
            nxt = H[ai + 1] if ai + 1 < len(H) else None
            for L in range(1, F + 1):
                if lead and nxt is not None and L == max(1, F - lead + 1):
                    out("; preheat next head while T%d prints layer %d" % (a, L))
                    out("M104 T%d S%s" % (nxt, _fmt(temp, 0)))
                    preheated.add(nxt)
                for c in m.gap_events.get(L, []):
                    if c.kind == "fan":
                        self.set_fans([a], float(c.text.split()[1]))
                for s in (order_fwd if L % 2 == 1 else order_rev):
                    self.block(L, s, [a], copy=False)
            # park this head
            self.retract([a], self.toolchange_retract - self.level[a])
            zp = self.safe_z([0, 1, 2, 3], o["park_clearance"])
            self.lift_to(zp)
            self.qpm(plan.park_home, "park-after-T%d" % a)
            if a != H[-1]:
                out("M104 T%d S%s" % (a, _fmt(o["standby_temp"], 0)))

        # ---------------- COPY mode -------------------------------------------------------
        out("; ===== COPY MODE: layers %d..%d, %d heads synchronized =====" % (F + 1, nL, len(H)))
        for t in H:
            out("M104 T%d S%s" % (t, _fmt(temp, 0)))
        for t in H:
            out("M109 T%d S%s" % (t, _fmt(temp, 0)))
        first_slot = order_fwd[0] if (F + 1) % 2 == 1 else order_rev[0]
        b = m.blocks[F]
        x0c, _ = plan.cmd_xy(0, first_slot, b.moves()[0].x0, b.moves()[0].y0, copy=True)
        pos = {t: (x0c + plan.copy_carriage_delta[t]) if t in H else plan.park_home[t] for t in range(4)}
        self.qpm(pos, "copy-entry")
        out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t0 MODE=PRIMARY")
        out("ACTIVATE_EXTRUDER EXTRUDER=extruder")
        out("SET_GCODE_OFFSET X=0 Y=0 Z=0 MOVE=0")
        out("G90")
        out("M83")
        for t in H[1:]:
            out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t%d MODE=COPY" % t)
            out("SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE=extruder" % EXTR[t])
        allt = list(H)
        for L in range(F + 1, nL + 1):
            out("SET_PRINT_STATS_INFO TOTAL_LAYER=%d CURRENT_LAYER=%d" % (nL, L))
            for c in m.gap_events.get(L, []):
                if c.kind == "fan":
                    self.set_fans(allt, float(c.text.split()[1]))
            for s in (order_fwd if L % 2 == 1 else order_rev):
                self.block(L, s, allt, copy=True)
        for Lx, evs in m.gap_events.items():
            if Lx > nL:
                for c in evs:
                    if c.kind == "fan":
                        self.set_fans(allt, float(c.text.split()[1]))

        # ---------------- shutdown ----------------------------------------------------------
        out("; ===== END =====")
        self.lift_to(self.safe_z(allt, o["park_clearance"]))
        out("M400")
        for t in range(4):
            out("SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE=%s" % (EXTR[t], EXTR[t]))
        out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t0 MODE=PRIMARY")
        out("ACTIVATE_EXTRUDER EXTRUDER=extruder")
        out("SET_GCODE_OFFSET X=0 Y=0 Z=0 MOVE=0")
        out("G90")
        out("M83")
        for t in (1, 2, 3):
            out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t%d MODE=INACTIVE" % t)
        self.qpm(plan.park_home, "final-park")
        out("TURN_OFF_HEATERS")
        for t in range(4):
            out("SET_FAN_SPEED FAN=%s SPEED=0" % p.extruders[t].fan)
            self.fan[t] = 0.0
        out("BED_MESH_PROFILE LOAD=%s" % p.mesh_name)
        out("M82")
        out("M117 %s complete" % title)
        text = self.out.text()
        return text


def generate(model, prof, plan, input_sha256="", profile_fp=""):
    g = FarmGenerator(model, prof, plan, input_sha256, profile_fp)
    text = g.run()
    return text, dict(g.counts, lines=len(g.out.lines), sha256=hashlib.sha256(text.encode()).hexdigest())
