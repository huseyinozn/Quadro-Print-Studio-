"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const HEADS = ["Head 1 (T0)", "Head 2 (T1)", "Head 3 (T2)", "Head 4 (T3)"];
  const HC = ["var(--h0)", "var(--h1)", "var(--h2)", "var(--h3)"];
  const NS = "http://www.w3.org/2000/svg";

  // `epoch` changes whenever an input changes. Any response or poll that
  // belongs to an older epoch is ignored, so an old result can never be
  // shown as the result of the new selection.
  let epoch = 0;
  let job = null;          // {id, layout}
  let pollTimer = null;
  let phase = "all";
  let source = "upload";   // "upload" | "example"
  const EXAMPLE_ID = "sharks-40";

  const els = {
    profile: $("profile"), model: $("model"), count: $("count"), check: $("check"),
    planStatus: $("plan-status"), layoutCard: $("layout-card"), rejectCard: $("reject-card"),
    resultCard: $("result-card"), gen: $("generate"), genStatus: $("gen-status"),
  };

  function countValue() { return parseInt(els.count.value, 10); }

  function resetResults() {
    epoch += 1;
    job = null;
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    for (const c of [els.layoutCard, els.rejectCard, els.resultCard]) c.classList.add("hidden");
    $("result-body").innerHTML = "";
    els.planStatus.textContent = "";
    els.genStatus.textContent = "";
    els.gen.disabled = false;
    const haveModel = source === "example" || els.model.files.length === 1;
    const ready = els.profile.files.length === 1 && haveModel && countValue() >= 4;
    els.check.disabled = !ready;
    $("copy-info").textContent = "Tap a copy to see its details.";
  }

  function setSource(src, count) {
    source = src;
    $("src-example").checked = src === "example";
    $("src-upload").checked = src === "upload";
    $("example-chosen").classList.toggle("hidden", src !== "example");
    $("upload-box").classList.toggle("hidden", src === "example");
    for (const c of document.querySelectorAll(".ex")) c.classList.toggle("sel", src === "example" && c.dataset.id === EXAMPLE_ID);
    if (count) els.count.value = count;
    resetResults();
  }

  $("src-example").addEventListener("change", () => setSource("example", 40));
  $("src-upload").addEventListener("change", () => setSource("upload"));
  $("own-model").addEventListener("click", () => { setSource("upload"); $("farm").scrollIntoView({ behavior: "smooth" }); });

  async function loadExamples() {
    let list = [];
    try { list = await (await fetch("/api/examples")).json(); } catch (e) { return; }
    const box = $("examples");
    box.innerHTML = "";
    for (const e of list) {
      const card = document.createElement("article");
      card.className = "ex";
      card.dataset.id = e.id;
      card.innerHTML = `<img src="${e.image}" alt="${escapeHtml(e.title)} example" loading="lazy">
        <div class="b"><div class="t"><h3>${escapeHtml(e.title)}</h3><span class="tag ${e.available ? "ok" : "soon"}">${e.available ? "Available" : "Coming soon"}</span></div>
        <span class="help-in">${escapeHtml(e.mode)}</span><p>${escapeHtml(e.text)}</p></div>`;
      const b = document.createElement("button");
      b.type = "button";
      if (e.available) {
        b.className = "primary";
        b.textContent = "Use this example";
        b.addEventListener("click", () => { setSource("example", e.count); $("farm").scrollIntoView({ behavior: "smooth" }); });
      } else {
        b.className = "secondary";
        b.textContent = "Coming soon";
        b.disabled = true;
      }
      card.querySelector(".b").appendChild(b);
      box.appendChild(card);
    }
  }

  els.profile.addEventListener("change", resetResults);
  els.model.addEventListener("change", resetResults);
  els.count.addEventListener("input", resetResults);
  $("minus").addEventListener("click", () => { els.count.value = Math.max(4, (Math.round(countValue() / 4) * 4 || 8) - 4); resetResults(); });
  $("plus").addEventListener("click", () => { els.count.value = Math.min(400, (Math.round(countValue() / 4) * 4 || 0) + 4); resetResults(); });

  els.check.addEventListener("click", checkLayout);
  els.gen.addEventListener("click", startGenerate);
  $("use-alt").addEventListener("click", () => {
    const c = parseInt($("use-alt").dataset.count, 10);
    els.count.value = c;
    resetResults();
    checkLayout();
  });

  async function checkLayout() {
    const my = epoch;
    const n = countValue();
    if (!(n >= 4) || n % 4 !== 0) {
      showReject({ problems: [{ text: "Choose a number of copies that is a multiple of 4.", detail: "" }] }, my);
      return;
    }
    els.check.disabled = true;
    els.planStatus.innerHTML = 'Checking the files and calculating the layout…<div class="prog"><i></i></div>';
    const fd = new FormData();
    fd.append("profile", els.profile.files[0]);
    if (source === "example") fd.append("example", EXAMPLE_ID);
    else fd.append("model", els.model.files[0]);
    fd.append("count", String(n));
    let res;
    try {
      const r = await fetch("/api/plan", { method: "POST", body: fd });
      res = await r.json();
      if (!r.ok && !res.state) throw new Error(res.error || r.statusText);
    } catch (e) {
      if (my !== epoch) return;
      els.planStatus.textContent = "Could not check the files: " + e.message;
      els.check.disabled = false;
      return;
    }
    if (my !== epoch) return;            // inputs changed meanwhile: drop this answer
    els.check.disabled = false;
    els.planStatus.textContent = "";
    if (res.state === "planned") {
      job = { id: res.job_id, layout: res.layout };
      showLayout(res);
    } else {
      showReject(res, my);
    }
  }

  function showReject(res, my) {
    if (my !== epoch) return;
    els.rejectCard.classList.remove("hidden");
    const ul = $("problems");
    ul.innerHTML = "";
    for (const p of res.problems || [{ text: res.message || res.error || "This selection cannot be used." }]) {
      const li = document.createElement("li");
      li.textContent = p.text;
      if (p.detail) { const s = document.createElement("small"); s.textContent = p.detail; li.appendChild(s); }
      if (p.hint) { const s = document.createElement("small"); s.textContent = p.hint; li.appendChild(s); }
      ul.appendChild(li);
    }
    const alt = res.alternative;
    if (alt) {
      $("alt").classList.remove("hidden");
      $("alt-text").textContent = "Alternative: " + alt.note;
      $("use-alt").textContent = "Use " + alt.count + " copies";
      $("use-alt").dataset.count = alt.count;
    } else {
      $("alt").classList.add("hidden");
    }
  }

  // ---------------------------------------------------------------- layout preview
  function showLayout(res) {
    const L = res.layout;
    els.layoutCard.classList.remove("hidden");
    const m = res.model;
    $("layout-sub").textContent = `${L.count} copies · ${m.footprint_mm[0]} × ${m.footprint_mm[1]} mm · ${m.layers} layers`;
    const F = L.foundation_layers;
    const ph = $("phases");
    ph.innerHTML = "";
    const phases = [["all", "All copies"]];
    for (let t = 0; t < 4; t++) phases.push(["h" + t, `Layers 1–${F} · ${HEADS[t].split(" (")[0]}`]);
    phases.push(["copy", `Layers ${F + 1}–${L.total_layers} · all heads`]);
    for (const [k, label] of phases) {
      const b = document.createElement("button");
      b.type = "button"; b.setAttribute("role", "tab"); b.textContent = label; b.dataset.phase = k;
      b.addEventListener("click", () => { phase = k; draw(L); });
      ph.appendChild(b);
    }
    phase = "all";
    draw(L);
    const lg = $("legend");
    lg.innerHTML = HEADS.map((h, t) => `<span><i style="background:${HC[t]}"></i>${h}: ${L.slots_per_head} copies</span>`).join("") +
      (L.mesh_affected.length ? `<span><i style="background:transparent;border:2px dashed var(--bad)"></i>${L.mesh_affected.length} copies depend on a marked mesh point</span>` : "");
    const w = $("warnings");
    w.innerHTML = "";
    for (const x of res.warnings || []) {
      const li = document.createElement("li");
      li.textContent = x.text;
      if (x.code === "MESH_DEVIATING_POINT") { const s = document.createElement("small"); s.textContent = x.detail; li.appendChild(s); }
      w.appendChild(li);
    }
    $("checklist").innerHTML = L.checks.map(c => `<li>${c.ok ? "✓" : "✗"} ${escapeHtml(c.detail)}</li>`).join("");
  }

  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function el(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function draw(L) {
    for (const b of $("phases").children) b.setAttribute("aria-selected", String(b.dataset.phase === phase));
    const F = L.foundation_layers;
    const notes = {
      all: `Each head prints ${L.slots_per_head} copies. Dashed lines join the copies printed together from layer ${F + 1}.`,
      copy: `Layers ${F + 1}–${L.total_layers}: all four heads print together, ${L.head_spacing.toFixed(1)} mm apart. Each dashed line is one group printed at the same time.`,
    };
    for (let t = 0; t < 4; t++) notes["h" + t] = `Layers 1–${F}: ${HEADS[t]} prints its ${L.slots_per_head} copies alone, after its own prime line, while the other heads wait parked.`;
    $("phase-note").textContent = notes[phase];
    const svg = $("bed");
    svg.innerHTML = "";
    svg.setAttribute("viewBox", "-22 -8 330 330");
    const Y = (y) => 300 - y;
    el("rect", { x: 0, y: 0, width: 300, height: 300, rx: 3, fill: "var(--bed)", stroke: "var(--ink)", "stroke-width": 0.8 }, svg);
    const [mx0, my0, mx1, my1] = L.mesh_area;
    el("rect", { x: mx0, y: Y(my1), width: mx1 - mx0, height: my1 - my0, fill: "none", stroke: "var(--mesh)", "stroke-width": 0.5, "stroke-dasharray": "3 2" }, svg);
    for (let v = 0; v <= 300; v += 100) {
      el("text", { x: v, y: 312, "font-size": 8, "text-anchor": "middle", fill: "var(--muted)" }, svg).textContent = v;
      el("text", { x: -3, y: Y(v) + 3, "font-size": 8, "text-anchor": "end", fill: "var(--muted)" }, svg).textContent = v;
    }
    const activeHead = phase.startsWith("h") ? parseInt(phase.slice(1), 10) : null;
    for (const p of L.purge) {
      const on = activeHead === null ? phase !== "copy" : activeHead === p.head;
      el("line", { x1: p.x0, y1: Y(p.y), x2: p.x1, y2: Y(p.y), stroke: HC[p.head], "stroke-width": Math.max(1.2, L.purge_width), "stroke-linecap": "round", opacity: on ? 1 : 0.2 }, svg);
    }
    for (const c of L.copies) {
      const on = activeHead === null || activeHead === c.head;
      const pts = c.hull.map(([x, y]) => `${x},${Y(y)}`).join(" ");
      el("polygon", { points: pts, fill: HC[c.head], "fill-opacity": on ? 0.35 : 0.06, stroke: HC[c.head], "stroke-width": 0.7, "stroke-opacity": on ? 1 : 0.25 }, svg);
      const poly = svg.lastChild;
      poly.dataset.copy = `${c.head}/${c.slot}`;
      poly.addEventListener("click", () => showCopy(c, L));
      if (c.mesh_effects) {
        const mk = el("polygon", { points: pts, fill: "none", stroke: "var(--bad)", "stroke-width": 1.2, "stroke-dasharray": "3 2", "data-mesh-mark": `${c.head}/${c.slot}`, "pointer-events": "none" }, svg);
      }
      const cx = c.hull.reduce((a, p) => a + p[0], 0) / c.hull.length;
      const cy = c.hull.reduce((a, p) => a + p[1], 0) / c.hull.length;
      el("text", { x: cx, y: Y(cy) + 2.5, "font-size": 7, "text-anchor": "middle", fill: "var(--ink)", opacity: on ? 1 : 0.3 }, svg).textContent = `${c.head + 1}.${c.slot + 1}`;
    }
    if (phase === "all" || phase === "copy") {
      for (const g of L.groups) {
        el("polyline", { points: g.map(([x, y]) => `${x},${Y(y)}`).join(" "), fill: "none", stroke: "var(--ink)", "stroke-width": phase === "copy" ? 0.8 : 0.4, "stroke-dasharray": "2 2", opacity: phase === "copy" ? 0.9 : 0.5 }, svg);
      }
    }
    for (const d of L.deviating_points) {
      el("circle", { cx: d.x, cy: Y(d.y), r: 5, fill: "none", stroke: "var(--bad)", "stroke-width": 1.4 }, svg);
      const t = el("title", {}, svg);
      t.textContent = `Measured mesh point (${d.x}, ${d.y}) = ${d.z} mm, ${d.deviation} mm from its neighbours (used as measured)`;
    }
  }

  function showCopy(c, L) {
    const box = $("copy-info");
    const F = L.foundation_layers;
    let html = `<b>Copy ${c.head + 1}.${c.slot + 1}</b> · ${HEADS[c.head]} · printed in group ${c.slot + 1} from layer ${F + 1}`;
    if (c.mesh_effects) {
      for (const e of c.mesh_effects) {
        html += `<div class="eff"><span>Marked mesh point</span><b>(${e.point[0]}, ${e.point[1]}) = ${e.point_z} mm</b>
          <span>Calculated effect on the first layer</span><b>up to ${e.first_layer_mm.toFixed(3)} mm</b>
          <span>Calculated effect on the common height at the end of layer ${F}</span><b>up to ${e.layer_end_top_mm.toFixed(3)} mm</b></div>`;
      }
      html += `<div>These are calculated from the measured mesh, which is used exactly as measured. Check the bed at the marked point before printing.</div>`;
    } else {
      html += `<div>This copy does not depend on a marked mesh point.</div>`;
    }
    box.innerHTML = html;
  }

  // ---------------------------------------------------------------- generate + verify
  async function startGenerate() {
    if (!job) return;
    const my = epoch;
    const id = job.id;
    els.gen.disabled = true;
    els.check.disabled = true;
    els.resultCard.classList.add("hidden");
    els.genStatus.innerHTML = 'Generating…<div class="prog"><i></i></div>';
    try {
      const r = await fetch("/api/generate/" + id, { method: "POST" });
      const s = await r.json();
      if (my !== epoch) return;
      if (r.status >= 400 && r.status !== 409) throw new Error(s.error || r.statusText);
      poll(id, my);
    } catch (e) {
      if (my !== epoch) return;
      els.genStatus.textContent = "Could not start: " + e.message;
      els.gen.disabled = false;
      els.check.disabled = false;
    }
  }

  async function poll(id, my) {
    if (my !== epoch) return;
    let s;
    try {
      const r = await fetch("/api/job/" + id, { cache: "no-store" });
      s = await r.json();
    } catch (e) {
      pollTimer = setTimeout(() => poll(id, my), 2000);
      return;
    }
    if (my !== epoch || !job || job.id !== id) return;
    if (s.state === "generating" || s.state === "verifying") {
      const msg = s.state === "generating" ? "Generating the G-code…" : "Running the independent verification on the generated file…";
      els.genStatus.innerHTML = `${msg} ${s.elapsed_s ? "(" + Math.round(s.elapsed_s) + " s)" : ""}<div class="prog"><i></i></div>`;
      pollTimer = setTimeout(() => poll(id, my), 1500);
      return;
    }
    els.genStatus.textContent = "";
    els.check.disabled = false;
    showResult(s);
  }

  function showResult(s) {
    els.resultCard.classList.remove("hidden");
    const body = $("result-body");
    body.innerHTML = "";
    const v = s.verify || {};
    if (s.state === "passed") {
      $("result-h").textContent = "Software checks passed";
      body.innerHTML = `
        <div class="ok-box">Software checks passed</div>
        <div class="facts">
          <span>Complete copies</span><b>${v.copies_complete} / ${s.inputs.count}</b>
          <span>Smallest gap between copies</span><b>${fmt(v.min_gap_mm)} mm</b>
          <span>First-layer height error</span><b>${fmt(Math.abs(v.first_layer_max_dev_mm || 0) * 1000, 1)} µm</b>
          <span>Lowest travel above printed parts</span><b>${fmt(v.min_transfer_clearance_mm)} mm</b>
          <span>Smallest carriage spacing</span><b>${fmt(v.min_carriage_gap_mm)} mm</b>
          <span>Heads parked, heaters off at the end</span><b>${v.final_parked && v.heaters_off ? "yes" : "no"}</b>
        </div>
        <a class="download" id="download" href="${s.download_url}" download="${escapeHtml(s.filename)}">Download G-code</a>
        <p class="fine">These are software checks of the generated file. It has not been tested on your printer. Watch the first layers of each head.</p>
        <p class="fine">File ${escapeHtml(s.filename)} · SHA-256 ${s.gcode_sha256}</p>`;
    } else {
      $("result-h").textContent = "Verification failed — download blocked";
      const items = (v.problems || []).map(p => `<li>${escapeHtml(p.text)}${p.detail ? "<small>" + escapeHtml(p.detail) + "</small>" : ""}</li>`).join("")
        || `<li>${escapeHtml(s.message || "The file did not pass verification.")}</li>`;
      body.innerHTML = `<p>${escapeHtml(s.message || "The generated file did not pass the independent verification, so it is not offered for download.")}</p>
        <ul class="problems">${items}</ul>`;
      els.gen.disabled = false;
    }
  }

  function fmt(x, d = 2) { return (x === null || x === undefined || isNaN(x)) ? "–" : Number(x).toFixed(d); }

  loadExamples();
  resetResults();
})();
