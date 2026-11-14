"""Adversarial tests: a correct generated file is mutated in ways a buggy
generator (or a hand edit) could produce. The independent verifier must reject
every mutation with the specific diagnostic. Planner adversarial cases follow.
Only synthetic, non-private inputs are used.
"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from quadro_studio.slicer import load_slice
from quadro_studio.profile import load_profile
from quadro_studio.plan import make_plan, LayoutOptions
from quadro_studio.generate import generate
from quadro_studio.verify import Sim, analyse
import synth
import synth_profile


class VerifierAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.slice = synth.write(os.path.join(cls.tmp, "box.gcode"), layers=8)
        cls.prof_path = synth_profile.build(os.path.join(cls.tmp, "p.zip"))
        cls.model, _ = load_slice(cls.slice)
        cls.prof, _ = load_profile(cls.prof_path)
        plan, d = make_plan(cls.model, cls.prof, LayoutOptions(count=8, preset="auto", foundation_layers=3))
        assert d.ok(), d.render()
        cls.plan = plan
        cls.text, _ = generate(cls.model, cls.prof, plan)
        cls.lines = cls.text.split("\n")

    def run_text(self, text):
        p = os.path.join(self.tmp, "m.gcode")
        with open(p, "w") as f:
            f.write(text)
        sim = Sim(self.prof, self.model, "adv").run(p, 3)
        return analyse(sim, expected_count=8, min_gap=2.0)

    def mutate(self, fn):
        return "\n".join(fn(list(self.lines)))

    def codes(self, res):
        return set(res["issue_counts"])

    def idx(self, pred, start=0):
        for i in range(start, len(self.lines)):
            if pred(self.lines[i]):
                return i
        raise AssertionError("pattern not found")

    def first_part(self, layer=2):
        return self.idx(lambda l: l.startswith("; FARM_PART layer=%d " % layer))

    def test_baseline_passes(self):
        res = self.run_text(self.text)
        self.assertTrue(res["passed"], res["issues"][:5])

    def test_missing_acceleration_rejected(self):
        i = self.idx(lambda l: l.startswith("SET_VELOCITY_LIMIT ACCEL=3000"), self.first_part(3))
        res = self.run_text(self.mutate(lambda L: L[:i] + L[i + 1:]))
        self.assertIn("ACCELERATION", self.codes(res))

    def test_changed_acceleration_rejected(self):
        i = self.idx(lambda l: l.startswith("SET_VELOCITY_LIMIT ACCEL=3000"), self.first_part(3))
        res = self.run_text(self.mutate(lambda L: L[:i] + ["SET_VELOCITY_LIMIT ACCEL=2500 ACCEL_TO_DECEL=1500"] + L[i + 1:]))
        self.assertIn("ACCELERATION", self.codes(res))

    def test_acceleration_above_machine_limit_rejected(self):
        i = self.idx(lambda l: l.startswith("SET_VELOCITY_LIMIT ACCEL=3000"), self.first_part(3))
        res = self.run_text(self.mutate(lambda L: L[:i] + ["SET_VELOCITY_LIMIT ACCEL=20000 ACCEL_TO_DECEL=1500"] + L[i + 1:]))
        self.assertIn("ACCEL_LIMIT", self.codes(res))

    def test_missing_pressure_advance_rejected(self):
        res = self.run_text(self.mutate(lambda L: [l for l in L if not l.startswith("SET_PRESSURE_ADVANCE EXTRUDER=extruder2")]))
        self.assertIn("PRESSURE_ADVANCE", self.codes(res))

    def test_changed_pressure_advance_rejected(self):
        res = self.run_text(self.mutate(lambda L: [l.replace("ADVANCE=0.04", "ADVANCE=0.06") if "extruder1" in l else l for l in L]))
        self.assertIn("PRESSURE_ADVANCE", self.codes(res))

    def test_narrow_gap_on_changed_park_trajectory_rejected(self):
        # move T2's stacked park 5 mm closer to T1: 51.25 mm < 56.25 mm clearance
        def f(L):
            return [re.sub(r"T2=-56\.50000", "T2=-61.50000", l) if l.startswith("QUADRO_PARALLEL_MOVE") else l for l in L]
        res = self.run_text(self.mutate(f))
        self.assertTrue({"QPM_CLEARANCE", "QPM_LIMIT"} & self.codes(res), res["issue_counts"])

    def test_crossing_park_trajectory_rejected(self):
        # target order swapped between T0 and T1 -> carriages would pass through each other
        i = self.idx(lambda l: l.startswith("QUADRO_PARALLEL_MOVE") and "T1=356.5" in l)
        bad = "QUADRO_PARALLEL_MOVE T0=300.00000 T1=150.00000 T2=412.75000 T3=469.00000 SPEED=350 MIN_CLEARANCE=56.25"
        res = self.run_text(self.mutate(lambda L: L[:i] + [bad] + L[i + 1:]))
        self.assertIn("QPM_CLEARANCE", self.codes(res))

    def test_arriving_profile_rejected(self):
        i = self.idx(lambda l: l.startswith("QUADRO_PARALLEL_MOVE") and "T1=356.5" in l)
        res = self.run_text(self.mutate(lambda L: L[:i] + [L[i] + " ARRIVING=T0"] + L[i + 1:]))
        self.assertIn("QPM_UNSUPPORTED_PROFILE", self.codes(res))

    def test_parallel_move_with_y_rejected(self):
        i = self.idx(lambda l: l.startswith("QUADRO_PARALLEL_MOVE") and "T1=356.5" in l)
        res = self.run_text(self.mutate(lambda L: L[:i] + [L[i] + " Y=20"] + L[i + 1:]))
        self.assertIn("QPM_UNSUPPORTED_PROFILE", self.codes(res))

    def test_lowered_transfer_rejected(self):
        # make one transfer lift stop at the layer height -> travel over deposits
        i = self.idx(lambda l: l.startswith("; FARM_TRANSFER"), self.first_part(3))
        j = max(k for k in range(i) if self.lines[k].startswith("G1 Z"))
        def f(L):
            L[j] = "G1 Z0.70000 F1000"
            return L
        res = self.run_text(self.mutate(f))
        self.assertTrue({"TRANSFER_CLEARANCE", "DESCENT_INTO_MATERIAL"} & self.codes(res), res["issue_counts"])

    def test_removed_model_move_rejected(self):
        i = self.idx(lambda l: l.startswith("G1 X") and " E" in l, self.first_part(4) + 5)
        res = self.run_text(self.mutate(lambda L: L[:i] + L[i + 1:]))
        self.assertFalse(res["passed"])
        self.assertIn("VISIT_MISMATCH", self.codes(res))

    def test_double_extrusion_rejected(self):
        a = self.first_part(4)
        b = self.idx(lambda l: l.startswith("; FARM_TRANSFER"), a)
        res = self.run_text(self.mutate(lambda L: L[:b] + L[a:b] + L[b:]))
        self.assertFalse(res["passed"])

    def test_first_layer_too_low_rejected(self):
        i = self.idx(lambda l: l.startswith("G1 X") and " E" in l, self.first_part(1))
        def f(L):
            L[i] = re.sub(r"Z(-?[\d.]+)", lambda m: "Z%.5f" % (float(m.group(1)) - 0.05), L[i])
            return L
        res = self.run_text(self.mutate(f))
        self.assertIn("FIRST_LAYER_HEIGHT", self.codes(res))

    def test_changed_feedrate_rejected(self):
        i = self.idx(lambda l: l.startswith("G1 X") and " E" in l and "F3000" in l, self.first_part(4))
        res = self.run_text(self.mutate(lambda L: L[:i] + [L[i].replace("F3000", "F3600")] + L[i + 1:]))
        self.assertFalse(res["passed"])

    def test_missing_heater_shutdown_rejected(self):
        res = self.run_text(self.mutate(lambda L: [l for l in L if l != "TURN_OFF_HEATERS"]))
        self.assertIn("FINAL_STATE", self.codes(res))
        self.assertFalse(res["final_state"]["heaters_off"])

    def test_missing_final_park_rejected(self):
        i = max(k for k, l in enumerate(self.lines) if l.startswith("QUADRO_PARALLEL_MOVE"))
        res = self.run_text(self.mutate(lambda L: L[:i] + L[i + 1:]))
        self.assertFalse(res["final_state"]["carriages_parked"])

    def test_extruders_left_synced_rejected(self):
        res = self.run_text(self.mutate(lambda L: [l for l in L if l != "SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=extruder2"]))
        self.assertFalse(res["final_state"]["extruders_unsynced"])

    def test_overtemperature_rejected(self):
        res = self.run_text(self.mutate(lambda L: [l.replace("M104 T2 S215", "M104 T2 S320") for l in L]))
        self.assertIn("TEMP_LIMIT", self.codes(res))

    def test_cold_extrusion_rejected(self):
        res = self.run_text(self.mutate(lambda L: [l for l in L if not l.startswith("M109 T1")]))
        self.assertIn("COLD_EXTRUDE", self.codes(res))

    def test_unknown_output_command_rejected(self):
        i = self.first_part(2)
        res = self.run_text(self.mutate(lambda L: L[:i] + ["G28"] + L[i:]))
        self.assertIn("OUT_UNKNOWN_COMMAND", self.codes(res))

    def test_runtime_mesh_stacked_rejected(self):
        i = self.first_part(2)
        res = self.run_text(self.mutate(lambda L: L[:i] + ["BED_MESH_PROFILE LOAD=default"] + L[i:]))
        self.assertIn("MESH_STACKED", self.codes(res))


class PlannerAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.model, _ = load_slice(synth.write(os.path.join(self.tmp, "s.gcode")))

    def plan(self, **kw):
        prof_kw = kw.pop("profile", {})
        prof, _ = load_profile(synth_profile.build(os.path.join(self.tmp, "p.zip"), **prof_kw))
        return make_plan(self.model, prof, LayoutOptions(**kw))

    def test_extreme_x_offset_breaks_copy_clearance(self):
        offs = {k: dict(v) for k, v in synth_profile.OFFSETS.items()}
        offs["T1"]["x"] = 4.9
        offs["T2"]["x"] = -4.9
        plan, d = self.plan(count=8, preset="reference-300", sync_pitch=58.0, cols_per_zone=1, profile=dict(offsets=offs))
        self.assertIn("PLAN_COPY_CLEARANCE", d.render())

    def test_steep_mesh_breaks_foundation_thickness(self):
        mesh = synth_profile.mesh_block(fn=lambda x, y: 0.004 * (x - 150))
        plan, d = self.plan(count=8, preset="auto", foundation_layers=3, profile=dict(meshes=[mesh]))
        self.assertIn("PLAN_FOUNDATION_THICKNESS", d.render())

    def test_implausible_offsets_rejected(self):
        offs = {k: dict(v) for k, v in synth_profile.OFFSETS.items()}
        offs["T3"]["z"] = 3.0
        from quadro_studio.diagnostics import StageError
        with self.assertRaises(StageError) as cm:
            self.plan(count=8, profile=dict(offsets=offs))
        self.assertIn("PROFILE_OFFSETS_IMPLAUSIBLE", str(cm.exception))

    def test_negative_park_margin_rejected(self):
        plan, d = self.plan(count=8, preset="auto", foundation_layers=3, park_margin=-1.0)
        self.assertIsNone(plan)
        self.assertIn("PLAN_PARK_MARGIN", d.render())

    def test_park_margin_moves_stacked_park(self):
        plan0, _ = self.plan(count=8, preset="auto", foundation_layers=3)
        plan1, d = self.plan(count=8, preset="auto", foundation_layers=3, park_margin=2.0)
        self.assertTrue(d.ok(), d.render())
        self.assertAlmostEqual(plan1.left_stack[2] - plan0.left_stack[2], 2.0)

    def test_foundation_layers_out_of_range(self):
        plan, d = self.plan(count=8, foundation_layers=99)
        self.assertIsNone(plan)
        self.assertIn("PLAN_FOUNDATION", d.render())


if __name__ == "__main__":
    unittest.main()


class CopyModeAdversarial(unittest.TestCase):
    """Copy Mode (T0 left, T1 right, T2/T3 parked): verifier rejects wrong files."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.slice = synth.write(os.path.join(cls.tmp, "box.gcode"), layers=9)
        cls.prof, _ = load_profile(synth_profile.build(os.path.join(cls.tmp, "p.zip")))
        cls.model, _ = load_slice(cls.slice)
        plan, d = make_plan(cls.model, cls.prof, LayoutOptions(mode="copy", count=2, preset="auto"))
        assert d.ok(), d.render()
        cls.plan = plan
        cls.text, _ = generate(cls.model, cls.prof, plan)
        cls.lines = cls.text.split("\n")

    def run_text(self, text):
        p = os.path.join(self.tmp, "c.gcode")
        with open(p, "w") as f:
            f.write(text)
        sim = Sim(self.prof, self.model, "copy", heads=[0, 1]).run(p, 5)
        return analyse(sim, expected_count=2, min_gap=2.0)

    def visit_range(self, layer, tools):
        a = next(i for i, l in enumerate(self.lines) if l.startswith("; FARM_PART layer=%d slot=0 tools=%s" % (layer, tools)))
        b = next(i for i in range(a + 1, len(self.lines)) if self.lines[i].startswith(("; FARM_TRANSFER", "; FARM_PARK", "M400")))
        return a, b

    def test_baseline_passes(self):
        res = self.run_text(self.text)
        self.assertTrue(res["passed"], res["issues"][:5])
        self.assertEqual(res["copies_complete"], 2)
        self.assertEqual(res["synchronized_from_layer"], 6)
        self.assertTrue(res["parked_heads"]["stayed_parked"])
        self.assertEqual(res["foundation_head_sequence"], [0, 1])

    def test_count_rule_is_not_farm_rule(self):
        plan, d = make_plan(self.model, self.prof, LayoutOptions(mode="copy", count=2, preset="auto"))
        self.assertTrue(d.ok())
        plan, d = make_plan(self.model, self.prof, LayoutOptions(mode="copy", count=4, preset="auto"))
        self.assertIn("Copy Mode prints exactly 2 copies", d.render())

    def test_third_head_synchronised_by_mistake_rejected(self):
        i = next(k for k, l in enumerate(self.lines) if l == "SYNC_EXTRUDER_MOTION EXTRUDER=extruder1 MOTION_QUEUE=extruder")
        bad = self.lines[:i + 1] + ["SET_DUAL_CARRIAGE CARRIAGE=carriage_t2 MODE=COPY",
                                    "SYNC_EXTRUDER_MOTION EXTRUDER=extruder2 MOTION_QUEUE=extruder"] + self.lines[i + 1:]
        res = self.run_text("\n".join(bad))
        self.assertFalse(res["passed"])
        self.assertIn("PARKED_HEAD_ACTIVE", res["issue_counts"])
        self.assertFalse(res["parked_heads"]["stayed_parked"])

    def test_parked_head_moved_rejected(self):
        bad = [l.replace("T2=412.75000", "T2=380.00000", 1) if l.startswith("QUADRO_PARALLEL_MOVE T0=130") or
               (l.startswith("QUADRO_PARALLEL_MOVE") and "copy" in l) else l for l in self.lines]
        k = next(i for i, l in enumerate(self.lines) if l.startswith("; FARM_PARK copy-entry")) + 1
        bad = list(self.lines)
        bad[k] = bad[k].replace("T2=412.75000", "T2=380.00000")
        res = self.run_text("\n".join(bad))
        self.assertIn("PARKED_HEAD_MOVED", res["issue_counts"])

    def test_skipped_foundation_layer_of_one_copy_rejected(self):
        a, b = self.visit_range(3, "1")
        res = self.run_text("\n".join(self.lines[:a] + self.lines[b:]))
        self.assertFalse(res["passed"])
        self.assertIn("COPY_INCOMPLETE", res["issue_counts"])

    def test_skipped_synchronised_layer_rejected(self):
        a, b = self.visit_range(7, "0,1")
        res = self.run_text("\n".join(self.lines[:a] + self.lines[b:]))
        self.assertFalse(res["passed"])
        self.assertIn("COPY_INCOMPLETE", res["issue_counts"])

    def test_late_copy_entry_rejected(self):
        # T1 is not taken into COPY: layer 6+ printed by T0 alone
        bad = [l for l in self.lines if l not in ("SET_DUAL_CARRIAGE CARRIAGE=carriage_t1 MODE=COPY",
                                                  "SYNC_EXTRUDER_MOTION EXTRUDER=extruder1 MOTION_QUEUE=extruder")]
        res = self.run_text("\n".join(bad))
        self.assertFalse(res["passed"])
        self.assertIn("COPY_PHASE", res["issue_counts"])
