"""Quadro Print Studio — local web app (Farm Mode).

Standard library only. Run:  python3 -m quadro_studio.web [--host 127.0.0.1] [--port 8765]

Flow: upload one sliced model (Co Print Slicer G-code) + the printer's current
profile ZIP + copy count  ->  plan & layout preview  ->  generate  ->
independent verification in a separate process  ->  download.

Safety rules enforced here:
* A job is identified by the SHA-256 of (slice, profile, count, engine version).
  Changing any input yields a different job; results are never re-labelled.
* The G-code file is only downloadable when the separate `verify` process
  passed for exactly this job, and the file's SHA-256 still equals the hash
  that was verified. Otherwise the server answers 403 and sends no G-code.
* Uploaded files are stored per job, never served back, never executed.
* Nothing is sent to a printer and no external service is contacted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from email.parser import BytesParser
from email.policy import default as email_default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import __version__
from ..diagnostics import Diagnostics, StageError
from ..profile import load_profile, profile_checks
from ..slicer import load_slice
from ..plan import make_plan, LayoutOptions, suggest_count
from ..generate import generate
from ..dualslice import load_dual
from ..dualplan import make_dual_plan, PAIR, PAIR_OF
from ..dualgen import generate_dual
from . import messages

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
EXAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
# Public example slicer inputs (no printer calibration inside). Only modes with a
# finished converter + verifier are selectable; the others are shown as examples.
EXAMPLES = [
    dict(id="sharks-40", title="40 Sharks", mode="Farm Mode", mode_id="farm", available=True, count=40,
         file="sharks_single.gcode", image="/examples/sharks.png",
         text="One shark sliced once; 40 identical copies, 10 per head. You only upload your printer profile."),
    dict(id="orcacube", title="OrcaCube", mode="Copy Mode", mode_id="copy", available=True, count=2,
         file="orca_cube.gcode", image="/examples/orcacube.png",
         text="Two cubes at the same time: T0 prints the left one, T1 the right one. You only upload your printer profile."),
    dict(id="fidgets-2c", title="Two-Color Fidgets", mode="Dual Color Copy Mode", mode_id="dual", available=True, count=2,
         file="two_color_fidget.gcode", image="/examples/fidgets.png",
         text="Two two-colour fidgets at once: T0 + T1 print colour 1 on both models together, then T2 + T3 colour 2. "
              "You only upload your printer profile."),
]
MAX_SLICE = 256 * 1024 * 1024
MAX_PROFILE = 24 * 1024 * 1024
MAX_BODY = MAX_SLICE + MAX_PROFILE + 1024 * 1024
ENGINE = "farm-v2-%s" % __version__
MIN_COUNT, MAX_COUNT = 4, 400


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


class Job:
    def __init__(self, jid, folder):
        self.id = jid
        self.dir = folder
        self.state = "new"           # planned | rejected | generating | verifying | passed | failed | error
        self.message = ""
        self.lock = threading.Lock()
        self.inputs = {}
        self.layout = None
        self.verified_sha = None
        self.verify_summary = None
        self.gcode_name = None
        self.started = None
        self.finished = None

    def public(self):
        d = dict(job_id=self.id, state=self.state, message=self.message, inputs=self.inputs,
                 verify=self.verify_summary, engine=ENGINE)
        if self.state == "passed":
            d["download_url"] = "/api/download/%s" % self.id
            d["gcode_sha256"] = self.verified_sha
            d["filename"] = self.gcode_name
        if self.started:
            d["elapsed_s"] = round((self.finished or time.time()) - self.started, 1)
        return d


class App:
    def __init__(self, workdir, verify_timeout=1800, post_generate_hook=None):
        self.workdir = workdir
        os.makedirs(os.path.join(workdir, "jobs"), exist_ok=True)
        self.jobs = {}
        self.lock = threading.Lock()
        self.verify_timeout = verify_timeout
        # test-only hook: called with the generated file path before verification
        self.post_generate_hook = post_generate_hook

    # ---------------------------------------------------------------- plan
    def plan(self, slice_name, slice_bytes, profile_bytes, count, mode="farm"):
        if mode not in ("farm", "copy", "dual"):
            raise ValueError("unknown mode")
        if mode in ("copy", "dual"):
            count = 2
        ss, ps = sha256_bytes(slice_bytes), sha256_bytes(profile_bytes)
        jid = sha256_bytes(("%s|%s|%d|%s|%s" % (ss, ps, count, mode, ENGINE)).encode())[:20]
        with self.lock:
            job = self.jobs.get(jid)
            if job is not None and job.state not in ("error",):
                return job, job.layout
            folder = os.path.join(self.workdir, "jobs", jid)
            os.makedirs(folder, exist_ok=True)
            job = Job(jid, folder)
            self.jobs[jid] = job
        with open(os.path.join(folder, "model.gcode"), "wb") as f:
            f.write(slice_bytes)
        with open(os.path.join(folder, "profile.zip"), "wb") as f:
            f.write(profile_bytes)
        job.inputs = dict(model=os.path.basename(slice_name or "model.gcode")[:120], model_sha256=ss, profile_sha256=ps,
                          count=count, mode=mode)
        if mode == "dual":
            return self._plan_dual(job, folder)
        diags = Diagnostics()
        result = dict(job_id=jid, inputs=job.inputs)
        try:
            model, d1 = load_slice(os.path.join(folder, "model.gcode"))
            diags.extend(d1)
            prof, d2 = load_profile(os.path.join(folder, "profile.zip"))
            diags.extend(d2)
            profile_checks(prof, diags)
        except StageError as e:
            diags.extend(e.diags)
            job.state = "rejected"
            job.message = messages.headline(diags)
            result.update(state=job.state, message=job.message, problems=messages.explain(diags), warnings=[])
            job.layout = result
            return job, result
        opts = LayoutOptions(count=count, preset="auto", mode=mode)
        plan, d3 = (None, None)
        if mode == "farm" and (count % 4 or not (MIN_COUNT <= count <= MAX_COUNT)):
            d3 = Diagnostics()
            d3.error("PLAN_COUNT", "requested count %d is not a multiple of 4 between %d and %d" % (count, MIN_COUNT, MAX_COUNT))
        else:
            plan, d3 = make_plan(model, prof, opts)
        diags.extend(d3)
        result["model"] = dict(footprint_mm=[round(model.bbox_extrude[2] - model.bbox_extrude[0], 2),
                                             round(model.bbox_extrude[3] - model.bbox_extrude[1], 2)],
                               layers=model.layer_count, height_mm=round(model.blocks[-1].z, 2),
                               nozzle_temp=model.nozzle_temp, bed_temp=model.bed_temp)
        if plan is None or not diags.ok():
            job.state = "rejected"
            job.message = messages.headline(diags)
            result.update(state="rejected", message=job.message, problems=messages.explain(diags), warnings=messages.warnings(diags))
            alt_c, alt_plan = (None, None)
            if count >= 8 and any(d.code in ("PLAN_DOES_NOT_FIT", "PLAN_BED_BOUNDS", "PLAN_MESH_COVERAGE", "PLAN_COPY_GAP",
                                              "PLAN_PURGE_LANES", "PLAN_Y_LIMITS", "PLAN_TOOL_REACH_T0", "PLAN_TOOL_REACH_T1",
                                              "PLAN_TOOL_REACH_T2", "PLAN_TOOL_REACH_T3", "PLAN_COPY_LIMITS") for d in diags.errors) \
                    and all(d.code.startswith("PLAN_") for d in diags.errors):
                alt_c, alt_plan = (suggest_count(model, prof, LayoutOptions(count=min(count, MAX_COUNT), preset="auto"), min(count, MAX_COUNT))
                                   if mode == "farm" else (None, None))
            if alt_c:
                result["alternative"] = dict(count=alt_c, layout=layout_json(model, prof, alt_plan),
                                             note="%d copies fit: the full layout checks pass for this model and profile." % alt_c)
            job.layout = result
            return job, result
        job.state = "planned"
        job.message = "Layout checks passed. Generate to create and verify the file."
        result.update(state="planned", message=job.message, warnings=messages.warnings(diags),
                      layout=layout_json(model, prof, plan))
        job.layout = result
        return job, result

    def _plan_dual(self, job, folder):
        """Dual Color Copy Mode: the preview is built even when the job is refused,
        so the user sees which head would print which region and why it stops."""
        diags = Diagnostics()
        result = dict(job_id=job.id, inputs=job.inputs)
        try:
            dual, d1 = load_dual(os.path.join(folder, "model.gcode"), Diagnostics(), strict=False)
            diags.extend(d1)
            prof, d2 = load_profile(os.path.join(folder, "profile.zip"))
            diags.extend(d2)
            profile_checks(prof, diags)
        except StageError as e:
            diags.extend(e.diags)
            job.state = "rejected"
            job.message = messages.headline(diags)
            result.update(state=job.state, message=job.message, problems=messages.explain(diags), warnings=[])
            job.layout = result
            return job, result
        plan, d3 = make_dual_plan(dual, prof)
        diags.extend(d3)
        m0 = dual.models[0]
        result["model"] = dict(footprint_mm=dual.stats.get("footprint") or [round(m0.bbox_extrude[2] - m0.bbox_extrude[0], 2),
                                                                         round(m0.bbox_extrude[3] - m0.bbox_extrude[1], 2)],
                               layers=m0.layer_count, height_mm=round(m0.blocks[-1].z, 2),
                               nozzle_temp=m0.nozzle_temp, bed_temp=m0.bed_temp)
        if plan is not None:
            result["model"]["footprint_mm"] = plan.slice_summary["footprint"]
            result["layout"] = dual_layout_json(dual, prof, plan)
        if plan is None or not diags.ok():
            job.state = "rejected"
            job.message = messages.headline(diags)
            result.update(state="rejected", message=job.message, problems=messages.explain(diags), warnings=messages.warnings(diags))
        else:
            job.state = "planned"
            job.message = "Layout checks passed. Generate to create and verify the file."
            result.update(state="planned", message=job.message, warnings=messages.warnings(diags))
        job.layout = result
        return job, result

    # ---------------------------------------------------------------- generate + verify
    def start_generate(self, jid):
        job = self.jobs.get(jid)
        if job is None:
            return None, "Unknown job. Upload the files again."
        with job.lock:
            if job.state in ("generating", "verifying", "passed"):
                return job, None
            if job.state != "planned" and job.state != "failed":
                return job, "This selection cannot be generated: " + (job.message or job.state)
            job.state = "generating"
            job.message = "Generating G-code..."
            job.verified_sha = None
            job.verify_summary = None
            job.started = time.time()
            job.finished = None
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job, None

    def _run(self, job):
        folder = job.dir
        out = os.path.join(folder, "farm.gcode")
        try:
            if os.path.exists(out):
                os.remove(out)
            prof, _ = load_profile(os.path.join(folder, "profile.zip"))
            if job.inputs["mode"] == "dual":
                dual, _ = load_dual(os.path.join(folder, "model.gcode"), strict=True)
                plan, d = make_dual_plan(dual, prof)
                if plan is None or not d.ok():
                    raise RuntimeError("plan no longer valid")
                text, info = generate_dual(dual, prof, plan, job.inputs["model_sha256"], prof.fingerprint)
            else:
                model, _ = load_slice(os.path.join(folder, "model.gcode"))
                plan, d = make_plan(model, prof, LayoutOptions(count=job.inputs["count"], preset="auto", mode=job.inputs["mode"]))
                if plan is None or not d.ok():
                    raise RuntimeError("plan no longer valid")
                text, info = generate(model, prof, plan, job.inputs["model_sha256"], prof.fingerprint)
            with open(out, "w") as f:
                f.write(text)
            if self.post_generate_hook:
                self.post_generate_hook(out)
            produced_sha = sha256_file(out)
            job.state = "verifying"
            job.message = "Running the independent verification..."
            vjson = os.path.join(folder, "verify.json")
            if os.path.exists(vjson):
                os.remove(vjson)
            cmd = [sys.executable, "-m", "quadro_studio", "verify", "--mode", job.inputs["mode"], "--slice", os.path.join(folder, "model.gcode"),
                   "--profile", os.path.join(folder, "profile.zip"), "--gcode", out, "--count", str(job.inputs["count"]),
                   "--foundation-layers", str(plan.foundation_layers), "--min-gap", str(plan.options["min_gap"]), "--out", vjson]
            env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=self.verify_timeout, env=env)
            res = json.load(open(vjson))["candidate_result"] if os.path.exists(vjson) else None
            job.verify_summary = messages.verify_summary(res, p.returncode)
            if p.returncode == 0 and res and res.get("passed") and json.load(open(vjson)).get("candidate_sha256") == produced_sha:
                job.verified_sha = produced_sha
                base = os.path.splitext(job.inputs["model"])[0][:60] or "model"
                job.gcode_name = "%s_%s_%dx.gcode" % (base, job.inputs["mode"], job.inputs["count"])
                job.state = "passed"
                job.message = "Software checks passed."
            else:
                job.state = "failed"
                job.message = "Verification failed. The file cannot be downloaded."
                if os.path.exists(out):
                    os.remove(out)        # never keep an unverified printable file
        except Exception as e:  # noqa: BLE001 - report every failure to the user, never leave a file behind
            job.state = "error"
            job.message = "Generation stopped: %s" % e
            traceback.print_exc()
            if os.path.exists(out):
                os.remove(out)
        finally:
            job.finished = time.time()

    def download_path(self, jid):
        job = self.jobs.get(jid)
        if job is None or job.state != "passed" or not job.verified_sha:
            return None, job
        path = os.path.join(job.dir, "farm.gcode")
        if not os.path.exists(path) or sha256_file(path) != job.verified_sha:
            return None, job
        return path, job


def layout_json(model, prof, plan):
    """Geometry for the preview. Measured mesh values are reported unchanged."""
    F = plan.foundation_layers
    copies = []
    for key, hull in plan.footprints.items():
        t, s = (int(v) for v in key.split("/"))
        copies.append(dict(head=t, slot=s, hull=hull))
    # Copies listed by the planner as depending (> 0.01 mm) on a measured mesh point
    # that deviates from its neighbours. Taken 1:1 from plan.affected_by_outliers.
    effects = {}
    for a in plan.affected_by_outliers:
        effects.setdefault("%d/%d" % (a["tool"], a["slot"]), []).append(dict(
            point=[a["point"][0], a["point"][1]], point_z=a["point_z"],
            first_layer_mm=a["first_layer_dz_max"], layer_end_top_mm=a["foundation_top_dz_max"]))
    for c in copies:
        k = "%d/%d" % (c["head"], c["slot"])
        if k in effects:
            c["mesh_effects"] = effects[k]
    groups = []
    for s in plan.slots:
        groups.append([[round(s.cx + plan.sync_pitch * t, 3), round(s.cy - plan.offsets[t][1], 3)] for t in plan.heads])
    m = prof.mesh
    return dict(
        bed=[0, 0, 300, 300], mesh_area=[m.min_x, m.min_y, m.max_x, m.max_y],
        mode=plan.mode, heads=list(plan.heads), parked_heads=[t for t in range(4) if t not in plan.heads],
        copies=copies, groups=groups, slots_per_head=len(plan.slots), count=len(plan.slots) * len(plan.heads),
        purge=[dict(head=t, x0=plan.purge[t]["x0"], x1=plan.purge[t]["x1"], y=plan.purge[t]["y"]) for t in plan.heads],
        purge_width=plan.purge["width_est"],
        foundation_layers=F, total_layers=len(plan.layer_z),
        head_spacing=plan.sync_pitch, gap_mm=round(min(c["value"] for c in plan.checks if c["name"] == "copy_gap"), 2)
        if any(c["name"] == "copy_gap" and "value" in c for c in plan.checks) else None,
        deviating_points=[dict(x=p["x"], y=p["y"], z=p["z"], deviation=p["deviation"], neighbour_median=p["neighbour_median"])
                          for p in plan.mesh_deviating_points],
        offsets={"T%d" % t: list(plan.offsets[t]) for t in range(4)},
        mesh_affected=sorted(effects),
        checks=[dict(name=c["name"], ok=c["ok"], detail=c["detail"]) for c in plan.checks],
    )


def _path_d(segs, step=0.1):
    """SVG path data of extrusion segments (consecutive segments joined)."""
    out, last = [], None
    q = lambda v: round(v / step) * step
    for x0, y0, x1, y1 in segs:
        a, b = (q(x0), q(y0)), (q(x1), q(y1))
        if last is None or abs(last[0] - a[0]) > 1e-9 or abs(last[1] - a[1]) > 1e-9:
            out.append("M%.1f %.1f" % (a[0], 300 - a[1]))
        if b != last:
            out.append("L%.1f %.1f" % (b[0], 300 - b[1]))
        last = b
    return "".join(out)


def dual_layout_json(dual, prof, plan):
    """Preview data for Dual Color Copy Mode: for a few layers, the region every head
    prints on which model (each head's OWN slice region, placed where it is printed),
    plus towers, prime lines and the reasons a job is refused."""
    F = plan.foundation_layers
    n = len(plan.layer_z)
    slot = plan.slots[0]
    tr = {0: (0.0, 0.0), 2: (0.0, 0.0), 1: tuple(dual.translation), 3: tuple(dual.translation)}
    diffs = [d["layer"] for k in ("colour1", "colour2") for d in (dual.region_differences.get(k) or [])]
    layers = sorted({1, F, F + 1, (n + 1) // 2, n} | ({diffs[0]} if diffs else set()))
    layers = [L for L in layers if 1 <= L <= n]

    def phys(t, x, y):
        return plan.phys_xy(t, x - tr[t][0] + slot.dx, y - tr[t][1] + slot.dy)

    regions = {}
    for L in layers:
        per = {}
        for t in range(4):
            segs = []
            for mv in dual.models[t].blocks[L - 1].moves():
                if mv.kind == "extrude":
                    a, b = phys(t, mv.x0, mv.y0), phys(t, mv.x, mv.y)
                    segs.append((a[0], a[1], b[0], b[1]))
            per[str(t)] = _path_d(segs)
        regions[str(L)] = per
    marks = []
    for k, (a, b) in (("colour1", (0, 1)), ("colour2", (2, 3))):
        for d in (dual.region_differences.get(k) or [])[:1]:
            bb = d.get("area_left_model_xy")
            if bb:
                for t in (a, b):
                    p0, p1 = phys(0, bb[0], bb[1]), phys(0, bb[2], bb[3])
                    if t in (1, 3):
                        p0, p1 = (p0[0] + plan.spacing, p0[1]), (p1[0] + plan.spacing, p1[1])
                    marks.append(dict(head=t, colour=k, first_layer=d["layer"], rect=[round(p0[0], 2), round(p0[1], 2), round(p1[0], 2), round(p1[1], 2)],
                                      layers=[x["layer"] for x in dual.region_differences[k]][:40]))
    m = prof.mesh
    hulls = {t: plan.footprints["%d/0" % t] for t in range(4)}
    effects = {}
    for a in plan.affected_by_outliers:
        effects.setdefault(str(a["tool"]), []).append(dict(point=a["point"], point_z=a["point_z"], first_layer_mm=a["first_layer_dz_max"],
                                                          layer_end_top_mm=a["foundation_top_dz_max"]))
    return dict(
        bed=[0, 0, 300, 300], mesh_area=[m.min_x, m.min_y, m.max_x, m.max_y], mode="dual", heads=[0, 1, 2, 3], parked_heads=[],
        count=2, foundation_layers=F, total_layers=n, preview_layers=layers, regions=regions,
        colours={str(t): dual.colours[t] for t in range(4)},
        mapping=[dict(head=t, model="left" if t in (0, 2) else "right", colour=1 if t in (0, 1) else 2,
                      pair="T0 + T1" if t in (0, 1) else "T2 + T3") for t in range(4)],
        hulls={str(t): hulls[t] for t in range(4)},
        towers={str(t): [[round(x, 2), round(y, 2)] for x, y in plan.tower_squares[t]] for t in range(4)},
        purge=[dict(head=t, x0=plan.purge[t]["x0"], x1=plan.purge[t]["x1"], y=plan.purge[t]["y"]) for t in range(4)],
        purge_width=plan.purge["width_est"], region_marks=marks,
        pair_z=plan.pair_z, pair_y=plan.pair_y, spacing=round(plan.spacing, 2),
        deviating_points=[dict(x=p["x"], y=p["y"], z=p["z"], deviation=p["deviation"], neighbour_median=p["neighbour_median"])
                          for p in plan.mesh_deviating_points],
        mesh_effects=effects, mesh_affected=sorted(effects),
        offsets={"T%d" % t: list(plan.offsets[t]) for t in range(4)},
        checks=[dict(name=c["name"], ok=c["ok"], detail=c["detail"]) for c in plan.checks],
    )


# ==================================================================== HTTP
def parse_multipart(handler, limit):
    ctype = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if not ctype.startswith("multipart/form-data") or length <= 0:
        raise ValueError("expected a multipart form upload")
    if length > limit:
        raise ValueError("upload too large")
    body = handler.rfile.read(length)
    msg = BytesParser(policy=email_default).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
    fields = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fields[name] = (part.get_filename(), part.get_payload(decode=True) or b"")
    return fields


class Handler(BaseHTTPRequestHandler):
    server_version = "QuadroPrintStudio/" + __version__
    app: App = None

    def log_message(self, fmt, *args):
        sys.stderr.write("[web] %s\n" % (fmt % args))

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self.static("index.html", "text/html; charset=utf-8")
        if path in ("/app.js", "/style.css"):
            return self.static(path[1:], "application/javascript" if path.endswith(".js") else "text/css")
        if path == "/api/examples":
            return self.send_json([{k: v for k, v in e.items() if k != "file"} for e in EXAMPLES])
        if path.startswith("/examples/") and path.endswith(".png"):
            name = os.path.basename(path)
            if name in {os.path.basename(e["image"]) for e in EXAMPLES}:
                return self.static(os.path.join("examples", name), "image/png")
        if path == "/api/health":
            return self.send_json(dict(ok=True, engine=ENGINE))
        if path.startswith("/api/job/"):
            job = self.app.jobs.get(path.rsplit("/", 1)[-1])
            if job is None:
                return self.send_json(dict(error="unknown job"), 404)
            return self.send_json(job.public())
        if path.startswith("/api/download/"):
            jid = path.rsplit("/", 1)[-1]
            p, job = self.app.download_path(jid)
            if p is None:
                return self.send_json(dict(error="Download blocked: this file has not passed verification for the current "
                                                 "selection.", state=job.state if job else "unknown"), 403)
            size = os.path.getsize(p)
            self.send_response(200)
            self.send_header("Content-Type", "text/x-gcode")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % job.gcode_name)
            self.send_header("X-Gcode-SHA256", job.verified_sha)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return
        self.send_json(dict(error="not found"), 404)

    def static(self, name, ctype):
        with open(os.path.join(STATIC, name), "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/plan":
                f = parse_multipart(self, MAX_BODY)
                ex_id = (f.get("example", (None, b""))[1] or b"").decode().strip()
                if ex_id:
                    ex = next((e for e in EXAMPLES if e["id"] == ex_id), None)
                    if ex is None:
                        return self.send_json(dict(error="Unknown example."), 400)
                    if not ex["available"]:
                        return self.send_json(dict(error="%s is coming soon." % ex["mode"]), 409)
                    with open(os.path.join(EXAMPLE_DIR, ex["file"]), "rb") as fh:
                        f["model"] = ("%s.gcode" % ex["id"], fh.read())
                if "model" not in f or "profile" not in f:
                    return self.send_json(dict(error="Upload your printer profile ZIP and choose an example or upload a sliced model."), 400)
                mname, mbytes = f["model"]
                _, pbytes = f["profile"]
                if len(mbytes) > MAX_SLICE or len(pbytes) > MAX_PROFILE:
                    return self.send_json(dict(error="File too large."), 413)
                try:
                    count = int((f.get("count", (None, b"40"))[1] or b"40").decode())
                except ValueError:
                    return self.send_json(dict(error="Copies must be a whole number."), 400)
                mode = (f.get("mode", (None, b"farm"))[1] or b"farm").decode().strip()
                if ex_id:
                    mode = ex["mode_id"]
                if mode not in ("farm", "copy", "dual"):
                    return self.send_json(dict(error="Unknown mode."), 409)
                job, result = self.app.plan(mname, mbytes, pbytes, count, mode)
                return self.send_json(result)
            if path.startswith("/api/generate/"):
                job, err = self.app.start_generate(path.rsplit("/", 1)[-1])
                if job is None:
                    return self.send_json(dict(error=err), 404)
                if err:
                    return self.send_json(dict(error=err, **job.public()), 409)
                return self.send_json(job.public(), 202)
            return self.send_json(dict(error="not found"), 404)
        except ValueError as e:
            return self.send_json(dict(error=str(e)), 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self.send_json(dict(error="Server error: %s" % e), 500)


def make_server(host="127.0.0.1", port=8765, workdir=None, **kw):
    app = App(workdir or tempfile.mkdtemp(prefix="quadro_studio_"), **kw)
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv, app


def main(argv=None):
    ap = argparse.ArgumentParser(prog="quadro_studio.web", description="Quadro Print Studio local web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workdir", default=None, help="folder for uploaded inputs and generated files (default: temporary)")
    a = ap.parse_args(argv)
    srv, app = make_server(a.host, a.port, a.workdir)
    print("Quadro Print Studio running on http://%s:%d  (work folder %s)" % (a.host, srv.server_address[1], app.workdir))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
