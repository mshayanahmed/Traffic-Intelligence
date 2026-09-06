/* Focused mobile/tablet QA - one browser per viewport, retry on crash. */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const BASE = "http://127.0.0.1:5000";
// Accept "WxH,mobile|desktop" as argv[2]; default runs one viewport at a time.
const arg = process.argv[2];
const VIEWPORTS = arg
  ? (function () {
      const [w, h, kind] = arg.split(",");
      return [{ w: +w, h: +h, mobile: kind !== "desktop" }];
    })()
  : [
      { w: 1024, h: 768, mobile: false },
      { w: 768, h: 1024, mobile: true },
      { w: 412, h: 915, mobile: true },
      { w: 390, h: 844, mobile: true },
      { w: 375, h: 812, mobile: true },
    ];
const ROUTES = ["live", "overview", "camera", "settings"];
const resFile = path.join(__dirname, "qa_mobile" + (arg ? "_" + arg.replace(/[^\w]/g, "") : "") + ".txt");
fs.writeFileSync(resFile, "");
function log(s) { console.log(s); fs.appendFileSync(resFile, s + "\n"); }

async function gotoRetry(page, url) {
  for (let i = 0; i < 5; i++) {
    try { await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 }); return true; }
    catch (e) { if (i === 4) return false; await page.waitForTimeout(700 * (i + 1)); }
  }
}
async function login(page) {
  if (!(await gotoRetry(page, BASE + "/login.html"))) return false;
  try {
    await page.waitForSelector("#username", { timeout: 15000 });
    await page.fill("#username", "admin");
    await page.fill("#password", "admin123");
    await Promise.all([
      page.waitForURL(BASE + "/", { timeout: 15000 }).catch(() => {}),
      page.click("#authSubmit"),
    ]);
    await page.waitForSelector("#systemStatus", { timeout: 15000 }).catch(() => {});
    return true;
  } catch (e) { return false; }
}
async function overflows(page) {
  try {
    return await page.evaluate(() => {
      // Authoritative real page-overflow: with mobile overlay scrollbars,
      // clientWidth == innerWidth, so a larger body width means the page
      // genuinely scrolls horizontally (an actual defect).
      const innerW = window.innerWidth;
      return {
        pageOverflow: document.body.scrollWidth > innerW || document.documentElement.scrollWidth > innerW,
        bodyScrollW: document.body.scrollWidth,
        vw: innerW,
      };
    });
  } catch (e) { return null; }
}

(async () => {
  const results = [];
  for (const vp of VIEWPORTS) {
    let browser;
    const offenders = [];
    const cons = [];
    const errs = [];
    let loginOk = false;
    // Up to 2 browser launches per viewport (crashes are tooling flakiness).
    for (let attempt = 0; attempt < 2 && !loginOk; attempt++) {
      try {
        browser = await chromium.launch({ headless: true, channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
        const ctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h }, isMobile: vp.mobile, hasTouch: vp.mobile });
        const page = await ctx.newPage();
        page.on("console", (m) => { if (m.type() === "error") cons.push(m.text()); });
        page.on("pageerror", (e) => errs.push(String(e)));
        loginOk = await login(page);
        if (loginOk) {
          for (const route of ROUTES) {
            await gotoRetry(page, BASE + "/#/" + route);
            await page.waitForTimeout(400);
            const f = await overflows(page);
            if (f && f.pageOverflow) {
              offenders.push(route + "[body=" + f.bodyScrollW + "/vw=" + f.vw + "]");
            }
          }
        }
        await ctx.close();
      } catch (e) {
        errs.push("launch: " + String(e));
      } finally {
        if (browser) { try { await browser.close(); } catch (e) { /* noop */ } }
      }
    }
    const realCons = cons.filter((m) => !/ERR_NETWORK_CHANGED|favicon/i.test(m));
    const pass = offenders.length === 0 && realCons.length === 0 && errs.length === 0;
    results.push({ vp: `${vp.w}x${vp.h}`, pass });
    log(`[${vp.w}x${vp.h}] PASS=${pass} overflow=${offenders.length} login=${loginOk}`);
    if (offenders.length) log("   offenders: " + JSON.stringify(offenders));
    if (realCons.length) log("   console: " + JSON.stringify(realCons.slice(0, 4)));
    if (errs.length) log("   err: " + JSON.stringify(errs.slice(0, 3)));
  }
  const failed = results.filter((r) => !r.pass);
  log("\n=== MOBILE/TABLET SUMMARY ===");
  for (const r of results) log(`  ${r.vp}: ${r.pass ? "PASS" : "FAIL"}`);
  log(`Total FAILED: ${failed.length}/${results.length}`);
})();