"use strict";
/* In-browser replacement for the local server's /api routes. Two Python workers:
   "main" plans and generates, "verify" checks the generated file in a separate interpreter.
   The download is offered only when the verified file's SHA-256 equals the generated one. */
(() => {
  function makeWorker(name) {
    const w = new Worker("worker.js", { name });
    const pending = new Map();
    let seq = 0;
    w.onmessage = (e) => {
      const p = pending.get(e.data.id);
      if (!p) return;
      pending.delete(e.data.id);
      e.data.ok ? p.resolve(e.data.result) : p.reject(new Error(e.data.error));
    };
    w.onerror = (e) => { for (const p of pending.values()) p.reject(new Error(e.message || "worker failed")); pending.clear(); };
    return (cmd, a = {}, transfer = []) => new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      w.postMessage({ id, cmd, a }, transfer);
    });
  }
  const mainW = makeWorker("plan-generate");
  let verifyW = null;                     // started on first use, kept separate from the generator
  const jobs = {};
  window.QPS_READY = mainW("ping");

  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  const pub = (j) => {
    const d = { job_id: j.id, state: j.state, message: j.message, inputs: j.inputs, verify: j.verify || null, engine: "browser" };
    if (j.state === "passed") { d.download_url = "#download-" + j.id; d.gcode_sha256 = j.sha; d.filename = j.filename; }
    if (j.started) d.elapsed_s = Math.round(((j.finished || Date.now()) - j.started) / 100) / 10;
    return d;
  };

  async function plan(form) {
    const get = (k) => form.get(k);
    const ex = get("example");
    const profile = get("profile");
    if (!profile) return json({ error: "Upload your printer profile ZIP." }, 400);
    const model = get("model");
    if (!ex && !model) return json({ error: "Choose an example or upload a sliced model." }, 400);
    const a = { mode: String(get("mode") || "farm"), count: parseInt(get("count") || "40", 10), profile: await profile.arrayBuffer() };
    const transfer = [a.profile];
    if (ex) a.example = String(ex);
    else { a.model = await model.arrayBuffer(); a.modelName = model.name; transfer.push(a.model); }
    const r = JSON.parse(await mainW("plan", a, transfer));
    if (r.error && !r.state) return json(r, 400);
    if (r.job_id) jobs[r.job_id] = Object.assign(jobs[r.job_id] || {}, { id: r.job_id, state: r.state, message: r.message, inputs: r.inputs });
    return json(r);
  }

  async function run(j) {
    try {
      j.state = "generating"; j.message = "Generating..."; j.started = Date.now(); j.finished = null; j.verify = null; j.zip = null;
      const g = await mainW("generate", { jobId: j.id });
      j.filename = g.meta.filename;
      j.state = "verifying"; j.message = "Running the independent verification...";
      verifyW = verifyW || makeWorker("verify");
      const gcode = g.gcode;
      const v = JSON.parse(await verifyW("verify", { args: g.meta.args, model: g.model, profile: g.profile, gcode: gcode.slice(0).buffer },
                                         [g.model.buffer, g.profile.buffer]));
      j.verify = v.summary;
      if (v.rc === 0 && v.summary && v.summary.passed && v.sha256 === g.meta.sha256) {
        const z = await verifyW("zip", { filename: j.filename });
        if (z.sha !== g.meta.sha256) throw new Error("file changed after verification");
        j.zip = new Blob([z.zip], { type: "application/zip" });
        j.sha = g.meta.sha256;
        j.state = "passed";
        j.message = (v.summary.warnings || []).length ? "Verified with warnings." : "Software checks passed.";
      } else {
        j.state = "failed"; j.message = "Verification failed. The file cannot be downloaded.";
      }
    } catch (err) {
      j.state = "error"; j.message = "Generation stopped: " + err.message;
    } finally { j.finished = Date.now(); }
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (!url.startsWith("/api/")) return realFetch(input, init);
    try {
      if (url === "/api/examples") return json(JSON.parse(await mainW("examples")));
      if (url === "/api/plan") return await plan(init.body);
      if (url.startsWith("/api/generate/")) {
        const j = jobs[url.split("/").pop()];
        if (!j) return json({ error: "Unknown job. Check the layout again." }, 404);
        if (j.state === "generating" || j.state === "verifying" || j.state === "passed") return json(pub(j), 202);
        if (j.state !== "planned" && j.state !== "failed") return json(Object.assign({ error: "This selection cannot be generated: " + j.message }, pub(j)), 409);
        run(j);
        return json(pub(j), 202);
      }
      if (url.startsWith("/api/job/")) {
        const j = jobs[url.split("/").pop()];
        return j ? json(pub(j)) : json({ error: "unknown job" }, 404);
      }
      if (url.startsWith("/api/download/")) return json({ error: "Download blocked: this file has not passed verification." }, 403);
      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: "Could not run: " + err.message }, 500);
    }
  };

  // Download: the verified G-code in a ZIP (the viewer's save dialog takes .zip, not .gcode)
  document.addEventListener("click", async (e) => {
    const a = e.target.closest && e.target.closest("#download");
    if (!a) return;
    e.preventDefault();
    const j = jobs[(a.getAttribute("href") || "").replace("#download-", "")];
    const note = document.getElementById("dl-note");
    if (!j || j.state !== "passed" || !j.zip) { if (note) note.textContent = "Download blocked: this file has not passed verification."; return; }
    const zipName = j.filename.replace(/\.gcode$/, "") + ".gcode.zip";
    let dl = null;
    try { dl = window.claude && window.claude.use ? await window.claude.use("downloads") : null; } catch (err) { dl = null; }
    if (dl) {
      try {
        await dl.save({ filename: zipName, data: j.zip });
        if (note) note.textContent = "Saved " + zipName + ". Unzip it to get " + j.filename + ".";
      } catch (err) {
        if (note) note.textContent = err && err.code === "declined" ? "Download cancelled." : "This view cannot save files. Open the page in a browser tab and try again.";
      }
      return;
    }
    const href = URL.createObjectURL(j.zip);
    const t = document.createElement("a");
    t.href = href; t.download = zipName; document.body.appendChild(t); t.click(); t.remove();
    setTimeout(() => URL.revokeObjectURL(href), 60000);
    if (note) note.textContent = "Downloaded " + zipName + ". Unzip it to get " + j.filename + ".";
  });
})();
