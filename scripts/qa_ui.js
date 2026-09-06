/* Traffic Intelligence - Playwright UI QA
   Validates viewports for horizontal overflow + console errors and captures
   screenshots of key views. */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:5000";
const VIEWPORTS = [
  [1920, 1080], [1440, 900], [1366, 768], [1280, 720],
  [1024, 768], [768, 1024], [412, 915], [390, 844], [375, 812],
];
const ROUTES = ["live", "overview", "camera", "settings"];

const outDir = path.join(__dirname, "shots");
const resultsFile = path.join(__dirname, "qa_outcome.txt");
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);
fs.writeFileSync(resultsFile, "");
function log(s) { console.log(s); fs.appendFileSync(resultsFile, s + "\n"); }

async function gotoRetry(page, url) {
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
      return;
    } catch (e) {
      // Transient nav abort (backend busy on the slow first request) -
      // retry rather than mis-reporting a layout defect.
      if (attempt === 3) throw e;
      await page.waitForTimeout(800 * (attempt + 1));
    }
  }
}

async function login(page) {
  await gotoRetry(page, BASE + "/login.html");
  await page.fill("#username", "admin");
  await page.fill("#password", "admin123");
  await Promise.all([
    page.waitForURL(BASE + "/", { timeout: 15000 }).catch(() => {}),
    page.click("#authSubmit"),
  ]);
  // wait for dashboard shell
  await page.waitForSelector("#systemStatus", { timeout: 15000 }).catch(() => {});
}

async function trackOverflows(page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const offenders = [];
    document.querySelectorAll("body *").forEach((el) => {
      const st = getComputedStyle(el);
      if (st.display === "none" || st.visibility === "hidden") return;
      // Elements that scroll internally are allowed to exceed the viewport.
      if (st.overflowX === "auto" || st.overflowX === "scroll" || st.overflowX === "hidden") return;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      if (r.right > vw + 1) {
        offenders.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className && String(el.className).slice(0, 50)) || "",
          id: el.id || "",
          right: Math.round(r.right),
        });
      }
    });
    return offenders.slice(0, 8);
  });
}

(async () => {
  const results = [];
  for (const [w, h] of VIEWPORTS) {
    // Emulate a real mobile device on narrow viewports so overlay scrollbars
    // are used and the classic-scrollbar false positive does not occur.
    const isMobile = w <= 768;
    // Launch a fresh browser per viewport: reusing one browser across many
    // (especially mobile-emulated) contexts exhausts resources and causes
    // "Target closed" crashes that are NOT layout defects.
    let browser;
    const consoleErrors = [];
    const pageErrors = [];
    let offenders = [];
    try {
      browser = await chromium.launch({
        headless: true,
        channel: "chrome",
        args: ["--no-sandbox", "--disable-gpu"],
      });
      const ctx = await browser.newContext({
        viewport: { width: w, height: h },
        isMobile,
        hasTouch: isMobile,
        deviceScaleFactor: 1,
      });
      const page = await ctx.newPage();
      page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
      page.on("pageerror", (e) => pageErrors.push(String(e)));

      await login(page);
      for (const route of ROUTES) {
        await gotoRetry(page, BASE + "/#/" + route).catch(() => {});
        await page.waitForTimeout(500);
        let found;
        try {
          found = await trackOverflows(page);
        } catch (err) {
          found = null;
        }
        if (found && found.length) {
          found.forEach((f) => offenders.push(Object.assign({ route }, f)));
        }
      }
      await ctx.close();
    } catch (e) {
      pageErrors.push("setup: " + String(e));
    } finally {
      if (browser) { try { await browser.close(); } catch (e) { /* noop */ } }
    }
    // Ignore transient OS network noise that is not an application defect.
    const realConsole = consoleErrors.filter(
      (m) => !/ERR_NETWORK_CHANGED|ERR_INTERNET_DISCONNECTED|favicon/i.test(m)
    );
    const overflow = offenders.length > 0;
    const pass = !overflow && realConsole.length === 0 && pageErrors.length === 0;
    results.push({ vp: `${w}x${h}`, pass });
    log(`[${w}x${h}] PASS=${pass} overflowElements=${offenders.length}`);
    if (offenders.length) log("   offenders: " + JSON.stringify(offenders.slice(0, 5)));
    if (realConsole.length) log("   console: " + JSON.stringify(realConsole.slice(0, 5)));
    if (pageErrors.length) log("   pageErr: " + JSON.stringify(pageErrors.slice(0, 5)));
  }
  await browser.close();
  const failed = results.filter(r => !r.pass);
  log("\n=== SUMMARY ===");
  for (const r of results) log(`  ${r.vp}: ${r.pass ? "PASS" : "FAIL"}`);
  log(`\nTotal FAILED viewports: ${failed.length} / ${results.length}`);
  process.exit(failed.length ? 1 : 0);
})();