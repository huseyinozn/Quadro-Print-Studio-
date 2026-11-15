"""Unit and negative tests that need no private fixtures."""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from quadro_studio.mesh import BedMesh
from quadro_studio.profile import load_profile, read_archive
from quadro_studio.diagnostics import Diagnostics, StageError
from quadro_studio.slicer import load_slice
from quadro_studio.plan import make_plan, LayoutOptions
from quadro_studio.geometry import poly_distance, flow_area
import synth
import synth_profile


class MeshTests(unittest.TestCase):
    def grid(self, fn):
        return [[fn(5 + i * 36.25, 5 + j * 36.25) for i in range(9)] for j in range(9)]

    def test_probe_points_reproduced(self):
        pts = self.grid(lambda x, y: ((x * 7 + y * 13) % 17) / 100.0)
        m = BedMesh(pts, 5, 295, 5, 295, 9, 9)
        for j in range(9):
            for i in range(9):
                self.assertAlmostEqual(m.z(5 + i * 36.25, 5 + j * 36.25), pts[j][i], places=9)

    def test_plane_is_exact_in_interior(self):
        # With tension 0.5 the cardinal spline is Catmull-Rom and reproduces a plane
        # exactly in interior intervals (Klipper's default 0.2 deliberately does not;
        # edge intervals duplicate the edge point as the firmware does).
        f = lambda x, y: 0.001 * x - 0.002 * y + 0.05
        m = BedMesh(self.grid(f), 5, 295, 5, 295, 9, 9, tension=0.5)
        for x, y in [(57.3, 200.1), (150, 150), (250.2, 47.7), (61.5, 61.5)]:
            self.assertAlmostEqual(m.z(x, y), f(x, y), places=9)

    def test_clamped_outside(self):
        f = lambda x, y: 0.001 * x
        m = BedMesh(self.grid(f), 5, 295, 5, 295, 9, 9)
        self.assertAlmostEqual(m.z(-10, 150), f(5, 150), places=9)
        self.assertAlmostEqual(m.z(150, 400), f(150, 295), places=9)

    def test_outlier_detected_not_repaired(self):
        pts = self.grid(lambda x, y: 0.0)
        pts[8][7] = -0.9
        m = BedMesh(pts, 5, 295, 5, 295, 9, 9)
        outs = m.outliers()
        self.assertEqual([(o["ix"], o["iy"]) for o in outs], [(7, 8)])
        self.assertEqual(m.points[8][7], -0.9)


class GeometryTests(unittest.TestCase):
    def test_poly_distance(self):
        a = [(0, 0), (1, 0), (1, 1), (0, 1)]
        b = [(3, 0), (4, 0), (4, 1), (3, 1)]
        self.assertAlmostEqual(poly_distance(a, b), 2.0)
        self.assertEqual(poly_distance(a, [(0.5, 0.5), (2, 0.5), (2, 2)]), 0.0)

    def test_flow_area(self):
        self.assertAlmostEqual(flow_area(0.2, 0.45), (0.45 - 0.2 * (1 - 3.141592653589793 / 4)) * 0.2)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def p(self, name):
        return os.path.join(self.tmp, name)

    def test_valid(self):
        prof, d = load_profile(synth_profile.build(self.p("ok.zip")))
        self.assertEqual(prof.mesh_name, "default")
        self.assertAlmostEqual(prof.offsets[1].z, 0.15)

    def test_missing_mesh(self):
        with self.assertRaises(StageError) as cm:
            load_profile(synth_profile.build(self.p("nomesh.zip"), meshes=[]))
        self.assertIn("PROFILE_MESH_MISSING", str(cm.exception))

    def test_ambiguous_mesh(self):
        path = synth_profile.build(self.p("two.zip"), meshes=[synth_profile.mesh_block("default"), synth_profile.mesh_block("old")])
        with self.assertRaises(StageError) as cm:
            load_profile(path)
        self.assertIn("PROFILE_MESH_AMBIGUOUS", str(cm.exception))
        prof, _ = load_profile(path, mesh_name="old")
        self.assertEqual(prof.mesh_name, "old")

    def test_missing_offsets(self):
        with self.assertRaises(StageError) as cm:
            load_profile(synth_profile.build(self.p("nooff.zip"), offsets=None))
        self.assertIn("PROFILE_OFFSETS_MISSING", str(cm.exception))

    def test_bad_offsets(self):
        with self.assertRaises(StageError) as cm:
            load_profile(synth_profile.build(self.p("badoff.zip"), offsets={"T0": {"x": 0, "y": 0, "z": 0}}))
        self.assertIn("PROFILE_OFFSETS_INVALID", str(cm.exception))

    def test_path_traversal_rejected(self):
        path = synth_profile.build(self.p("trav.zip"), extra={"../../evil.cfg": "x"})
        with self.assertRaises(StageError) as cm:
            load_profile(path)
        self.assertIn("PROFILE_UNSAFE_PATH", str(cm.exception))

    def test_absolute_path_rejected(self):
        path = synth_profile.build(self.p("abs.zip"), extra={"/etc/passwd.cfg": "x"})
        with self.assertRaises(StageError) as cm:
            load_profile(path)
        self.assertIn("PROFILE_UNSAFE_PATH", str(cm.exception))

    def test_symlink_rejected(self):
        path = self.p("link.zip")
        synth_profile.build(path)
        with zipfile.ZipFile(path, "a") as z:
            zi = zipfile.ZipInfo("config/link.cfg")
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(zi, "/etc/passwd")
        with self.assertRaises(StageError) as cm:
            load_profile(path)
        self.assertIn("PROFILE_UNSAFE_PATH", str(cm.exception))

    def test_zip_bomb_rejected(self):
        path = self.p("bomb.zip")
        synth_profile.build(path)
        with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr("config/bomb.cfg", "#" * (3 * 1024 * 1024))
        with self.assertRaises(StageError) as cm:
            load_profile(path)
        self.assertIn("PROFILE_ZIP_BOMB", str(cm.exception))

    def test_missing_include(self):
        path = synth_profile.build(self.p("noinc.zip"), drop=("config/heads.cfg",))
        with self.assertRaises(StageError) as cm:
            load_profile(path)
        self.assertIn("PROFILE_INCLUDE_MISSING", str(cm.exception))

    def test_macros_never_executed(self):
        # a macro body with template code must be kept as inert text
        path = synth_profile.build(self.p("mac.zip"), extra={"config/x.cfg": "[gcode_macro EVIL]\ngcode:\n  {{ __import__('os').system('false') }}\n"})
        prof, _ = load_profile(path)
        self.assertNotIn("EVIL", prof.macros)  # not included -> not even registered


class SliceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def slice_with(self, **kw):
        p = os.path.join(self.tmp, "s.gcode")
        synth.write(p, **kw)
        return p

    def assert_rejects(self, code, **kw):
        with self.assertRaises(StageError) as cm:
            load_slice(self.slice_with(**kw))
        self.assertIn(code, str(cm.exception))

    def test_valid_box(self):
        m, d = load_slice(self.slice_with())
        self.assertEqual(m.layer_count, 12)
        self.assertAlmostEqual(m.initial_retract, 0.4)

    def test_arc_rejected(self):
        self.assert_rejects("SLICE_UNSUPPORTED_GCODE", layer_extra=lambda L: ["G2 X1 Y1 I1 J0 E.1"] if L == 3 else [])

    def test_second_tool_rejected(self):
        self.assert_rejects("SLICE_UNSUPPORTED_TOOL", layer_extra=lambda L: ["T1"] if L == 4 else [])

    def test_relative_xyz_rejected(self):
        self.assert_rejects("SLICE_UNSUPPORTED_GCODE", layer_extra=lambda L: ["G91"] if L == 2 else [])

    def test_unknown_macro_rejected(self):
        self.assert_rejects("SLICE_UNSUPPORTED_GCODE", layer_extra=lambda L: ["MY_SECRET_MACRO A=1"] if L == 2 else [])

    def test_unknown_motion_rejected(self):
        self.assert_rejects("SLICE_UNKNOWN_MOTION", extra_lines=["G1 X10 Y10 F3000"])

    def test_exclude_object_limitation_named(self):
        with self.assertRaises(StageError) as cm:
            load_slice(self.slice_with(layer_extra=lambda L: ["EXCLUDE_OBJECT_START NAME=box"] if L == 2 else []))
        self.assertIn("EXCLUDE_OBJECT_START", str(cm.exception))
        self.assertIn("object exclusion would cancel one copy", str(cm.exception))

    def test_filament_change_limitation_named(self):
        with self.assertRaises(StageError) as cm:
            load_slice(self.slice_with(layer_extra=lambda L: ["M600"] if L == 3 else []))
        self.assertIn("filament change pauses one head", str(cm.exception))

    def test_unknown_macro_limitation_named(self):
        with self.assertRaises(StageError) as cm:
            load_slice(self.slice_with(layer_extra=lambda L: ["MY_SECRET_MACRO A=1"] if L == 2 else []))
        self.assertIn("unknown semantics", str(cm.exception))

    def test_temperature_change_rejected(self):
        self.assert_rejects("SLICE_UNSUPPORTED_FEATURE", layer_extra=lambda L: ["M104 S230"] if L == 5 else [])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = os.path.join(self.tmp, "s.gcode")
        synth.write(p)
        self.model, _ = load_slice(p)
        self.prof, _ = load_profile(synth_profile.build(os.path.join(self.tmp, "p.zip")))

    def test_count_not_multiple_of_four(self):
        plan, d = make_plan(self.model, self.prof, LayoutOptions(count=10))
        self.assertIsNone(plan)
        self.assertIn("PLAN_COUNT", d.render())

    def test_unsafe_packing_rejected(self):
        plan, d = make_plan(self.model, self.prof, LayoutOptions(count=16, preset="reference-300", col_pitch=18.0, row_pitch=14.5))
        self.assertFalse(d.ok())
        self.assertIn("PLAN_COPY_GAP", d.render())

    def test_carriage_clearance_rejected(self):
        plan, d = make_plan(self.model, self.prof, LayoutOptions(count=8, preset="reference-300", sync_pitch=50.0, cols_per_zone=1))
        self.assertFalse(d.ok())
        self.assertIn("PLAN_COPY_CLEARANCE", d.render())

    def test_too_many_copies_rejected(self):
        plan, d = make_plan(self.model, self.prof, LayoutOptions(count=400, preset="auto"))
        self.assertIsNone(plan)
        self.assertIn("PLAN_DOES_NOT_FIT", d.render())

    def test_auto_layout_ok(self):
        plan, d = make_plan(self.model, self.prof, LayoutOptions(count=24, preset="auto", foundation_layers=3))
        self.assertTrue(d.ok(), d.render())
        self.assertEqual(len(plan.slots), 6)

    def test_auto_layout_centres_y_offset_shift(self):
        """Copies of T1-T3 are shifted in Y by their offsets: whenever the auto search accepts an
        arrangement, the shifted copies must still lie inside the measured mesh (one-sided Y offsets)."""
        offs = {"T0": {"x": 0.0, "y": 0.0, "z": 0.0}, "T1": {"x": 0.14, "y": -1.63, "z": 0.30},
                "T2": {"x": -1.03, "y": -1.54, "z": 0.19}, "T3": {"x": -0.95, "y": -1.18, "z": 0.14}}
        prof, _ = load_profile(synth_profile.build(os.path.join(self.tmp, "yoff.zip"), offsets=offs))
        fitted = 0
        for n in range(4, 404, 4):
            plan, d = make_plan(self.model, prof, LayoutOptions(count=n, preset="auto", foundation_layers=3))
            if plan is None:
                continue
            fitted += 1
            self.assertNotIn("PLAN_MESH_COVERAGE", d.render(), "count %d" % n)
        self.assertGreater(fitted, 10)


if __name__ == "__main__":
    unittest.main()
