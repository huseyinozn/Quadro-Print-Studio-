"""End-to-end tests: generate -> independent verify, regeneration and isolation.

Synthetic inputs run everywhere. Tests marked *fixture* use the private Co
Print fixtures when QUADRO_FIXTURES points to the folder containing
single_shark.gcode and profile.zip (they are skipped otherwise). The golden
oracle is never used here.
"""
import hashlib
import os
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
from quadro_studio import cli
import synth
import synth_profile

FIX = os.environ.get("QUADRO_FIXTURES")
_AUDIT = {"on": False, "opened": []}


def _hook(event, args):
    if _AUDIT["on"] and event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _AUDIT["opened"].append(os.path.abspath(os.fsdecode(args[0])))


sys.addaudithook(_hook)


def run_pipeline(slice_path, profile_path, out, **opts):
    model, _ = load_slice(slice_path)
    prof, _ = load_profile(profile_path)
    plan, d = make_plan(model, prof, LayoutOptions(**opts))
    assert plan is not None and d.ok(), d.render()
    text, info = generate(model, prof, plan)
    with open(out, "w") as f:
        f.write(text)
    sim = Sim(prof, model, "t").run(out, plan.foundation_layers)
    res = analyse(sim, expected_count=opts.get("count", 40), min_gap=plan.options["min_gap"])
    return plan, text, res


class SyntheticPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.slice = synth.write(os.path.join(cls.tmp, "box.gcode"))
        cls.prof = synth_profile.build(os.path.join(cls.tmp, "p.zip"))

    def test_different_model_and_count(self):
        plan, text, res = run_pipeline(self.slice, self.prof, os.path.join(self.tmp, "o8.gcode"),
                                       count=8, preset="auto", foundation_layers=3)
        self.assertTrue(res["passed"], res["issues"][:5])
        self.assertEqual(res["copies_complete"], 8)
        self.assertTrue(res["foundation_independent"])
        self.assertTrue(res["final_state"]["modes_ok"])

    def test_deterministic(self):
        _, t1, _ = run_pipeline(self.slice, self.prof, os.path.join(self.tmp, "d1.gcode"), count=4, preset="auto", foundation_layers=3)
        _, t2, _ = run_pipeline(self.slice, self.prof, os.path.join(self.tmp, "d2.gcode"), count=4, preset="auto", foundation_layers=3)
        self.assertEqual(hashlib.sha256(t1.encode()).hexdigest(), hashlib.sha256(t2.encode()).hexdigest())

    def test_modified_calibration_regenerates(self):
        offs = {k: dict(v) for k, v in synth_profile.OFFSETS.items()}
        offs["T1"]["z"] += 0.05
        p2 = synth_profile.build(os.path.join(self.tmp, "p2.zip"), offsets=offs)
        plan1, t1, r1 = run_pipeline(self.slice, self.prof, os.path.join(self.tmp, "c1.gcode"), count=4, preset="auto", foundation_layers=3)
        plan2, t2, r2 = run_pipeline(self.slice, p2, os.path.join(self.tmp, "c2.gcode"), count=4, preset="auto", foundation_layers=3)
        self.assertTrue(r1["passed"] and r2["passed"])
        self.assertNotEqual(t1, t2)
        # layer-1 commanded Z of T1 moves by exactly the offset change
        s = plan1.slots[0]
        b = load_slice(self.slice)[0].blocks[0].moves()[0]
        x, y = b.x + s.dx, b.y + s.dy
        self.assertAlmostEqual(plan2.zbase(1, 1, x, y) - plan1.zbase(1, 1, x, y), 0.05, places=9)
        self.assertAlmostEqual(plan2.zbase(1, 0, x, y) - plan1.zbase(1, 0, x, y), 0.0, places=9)

    def test_outlier_preserved_and_reported(self):
        meshes = [synth_profile.mesh_block(outlier=((4, 4), -0.6))]
        p3 = synth_profile.build(os.path.join(self.tmp, "p3.zip"), meshes=meshes)
        prof, _ = load_profile(p3)
        self.assertAlmostEqual(prof.mesh.points[4][4], -0.6, places=6)   # preserved as measured, not repaired
        model, _ = load_slice(self.slice)
        plan, d = make_plan(model, prof, LayoutOptions(count=8, preset="auto", foundation_layers=3))
        self.assertTrue(plan.affected_by_outliers)
        self.assertIn("PLAN_OUTLIER_AFFECTED", d.render())

    def test_generation_reads_only_slice_and_profile(self):
        out = os.path.join(self.tmp, "iso.gcode")
        _AUDIT["opened"].clear()
        _AUDIT["on"] = True
        try:
            rc = cli.main(["generate", "--slice", self.slice, "--profile", self.prof, "--out", out,
                           "--count", "4", "--preset", "auto", "--foundation-layers", "3"])
        finally:
            _AUDIT["on"] = False
        self.assertEqual(rc, 0)
        allowed = {os.path.abspath(self.slice), os.path.abspath(self.prof), os.path.abspath(out), os.path.abspath(out + ".partial")}
        data_files = [p for p in _AUDIT["opened"] if not p.endswith((".py", ".pyc")) and "/python3" not in p and "__pycache__" not in p]
        self.assertTrue(set(data_files) <= allowed, set(data_files) - allowed)

    def test_refuses_and_writes_nothing_on_error(self):
        out = os.path.join(self.tmp, "refused.gcode")
        with open(out, "w") as f:
            f.write("stale")
        rc = cli.main(["generate", "--slice", self.slice, "--profile", self.prof, "--out", out, "--count", "6"])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(out))


@unittest.skipUnless(FIX and os.path.exists(os.path.join(FIX or "", "single_shark.gcode")), "private fixtures not available")
class FixturePipeline(unittest.TestCase):
    def test_shark_8_copies(self):
        tmp = tempfile.mkdtemp()
        plan, text, res = run_pipeline(os.path.join(FIX, "single_shark.gcode"), os.path.join(FIX, "profile.zip"),
                                       os.path.join(tmp, "s8.gcode"), count=8)
        self.assertTrue(res["passed"], res["issues"][:5])
        self.assertEqual(res["copies_complete"], 8)


if __name__ == "__main__":
    unittest.main()
