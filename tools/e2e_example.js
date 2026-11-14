// Browser acceptance test of the "40 Sharks" example flow on a phone viewport.
// node tools/e2e_example.js BASE PROFILE_ZIP EXPECTED_MARKS_JSON OUT_DIR [layout-only]
// EXPECTED_MARKS_JSON: ["head/slot", ...] taken from the planner for the same inputs.
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

(async () => {
  const [, , base, profile, expectedPath, outDir, mode] = process.argv;
  const expected = JSON.parse(fs.readFileSync(expectedPath, "utf8")).sort();
  fs.mkdirSync(outDir, { recursive: true });
  const r = { expected_marks: expected };
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...devices["iPhone 13"], acceptDownloads: true });
  const page = await ctx.newPage();
  const asked = new Set();
  page.on("filechooser", fc => asked.add(fc.element ? "chooser" : "chooser"));
  await page.goto(base);
  await page.waitForSelector(".ex[data-id=sharks-40] button");
  r.example_cards = await page.$$eval(".ex", cs => cs.map(c => c.innerText.replace(/\s+/g, " ").trim()));
  await page.screenshot({ path: path.join(outDir, "01_examples.png"), fullPage: true });
  await page.click(".ex[data-id=sharks-40] button");
  r.count_after_select = await page.inputValue("#count");
  r.model_input_visible = await page.isVisible("#model");
  r.example_chosen_visible = await page.isVisible("#example-chosen");
  await page.setInputFiles("#profile", profile);          // the ONLY file the user provides
  r.check_enabled = await page.isEnabled("#check");
  await page.click("#check");
  await page.waitForSelector("#layout-card:not(.hidden), #reject-card:not(.hidden)", { timeout: 180000 });
  r.layout_sub = await page.textContent("#layout-sub");
  r.marks = (await page.$$eval("#bed [data-mesh-mark]", ps => ps.map(p => p.getAttribute("data-mesh-mark")))).sort();
  r.marks_equal_planner = JSON.stringify(r.marks) === JSON.stringify(expected);
  r.copies = await page.$$eval("#bed polygon[data-copy]", ps => ps.length);
  await page.screenshot({ path: path.join(outDir, "02_layout.png"), fullPage: true });
  if (expected.length) {
    const [h, s] = expected[0].split("/");
    await page.click(`#bed polygon[data-copy="${expected[0]}"]`);   // tap the copy itself, as a user would
    r.copy_info = await page.textContent("#copy-info");
    r.copy_info_has_both = /first layer/i.test(r.copy_info) && /end of layer 5/i.test(r.copy_info);
    r.copy_info_no_error_wording = !/error|fault|mistake/i.test(r.copy_info);
    await page.locator("#layout-card").screenshot({ path: path.join(outDir, "03_copy_details.png") });
  } else {
    await page.click("#bed polygon[data-copy]");
    r.copy_info = await page.textContent("#copy-info");
    r.copy_info_has_both = true;
    r.copy_info_no_error_wording = !/error|fault|mistake/i.test(r.copy_info);
  }
  let ok = r.count_after_select === "40" && !r.model_input_visible && r.example_chosen_visible && r.check_enabled
    && r.copies === 40 && r.marks_equal_planner && r.copy_info_has_both && r.copy_info_no_error_wording;
  if (mode !== "layout-only") {
    await page.click("#generate");
    await page.waitForSelector("#result-card:not(.hidden)", { timeout: 1800000 });
    r.result_heading = await page.textContent("#result-h");
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#download")]);
    const p = path.join(outDir, "downloaded.gcode");
    await dl.saveAs(p);
    r.downloaded_sha256 = crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
    r.sha_matches_verified = (await page.textContent("#result-body")).includes(r.downloaded_sha256);
    r.downloaded_bytes = fs.statSync(p).size;
    fs.unlinkSync(p);
    await page.screenshot({ path: path.join(outDir, "04_result.png"), fullPage: true });
    ok = ok && r.result_heading.includes("Software checks passed") && r.sha_matches_verified;
  }
  r.file_choosers_opened_by_page = asked.size;   // the test never opens a model chooser
  r.ok = ok;
  fs.writeFileSync(path.join(outDir, "e2e_result.json"), JSON.stringify(r, null, 1));
  await browser.close();
  console.log("E2E example", ok ? "OK" : "FAILED");
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
