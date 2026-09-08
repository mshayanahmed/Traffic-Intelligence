/* Traffic Intelligence - responsive layout QA
   Validates horizontal overflow + console errors across mobile (320/375/430)
   and desktop (1920) viewports using the CURRENT auth flow (email/password),
   then exercises mobile sign-out and desktop sign-out. Run against a local
   backend at 127.0.0.1:5000 with a provisioned admin account. */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:5000";
const ADMIN_EMAIL = "admin@traffic-intelligence.com";
const ADMIN_PASSWORD = "QaAdminPass1";

const VIEWPORTS = [
  { w: 320, h: 568, mobile: true },
  { w: 375, h: 812, mobile: true },
  { w: 430, h: 915, mobile: true },
  { w: 1920, h: 1080, mobile: false },
];
// Optional single-viewport filter: node qa_responsive.js 375
const only = process.argv[2];
const VIEWPORTS_RUN = only ? VIEWPORTS.filter((v) => String(v.w) === only) : VIEWPORTS;
const ROUTES = ["live", "camera", "overview", "heatmap", "violations", "evidence", "sessions", "settings", "reports", "admin"];

const resFile = path.join(__dirname, "qa_responsive.txt");
fs.writeFileSync(resFile, "");
function log(s) { console.log(s); fs.appendFileSync(resFile, s + "\n"); }

async function gotoRetry(page, url) {
  for (let i = 0; i < 6; i++) {
    try { await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 }); return true; }
    catch (e) { if (i === 5) return false; await page.waitForTimeout(600 * (i + 1)); }
  }
}
async function login(page) {
  if (!(await gotoRetry(page, BASE + "/login.html"))) return false;
  try {
    await page.waitForSelector("#loginEmail", { timeout: 20000 });
    await page.fill("#loginEmail", ADMIN_EMAIL);
    await page.fill("#loginPassword", ADMIN_PASSWORD);
    // Use Promise.all to avoid a click that races the nav.
    await Promise.all([
      page.waitForURL(BASE + "/", { timeout: 25000 }).catch(() => {}),
      page.click("#step-login button[type=submit]"),
    ]);
    await page.waitForSelector("#systemStatus", { timeout: 25000 }).catch(() => {});
    return true;
  } catch (e) { return false; }
}
async function overflows(page) {
  try {
    return await page.evaluate(() => {
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
  for (const vp of VIEWPORTS_RUN) {
    let browser;
    const offenders = [];
    const cons = [];
    const errs = [];
    let loginOk = false;
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
          await page.waitForTimeout(500);
          const f = await overflows(page);
          if (f && f.pageOverflow) {
            offenders.push(route + "[body=" + f.bodyScrollW + "/vw=" + f.vw + "]");
          }
        }

        // Mobile sign-out: the tab bar must expose a working Sign out button.
        if (vp.mobile) {
          const logoutBtn = await page.$("#logoutMobile");
          if (!logoutBtn) {
            errs.push("mobile: #logoutMobile (Sign out) not present in tab bar");
          } else {
            try {
              await Promise.all([
                page.waitForURL(/login/, { timeout: 20000 }).catch(() => {}),
                page.click("#logoutMobile"),
              ]);
              const endedAtLogin = await page.url().includes("login");
              if (!endedAtLogin) errs.push("mobile: sign-out did not redirect to login");
            } catch (e) { errs.push("mobile: sign-out click failed"); }
          }
        } else {
          // Desktop sign-out via account menu.
          const logoutBtn = await page.$("#logoutBtn");
          if (!logoutBtn) { errs.push("desktop: #logoutBtn not present"); }
        }
      } else {
        errs.push("login failed");
      }
      await ctx.close();
    } catch (e) {
      errs.push("launch/run: " + String(e));
    } finally {
      if (browser) { try { await browser.close(); } catch (e) { /* noop */ } }
    }
    const realCons = cons.filter((m) => !/ERR_NETWORK_CHANGED|favicon|fonts\.gstatic/i.test(m));
    const pass = loginOk && offenders.length === 0 && realCons.length === 0 && errs.length === 0;
    results.push({ vp: `${vp.w}x${vp.h}`, pass });
    log(`[${vp.w}x${vp.h}] PASS=${pass} login=${loginOk} overflow=${offenders.length}`);
    if (offenders.length) log("   overflow: " + JSON.stringify(offenders));
    if (realCons.length) log("   console: " + JSON.stringify(realCons.slice(0, 4)));
    if (errs.length) log("   err: " + JSON.stringify(errs.slice(0, 4)));
  }
  log("\n=== RESPONSIVE SUMMARY ===");
  for (const r of results) log(`  ${r.vp}: ${r.pass ? "PASS" : "FAIL"}`);
  log("Total FAILED: " + results.filter((r) => !r.pass).length + "/" + results.length);
})();