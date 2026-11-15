"""In-browser backend (Pyodide) for the public static site.

Same planner, generator and verifier as the local server; no network, no subprocess:
the page runs generation in one Web Worker and the independent verification in a
second, separate Web Worker (its own Python interpreter), then compares the SHA-256
of the verified file with the generated one before it offers the download.
Uploaded files stay in the browser.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile

WORK = "/work"


def _app():
    from .server import App
    global _APP
    try:
        return _APP
    except NameError:
        os.makedirs(WORK, exist_ok=True)
        _APP = App(WORK)
        return _APP


def examples():
    from .server import EXAMPLES
    out = []
    for e in EXAMPLES:
        d = {k: v for k, v in e.items() if k not in ("file", "right_from_left")}
        d["image"] = d["image"].lstrip("/")
        out.append(d)
    return json.dumps(out)


def _b(x):
    return x.to_bytes() if hasattr(x, "to_bytes") else bytes(x)


def plan(mode, count, model_name, model_path, profile_path, example_id=""):
    """Inputs were written into the worker's file system by the page (no copies through JS)."""
    with open(model_path, "rb") as f:
        model_bytes = f.read()
    with open(profile_path, "rb") as f:
        profile_bytes = f.read()
    from .server import EXAMPLES
    app = _app()
    rfl = False
    if example_id:
        ex = next((e for e in EXAMPLES if e["id"] == example_id), None)
        if ex is None:
            return json.dumps(dict(error="Unknown example."))
        mode = ex["mode_id"]
        rfl = bool(ex.get("right_from_left"))
        model_name = "%s.gcode" % ex["id"]
    if mode not in ("farm", "copy", "dual"):
        return json.dumps(dict(error="Unknown mode."))
    job, result = app.plan(model_name, model_bytes, profile_bytes, int(count), mode, right_from_left=rfl)
    return json.dumps(result, default=str)


def generate(job_id):
    """Generate only (verification runs in the other worker). Returns the text and what the
    verifier needs; the job stays 'generating' until the page reports the verification."""
    from ..profile import load_profile
    from ..slicer import load_slice
    from ..plan import make_plan, LayoutOptions
    from ..generate import generate as gen_farm
    from ..dualslice import load_dual
    from ..dualplan import make_dual_plan
    from ..dualgen import generate_dual
    app = _app()
    job = app.jobs.get(job_id)
    if job is None or job.state not in ("planned", "failed"):
        return json.dumps(dict(error="This selection cannot be generated."))
    folder = job.dir
    prof, _ = load_profile(os.path.join(folder, "profile.zip"))
    if job.inputs["mode"] == "dual":
        dual, _ = load_dual(os.path.join(folder, "model.gcode"), strict=True, right_from_left=job.inputs.get("right_from_left", False))
        p, d = make_dual_plan(dual, prof)
        if p is None or not d.ok():
            return json.dumps(dict(error="Plan no longer valid."))
        text, info = generate_dual(dual, prof, p, job.inputs["model_sha256"], prof.fingerprint)
    else:
        model, _ = load_slice(os.path.join(folder, "model.gcode"))
        p, d = make_plan(model, prof, LayoutOptions(count=job.inputs["count"], preset="auto", mode=job.inputs["mode"]))
        if p is None or not d.ok():
            return json.dumps(dict(error="Plan no longer valid."))
        text, info = gen_farm(model, prof, p, job.inputs["model_sha256"], prof.fingerprint)
    sha = hashlib.sha256(text.encode()).hexdigest()
    args = ["verify", "--mode", job.inputs["mode"], "--slice", "/v/model.gcode", "--profile", "/v/profile.zip",
            "--gcode", "/v/out.gcode", "--count", str(job.inputs["count"]), "--foundation-layers", str(p.foundation_layers),
            "--min-gap", str(p.options["min_gap"]), "--out", "/v/verify.json"]
    if job.inputs.get("right_from_left"):
        args.append("--right-from-left")
    base = os.path.splitext(job.inputs["model"])[0][:60] or "model"
    name = "%s_%s_%dx.gcode" % (base, job.inputs["mode"], job.inputs["count"])
    with open(os.path.join(folder, "out.gcode"), "w") as f:
        f.write(text)
    return json.dumps(dict(folder=folder, sha256=sha, args=args, filename=name, lines=info.get("lines")))


def verify(args_json):
    """Independent verification, called in the SECOND worker (separate interpreter). The page
    has written /v/model.gcode, /v/profile.zip and /v/out.gcode into this worker's file system."""
    from ..cli import main
    from . import messages
    if os.path.exists("/v/verify.json"):
        os.remove("/v/verify.json")
    rc = main(list(json.loads(args_json)))
    if not os.path.exists("/v/verify.json"):
        return json.dumps(dict(rc=rc, summary=messages.verify_summary(None, rc), sha256=None))
    out = json.load(open("/v/verify.json"))
    res = out["candidate_result"]
    return json.dumps(dict(rc=rc, sha256=out.get("candidate_sha256"), summary=messages.verify_summary(res, rc)), default=str)


def zip_gcode(filename):
    """The viewer's save dialog accepts .zip, not .gcode: the verified file goes inside a ZIP."""
    with open("/v/out.gcode", "rb") as f:
        data = f.read()
    with zipfile.ZipFile("/v/out.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(filename, data)
    return hashlib.sha256(data).hexdigest()
