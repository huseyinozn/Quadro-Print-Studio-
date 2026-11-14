"""Dual Color Copy Mode G-code generation.

Sequence
* Start-up identical to Farm/Copy (home, mesh cleared, bed, all heads parked).
* Layers 1..F: T0, T1, T2, T3 each print their own region alone. Before its
  first print a head lays its own straight front prime line; every turn starts
  with the head's 10 x 10 mm solid tower layer (outer wall 16.7 mm/s, infill
  100 mm/s), then the slice's wipe + retract, z-hop travel and prime into the
  model region. Carriages go directly from the current arrangement to the next
  one (no park-right-then-left detour).
* Layers > F: pair A (T0 primary, T1 COPY, extruder1 synced) prints both
  colour-1 regions incl. towers, retracts, unsyncs, parks; pair B (T2, T3)
  prints the colour-2 regions of the same layer.
* Waiting heads drop to standby; each head is preheated by estimated motion
  time (slice preheat_time) before it is needed, and M109 waits before any
  extrusion.
"""
from __future__ import annotations

import math

from . import __version__
from .generate import FarmGenerator, EXTR, _fmt
from .geometry import flow_area
from .plan import Slot
from .dualplan import PAIR, PAIR_OF


class DualGenerator(FarmGenerator):
    def __init__(self, dual, prof, plan, input_sha256="", profile_fp=""):
        super().__init__(dual.models[0], prof, plan, input_sha256, profile_fp)
        self.dual = dual
        self.tslot = Slot(0, 0, 0, 0.0, 0.0, 0.0, 0.0)
        self.tower_layers = {t: 0 for t in range(4)}
        self.first = {t: True for t in range(4)}

    # ------------------------------------------------------------------ carriage arrangement
    def arrangement(self, active, x_active):
        """Positions: heads left of the active one(s) at their left stack/home,
        right of them at the right stack/home, active heads at x_active."""
        plan = self.plan
        pos = {}
        lo_act, hi_act = min(active), max(active)
        for t in range(4):
            if t in active:
                pos[t] = x_active[t]
            elif t < lo_act:
                pos[t] = plan.park_home[t] if plan.park_home[t] <= plan.left_stack[t] else plan.left_stack[t]
            else:
                pos[t] = plan.park_home[t] if plan.park_home[t] >= plan.right_stack[t] else plan.right_stack[t]
        return pos

    def tower_corner(self, t, P, L, copy):
        x0, y0 = self.plan.towers[P]
        return x0, y0

    # ------------------------------------------------------------------ tower
    def tower(self, L, tools, t_ref, copy):
        plan, o, out = self.plan, self.o, self.out
        P = PAIR_OF[tools[0]]
        x0, y0 = plan.towers[P]
        tw = plan.tower_size
        w = 0.45
        h0 = self.m.blocks[L - 1].height
        af = math.pi * (self.m.config["filament_diameter"] / 2.0) ** 2
        wall_f = o["tower_wall_speed"] * 60.0
        fill_f = o["tower_infill_speed"] * 60.0
        a, b = x0 + w / 2.0, y0 + w / 2.0
        c, d = x0 + tw - w / 2.0, y0 + tw - w / 2.0
        path = [(a, b, wall_f), (c, b, wall_f), (c, d, wall_f), (a, d, wall_f), (a, b, wall_f)]
        inner = w
        lines = []
        if L % 2:
            y = b + inner
            k = 0
            while y <= d - inner + 1e-9:
                xs = (a + inner, c - inner) if k % 2 == 0 else (c - inner, a + inner)
                lines.append(((xs[0], y), (xs[1], y)))
                y += w
                k += 1
        else:
            x = a + inner
            k = 0
            while x <= c - inner + 1e-9:
                ys = (b + inner, d - inner) if k % 2 == 0 else (d - inner, b + inner)
                lines.append(((x, ys[0]), (x, ys[1])))
                x += w
                k += 1
        # approach
        zt = self.safe_z(tools, o["travel_clearance"])
        self.lift_to(zt)
        acc = self.first_travel_acc if L == 1 else self.travel_acc
        out("SET_VELOCITY_LIMIT ACCEL=%s ACCEL_TO_DECEL=%s" % (_fmt(acc, 0), _fmt(acc / 2, 0)))
        X, Y = plan.cmd_xy(tools[0], self.tslot, a, b, copy)
        self.g1(x=X, y=Y, f=self.travel_f)
        self.g1(z=plan.zbase(L, t_ref, a, b), f=self.descend_f)
        out("; DUAL_TOWER head=%s layer=%d" % (",".join(str(t) for t in tools), L))
        lv = self.level[tools[0]]
        if lv > 1e-9:
            self.g1(e=round(lv, 5), f=self.m.config["deretraction_speed"] * 60.0 or self.retract_f)
            for t in tools:
                self.level[t] = 0.0
        out("SET_VELOCITY_LIMIT ACCEL=1000 ACCEL_TO_DECEL=500")
        cur = (a, b)
        segs = [((path[i][0], path[i][1]), (path[i + 1][0], path[i + 1][1]), wall_f) for i in range(4)]
        prev = (a, b)
        for (p0, p1) in lines:
            if (abs(p0[0] - prev[0]) + abs(p0[1] - prev[1])) > 1e-9:
                segs.append((prev, p0, fill_f))
            segs.append((p0, p1, fill_f))
            prev = p1
        for (p0, p1, f) in segs:
            Lseg = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            n = max(1, int(math.ceil(Lseg / 2.0)))
            for kk in range(1, n + 1):
                fr0, fr1 = (kk - 1) / n, kk / n
                ex, ey = p0[0] + (p1[0] - p0[0]) * fr1, p0[1] + (p1[1] - p0[1]) * fr1
                mx, my = p0[0] + (p1[0] - p0[0]) * (fr0 + fr1) / 2, p0[1] + (p1[1] - p0[1]) * (fr0 + fr1) / 2
                hh = plan.thickness(L, t_ref, mx, my)
                e = flow_area(hh, w) * Lseg / n / af
                Z = plan.zbase(L, t_ref, ex, ey)
                X, Y = plan.cmd_xy(tools[0], self.tslot, ex, ey, copy)
                self.g1(x=X, y=Y, z=Z, e=e, f=f)
                for t in tools:
                    self.top_phys = max(self.top_phys, Z - plan.offsets[t][2])
        # leave the tower like the slice leaves a path: wipe back while retracting, then z-hop travel (block approach)
        wd = float(self.m.config.get("wipe_distance", 0.0) or 0.0)
        lastp0, lastp1, _f = segs[-1]
        Lw = min(wd, math.hypot(lastp1[0] - lastp0[0], lastp1[1] - lastp0[1]))
        if Lw > 0:
            ux = (lastp0[0] - lastp1[0]) / max(1e-9, math.hypot(lastp1[0] - lastp0[0], lastp1[1] - lastp0[1]))
            uy = (lastp0[1] - lastp1[1]) / max(1e-9, math.hypot(lastp1[0] - lastp0[0], lastp1[1] - lastp0[1]))
            ex, ey = lastp1[0] + ux * Lw, lastp1[1] + uy * Lw
            X, Y = plan.cmd_xy(tools[0], self.tslot, ex, ey, copy)
            self.g1(x=X, y=Y, z=plan.zbase(L, t_ref, ex, ey), e=-self.retract_len, f=self.travel_f * 0.8)
        else:
            self.g1(e=-self.retract_len, f=self.retract_f)
        for t in tools:
            self.level[t] += self.retract_len
        out("; DUAL_TOWER_END")

    # ------------------------------------------------------------------ head / pair turns
    def activate(self, primary, copy_head=None):
        out = self.out
        out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t%d MODE=PRIMARY" % primary)
        out("ACTIVATE_EXTRUDER EXTRUDER=%s" % EXTR[primary])
        out("SET_GCODE_OFFSET X=0 Y=0 Z=0 MOVE=0")
        out("G90")
        out("M83")
        if copy_head is not None:
            out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t%d MODE=COPY" % copy_head)
            out("SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE=%s" % (EXTR[copy_head], EXTR[primary]))

    def heat_before(self, heads):
        for t in heads:
            if self.started_heating.get(t):
                continue
            self.schedule_preheat(t, self.temp, self.pre_t, "standby_T%d" % t)
        for t in heads:
            self.out("M109 T%d S%s" % (t, _fmt(self.temp, 0)))
        for t in heads:
            self.started_heating[t] = False

    def standby(self, heads):
        for t in heads:
            self.mark("standby_T%d" % t)
            self.out("M104 T%d S%s" % (t, _fmt(self.standby_temp, 0)))

    def purge(self, a):
        plan, o, p, out = self.plan, self.o, self.p, self.out
        pg = plan.purge[a]
        out("; FARM_PURGE tool=%d" % a)
        x0p = pg["x0"]
        y_phys = pg["y"]
        ycmd = y_phys + plan.offsets[a][1]
        self.g1(y=ycmd, f=12000)
        z_at = lambda xp: plan.layer_z[0] + p.mesh.z(xp, y_phys) + plan.offsets[a][2]
        self.g1(z=z_at(x0p), f=450)
        steps = int(round(o["purge_length"] / o["purge_step"]))
        for k in range(1, steps + 1):
            xp = x0p + k * o["purge_length"] / steps
            self.g1(x=xp + plan.offsets[a][0], z=z_at(xp), e=o["purge_e_per_mm"] * o["purge_length"] / steps, f=o["purge_feed"])
        self.top_phys = max(self.top_phys, plan.layer_z[0] + max(p.mesh.z(x0p, y_phys), p.mesh.z(pg["x1"], y_phys)))
        self.level[a] = 0.0
        self.retract([a], self.retract_len)
        out("; FARM_PURGE_END")

    def solo_turn(self, a, L):
        plan, o = self.plan, self.o
        P = PAIR_OF[a]
        tx, ty = plan.towers[P]
        X, _ = plan.cmd_xy(a, self.tslot, tx, ty, False)
        start_x = plan.purge[a]["x0"] + plan.offsets[a][0] if self.first[a] else X
        self.lift_to(max(self.safe_z([0, 1, 2, 3], o["park_clearance"]), o["start_z"] if L == 1 else 0.0))
        self.qpm(self.arrangement([a], {a: start_x}), "arrange-T%d-layer%d" % (a, L))
        self.activate(a)
        self.heat_before([a])
        if self.first[a]:
            self.purge(a)
            self.first[a] = False
        self.m = self.dual.models[PAIR[P][0]]
        self.tower(L, [a], a, False)
        self.block(L, self.plan.slots[0], [a], copy=False)
        self.retract([a], self.toolchange_retract - self.level[a])
        self.standby([a])

    def pair_turn(self, P, L):
        plan, o, out = self.plan, self.o, self.out
        h0, h1 = PAIR[P]
        tx, ty = plan.towers[P]
        X, _ = plan.cmd_xy(h0, self.tslot, tx, ty, True)
        self.lift_to(self.safe_z([0, 1, 2, 3], o["park_clearance"]))
        self.qpm(self.arrangement([h0, h1], {h0: X, h1: X + plan.copy_carriage_delta[P]}), "pair-%s-layer%d" % (P, L))
        self.activate(h0, h1)
        self.heat_before([h0, h1])
        self.m = self.dual.models[h0]
        self.tower(L, [h0, h1], P, True)
        self.block(L, self.plan.slots[0], [h0, h1], copy=True)
        self.retract([h0, h1], self.toolchange_retract - self.level[h0])
        self.lift_to(self.safe_z([0, 1, 2, 3], o["travel_clearance"]))
        out("M400")
        out("SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE=%s" % (EXTR[h1], EXTR[h1]))
        out("SET_DUAL_CARRIAGE CARRIAGE=carriage_t%d MODE=INACTIVE" % h1)
        self.standby([h0, h1])

    # ------------------------------------------------------------------ main
    def run(self):
        o, p, plan, out = self.o, self.p, self.plan, self.out
        m0 = self.dual.models[0]
        F = plan.foundation_layers
        nL = m0.layer_count
        self.temp, bed = m0.nozzle_temp, m0.bed_temp
        self.standby_temp = o["standby_temp"] if o.get("standby_temp") is not None else max(0.0, self.temp + self.dual.standby_delta)
        self.pre_t = float(self.dual.preheat_time or 0.0)
        self.started_heating = {t: False for t in range(4)}
        out("; Quadro Print Studio %s - Dual Color Copy Mode" % __version__)
        out("; CANDIDATE OUTPUT - software-verified only, NOT validated on hardware.")
        out("; copies=2 (left: T0 colour 1 + T2 colour 2, right: T1 colour 1 + T3 colour 2) layers=%d foundation_layers=%d" % (nL, F))
        out("; pairs from layer %d: A = T0+T1 (T1 COPY), then B = T2+T3 (T3 COPY) on every layer" % (F + 1))
        out("; slice_sha256=%s profile_fingerprint=%s mesh=%s" % (self.input_sha, self.profile_fp, p.mesh_name))
        out("; tool offsets (x,y,z) applied in coordinates: " + " ".join("T%d=(%.4f,%.4f,%.4f)" % (t, *plan.offsets[t]) for t in range(4)))
        out("; bed mesh embedded in Z (runtime mesh cleared; restored at end). Do not enable runtime bed mesh.")
        out("G90")
        out("M83")
        out("M220 S100")
        out("M221 S100")
        for t in range(4):
            out("SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE=%s" % (EXTR[t], EXTR[t]))
        out("_PRINT_HOME TEMP=%s BED_TEMP=%s" % (_fmt(self.temp, 0), _fmt(bed, 0)))
        out("G90")
        out("M83")
        out("BED_MESH_CLEAR")
        out("SET_GCODE_OFFSET X=0 Y=0 Z=0 MOVE=0")
        self.g1(z=o["start_z"], f=900)
        out("M190 S%s" % _fmt(bed, 0))
        self.carriage = {0: 150.0, 1: 356.5, 2: plan.park_home[2], 3: plan.park_home[3]}   # after homing
        for t in range(4):
            self.mark("standby_T%d" % t)
            if t == 0:
                out("M104 T0 S%s" % _fmt(self.temp, 0))
                self.started_heating[0] = True
            else:
                out("M104 T%d S%s" % (t, _fmt(self.standby_temp, 0)))
            out("SET_PRESSURE_ADVANCE EXTRUDER=%s ADVANCE=%s" % (EXTR[t], repr(m0.pressure_advance)))
            self.set_fans([t], 0.0)
        for L in range(1, nL + 1):
            out("SET_PRINT_STATS_INFO TOTAL_LAYER=%d CURRENT_LAYER=%d" % (nL, L))
            if L <= F:
                for a in range(4):
                    self.solo_turn(a, L)
            else:
                self.pair_turn("A", L)
                self.pair_turn("B", L)
        out("; ===== END =====")
        self.lift_to(self.safe_z([0, 1, 2, 3], o["park_clearance"]))
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
        out("BED_MESH_PROFILE LOAD=%s" % p.mesh_name)
        out("M82")
        out("M117 Dual Color Copy Mode complete")
        return self.out.text()


def generate_dual(dual, prof, plan, input_sha256="", profile_fp=""):
    import hashlib
    g = DualGenerator(dual, prof, plan, input_sha256, profile_fp)
    text = g.run()
    return text, dict(g.counts, lines=len(g.out.lines), sha256=hashlib.sha256(text.encode()).hexdigest(), preheats=g.preheats)
