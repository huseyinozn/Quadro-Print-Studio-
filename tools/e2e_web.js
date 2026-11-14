// End-to-end browser test of the Farm Mode web flow (Playwright, phone viewport).
// node tools/e2e_web.js BASE_URL PROFILE_ZIP MODEL_GCODE COUNT EXPECT(pass|fail|reject) OUT_DIR
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

(async () => {
  const [, , base, profile, model, count, expect, outDir] = process.argv;
  fs.mkdirSync(outDir, { recursive: true });
  const res = { base, count: Number(count), expect, steps: [] };
  const log = (s, extra) => { res.steps.push(Object.assign({ step: s, t: Date.now() }, extra || {})); console.log(s, extra ? JSON.stringify(extra) : ""); };
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...devices["iPhone 13"], acceptDownloads: true });
  const page = await ctx.newPage();
  await page.goto(base);
  log("opened");
  await page.screenshot({ path: path.join(outDir, "01_home.png"), fullPage: true });
  res.modes = await page.$$eval(".mode", ms => ms.map(m => m.innerText.replace(/\s+/g, " ").trim()));
  await page.setInputFiles("#profile", profile);
  await page.setInputFiles("#model", model);
  await page.fill("#count", String(count));
  await page.dispatchEvent("#count", "input");
  await page.click("#check");
  log("check clicked");
  if (expect === "reject") {
    await page.waitForSelector("#reject-card:not(.hidden)", { timeout: 120000 });
    res.problems = await page.$$eval("#problems li", ls => ls.map(l => l.innerText));
    res.alternative = (await page.isVisible("#alt")) ? await page.textContent("#use-alt") : null;
    await page.screenshot({ path: path.join(outDir, "02_reject.png"), fullPage: true });
    if (res.alternative) {
      await page.click("#use-alt");
      await page.waitForSelector("#layout-card:not(.hidden)", { timeout: 120000 });
      res.alternative_layout_shown = true;
      res.alternative_count_field = await page.inputValue("#count");
      await page.screenshot({ path: path.join(outDir, "03_alternative_layout.png"), fullPage: true });
    }
    res.ok = res.problems.length > 0;
  } else {
    await page.waitForSelector("#layout-card:not(.hidden)", { timeout: 120000 });
    log("layout shown", { sub: await page.textContent("#layout-sub") });
    res.phases = await page.$$eval("#phases button", bs => bs.map(b => b.textContent));
    res.copies_drawn = await page.$$eval("#bed polygon", ps => ps.length);
    res.deviating_marks = await page.$$eval("#bed circle", cs => cs.length);
    await page.screenshot({ path: path.join(outDir, "02_layout_all.png"), fullPage: true });
    await page.click("#phases button:nth-child(5)");
    await page.locator("#layout-card").screenshot({ path: path.join(outDir, "03_layout_head4_layers1-5.png") });
    await page.click("#phases button:nth-child(6)");
    res.copy_phase_note = await page.textContent("#phase-note");
    await page.locator("#layout-card").screenshot({ path: path.join(outDir, "04_layout_all_heads.png") });
    res.download_before_verify = await page.$$eval("a.download", a => a.length);
    await page.click("#generate");
    log("generate clicked");
    await page.waitForSelector("#result-card:not(.hidden)", { timeout: 1800000 });
    res.result_heading = await page.textContent("#result-h");
    res.download_links = await page.$$eval("a.download", a => a.length);
    await page.screenshot({ path: path.join(outDir, "05_result.png"), fullPage: true });
    if (expect === "pass") {
      const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#download")]);
      const p = path.join(outDir, "downloaded.gcode");
      await dl.saveAs(p);
      const sha = crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
      const shown = (await page.textContent("#result-body")).match(/SHA-256 ([0-9a-f]{64})/);
      res.downloaded_sha256 = sha;
      res.sha_matches_verified = !!shown && shown[1] === sha;
      res.downloaded_bytes = fs.statSync(p).size;
      fs.unlinkSync(p);
      // stale-result guard: changing the count must hide the old result and download
      await page.fill("#count", String(Number(count) - 4));
      await page.dispatchEvent("#count", "input");
      res.after_change_result_hidden = await page.isHidden("#result-card");
      res.after_change_download_links = await page.$$eval("a.download", a => a.length);
      res.ok = res.result_heading.includes("Software checks passed") && res.download_links === 1 && res.sha_matches_verified
        && res.after_change_result_hidden && res.after_change_download_links === 0 && res.download_before_verify === 0;
    } else {
      res.problems = await page.$$eval("#result-body li", ls => ls.map(l => l.innerText));
      const jobId = await page.evaluate(() => fetch("/api/health").then(() => null));
      res.ok = res.result_heading.includes("download blocked") && res.download_links === 0;
    }
  }
  await browser.close();
  fs.writeFileSync(path.join(outDir, "e2e_result.json"), JSON.stringify(res, null, 1));
  console.log("E2E", expect, res.ok ? "OK" : "FAILED");
  process.exit(res.ok ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
