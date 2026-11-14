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
        for t in ("Farm Mode", "Copy Mode", "Dual Color Copy Mode", "Coming soon", "Available"):
            self.assertIn(t, html)
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
        self.assertEqual(s["message"], "Software checks passed.")
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
