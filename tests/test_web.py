"""Web app tests (HTTP level, synthetic non-private inputs).

Covers: product page with the three modes, plan + layout, oversize count with
a verified alternative, generate -> independent verify -> download with
matching SHA-256, download blocked before verification, download blocked when
verification fails (fault injected after generation), and input changes giving
a different job (no stale result re-used).
"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from quadro_studio.web.server import make_server
import synth
import synth_profile


def multipart(fields):
    b = uuid.uuid4().hex
    out = []
    for name, (fname, data) in fields.items():
        out.append(("--%s\r\n" % b).encode())
        if fname:
            out.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (name, fname)).encode())
        else:
            out.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode())
        out.append(data if isinstance(data, bytes) else data.encode())
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % b).encode())
    return b"".join(out), "multipart/form-data; boundary=%s" % b


def drop_accel(path):
    with open(path) as f:
        L = f.read().split("\n")
    i = next(k for k, l in enumerate(L) if l.startswith("; FARM_PART layer=3 "))
    j = next(k for k in range(i, len(L)) if L[k].startswith("SET_VELOCITY_LIMIT"))   # block-start model acceleration
    del L[j]
    with open(path, "w") as f:
        f.write("\n".join(L))


class WebBase(unittest.TestCase):
    hook = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.srv, cls.app = make_server("127.0.0.1", 0, os.path.join(cls.tmp, "work"), post_generate_hook=cls.hook)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.model = open(synth.write(os.path.join(cls.tmp, "box.gcode"), size=(24.0, 16.0), layers=8), "rb").read()
        cls.profile = open(synth_profile.build(os.path.join(cls.tmp, "p.zip")), "rb").read()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, data=None, ctype=None, method=None):
        r = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data is not None else "GET"))
        if ctype:
            r.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def plan(self, count, model=None):
        body, ct = multipart({"profile": ("p.zip", self.profile), "model": ("box.gcode", model or self.model), "count": (None, str(count))})
        st, data, _ = self.req("/api/plan", body, ct)
        self.assertEqual(st, 200, data[:300])
        return json.loads(data)

    def run_job(self, jid):
        st, data, _ = self.req("/api/generate/" + jid, b"")
        self.assertIn(st, (202, 409))
        for _ in range(600):
            st, data, _ = self.req("/api/job/" + jid)
            s = json.loads(data)
            if s["state"] not in ("generating", "verifying"):
                return s
            time.sleep(0.2)
        self.fail("timeout")


class WebFlow(WebBase):
    def test_page_shows_three_modes(self):
        st, html, _ = self.req("/")
        html = html.decode()
        self.assertEqual(st, 200)
        for t in ("Farm Mode", "Copy Mode", "Dual Color Copy Mode", "Available", 'id="mode-dual"'):
            self.assertIn(t, html)
        self.assertNotIn("Coming soon", html)
        self.assertNotIn("influencer", html.lower())

    def test_success_flow_download_matches_verified_file(self):
        r = self.plan(8)
        self.assertEqual(r["state"], "planned")
        L = r["layout"]
        self.assertEqual(len(L["copies"]), 8)
        self.assertEqual(L["foundation_layers"], 5)
        self.assertEqual(sorted({c["head"] for c in L["copies"]}), [0, 1, 2, 3])
        st, _, _ = self.req("/api/download/" + r["job_id"])
        self.assertEqual(st, 403)                    # nothing before verification
        s = self.run_job(r["job_id"])
        self.assertEqual(s["state"], "passed", s)
        self.assertIn(s["message"], ("Software checks passed.", "Verified with warnings."))
        st, data, hdr = self.req(s["download_url"])
        self.assertEqual(st, 200)
        self.assertEqual(hashlib.sha256(data).hexdigest(), s["gcode_sha256"])
        self.assertIn("attachment", hdr.get("Content-Disposition", ""))

    def test_changed_inputs_make_a_new_job(self):
        a = self.plan(8)
        b = self.plan(4)
        self.assertNotEqual(a["job_id"], b["job_id"])
        self.assertEqual(b["layout"]["count"], 4)
        st, _, _ = self.req("/api/download/" + b["job_id"])
        self.assertEqual(st, 403)                    # new selection has no file until verified

    def test_layout_follows_model_size(self):
        small = self.plan(8)["layout"]["head_spacing"]
        big_model = open(synth.write(os.path.join(self.tmp, "big.gcode"), size=(60.0, 30.0), layers=6), "rb").read()
        big = self.plan(8, model=big_model)
        self.assertEqual(big["state"], "planned", big.get("problems"))
        self.assertGreater(big["layout"]["head_spacing"], small)

    def test_count_too_large_gives_verified_alternative(self):
        r = self.plan(400)
        self.assertEqual(r["state"], "rejected")
        self.assertTrue(any("do not fit" in p["text"] for p in r["problems"]))
        alt = r.get("alternative")
        self.assertIsNotNone(alt)
        self.assertEqual(alt["count"] % 4, 0)
        ok = self.plan(alt["count"])
        self.assertEqual(ok["state"], "planned")

    def test_count_not_multiple_of_four(self):
        r = self.plan(10)
        self.assertEqual(r["state"], "rejected")
        self.assertIn("multiple of 4", r["problems"][0]["text"])

    def test_bad_profile_explained(self):
        bad = synth_profile.build(os.path.join(self.tmp, "nomesh.zip"), meshes=[])
        body, ct = multipart({"profile": ("p.zip", open(bad, "rb").read()), "model": ("box.gcode", self.model), "count": (None, "8")})
        st, data, _ = self.req("/api/plan", body, ct)
        r = json.loads(data)
        self.assertEqual(r["state"], "rejected")
        self.assertIn("no saved bed mesh", r["problems"][0]["text"])


class WebExamplesAndMeshMarks(WebBase):
    def plan_example(self, ex, profile=None):
        body, ct = multipart({"profile": ("p.zip", profile or self.profile), "example": (None, ex), "count": (None, "40")})
        st, data, _ = self.req("/api/plan", body, ct)
        return st, json.loads(data)

    def test_examples_listed(self):
        st, data, _ = self.req("/api/examples")
        ex = {e["id"]: e for e in json.loads(data)}
        self.assertTrue(ex["sharks-40"]["available"])
        self.assertEqual(ex["sharks-40"]["mode"], "Farm Mode")
        self.assertTrue(ex["orcacube"]["available"])
        self.assertEqual(ex["orcacube"]["mode_id"], "copy")
        self.assertTrue(ex["fidgets-2c"]["available"])
        self.assertEqual(ex["fidgets-2c"]["mode_id"], "dual")
        self.assertNotIn("file", ex["sharks-40"])                # example source is never offered for download
        for e in ex.values():
            st, img, hdr = self.req(e["image"])
            self.assertEqual(st, 200)
            self.assertTrue(img.startswith(b"\x89PNG"))
        st, _, _ = self.req("/examples/../server.py")
        self.assertEqual(st, 404)

    def test_example_needs_only_profile(self):
        st, r = self.plan_example("sharks-40")
        self.assertEqual(st, 200)
        self.assertEqual(r["state"], "planned", r.get("problems"))
        self.assertEqual(r["layout"]["count"], 40)
        self.assertEqual(r["model"]["layers"], 83)

    def test_fidget_example_refused_for_room_with_values_shown(self):
        # Y/Z within the owner limits, into-material and travel checks pass; the room for the filament
        # where colours stack cannot be made enough on both models (one shared Z and E per pair)
        st, r = self.plan_example("fidgets-2c")
        self.assertEqual(st, 200)
        self.assertEqual(r["state"], "rejected")
        self.assertEqual({p["code"] for p in r["problems"]}, {"PLAN_COLOUR_STACK_ROOM"})
        L = r["layout"]
        ck = {c["name"]: c for c in L["checks"]}
        for name in ("colour_mapping", "pair_y_registration", "pair_z_consistency", "into_material", "travel_drag"):
            self.assertTrue(ck[name]["ok"], name)
        self.assertFalse(ck["colour_stack_room"]["ok"])
        self.assertIn("left", L["room"]["before"])
        self.assertIn("right", L["room"]["before"])
        st, _, _ = self.req("/api/download/" + r["job_id"])
        self.assertEqual(st, 403)

    def test_dual_small_negative_room_generates_and_downloads_with_warning(self):
        import synth_dual
        from test_dual import GROW_MISMATCH
        dual = synth_dual.dual_gcode(layers=20, grow=True).encode()
        prof = open(synth_profile.build(os.path.join(self.tmp, "grow_mm.zip"), offsets=GROW_MISMATCH), "rb").read()
        body, ct = multipart({"profile": ("p.zip", prof), "model": ("grow.gcode", dual), "mode": (None, "dual"), "count": (None, "2")})
        st, data, _ = self.req("/api/plan", body, ct)
        r = json.loads(data)
        self.assertEqual(r["state"], "planned", r.get("problems"))
        self.assertIn("PLAN_SMALL_NEGATIVE_ROOM", {w["code"] for w in r["warnings"]})
        s = self.run_job(r["job_id"])
        self.assertEqual(s["state"], "passed", s)
        self.assertIn("DUAL_SMALL_NEGATIVE_ROOM", {w["code"] for w in s["verify"]["warnings"]})
        self.assertLess(s["verify"]["stack_room"]["right"]["centre_min_mm"], 0)
        st, data, _ = self.req(s["download_url"])
        self.assertEqual(st, 200)
        self.assertEqual(hashlib.sha256(data).hexdigest(), s["gcode_sha256"])

    def test_own_upload_of_differently_coloured_models_not_changed(self):
        # the same file uploaded as the user's own model: its right model is NOT silently replaced
        from quadro_studio.web.server import EXAMPLE_DIR
        fid = open(os.path.join(EXAMPLE_DIR, "two_color_fidget.gcode"), "rb").read()
        body, ct = multipart({"profile": ("p.zip", self.profile), "model": ("mine.gcode", fid), "mode": (None, "dual"), "count": (None, "2")})
        st, data, _ = self.req("/api/plan", body, ct)
        r = json.loads(data)
        self.assertEqual(r["state"], "rejected")
        self.assertIn("DUAL_NOT_COPYABLE", {p["code"] for p in r["problems"]})
        self.assertTrue(r["layout"]["region_marks"])

    def test_marks_equal_planner_list(self):
        from quadro_studio.slicer import load_slice
        from quadro_studio.profile import load_profile
        from quadro_studio.plan import make_plan, LayoutOptions
        from quadro_studio.web.server import EXAMPLE_DIR
        meshes = [synth_profile.mesh_block(outlier=((7, 8), -0.52))]
        prof_path = synth_profile.build(os.path.join(self.tmp, "dev.zip"), meshes=meshes)
        st, r = self.plan_example("sharks-40", open(prof_path, "rb").read())
        self.assertEqual(r["state"], "planned", r.get("problems"))
        model, _ = load_slice(os.path.join(EXAMPLE_DIR, "sharks_single.gcode"))
        prof, _ = load_profile(prof_path)
        plan, _ = make_plan(model, prof, LayoutOptions(count=40, preset="auto"))
        expected = sorted({"%d/%d" % (a["tool"], a["slot"]) for a in plan.affected_by_outliers})
        self.assertTrue(expected)
        self.assertEqual(r["layout"]["mesh_affected"], expected)
        marked = sorted("%d/%d" % (c["head"], c["slot"]) for c in r["layout"]["copies"] if "mesh_effects" in c)
        self.assertEqual(marked, expected)
        for c in r["layout"]["copies"]:
            for e in c.get("mesh_effects", []):
                self.assertIn("first_layer_mm", e)
                self.assertIn("layer_end_top_mm", e)
        self.assertEqual(len(r["layout"]["deviating_points"]), 1)

    def test_orcacube_example_copy_mode(self):
        st, r = self.plan_example("orcacube")
        self.assertEqual(st, 200)
        self.assertEqual(r["state"], "planned", r.get("problems"))
        L = r["layout"]
        self.assertEqual((L["mode"], L["count"], L["heads"], L["parked_heads"]), ("copy", 2, [0, 1], [2, 3]))
        self.assertEqual(sorted(c["head"] for c in L["copies"]), [0, 1])
        left = next(c for c in L["copies"] if c["head"] == 0)
        right = next(c for c in L["copies"] if c["head"] == 1)
        self.assertLess(max(p[0] for p in left["hull"]), min(p[0] for p in right["hull"]))   # T0 left, T1 right
        self.assertEqual(r["model"]["layers"], 225)

    def test_dual_mode_single_colour_model_refused(self):
        body, ct = multipart({"profile": ("p.zip", self.profile), "model": ("box.gcode", self.model), "mode": (None, "dual"), "count": (None, "2")})
        st, data, _ = self.req("/api/plan", body, ct)
        r = json.loads(data)
        self.assertEqual(r["state"], "rejected")
        self.assertIn("DUAL_TOOLS", {p["code"] for p in r["problems"]})

    def test_dual_mode_own_model_end_to_end(self):
        import synth_dual
        from test_dual import CONSISTENT
        dual = synth_dual.dual_gcode(layers=10).encode()
        prof = open(synth_profile.build(os.path.join(self.tmp, "dual_ok.zip"), offsets=CONSISTENT), "rb").read()
        body, ct = multipart({"profile": ("p.zip", prof), "model": ("two.gcode", dual), "mode": (None, "dual"), "count": (None, "9")})
        st, data, _ = self.req("/api/plan", body, ct)
        r = json.loads(data)
        self.assertEqual(r["state"], "planned", r.get("problems"))
        self.assertEqual(r["inputs"]["count"], 2)
        st, _, _ = self.req("/api/download/" + r["job_id"])
        self.assertEqual(st, 403)                                  # nothing before verification
        s = self.run_job(r["job_id"])
        self.assertEqual(s["state"], "passed", s)
        self.assertEqual(s["verify"]["region_layers_ok"], {"T0": "10/10", "T1": "10/10", "T2": "10/10", "T3": "10/10"})
        st, data, _ = self.req(s["download_url"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), s["gcode_sha256"])
        self.assertIn(b"Dual Color Copy Mode", data[:500])

    def test_copy_mode_own_model_end_to_end(self):
        body, ct = multipart({"profile": ("p.zip", self.profile), "model": ("box.gcode", self.model), "mode": (None, "copy"), "count": (None, "7")})
        st, data, _ = self.req("/api/plan", body, ct)
        r = json.loads(data)
        self.assertEqual(r["state"], "planned", r.get("problems"))
        self.assertEqual(r["inputs"]["count"], 2)                 # the multiple-of-4 rule does not apply; Copy prints 2
        s = self.run_job(r["job_id"])
        self.assertEqual(s["state"], "passed", s)
        st, data, _ = self.req(s["download_url"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), s["gcode_sha256"])
        self.assertIn(b"; printing heads: T0,T1; parked for the whole print: T2,T3", data[:2000])
        farm = self.plan(8)
        self.assertNotEqual(farm["job_id"], r["job_id"])

    def test_clean_mesh_has_no_marks(self):
        st, r = self.plan_example("sharks-40")
        self.assertEqual(r["layout"]["mesh_affected"], [])
        self.assertEqual(r["layout"]["deviating_points"], [])
        self.assertFalse(any("mesh_effects" in c for c in r["layout"]["copies"]))
        self.assertFalse(any(w["code"].startswith("MESH_DEVIATING") for w in r["warnings"]))


class WebVerificationFailure(WebBase):
    hook = staticmethod(drop_accel)

    def test_failed_verification_blocks_download(self):
        r = self.plan(8)
        s = self.run_job(r["job_id"])
        self.assertEqual(s["state"], "failed")
        self.assertNotIn("download_url", s)
        self.assertTrue(any(p["code"] == "ACCELERATION" for p in s["verify"]["problems"]))
        st, data, _ = self.req("/api/download/" + r["job_id"])
        self.assertEqual(st, 403)
        self.assertNotIn(b"G1 ", data)
        self.assertFalse(os.path.exists(os.path.join(self.app.jobs[r["job_id"]].dir, "farm.gcode")))


if __name__ == "__main__":
    unittest.main()
