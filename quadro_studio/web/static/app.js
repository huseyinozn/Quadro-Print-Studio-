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
  let exampleId = "sharks-40";
  let mode = "farm";        // "farm" | "copy" | "dual"
  let pLayer = null;        // dual preview layer
  let examples = [];

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
    const ready = els.profile.files.length === 1 && haveModel && (mode !== "farm" || countValue() >= 4);
    els.check.disabled = !ready;
    $("copy-info").textContent = "Tap a copy to see its details.";
    $("mapping").classList.add("hidden");
    $("layer-pick").classList.add("hidden");
    els.gen.classList.remove("hidden");
  }

  function setMode(m) {
    mode = m;
    $("mode-farm").checked = m === "farm";
    $("mode-copy").checked = m === "copy";
    $("mode-dual").checked = m === "dual";
    $("copies-help").classList.toggle("hidden", m !== "farm");
    $("stepper").classList.toggle("hidden", m !== "farm");
    $("copy-fixed").classList.toggle("hidden", m !== "copy");
    $("dual-fixed").classList.toggle("hidden", m !== "dual");
    $("upload-help").textContent = m === "dual"
      ? "G-code from Co Print Slicer with two identical two-colour models and four tools: left model T0 + T2, right model T1 + T3."
      : "G-code of one single-colour model from Co Print Slicer, sliced once for T0.";
    if (m !== "farm") els.count.value = 2;
    else if (countValue() < 4) els.count.value = 40;
    // an example belongs to one mode; switching mode drops a mismatching example
    const ex = examples.find(e => e.id === exampleId);
    if (source === "example" && ex && ex.mode_id !== m) source = "upload";
    const exForMode = examples.find(e => e.mode_id === m && e.available);
    if (exForMode) exampleId = exForMode.id;
    refreshSource();
    resetResults();
  }

  function refreshSource() {
    const ex = examples.find(e => e.id === exampleId);
    $("src-example").checked = source === "example";
    $("src-upload").checked = source === "upload";
    if (ex) {
      $("src-example-l").textContent = "Example: " + ex.title;
      $("chosen-title").textContent = ex.title;
      $("chosen-img").src = ex.image;
    }
    $("example-chosen").classList.toggle("hidden", source !== "example");
    $("upload-box").classList.toggle("hidden", source === "example");
    for (const c of document.querySelectorAll(".ex")) c.classList.toggle("sel", source === "example" && c.dataset.id === exampleId);
  }

  $("mode-farm").addEventListener("change", () => setMode("farm"));
  $("mode-copy").addEventListener("change", () => setMode("copy"));
  $("mode-dual").addEventListener("change", () => setMode("dual"));

  function setSource(src, count, exId) {
    source = src;
    if (exId) {
      exampleId = exId;
      const ex = examples.find(e => e.id === exId);
      if (ex && ex.mode_id !== mode) { setMode(ex.mode_id); source = src; }
    }
    refreshSource();
    if (count) els.count.value = count;
    resetResults();
  }

  $("src-example").addEventListener("change", () => {
    const ex = examples.find(e => e.id === exampleId);
    setSource("example", ex ? ex.count : null, exampleId);
  });
  $("src-upload").addEventListener("change", () => setSource("upload"));
  $("own-model").addEventListener("click", () => { setSource("upload"); $("farm").scrollIntoView({ behavior: "smooth" }); });

  async function loadExamples() {
    let list = [];
    try { list = await (await fetch("/api/examples")).json(); } catch (e) { return; }
    examples = list;
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
        b.addEventListener("click", () => { setSource("example", e.count, e.id); $("farm").scrollIntoView({ behavior: "smooth" }); });
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
    if (mode === "farm" && (!(n >= 4) || n % 4 !== 0)) {
      showReject({ problems: [{ text: "Choose a number of copies that is a multiple of 4.", detail: "" }] }, my);
      return;
    }
    els.check.disabled = true;
    els.planStatus.innerHTML = 'Checking the files and calculating the layout…<div class="prog"><i></i></div>';
    const fd = new FormData();
    fd.append("profile", els.profile.files[0]);
    fd.append("mode", mode);
    if (source === "example") fd.append("example", exampleId);
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
    if (res.layout && res.layout.mode === "dual") {
      showLayout(res);                 // show what would be printed where, and why it stops
      els.gen.classList.add("hidden");
    }
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
    if (L.mode === "dual") return showDual(res);
    const m = res.model;
    $("layout-sub").textContent = `${L.count} copies · ${m.footprint_mm[0]} × ${m.footprint_mm[1]} mm · ${m.layers} layers`;
    const F = L.foundation_layers;
    const ph = $("phases");
    ph.innerHTML = "";
    const phases = [["all", "All copies"]];
    for (const t of L.heads) phases.push(["h" + t, `Layers 1–${F} · ${HEADS[t].split(" (")[0]}`]);
    phases.push(["copy", `Layers ${F + 1}–${L.total_layers} · ${L.heads.length === 4 ? "all heads" : L.heads.map(t => "T" + t).join(" + ") + " together"}`]);
    for (const [k, label] of phases) {
      const b = document.createElement("button");
      b.type = "button"; b.setAttribute("role", "tab"); b.textContent = label; b.dataset.phase = k;
      b.addEventListener("click", () => { phase = k; draw(L); });
      ph.appendChild(b);
    }
    phase = "all";
    draw(L);
    const lg = $("legend");
    lg.innerHTML = L.heads.map(t => `<span><i style="background:${HC[t]}"></i>${HEADS[t]}: ${L.slots_per_head} ${L.slots_per_head === 1 ? "copy" : "copies"}${L.mode === "copy" ? (t === 0 ? " (left)" : " (right)") : ""}</span>`).join("") +
      L.parked_heads.map(t => `<span><i style="background:transparent;border:2px solid ${HC[t]}"></i>${HEADS[t]}: parked, does not print</span>`).join("") +
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
    if (L.mode === "copy") {
      notes.all = `T0 prints the left copy, T1 the right copy. T2 and T3 stay parked. From layer ${F + 1} both copies are printed together.`;
      notes.copy = `Layers ${F + 1}–${L.total_layers}: T0 and T1 print together, ${L.head_spacing.toFixed(1)} mm apart. T2 and T3 stay parked.`;
    }
    for (const t of L.heads) notes["h" + t] = `Layers 1–${F}: ${HEADS[t]} prints ${L.slots_per_head === 1 ? "its copy" : "its " + L.slots_per_head + " copies"} alone, right after its own prime line, while the other heads wait parked.`;
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

  // ---------------------------------------------------------------- dual colour preview
  const MODEL = { left: "Left model", right: "Right model" };

  function showDual(res) {
    const L = res.layout, m = res.model, F = L.foundation_layers;
    $("layout-sub").textContent = `2 two-colour copies · ${m.footprint_mm[0]} × ${m.footprint_mm[1]} mm each · ${m.layers} layers`;
    const ph = $("phases");
    ph.innerHTML = "";
    const phases = [["all", "All heads"], ["solo", `Layers 1–${F} · each head alone`], ["A", `Layers ${F + 1}–${L.total_layers} · T0 + T1`], ["B", `Layers ${F + 1}–${L.total_layers} · T2 + T3`]];
    for (const [k, label] of phases) {
      const b = document.createElement("button");
      b.type = "button"; b.setAttribute("role", "tab"); b.textContent = label; b.dataset.phase = k;
      b.addEventListener("click", () => { phase = k; if (k === "solo") pLayer = 1; else if ((k === "A" || k === "B") && pLayer <= F) pLayer = F + 1; drawDual(L); });
      ph.appendChild(b);
    }
    const lp = $("layer-pick");
    lp.classList.remove("hidden");
    lp.innerHTML = "";
    for (const n of L.preview_layers) {
      const b = document.createElement("button");
      b.type = "button"; b.setAttribute("role", "tab"); b.textContent = "Layer " + n; b.dataset.layer = n;
      b.addEventListener("click", () => { pLayer = n; drawDual(L); });
      lp.appendChild(b);
    }
    phase = "all";
    pLayer = L.region_marks.length ? L.region_marks[0].first_layer : F + 1;
    drawDual(L);
    $("legend").innerHTML = L.mapping.map(r => `<span><i style="background:${L.colours[r.head]}"></i>${HEADS[r.head]}: ${MODEL[r.model].toLowerCase()}, colour ${r.colour}</span>`).join("") +
      `<span><i style="background:transparent;border:2px solid var(--ink)"></i>Wipe tower (10 × 10 mm) per head</span>` +
      (L.region_marks.length ? `<span><i style="background:transparent;border:2px dashed var(--bad)"></i>Region that differs between the two models</span>` : "") +
      ((L.z_findings || []).length ? `<span><i style="background:transparent;border:2px solid var(--bad);border-radius:50%"></i>Height problem on the shown layer</span>` : "");
    const w = $("warnings");
    w.innerHTML = "";
    for (const x of res.warnings || []) {
      const li = document.createElement("li");
      li.textContent = x.text;
      if (x.code === "MESH_DEVIATING_POINT") { const s = document.createElement("small"); s.textContent = x.detail; li.appendChild(s); }
      w.appendChild(li);
    }
    const mp = $("mapping");
    mp.classList.remove("hidden");
    const chk = (n) => L.checks.find(c => c.name === n) || { ok: true, detail: "", name: n };
    const KIND = { colour_change_interior: "colour changes from one layer to the next",
                   colour_change_edge: "bead edge on the other colour at a colour border",
                   sink_extrude: "nozzle prints lower than the other colour beside it",
                   sink_travel: "travel/wipe at layer height through the other colour" };
    let zHtml = "";
    if (L.z_summary && Object.keys(L.z_summary).length) {
      zHtml = Object.entries(L.z_summary).map(([side, st]) => {
        const ci = st.colour_change_interior, ce = st.colour_change_edge;
        return `<li><b>${MODEL[side]}</b>: colour changes ${ci.out_of_limits + ce.out_of_limits} cells outside ${L.pair_z.limits.map(v => v.toFixed(2)).join("–")} mm
          (from one layer to the next: ${ci.out_of_limits}${ci.layers.length ? ", layers " + ci.layers.slice(0, 6).join(", ") : ""}; at colour borders: ${ce.out_of_limits});
          nozzle lower than the colour beside it: ${st.sink_extrude.points} points (max ${st.sink_extrude.max_depth.toFixed(3)} mm);
          travels through the other colour: ${st.sink_travel.moves}</li>`;
      }).join("");
      const ex = L.z_table || [];
      zHtml = `<ul class="zsum">${zHtml}</ul>` + (ex.length ? `<table class="zf"><thead><tr><th>Layer</th><th>Head · model</th><th>X, Y (mm)</th><th>What</th></tr></thead><tbody>` +
        ex.map(f => `<tr data-layer="${f.layer}"><td><button type="button" class="lnk" data-goto="${f.layer}">${f.layer}</button></td><td>${f.head} · ${f.model}</td><td>${f.x}, ${f.y}</td><td>${KIND[f.kind]}: ${f.thickness !== undefined ? f.thickness.toFixed(3) + " mm thick on " + escapeHtml(f.below) : f.depth.toFixed(3) + " mm into " + escapeHtml(f.beside || f.over)}</td></tr>`).join("") + `</tbody></table>` : "");
    }
    const cm = chk("colour_mapping"), cz = chk("pair_z_consistency"), cy = chk("pair_y_registration");
    const ci = chk("into_material"), ct = chk("travel_drag");
    const dev = (c) => (c.value !== undefined && c.value !== null && c.limit !== undefined) ? (c.name === "colour_stack_room"
      ? `<div class="devline">Smallest room <b>${Number(c.value).toFixed(3)} mm</b> · needed ${Number(c.limit).toFixed(2)} mm</div>`
      : `<div class="devline">Deviation <b>${Number(c.value).toFixed(3)} mm</b> · limit ${Number(c.limit).toFixed(2)} mm</div>`) : "";
    const sec = (title, c, extra) => `<div class="${c.ok ? "okb" : "why"}" data-check="${escapeHtml(title)}"><b>${c.ok ? "✓" : "✗"} ${title}</b>${dev(c)}<div>${escapeHtml(c.detail)}</div>${extra || ""}</div>`;
    const why = sec("Colour mapping", cm) + sec("Colour alignment (Y)", cy) + sec("Height (Z)", cz) +
      sec("Nozzle into the other colour", ci, (L.z_blocking || []).includes("nozzle_into_material") ? zHtml : "") +
      sec("Travels and wipes", ct) + sec("Room for the filament", chk("colour_stack_room"), roomHtml(L)) +
      L.checks.filter(c => !c.ok && !["colour_mapping", "pair_z_consistency", "pair_y_registration", "into_material", "travel_drag", "colour_stack_room"].includes(c.name))        .map(c => `<div class="why">✗ ${escapeHtml(c.detail)}</div>`).join("");
    mp.innerHTML = `<table class="map"><thead><tr><th>Head</th><th>Model</th><th>Colour</th><th>Layers 1–${F}</th><th>From layer ${F + 1}</th></tr></thead><tbody>` +
      L.mapping.map(r => `<tr data-head="${r.head}"><td>${HEADS[r.head]}</td><td>${MODEL[r.model]}</td><td><span class="sw" style="background:${L.colours[r.head]}"></span>${r.colour}</td><td>alone</td><td>with ${r.pair.replace("T" + r.head, "").replace(" + ", "").trim()} (pair ${r.pair})</td></tr>`).join("") +
      `</tbody></table>` +
      `<p class="fine">Order on every layer from ${F + 1}: T0 + T1 print colour 1 on both models, park, then T2 + T3 print colour 2. Each head starts every layer on its own wipe tower.</p>` + why;
    $("checklist").innerHTML = L.checks.map(c => `<li>${c.ok ? "✓" : "✗"} ${escapeHtml(c.detail)}</li>`).join("");
    $("copy-info").textContent = "Tap a coloured region to see which head prints it.";
    for (const b of mp.querySelectorAll("[data-goto]")) b.addEventListener("click", () => {
      pLayer = parseInt(b.dataset.goto, 10);
      if (!L.preview_layers.includes(pLayer)) pLayer = L.preview_layers[0];
      phase = "all"; drawDual(L); $("bed").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  function drawDual(L) {
    const F = L.foundation_layers;
    for (const b of $("phases").children) b.setAttribute("aria-selected", String(b.dataset.phase === phase));
    for (const b of $("layer-pick").children) b.setAttribute("aria-selected", String(parseInt(b.dataset.layer, 10) === pLayer));
    const solo = pLayer <= F;
    const on = (t) => phase === "all" || phase === "solo" || (phase === "A" ? t < 2 : phase === "B" ? t >= 2 : true);
    const notes = {
      all: `Layer ${pLayer}: ${solo ? "each head prints its region alone (T0, T1, T2, T3), right after its own prime line." : "T0 + T1 print colour 1 on both models together, then T2 + T3 print colour 2."}`,
      solo: `Layers 1–${F}: every head prints its own region alone on its own measured bed height. Layer ${pLayer} shown.`,
      A: `From layer ${F + 1}: T0 (left model) and T1 (right model) print colour 1 at the same time, ${L.spacing.toFixed(1)} mm apart. T2 and T3 wait parked.`,
      B: `From layer ${F + 1}: after T0 + T1 park, T2 (left model) and T3 (right model) print colour 2 of the same layer.`,
    };
    $("phase-note").textContent = notes[phase];
    const svg = $("bed");
    svg.innerHTML = "";
    const xs = Object.values(L.hulls).flat().concat(Object.values(L.towers).flat());
    const x0 = Math.min(...xs.map(p => p[0])) - 12, x1 = Math.max(...xs.map(p => p[0])) + 12;
    const y0 = Math.min(...xs.map(p => p[1])) - 12, y1 = Math.max(...xs.map(p => p[1])) + 12;
    svg.setAttribute("viewBox", `${x0} ${300 - y1} ${x1 - x0} ${y1 - y0}`);
    const Y = (y) => 300 - y;
    el("rect", { x: x0, y: 300 - y1, width: x1 - x0, height: y1 - y0, fill: "var(--bed)", stroke: "var(--line)", "stroke-width": 0.3 }, svg);
    for (const t of [0, 2, 1, 3]) {
      const d = (L.regions[pLayer] || {})[t];
      if (!d) continue;
      const p = el("path", { d, fill: "none", stroke: L.colours[t], "stroke-width": 0.45, "stroke-linecap": "round", "stroke-linejoin": "round", opacity: on(t) ? 1 : 0.12, "data-head": t }, svg);
    }
    for (const t of [0, 1, 2, 3]) {
      const hull = L.hulls[t];
      const hit = el("polygon", { points: hull.map(([x, y]) => `${x},${Y(y)}`).join(" "), fill: "transparent", stroke: "none", "data-region": t }, svg);
      hit.addEventListener("click", () => showRegion(t, L));
      const sq = L.towers[t];
      el("polygon", { points: sq.map(([x, y]) => `${x},${Y(y)}`).join(" "), fill: L.colours[t], "fill-opacity": on(t) ? 0.5 : 0.08, stroke: "var(--ink)", "stroke-width": 0.3, opacity: on(t) ? 1 : 0.3 }, svg);
      const cx = sq.reduce((a, p) => a + p[0], 0) / 4, cy = sq.reduce((a, p) => a + p[1], 0) / 4;
      el("text", { x: cx, y: Y(cy) + 1.2, "font-size": 3.4, "text-anchor": "middle", fill: "var(--ink)", opacity: on(t) ? 1 : 0.3 }, svg).textContent = "T" + t;
    }
    for (const side of ["left", "right"]) {
      const h = L.hulls[side === "left" ? 0 : 1];
      const cx = h.reduce((a, p) => a + p[0], 0) / h.length, top = Math.max(...h.map(p => p[1]));
      el("text", { x: cx, y: Y(top) - 3, "font-size": 4.2, "text-anchor": "middle", fill: "var(--muted)" }, svg).textContent = MODEL[side] + (side === "left" ? " · T0 / T2" : " · T1 / T3");
    }
    for (const f of (L.z_findings || [])) {
      if (f.layer !== pLayer || f.kind === "colour_change_edge") continue;
      const c = el("circle", { cx: f.x, cy: Y(f.y), r: 1.6, fill: "none", stroke: "var(--bad)", "stroke-width": 0.5, "data-zfind": f.kind, "pointer-events": "none" }, svg);
      el("title", {}, c).textContent = `Layer ${f.layer} ${f.head} (${f.x}, ${f.y})`;
    }
    for (const mk of L.region_marks) {
      if (!mk.layers.includes(pLayer)) continue;
      const [a, b, c, d] = mk.rect;
      el("rect", { x: Math.min(a, c) - 1, y: Y(Math.max(b, d)) - 1, width: Math.abs(c - a) + 2, height: Math.abs(d - b) + 2, fill: "none", stroke: "var(--bad)", "stroke-width": 0.6, "stroke-dasharray": "1.5 1", "data-diff": mk.head, "pointer-events": "none" }, svg);
    }
  }

  function roomHtml(L) {
    const r = L.room;
    if (!r) return "";
    const f = (v) => v === null || v === undefined ? "–" : Number(v).toFixed(3);
    let h = `<table class="zf room"><thead><tr><th></th><th>Left model</th><th>Right model</th></tr></thead><tbody>
      <tr><td>Smallest room under a bead on the other colour</td><td id="room-left">${f(r.before.left.centre_min)} mm</td><td id="room-right">${f(r.before.right.centre_min)} mm</td></tr>
      <tr><td>Largest</td><td>${f(r.before.left.centre_max)} mm</td><td>${f(r.before.right.centre_max)} mm</td></tr>
      <tr><td>At colour borders (bead edge)</td><td>${f(r.before.left.edge_min)} mm</td><td>${f(r.before.right.edge_min)} mm</td></tr>
      <tr><td>With local layer compensation</td><td>${f(r.after.left.centre_min)} mm</td><td>${f(r.after.right.centre_min)} mm</td></tr></tbody></table>`;
    const ff = (r.sync_flow_failures || []).slice(0, 6);
    if (ff.length) h += `<p class="fine">Same move, same point on both models (one E for the synchronised pair):</p><table class="zf" id="sync-conflicts"><thead><tr><th>Layer · pair · slicer line</th><th>Room left / right</th><th>E needed left / right</th></tr></thead><tbody>` +
      ff.map(x => `<tr><td>${x.layer} · ${x.pair} · ${x.slicer_line}</td><td>${f(x.room_left)} / ${f(x.room_right)} mm</td><td>${x.e_left_pct} % / ${x.e_right_pct} %</td></tr>`).join("") + `</tbody></table>`;
    const fc = L.foundation_choice;
    if (fc) h += `<p class="fine" id="found-choice">Independent layers: <b>${fc.chosen}</b> (default ${fc.default}); pairs print together from layer ${fc.pairs_from_layer}.</p>`;
    return h;
  }

  function showRegion(t, L) {
    const r = L.mapping[t], F = L.foundation_layers;
    const mate = { 0: 1, 1: 0, 2: 3, 3: 2 }[t];
    let html = `<b>${HEADS[t]}</b> · ${MODEL[r.model]} · colour ${r.colour} <span class="sw" style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${L.colours[t]}"></span>
      <div>Layers 1–${F}: prints this region alone. From layer ${F + 1}: prints it together with ${HEADS[mate]} (${r.pair}), ${t < 2 ? "first" : "after T0 + T1 have parked"}.</div>`;
    const diff = L.region_marks.filter(m => m.head === t);
    if (diff.length) html += `<div>This region differs from the other model's on layers ${diff[0].layers.slice(0, 8).join(", ")}${diff[0].layers.length > 8 ? "…" : ""} (dashed box), so it cannot be printed as a copy.</div>`;
    for (const e of (L.mesh_effects[t] || [])) {
      html += `<div class="eff"><span>Marked mesh point</span><b>(${e.point[0]}, ${e.point[1]}) = ${e.point_z} mm</b>
        <span>Calculated effect on the first layer</span><b>up to ${e.first_layer_mm.toFixed(3)} mm</b>
        <span>Calculated effect on the common height at the end of layer ${F}</span><b>up to ${e.layer_end_top_mm.toFixed(3)} mm</b></div>`;
    }
    $("copy-info").innerHTML = html;
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
    if (s.state === "passed" && v.mode === "dual") {
      const withWarn = (v.warnings || []).length > 0;
      $("result-h").textContent = withWarn ? "Verified with warnings" : "Software checks passed";
      const reg = v.region_layers_ok || {};
      body.innerHTML = `
        <div class="ok-box${withWarn ? " warnbox" : ""}" id="verdict">${withWarn ? "Verified with warnings" : "Software checks passed"}</div>
        <div class="facts">
          ${v.flow_warning ? `<span>Moves where both heads keep the slicer amount although the models need different amounts</span><b id="flow-moves">${v.flow_warning.moves}</b>
          <span>Largest flow difference between the two models</span><b id="flow-max">${fmt(v.flow_warning.max_diff_pct, 1)} % (${fmt(v.flow_warning.max_ratio, 2)}×)</b>` : ""}
          <span>Colour regions reproduced (per head, all layers)</span><b id="dual-regions">${["T0", "T1", "T2", "T3"].map(h => `${h} ${reg[h] || "–"}`).join(" · ")}</b>
          <span>Colour alignment (Y) left / right model</span><b id="dual-y">${fmt((v.registration_mm || {}).left, 3)} / ${fmt((v.registration_mm || {}).right, 3)} mm (limit ${fmt((v.limits || {}).y_registration, 2)})</b>
          <span>Height (Z) where colours stack, largest deviation</span><b id="dual-z">${fmt(v.z_deviation_mm, 3)} mm (limit ${fmt((v.limits || {}).z_deviation, 2)})</b>
          <span>Nozzle below the other colour, deepest</span><b>${fmt(v.into_material_mm, 3)} mm (limit ${fmt((v.limits || {}).into_material, 2)})</b>
          <span>Smallest room for the filament, left / right model</span><b id="dual-room">${fmt(((v.stack_room || {}).left || {}).centre_min_mm, 3)} / ${fmt(((v.stack_room || {}).right || {}).centre_min_mm, 3)} mm (min ${fmt(v.stack_room_min, 2)})</b>
          <span>Travel/wipe below the other colour, deepest</span><b>${fmt(v.travel_drag_mm, 3)} mm (limit ${fmt((v.limits || {}).into_material, 2)})</b>
          <span>Pairs print together from layer</span><b>${v.synchronized_from_layer ?? "–"}</b>
          <span>Gap between the two models</span><b>${fmt(v.min_gap_models_mm)} mm</b>
          <span>Wipe towers from the models</span><b>${fmt(v.tower_gap_mm)} mm</b>
          <span>First-layer height error</span><b>${fmt(Math.abs(v.first_layer_max_dev_mm || 0) * 1000, 1)} µm</b>
          <span>Lowest travel above printed parts</span><b>${fmt(v.min_transfer_clearance_mm)} mm</b>
          <span>Head spacing while printing together</span><b>${fmt(v.min_sync_head_gap_mm)} mm</b>
          <span>Heads parked, heaters off at the end</span><b>${v.final_parked && v.heaters_off ? "yes" : "no"}</b>
        </div>
        ${(v.warnings || []).length ? `<ul class="warn" id="result-warnings">${v.warnings.map(w => `<li>${escapeHtml(w.text)} (${w.count}×)<small>${escapeHtml(w.detail)}</small></li>`).join("")}</ul>` : ""}
        <a class="download" id="download" href="${s.download_url}" download="${escapeHtml(s.filename)}">Download G-code</a>
        <p class="fine" id="dl-note" role="status"></p>
        <p class="fine">These are software checks of the generated file. It has not been tested on your printer. Watch the first layers of each head.</p>
        <p class="fine hash">File ${escapeHtml(s.filename)} · SHA-256 ${s.gcode_sha256}</p>`;
    } else if (s.state === "passed") {
      $("result-h").textContent = "Software checks passed";
      body.innerHTML = `
        <div class="ok-box">Software checks passed</div>
        <div class="facts">
          <span>Complete copies</span><b>${v.copies_complete} / ${s.inputs.count}</b>
          <span>Smallest gap between copies</span><b>${fmt(v.min_gap_mm)} mm</b>
          <span>First-layer height error</span><b>${fmt(Math.abs(v.first_layer_max_dev_mm || 0) * 1000, 1)} µm</b>
          <span>Lowest travel above printed parts</span><b>${fmt(v.min_transfer_clearance_mm)} mm</b>
          <span>Head spacing while printing together</span><b>${fmt(v.min_sync_head_gap_mm)} mm</b>
          <span>Smallest carriage spacing incl. parking</span><b>${fmt(v.min_carriage_gap_mm)} mm</b>
          <span>Heads parked, heaters off at the end</span><b>${v.final_parked && v.heaters_off ? "yes" : "no"}</b>
          <span>Layers completed per copy</span><b id="layers-per-copy">${layersText(v)}</b>
          <span>Heads print together from layer</span><b>${v.synchronized_from_layer ?? "–"}</b>
          ${v.parked_heads && v.parked_heads.length ? `<span>${v.parked_heads.join(", ")} stayed parked all print</span><b>${v.parked_heads_stayed ? "yes" : "no"}</b>` : ""}
        </div>
        <a class="download" id="download" href="${s.download_url}" download="${escapeHtml(s.filename)}">Download G-code</a>
        <p class="fine" id="dl-note" role="status"></p>
        <p class="fine">These are software checks of the generated file. It has not been tested on your printer. Watch the first layers of each head.</p>
        <p class="fine hash">File ${escapeHtml(s.filename)} · SHA-256 ${s.gcode_sha256}</p>`;
    } else {
      $("result-h").textContent = "Verification failed — download blocked";
      const items = (v.problems || []).map(p => `<li>${escapeHtml(p.text)}${p.detail ? "<small>" + escapeHtml(p.detail) + "</small>" : ""}</li>`).join("")
        || `<li>${escapeHtml(s.message || "The file did not pass verification.")}</li>`;
      body.innerHTML = `<p>${escapeHtml(s.message || "The generated file did not pass the independent verification, so it is not offered for download.")}</p>
        <ul class="problems">${items}</ul>`;
      els.gen.disabled = false;
    }
  }

  function layersText(v) {
    const l = v.layers_per_copy || [];
    if (!l.length) return "–";
    const mn = Math.min(...l), mx = Math.max(...l);
    return mn === mx ? `${mn} on all ${l.length}` : `${mn}–${mx}`;
  }

  function fmt(x, d = 2) { return (x === null || x === undefined || isNaN(x)) ? "–" : Number(x).toFixed(d); }

  loadExamples();
  resetResults();
})();
