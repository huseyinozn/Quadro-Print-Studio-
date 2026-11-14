// Browser acceptance test of Dual Color Copy Mode on a phone viewport.
// node tools/e2e_dual.js BASE PROFILE_ZIP OUT_DIR example|upload [MODEL_GCODE]
//   example: Two-Color Fidgets example + profile  -> expected: refused, preview + reasons, no generate
//   upload:  own 4-tool model + profile            -> expected: generate, verify, download
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

(async () => {
  const [, , base, profile, outDir, how, model] = process.argv;
  fs.mkdirSync(outDir, { recursive: true });
  const r = { how };
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...devices["iPhone 13"], acceptDownloads: true });
  const page = await ctx.newPage();
  await page.goto(base);
  await page.waitForSelector(".ex[data-id=fidgets-2c] button");
  r.mode_cards = await page.$$eval(".mode", ms => ms.map(m => m.innerText.replace(/\s+/g, " ").trim()));
  r.example_tags = await page.$$eval(".ex", es => es.map(e => e.querySelector("h3").textContent + ": " + e.querySelector(".tag").textContent));
  if (how === "example") {
    await page.click(".ex[data-id=fidgets-2c] button");
    r.model_input_visible = await page.isVisible("#model");
    r.chosen = await page.textContent("#chosen-title");
  } else {
    await page.click("#own-model");
    await page.check("#mode-dual");
    await page.setInputFiles("#model", model);
    r.model_input_visible = true;
  }
  r.mode_dual_checked = await page.isChecked("#mode-dual");
  r.dual_fixed_text = await page.textContent("#dual-fixed");
  await page.setInputFiles("#profile", profile);
  await page.screenshot({ path: path.join(outDir, "01_setup.png"), fullPage: true });
  await page.click("#check");
  await page.waitForSelector("#layout-card:not(.hidden)", { timeout: 300000 });
  await page.waitForTimeout(300);
  r.layout_sub = await page.textContent("#layout-sub");
  r.phases = await page.$$eval("#phases button", bs => bs.map(b => b.textContent));
  r.layers = await page.$$eval("#layer-pick button", bs => bs.map(b => b.textContent));
  r.region_paths = await page.$$eval("#bed path[data-head]", ps => ps.map(p => p.getAttribute("data-head")));
  r.mapping = await page.$$eval("#mapping table.map tbody tr", rs => rs.map(x => x.innerText.replace(/\s+/g, " ").trim()));
  r.diff_marks = await page.$$eval("#bed rect[data-diff]", ps => ps.length);
  r.legend = await page.textContent("#legend");
  r.reject_visible = await page.isVisible("#reject-card");
  r.generate_visible = await page.isVisible("#generate");
  await page.screenshot({ path: path.join(outDir, "02_layout.png"), fullPage: true });
  await page.locator("#layout-card").screenshot({ path: path.join(outDir, "03_layout_card.png") });
  // tap the right model's colour-2 region (T3)
  await page.locator("#bed polygon[data-region='3']").click({ force: true });
  r.region_tap = await page.textContent("#copy-info");
  await page.click("#phases button[data-phase=B]");
  r.phase_b_note = await page.textContent("#phase-note");
  await page.locator("#layout-card").screenshot({ path: path.join(outDir, "04_pair_B.png") });
  if (how === "example") {
    r.problems = await page.$$eval("#problems li", ls => ls.map(l => l.innerText.replace(/\s+/g, " ").trim()));
    r.why = await page.$$eval("#mapping .why, #mapping .okb", ls => ls.map(l => l.innerText.replace(/\s+/g, " ").trim()));
    r.z_rows = await page.$$eval("#mapping table.zf tbody tr", rs => rs.length);
    await page.locator("#mapping table.zf [data-goto]").first().click();
    await page.waitForTimeout(300);
    r.z_layer_shown = await page.textContent("#layer-pick button[aria-selected=true]");
    r.z_markers = await page.$$eval("#bed circle[data-zfind]", cs => cs.length);
    await page.locator("#layout-card").screenshot({ path: path.join(outDir, "06_z_finding_layer.png") });
    await page.locator("#mapping").screenshot({ path: path.join(outDir, "07_mapping_z_y.png") });
    await page.locator("#reject-card").screenshot({ path: path.join(outDir, "05_refused_reasons.png") });
    const dl = await page.request.get(base + "/api/download/nonexistent");
    r.download_blocked_status = dl.status();
    r.ok = r.mode_dual_checked && !r.model_input_visible && r.reject_visible && !r.generate_visible
      && r.region_paths.length === 4 && r.mapping.length === 4 && !r.problems.some(p => p.includes("not coloured the same way"))
      && r.problems.some(p => p.startsWith("Height (Z)")) && r.problems.some(p => p.startsWith("Colour alignment (Y)"))
      && r.z_rows > 0 && r.z_markers > 0;
  } else {
    await page.click("#generate");
    await page.waitForSelector("#result-card:not(.hidden)", { timeout: 1800000 });
    r.result_heading = await page.textContent("#result-h");
    r.facts = await page.textContent(".facts");
    r.regions_text = await page.textContent("#dual-regions");
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#download")]);
    const p = path.join(outDir, "downloaded.gcode");
    await dl.saveAs(p);
    r.downloaded_sha256 = crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
    r.sha_matches_verified = (await page.textContent("#result-body")).includes(r.downloaded_sha256);
    r.filename = dl.suggestedFilename();
    fs.unlinkSync(p);
    await page.screenshot({ path: path.join(outDir, "05_result.png"), fullPage: true });
    await page.locator("#result-card").screenshot({ path: path.join(outDir, "06_result_card.png") });
    r.ok = r.mode_dual_checked && !r.reject_visible && r.region_paths.length === 4 && r.mapping.length === 4
      && r.result_heading.includes("Software checks passed") && r.sha_matches_verified
      && /T0 \d+\/\d+ · T1 (\d+)\/\1/.test(r.regions_text);
  }
  fs.writeFileSync(path.join(outDir, "e2e_result.json"), JSON.stringify(r, null, 1));
  await browser.close();
  console.log("E2E dual", how, r.ok ? "OK" : "FAILED");
  process.exit(r.ok ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
