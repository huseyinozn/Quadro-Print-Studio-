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
    def test_inconsistent_offsets_refused_with_reason(self):
        plan, d = make_dual_plan(self.dual, self.bad_prof)
        self.assertIsNotNone(plan)                     # preview still available
        codes = {x.code for x in d.errors}
        self.assertIn("PLAN_PAIR_Z_CONSISTENCY", codes)
        self.assertIn("PLAN_PAIR_Y_REGISTRATION", codes)

    def test_z_residual_located_in_this_model(self):
        plan, d = make_dual_plan(self.dual, self.bad_prof)
        za = plan.z_analysis
        self.assertIsNotNone(za)
        self.assertIn("colour_change_thickness", za["blocking"])
        # colours swap halves every 10 layers: the vertical colour change is at layer 11
        layers = set()
        for side in ("left", "right"):
            for it in za["sides"][side]["examples"]["colour_change_interior"]:
                layers.add(it["layer"])
                self.assertIn("below", it)
                self.assertTrue(it["thickness"] < 0.08 or it["thickness"] > 0.30)
        self.assertEqual(layers, {11})
        # one side's colour 2 is lower: its nozzle runs into colour 1 beside it
        sinks = [za["sides"][s]["stats"]["sink_extrude"]["points"] for s in ("left", "right")]
        self.assertTrue(any(sinks))
        detail = next(c for c in plan.checks if c["name"] == "pair_z_consistency")["detail"]
        self.assertIn("In this model", detail)

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
        self.assertFalse(y["ok"])
        self.assertIn("separate from Z", y["detail"])
        self.assertGreater(plan.pair_y["registration_error"], 0.10)

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

    def test_forced_inconsistent_output_fails_verification(self):
        plan, _ = make_dual_plan(self.dual, self.bad_prof)
        text, _ = generate_dual(self.dual, self.bad_prof, plan)
        res, sim = self.verify_text(text, self.bad_prof)
        self.assertFalse(res["passed"])
        self.assertIn("DUAL_REGISTRATION", res["issue_counts"])
        self.assertIn("COPY_THICKNESS", res["issue_counts"])

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
