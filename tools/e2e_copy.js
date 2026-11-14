// Browser acceptance test of Copy Mode on a phone viewport.
// node tools/e2e_copy.js BASE PROFILE_ZIP OUT_DIR example|upload [MODEL_GCODE]
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
  await page.waitForSelector(".ex[data-id=orcacube] button");
  r.mode_cards = await page.$$eval(".mode", ms => ms.map(m => m.innerText.replace(/\s+/g, " ").trim()));
  if (how === "example") {
    await page.click(".ex[data-id=orcacube] button");
    r.model_input_visible = await page.isVisible("#model");
    r.chosen = await page.textContent("#chosen-title");
  } else {
    await page.click("#own-model");
    await page.check("#mode-copy");
    await page.setInputFiles("#model", model);
    r.model_input_visible = true;
  }
  r.mode_copy_checked = await page.isChecked("#mode-copy");
  r.stepper_visible = await page.isVisible("#stepper");
  r.copy_fixed_text = await page.textContent("#copy-fixed");
  await page.setInputFiles("#profile", profile);
  await page.screenshot({ path: path.join(outDir, "01_setup.png"), fullPage: true });
  await page.click("#check");
  await page.waitForSelector("#layout-card:not(.hidden), #reject-card:not(.hidden)", { timeout: 180000 });
  r.layout_sub = await page.textContent("#layout-sub");
  r.phases = await page.$$eval("#phases button", bs => bs.map(b => b.textContent));
  r.copies = await page.$$eval("#bed polygon[data-copy]", ps => ps.map(p => p.getAttribute("data-copy")));
  r.legend = await page.textContent("#legend");
  await page.screenshot({ path: path.join(outDir, "02_layout.png"), fullPage: true });
  await page.locator("#layout-card").screenshot({ path: path.join(outDir, "03_layout_card.png") });
  await page.click("#generate");
  await page.waitForSelector("#result-card:not(.hidden)", { timeout: 1800000 });
  r.result_heading = await page.textContent("#result-h");
  r.facts = await page.textContent(".facts");
  r.layers_per_copy = await page.textContent("#layers-per-copy");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#download")]);
  const p = path.join(outDir, "downloaded.gcode");
  await dl.saveAs(p);
  r.downloaded_sha256 = crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
  r.sha_matches_verified = (await page.textContent("#result-body")).includes(r.downloaded_sha256);
  r.filename = dl.suggestedFilename();
  fs.unlinkSync(p);
  await page.screenshot({ path: path.join(outDir, "04_result.png"), fullPage: true });
  await page.locator("#result-card").screenshot({ path: path.join(outDir, "05_result_card.png") });
  r.ok = r.mode_copy_checked && !r.stepper_visible && r.copies.length === 2 && r.copies.includes("0/0") && r.copies.includes("1/0")
    && r.result_heading.includes("Software checks passed") && r.sha_matches_verified && (how !== "example" || !r.model_input_visible);
  fs.writeFileSync(path.join(outDir, "e2e_result.json"), JSON.stringify(r, null, 1));
  await browser.close();
  console.log("E2E copy", how, r.ok ? "OK" : "FAILED");
  process.exit(r.ok ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
