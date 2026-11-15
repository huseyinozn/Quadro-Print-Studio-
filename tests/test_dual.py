"""Dual Color Copy Mode and Copy Mode preheat tests (synthetic inputs only).

Left model: T0 colour 1 + T2 colour 2; right model: T1 colour 1 + T3 colour 2.
The consistent calibration used here is TEST-ONLY: T3's offsets are chosen so
that both pairs can print both models at one shared Z and Y.
"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from quadro_studio.diagnostics import StageError
from quadro_studio.dualslice import load_dual
from quadro_studio.dualplan import make_dual_plan
from quadro_studio.dualgen import generate_dual
from quadro_studio.profile import load_profile
from quadro_studio.slicer import load_slice
from quadro_studio.plan import make_plan, LayoutOptions
from quadro_studio.generate import generate
from quadro_studio.verify import Sim, analyse
from quadro_studio.verify_dual import verify_dual
import synth
import synth_dual
import synth_profile

LAYERS = 14
CONSISTENT = {"T0": {"x": 0.0, "y": 0.0, "z": 0.0}, "T1": {"x": 0.06, "y": -0.25, "z": 0.15},
              "T2": {"x": -1.55, "y": -0.05, "z": 0.10}, "T3": {"x": -1.74, "y": -0.30, "z": 0.25}}


class DualMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.slice = synth_dual.write(os.path.join(cls.tmp, "dual.gcode"), layers=LAYERS)
        cls.dual, _ = load_dual(cls.slice)
        cls.prof, _ = load_profile(synth_profile.build(os.path.join(cls.tmp, "ok.zip"), offsets=CONSISTENT))
        cls.bad_prof, _ = load_profile(synth_profile.build(os.path.join(cls.tmp, "bad.zip")))
        cls.plan, d = make_dual_plan(cls.dual, cls.prof)
        assert d.ok(), d.render()
        cls.text, cls.info = generate_dual(cls.dual, cls.prof, cls.plan)
        cls.lines = cls.text.split("\n")

    def verify_text(self, text, prof=None):
        p = os.path.join(self.tmp, "v.gcode")
        with open(p, "w") as f:
            f.write(text)
        res, sim = verify_dual(prof or self.prof, self.dual.models, p, self.plan.foundation_layers)
        return res, sim

    def layer_range(self, L):
        a = self.lines.index("SET_PRINT_STATS_INFO TOTAL_LAYER=%d CURRENT_LAYER=%d" % (LAYERS, L))
        b = self.lines.index("SET_PRINT_STATS_INFO TOTAL_LAYER=%d CURRENT_LAYER=%d" % (LAYERS, L + 1))
        return a, b

    # ------------------------------------------------------------------ pass
    def test_baseline_passes_every_layer_and_region(self):
        res, sim = self.verify_text(self.text)
        self.assertTrue(res["passed"], res["issues"][:5])
        for t in range(4):
            self.assertEqual(res["dual_region_layers_ok"]["T%d" % t], "%d/%d" % (LAYERS, LAYERS))
        self.assertEqual(res["synchronized_from_layer"], 4)
        self.assertEqual(res["dual_registration_mm"], {"left": 0.0, "right": 0.0})
        self.assertEqual(res["copies_complete"], 4)
        self.assertFalse(res["idle_moves_toward_printing_heads"])
        self.assertLessEqual(abs(sim.stats["copy_thick_max_dev"]), 0.004)

    def test_pairs_and_order(self):
        _, sim = self.verify_text(self.text)
        got = {}
        for vs in sim.visit_states:
            got.setdefault(vs["layer"], []).append(vs["moving"])
        self.assertEqual(got[1], [[0], [1], [2], [3]])
        self.assertEqual(got[3], [[0], [1], [2], [3]])
        self.assertEqual(got[4], [[0, 1], [2, 3]])
        self.assertEqual(got[LAYERS], [[0, 1], [2, 3]])

    def test_prime_line_directly_before_each_heads_first_print(self):
        res, sim = self.verify_text(self.text)
        self.assertEqual(res["prime_line_problems"], [])
        self.assertEqual(sorted(p["nozzle"] for p in sim.purges), [0, 1, 2, 3])
        for fp in res["first_prints"]:
            if fp["event"] == "after_purge":
                self.assertTrue(fp["waited"])

    def test_tower_speeds_and_size(self):
        res, _ = self.verify_text(self.text)
        self.assertEqual(res["tower_problems"], [])
        self.assertIn("F1002", self.text)       # 16.7 mm/s outer wall
        self.assertIn("F6000", self.text)       # 100 mm/s infill
        for t, v in res["towers"].items():
            bb = v["bbox"]
            self.assertLessEqual(bb[2] - bb[0], 10.0)
            self.assertLessEqual(bb[3] - bb[1], 10.0)

    def test_tower_to_model_keeps_retract_wipe_zhop_deretract(self):
        i = self.lines.index("; DUAL_TOWER_END")
        wipe = self.lines[i - 1]
        self.assertRegex(wipe, r"^G1 X\S+ Y\S+ Z\S+ E-0\.4")             # wipe while retracting
        j = next(k for k in range(i, len(self.lines)) if self.lines[k].startswith("; FARM_PART"))
        between = self.lines[i + 1:j]
        self.assertTrue(any(re.match(r"^G1 Z\S+ F", l) for l in between[:2]))   # lift (z-hop) before travel
        self.assertTrue(self.lines[j + 1].startswith("G1 E0.4"))         # deretract on the model

    def test_preheat_before_every_m109_and_never_cold(self):
        res, _ = self.verify_text(self.text)
        self.assertNotIn("COLD_EXTRUDE", res["issue_counts"])
        self.assertTrue(self.info["preheats"])
        self.assertTrue(all(p["lead_s"] >= p["wanted_s"] - 1e-6 or p["limited_by_standby"] for p in self.info["preheats"]))

    def test_deterministic(self):
        t2, _ = generate_dual(self.dual, self.prof, self.plan)
        self.assertEqual(t2, self.text)

    # ------------------------------------------------------------------ refusals
    def test_inconsistent_offsets_pass_limits_with_least_into_material_split(self):
        # synthetic offsets like the real printer: T2-T0 = +0.10, T3-T1 = -0.13 (Z); Y residual 0.21
        plan, d = make_dual_plan(self.dual, self.bad_prof)
        ck = {c["name"]: c for c in plan.checks}
        for name in ("pair_z_consistency", "pair_y_registration", "into_material", "travel_drag"):
            self.assertTrue(ck[name]["ok"], ck[name]["detail"])
        # ...but the room for the filament where colours stack is not enough on both models
        self.assertFalse(ck["colour_stack_room"]["ok"])
        self.assertEqual({x.code for x in d.errors}, {"PLAN_COLOUR_STACK_ROOM"})
        self.assertEqual(plan.z_split, "least_into_material")
        self.assertIn("Fix applied", ck["into_material"]["detail"])
        self.assertLessEqual(plan.z_splits["least_into_material"]["max_into_material"], 0.03)

    def test_symmetric_split_still_blocked_by_into_material(self):
        # the Z deviation (0.115) is within 0.30, but the separate into-material check is NOT relaxed
        from quadro_studio.dualplan import DualOptions
        plan, d = make_dual_plan(self.dual, self.bad_prof, DualOptions(z_split="symmetric"))
        ck = {c["name"]: c for c in plan.checks}
        self.assertTrue(ck["pair_z_consistency"]["ok"])
        self.assertFalse(ck["into_material"]["ok"])
        self.assertIn("PLAN_INTO_MATERIAL", {x.code for x in d.errors})
        za = plan.z_analysis
        self.assertNotIn("z_deviation", za["blocking"])
        self.assertIn("nozzle_into_material", za["blocking"])
        # colours swap halves every 10 layers: the vertical colour change is at layer 11
        layers = {it["layer"] for side in ("left", "right") for it in za["sides"][side]["examples"]["colour_change_interior"]}
        self.assertTrue(layers <= {11} or not layers)
        text, info = generate_dual(self.dual, self.bad_prof, plan)
        res, _ = self.verify_text(text, self.bad_prof)
        self.assertIn("DUAL_INTO_MATERIAL", res["issue_counts"])

    def test_z_lattice_matches_planned_heights(self):
        from quadro_studio.dualz import _Lattice
        import numpy as np
        plan, _ = make_dual_plan(self.dual, self.bad_prof)
        m = self.dual.models[0]
        lat = _Lattice(plan, m.bbox_motion[0] - 2, m.bbox_motion[1] - 2, m.bbox_motion[2] + 2, m.bbox_motion[3] + 2)
        sl = plan.slots[0]
        for t in range(4):
            for L in (1, 2, 3, 4, 9):
                for (x, y) in ((m.bbox_extrude[0] + 1.3, m.bbox_extrude[1] + 2.1), (m.bbox_extrude[2] - 0.7, m.bbox_extrude[3] - 3.3)):
                    ref = plan.zbase(L, t if L <= plan.foundation_layers else ("A" if t < 2 else "B"), x + sl.dx, y + sl.dy) - plan.offsets[t][2]
                    self.assertAlmostEqual(float(lat.tip(t, L, np.array([x]), np.array([y]))[0]), ref, delta=2e-4)

    def test_y_residual_reported_separately(self):
        plan, d = make_dual_plan(self.dual, self.bad_prof)
        y = next(c for c in plan.checks if c["name"] == "pair_y_registration")
        self.assertTrue(y["ok"])
        self.assertIn("Separate from Z", y["detail"])
        self.assertAlmostEqual(plan.pair_y["registration_error"], 0.21, places=4)
        self.assertEqual(y["limit"], 0.30)

    def test_consistent_offsets_no_z_findings(self):
        self.assertIsNone(self.plan.z_analysis)
        self.assertTrue(next(c for c in self.plan.checks if c["name"] == "pair_z_consistency")["ok"])

    def test_example_right_copy_from_left_only_when_asked(self):
        p = synth_dual.write(os.path.join(self.tmp, "patch2.gcode"), layers=LAYERS, differ_layer=6, right_patch=True)
        ds, _ = load_dual(p, right_from_left=True)
        self.assertIs(ds.models[1], ds.models[0])
        self.assertIs(ds.models[3], ds.models[2])
        self.assertEqual([x["layer"] for x in ds.right_replaced["colour1"]], [6])
        plan, d = make_dual_plan(ds, self.prof)
        self.assertTrue(d.ok(), d.render())
        cm = next(c for c in plan.checks if c["name"] == "colour_mapping")
        self.assertIn("not used", cm["detail"])
        with self.assertRaises(StageError):            # the user's own file is never silently changed
            load_dual(p)

    def test_renumbered_slice_mapped_by_geometry(self):
        # slice with left model T0/T1 and right model T2/T3: right counterparts found by geometry,
        # physical pairs stay T0+T1 (colour 1) then T2+T3 (colour 2)
        p = synth_dual.write(os.path.join(self.tmp, "renum.gcode"), layers=LAYERS, renumber=True)
        ds, _ = load_dual(p)
        self.assertEqual(ds.tool_map, {0: 0, 1: 2, 2: 1, 3: 3})
        plan, d = make_dual_plan(ds, self.prof)
        self.assertTrue(d.ok(), d.render())
        self.assertIn("head T0 = slice T0 (left), T1 = slice T2 (right)", next(c for c in plan.checks if c["name"] == "colour_mapping")["detail"])
        text, _ = generate_dual(ds, self.prof, plan)
        out = os.path.join(self.tmp, "renum_out.gcode")
        with open(out, "w") as f:
            f.write(text)
        res, sim = verify_dual(self.prof, ds.models, out, plan.foundation_layers)
        self.assertTrue(res["passed"], res["issues"][:4])
        self.assertEqual([vs["moving"] for vs in sim.visit_states if vs["layer"] == 5], [[0, 1], [2, 3]])

    def test_renumbered_slice_with_different_right_painting_refused(self):
        p = synth_dual.write(os.path.join(self.tmp, "renum_bad.gcode"), layers=LAYERS, renumber=True, differ_layer=5, right_patch=True)
        with self.assertRaises(StageError) as cm:
            load_dual(p)
        self.assertIn("DUAL_NOT_COPYABLE", cm.exception.diags.render())

    def test_inconsistent_offsets_output_blocked_by_independent_flow(self):
        plan, d = make_dual_plan(self.dual, self.bad_prof)
        text, _ = generate_dual(self.dual, self.bad_prof, plan)        # forced, for the verifier only
        res, sim = self.verify_text(text, self.bad_prof)
        self.assertFalse(res["passed"])
        self.assertIn("DUAL_SYNC_FLOW", res["issue_counts"])              # both rooms positive: never auto-accepted
        self.assertIn("DUAL_SMALL_NEGATIVE_ROOM", res["issue_counts"])    # the small negative room: warning only
        self.assertNotIn("DUAL_INTO_MATERIAL", res["issue_counts"])
        self.assertAlmostEqual(res["dual_registration_mm"]["left"], 0.21, delta=2e-3)

    def test_room_analysis_conditions(self):
        plan, d = make_dual_plan(self.dual, self.bad_prof)
        r = plan.room
        self.assertAlmostEqual(r["model_room_difference"], 0.23, delta=1e-4)   # (T3-T1) - (T2-T0) Z offsets
        self.assertFalse(r["conditions"]["synchronised_flow"])
        for x in r["sync_flow_failures"]:
            self.assertIn(x["pair"], ("T0+T1", "T2+T3"))
            # what remains after the small-negative rule: both rooms positive (or a large negative room)
            self.assertTrue(x["kind"] == "independent flow mismatch" or x["room_left"] <= -0.05 or x["room_right"] <= -0.05, x)
        self.assertTrue(r["sync_flow_failures"])
        self.assertLess(r["before"]["right"]["centre_min"], 0.08)

    def test_sync_flow_checked_point_by_point(self):
        plan, d = make_dual_plan(self.dual, self.bad_prof)
        r = plan.room
        fc = r["sync_with_compensation"]["first_conflict"]
        self.assertIsNotNone(fc)
        for k in ("layer", "pair", "slicer_line", "left_xy", "right_xy", "room_left", "room_right", "e_left_pct", "e_right_pct"):
            self.assertIn(k, fc)
        self.assertGreater(fc["layer"], plan.foundation_layers)            # only synchronised turns are judged
        st = r["sync_as_planned"]["stats"]
        self.assertGreater(st["both_on_material"], st["conflict_points"])   # most points have a common E

    def test_consistent_offsets_have_common_e_everywhere(self):
        from quadro_studio.dualroom import sync_flow_points
        r = sync_flow_points(self.dual, self.plan, 0.08)
        self.assertTrue(r["feasible"], r["first_conflict"])
        self.assertEqual(r["stats"]["conflict_points"], 0)

    def test_consistent_offsets_room_ok(self):
        ck = {c["name"]: c for c in self.plan.checks}
        self.assertTrue(ck["colour_stack_room"]["ok"])
        res, _ = self.verify_text(self.text)
        self.assertNotIn("DUAL_STACK_ROOM", res["issue_counts"])

    def test_models_coloured_differently_refused(self):
        p = synth_dual.write(os.path.join(self.tmp, "patch.gcode"), layers=LAYERS, differ_layer=6, right_patch=True)
        with self.assertRaises(StageError) as cm:
            load_dual(p)
        self.assertIn("DUAL_NOT_COPYABLE", cm.exception.diags.render())
        ds, d = load_dual(p, strict=False)
        self.assertEqual([x["layer"] for x in ds.region_differences["colour1"]], [6])

    def test_single_colour_file_refused(self):
        p = synth.write(os.path.join(self.tmp, "mono.gcode"))
        with self.assertRaises(StageError) as cm:
            load_dual(p)
        self.assertIn("DUAL_TOOLS", cm.exception.diags.render())

    # ------------------------------------------------------------------ mutations
    def test_mutation_wrong_head_pair(self):
        a, b = self.layer_range(8)
        m = list(self.lines)
        for i in range(a, b):
            m[i] = (m[i].replace("carriage_t3", "carriage_t1")
                    .replace("EXTRUDER=extruder3 MOTION_QUEUE=extruder2", "EXTRUDER=extruder1 MOTION_QUEUE=extruder2")
                    .replace("EXTRUDER=extruder3 MOTION_QUEUE=extruder3", "EXTRUDER=extruder1 MOTION_QUEUE=extruder1"))
        res, _ = self.verify_text("\n".join(m))
        self.assertFalse(res["passed"])
        self.assertIn("DUAL_PAIR", res["issue_counts"])
        self.assertIn("DUAL_REGION", res["issue_counts"])

    def test_mutation_missing_colour_region(self):
        a, b = self.layer_range(8)
        m = list(self.lines)
        inb = False
        for i in range(a, b):
            if m[i].startswith("; FARM_PART"):
                inb = "tools=2,3" in m[i]
            if inb and m[i].startswith("G1 X") and re.search(r" E\d*\.\d*[1-9]", m[i]):
                m[i] = re.sub(r" E[\d.]+", "", m[i])
        res, _ = self.verify_text("\n".join(m))
        self.assertFalse(res["passed"])
        self.assertIn("DUAL_REGION", res["issue_counts"])
        self.assertTrue(any(p["layer"] == 8 and p["head"] in ("T2", "T3") for p in res["dual_region_problems"]))

    def test_mutation_parked_head_pushes_filament(self):
        a, b = self.layer_range(8)
        m = list(self.lines)
        k = next(i for i in range(a, b) if m[i] == "SYNC_EXTRUDER_MOTION EXTRUDER=extruder1 MOTION_QUEUE=extruder")
        m.insert(k + 1, "SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=extruder")
        res, _ = self.verify_text("\n".join(m))
        self.assertFalse(res["passed"])
        self.assertIn("OUT_PARKED_EXTRUDE", res["issue_counts"])

    def test_mutation_idle_head_detour(self):
        # move parked T3 towards T2 while T2 prints alone: right-then-left detour
        m = list(self.lines)
        k = next(i for i, l in enumerate(m) if l.startswith("; FARM_PARK arrange-T2-layer2"))
        q = m[k + 1]
        t3 = float(re.search(r"T3=([-\d.]+)", q).group(1))
        m[k + 1] = q.replace("T3=%s" % re.search(r"T3=([-\d.]+)", q).group(1), "T3=%.5f" % (t3 - 20.0))
        res, _ = self.verify_text("\n".join(m))
        self.assertIn("CARRIAGE_DETOUR", res["issue_counts"])


FLAT = [synth_profile.mesh_block(fn=lambda x, y: 0.0)]


def _offsets(ry=0.0):
    """Consistent Z offsets; Y offsets with a pair Y residual of exactly ry (k = -2 ry)."""
    return {"T0": {"x": 0.0, "y": 0.0, "z": 0.0}, "T1": {"x": 0.06, "y": 0.0, "z": 0.15},
            "T2": {"x": -1.55, "y": 0.0, "z": 0.10}, "T3": {"x": -1.74, "y": 2 * ry, "z": 0.25}}


class DualThresholds(unittest.TestCase):
    """Owner limits (dualtol): Y 0.30 mm, Z deviation 0.30 mm; below, equal, above, in the planner
    AND the independent verifier; into-material stays separate."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.slice = synth_dual.write(os.path.join(cls.tmp, "d.gcode"), layers=LAYERS)
        cls.dual, _ = load_dual(cls.slice)

    def prof(self, ry):
        return load_profile(synth_profile.build(os.path.join(self.tmp, "p%.3f.zip" % ry), meshes=FLAT, offsets=_offsets(ry)))[0]

    def test_within_rule(self):
        from quadro_studio.dualtol import within, DUAL_LIMITS
        self.assertEqual(DUAL_LIMITS["y_registration"], 0.30)
        self.assertEqual(DUAL_LIMITS["z_deviation"], 0.30)
        self.assertEqual(DUAL_LIMITS["into_material"], 0.03)
        self.assertTrue(within(0.29, 0.30))
        self.assertTrue(within(0.30, 0.30))
        self.assertFalse(within(0.31, 0.30))
        self.assertFalse(within(-0.31, 0.30))

    def test_y_planner_and_verifier_below_equal_above(self):
        for ry, ok in ((0.29, True), (0.30, True), (0.31, False)):
            p = self.prof(ry)
            plan, d = make_dual_plan(self.dual, p)
            y = next(c for c in plan.checks if c["name"] == "pair_y_registration")
            self.assertEqual(y["ok"], ok, (ry, y["detail"]))
            self.assertEqual(d.ok(), ok, (ry, d.render()))
            text, _ = generate_dual(self.dual, p, plan)
            out = os.path.join(self.tmp, "y%.2f.gcode" % ry)
            with open(out, "w") as f:
                f.write(text)
            res, _ = verify_dual(p, self.dual.models, out, plan.foundation_layers)
            self.assertAlmostEqual(res["dual_registration_mm"]["left"], ry, delta=2e-4)
            self.assertEqual("DUAL_REGISTRATION" in res["issue_counts"], not ok, (ry, res["issue_counts"]))
            self.assertEqual(res["passed"], ok, (ry, res["issues"][:3]))

    def test_z_planner_below_equal_above(self):
        from quadro_studio.dualz import analyse_z
        p = self.prof(0.0)
        plan, d = make_dual_plan(self.dual, p)
        self.assertTrue(d.ok(), d.render())
        base = plan.dstar
        for dz, over in ((0.29, False), (0.30, False), (0.31, True)):
            plan.dstar = base - dz              # colour 2 dz lower than colour 1 on both models
            za = analyse_z(self.dual, plan)
            mx = max(v["stats"]["colour_change_interior"]["max_abs_deviation"] for v in za["sides"].values())
            self.assertAlmostEqual(mx, dz, delta=2e-4)
            self.assertEqual("z_deviation" in za["blocking"], over, (dz, mx))
            self.assertIn("nozzle_into_material", za["blocking"])     # separate check, still enforced
        plan.dstar = base

    def test_z_verifier_below_equal_above(self):
        p = self.prof(0.0)
        plan, d = make_dual_plan(self.dual, p)
        text, _ = generate_dual(self.dual, p, plan)
        lines = text.split("\n")
        for dz, over in ((0.29, False), (0.30, False), (0.31, True)):
            out, inb = [], False
            for l in lines:
                if l.startswith("; FARM_PART"):
                    m = re.search(r"layer=(\d+) slot=0 tools=(\S+)", l)
                    inb = m.group(2) == "2,3" and int(m.group(1)) > plan.foundation_layers
                elif l.startswith(("; FARM_TRANSFER", "; FARM_PARK", "; DUAL_TOWER", "SET_DUAL_CARRIAGE")):
                    inb = False
                if inb and l.startswith("G1") and " Z" in l:
                    l = re.sub(r" Z([-\d.]+)", lambda mm: " Z%.5f" % (float(mm.group(1)) - dz), l)
                out.append(l)
            path = os.path.join(self.tmp, "z%.2f.gcode" % dz)
            with open(path, "w") as f:
                f.write("\n".join(out))
            res, _ = verify_dual(p, self.dual.models, path, plan.foundation_layers)
            self.assertAlmostEqual(res["dual_z_deviation"]["max_mm"], dz, delta=2e-4)
            self.assertEqual("DUAL_Z_DEVIATION" in res["issue_counts"], over, (dz, res["dual_z_deviation"]))
            self.assertIn("DUAL_INTO_MATERIAL", res["issue_counts"])   # separate check, still enforced

    def test_raised_travel_removes_drags_and_is_verified(self):
        from quadro_studio.dualplan import DualOptions
        p = load_profile(synth_profile.build(os.path.join(self.tmp, "bad.zip")))[0]
        plan, d = make_dual_plan(self.dual, p, DualOptions(z_split="symmetric"))
        text, info = generate_dual(self.dual, p, plan)
        out = os.path.join(self.tmp, "lift.gcode")
        with open(out, "w") as f:
            f.write(text)
        res, _ = verify_dual(p, self.dual.models, out, plan.foundation_layers)
        if plan.travel_lift:
            self.assertGreater(info.get("raised_travels", 0), 0)
            self.assertGreater(res["raised_travel_z_moves"], 0)
        self.assertEqual(res["dual_travel_drag"]["moves"], 0, res["dual_travel_drag"])
        self.assertEqual(res["visits_failed"], 0, res["visits_failed_examples"])


GROW_MISMATCH = {"T0": {"x": 0.0, "y": 0.0, "z": 0.0}, "T1": {"x": 0.06, "y": -0.25, "z": 0.15},
                 "T2": {"x": -1.55, "y": -0.05, "z": 0.10}, "T3": {"x": -1.74, "y": -0.30, "z": 0.02}}


class DualSmallNegativeRoom(unittest.TestCase):
    """Owner rule: on extrusion moves where the colours stack or meet, a negative local room smaller
    than 0.050 mm is an accepted deviation (warning), in the planner and the verifier alike."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.grow, _ = load_dual(synth_dual.write(os.path.join(cls.tmp, "g.gcode"), layers=24, grow=True))

    def test_rule_boundary(self):
        from quadro_studio.dualtol import small_negative, DUAL_LIMITS
        self.assertEqual(DUAL_LIMITS["small_negative_room"], 0.050)
        self.assertEqual(DUAL_LIMITS["into_material"], 0.03)            # travel / wipe / descent unchanged
        self.assertTrue(small_negative(-0.019))
        self.assertTrue(small_negative(-0.049))
        self.assertFalse(small_negative(-0.050))
        self.assertFalse(small_negative(-0.051))
        self.assertFalse(small_negative(0.0))

    def test_mismatch_with_only_small_negative_room_passes_with_warnings(self):
        p = load_profile(synth_profile.build(os.path.join(self.tmp, "m.zip"), offsets=GROW_MISMATCH))[0]
        plan, d = make_dual_plan(self.grow, p)
        self.assertTrue(d.ok(), d.render())
        self.assertIn("PLAN_SMALL_NEGATIVE_ROOM", {w.code for w in d.warnings})
        self.assertLess(plan.room["before"]["right"]["centre_min"], 0)
        text, _ = generate_dual(self.grow, p, plan)
        out = os.path.join(self.tmp, "m.gcode")
        with open(out, "w") as f:
            f.write(text)
        res, _ = verify_dual(p, self.grow.models, out, plan.foundation_layers)
        self.assertTrue(res["passed"], [i for i in res["issues"] if i["severity"] == "error"][:3])
        self.assertIn("DUAL_SMALL_NEGATIVE_ROOM", res["issue_counts"])
        self.assertLess(res["dual_stack_room"]["right"]["centre_min_mm"], 0)
        self.assertGreater(res["dual_stack_room"]["right"]["centre_min_mm"], -0.050)

    def test_planner_boundary(self):
        from quadro_studio.dualz import analyse_z
        from quadro_studio.dualroom import analyse_room
        p = load_profile(synth_profile.build(os.path.join(self.tmp, "c.zip"), meshes=FLAT, offsets=CONSISTENT))[0]
        plan, d = make_dual_plan(self.grow, p)
        base = plan.dstar
        for room, ok in ((-0.049, True), (-0.050, False), (-0.051, False)):
            plan.dstar = base + (0.2 - room)          # colour 2 sits 0.2 - room higher: colour 1 on it has `room`
            za = analyse_z(self.grow, plan)
            rm = analyse_room(plan, za, 0.08, edge_goal=False)
            self.assertAlmostEqual(rm["before"]["right"]["centre_min"], room, delta=2e-4)
            self.assertEqual("nozzle_into_material" not in za["blocking"], ok, (room, za["blocking"]))
            # accepted: no compensation needed; not accepted: compensation needed (or not feasible)
            self.assertEqual(rm["layers_changed"] == [] and rm["conditions"]["centre_room"], ok, (room, rm["conditions"]))
        plan.dstar = base

    def test_verifier_boundary(self):
        p = load_profile(synth_profile.build(os.path.join(self.tmp, "c2.zip"), meshes=FLAT, offsets=CONSISTENT))[0]
        plan, d = make_dual_plan(self.grow, p)
        text, _ = generate_dual(self.grow, p, plan)
        lines = text.split("\n")
        for room, ok in ((-0.049, True), (-0.050, False), (-0.051, False)):
            dz = 0.2 - room                              # raise pair B: colour 1 of the next layer gets `room`
            out, inb = [], False
            for l in lines:
                if l.startswith("; FARM_PART"):
                    m = re.search(r"layer=(\d+) slot=0 tools=(\S+)", l)
                    inb = m.group(2) == "2,3"
                elif l.startswith(("; FARM_TRANSFER", "; FARM_PARK", "; DUAL_TOWER", "SET_DUAL_CARRIAGE")):
                    inb = False
                if inb and l.startswith("G1") and " Z" in l:
                    l = re.sub(r" Z([-\d.]+)", lambda mm: " Z%.5f" % (float(mm.group(1)) + dz), l)
                out.append(l)
            path = os.path.join(self.tmp, "b%.3f.gcode" % room)
            with open(path, "w") as f:
                f.write("\n".join(out))
            res, _ = verify_dual(p, self.grow.models, path, plan.foundation_layers)
            self.assertAlmostEqual(res["dual_stack_room"]["right"]["centre_min_mm"], room, delta=2e-4)
            self.assertEqual("DUAL_STACK_ROOM" not in res["issue_counts"], ok, (room, res["issue_counts"]))
            self.assertEqual("DUAL_INTO_MATERIAL" not in res["issue_counts"], ok, (room, res["issue_counts"]))
            if ok:
                self.assertIn("DUAL_SMALL_NEGATIVE_ROOM", res["issue_counts"])


class DualFoundationLayers(unittest.TestCase):
    """Independent layers: 3 by default, more only when 3 cannot even out bed + head heights."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.slice = synth_dual.write(os.path.join(cls.tmp, "d.gcode"), layers=24)
        cls.dual, _ = load_dual(cls.slice)

    def test_default_three_when_enough(self):
        p = load_profile(synth_profile.build(os.path.join(self.tmp, "ok.zip"), offsets=CONSISTENT))[0]
        plan, d = make_dual_plan(self.dual, p)
        self.assertEqual(plan.foundation_layers, 3)
        self.assertEqual(plan.foundation_choice["pairs_from_layer"], 4)

    def test_steep_bed_uses_the_smallest_sufficient_count(self):
        steep = [synth_profile.mesh_block(fn=lambda x, y: 0.006 * (x - 150))]
        p = load_profile(synth_profile.build(os.path.join(self.tmp, "steep.zip"), meshes=steep, offsets=CONSISTENT))[0]
        plan, d = make_dual_plan(self.dual, p)
        fc = plan.foundation_choice
        self.assertGreater(fc["chosen"], 3, fc)
        self.assertFalse(fc["tried"][0]["ok"])
        self.assertTrue(fc["tried"][-1]["ok"])
        self.assertEqual(len(fc["tried"]), fc["chosen"] - 2)                 # 3, 4, ... stops at the first that works
        self.assertLessEqual(fc["chosen"], fc["max_allowed"])
        self.assertTrue(d.ok(), d.render())
        text, _ = generate_dual(self.dual, p, plan)
        self.assertIn("foundation_layers=%d" % fc["chosen"], text[:600])
        out = os.path.join(self.tmp, "steep.gcode")
        with open(out, "w") as f:
            f.write(text)
        res, sim = verify_dual(p, self.dual.models, out, fc["chosen"])
        self.assertTrue(res["passed"], res["issues"][:4])
        self.assertEqual(res["synchronized_from_layer"], fc["chosen"] + 1)


class CopyPreheat(unittest.TestCase):
    def test_medium_model_regression(self):
        # 40 x 30 mm: the same copy must keep one key across output rounding (verifier regression)
        text, info, res = self.make((40.0, 30.0), 17)
        self.assertTrue(res["passed"], res["issues"][:3])

    """Copy Mode heats the waiting head by time (slice preheat_time), in the new print order."""

    def make(self, size, preheat):
        tmp = tempfile.mkdtemp()
        txt = synth.box_gcode(size=size, layers=9).replace("; CONFIG_BLOCK_END", "; preheat_time = %d\n; CONFIG_BLOCK_END" % preheat)
        p = os.path.join(tmp, "m.gcode")
        with open(p, "w") as f:
            f.write(txt)
        model, _ = load_slice(p)
        prof, _ = load_profile(synth_profile.build(os.path.join(tmp, "p.zip")))
        plan, d = make_plan(model, prof, LayoutOptions(mode="copy", count=2, preset="auto"))
        assert d.ok(), d.render()
        text, info = generate(model, prof, plan)
        out = os.path.join(tmp, "o.gcode")
        with open(out, "w") as f:
            f.write(text)
        res = analyse(Sim(prof, model, "c", heads=[0, 1]).run(out, plan.foundation_layers), expected_count=2)
        return text, info, res

    def preheat_layer(self, text):
        lines = text.split("\n")
        k = next(i for i, l in enumerate(lines) if l.startswith("M104 T1 S215 ; preheat"))
        layer = None
        for l in lines[:k]:
            m = re.match(r"; FARM_PART layer=(\d+)", l)
            if m:
                layer = int(m.group(1))
        return k, layer

    def test_long_layers_preheat_within_last_layer(self):
        text, info, res = self.make((60.0, 50.0), 17)
        self.assertTrue(res["passed"], res["issues"][:3])
        (p,) = info["preheats"]
        self.assertGreaterEqual(p["lead_s"], 17.0)
        self.assertLess(p["lead_s"], 25.0)
        _, layer = self.preheat_layer(text)
        self.assertEqual(layer, 5)                 # inside T0's last foundation layer

    def test_short_layers_preheat_reaches_back_several_layers(self):
        text, info, res = self.make((24.0, 18.0), 17)
        self.assertTrue(res["passed"], res["issues"][:3])
        (p,) = info["preheats"]
        self.assertGreaterEqual(p["lead_s"], 17.0)
        _, layer = self.preheat_layer(text)
        self.assertEqual(layer, 2)                 # three layers back: layers are short

    def test_very_short_wait_preheats_at_standby_and_m109_waits(self):
        # the waiting head's whole wait is shorter than preheat_time: heat right away, M109 waits the rest
        text, info, res = self.make((8.0, 6.0), 17)
        self.assertTrue(res["passed"], res["issues"][:3])
        (p,) = info["preheats"]
        self.assertTrue(p["limited_by_standby"])
        self.assertLess(p["lead_s"], 17.0)

    def test_m109_always_before_extrusion(self):
        text, info, res = self.make((8.0, 6.0), 17)
        self.assertNotIn("COLD_EXTRUDE", res["issue_counts"])
        lines = text.split("\n")
        k = next(i for i, l in enumerate(lines) if l.startswith("; FARM_PURGE tool=1"))
        self.assertTrue(any(l.startswith("M109 T1") for l in lines[k - 5:k]))

    def test_preheat_time_changes_placement(self):
        t17, i17, _ = self.make((60.0, 50.0), 17)
        t40, i40, _ = self.make((60.0, 50.0), 40)
        self.assertLess(self.preheat_layer(t40)[0], self.preheat_layer(t17)[0])
