/* Verify one file-input change => exactly one POST /api/upload */
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto('http://127.0.0.1:5000/login.html');
  await page.fill('#username', 'admin');
  await page.fill('#password', 'admin123');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'load', timeout: 20000 }).catch(() => {}),
    page.click('button[type=submit]')
  ]);
  await page.waitForTimeout(1500);
  await page.waitForTimeout(3000);
  const posts = [];
  page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/api/upload')) posts.push(r.url()); });
  await page.setInputFiles('#fileInput', require('path').join(__dirname, 'fixture_traffic.mp4'));
  await page.waitForTimeout(5000);
  console.log('POST /api/upload count:', posts.length, posts);
  console.log(posts.length === 1 ? 'DUP-TEST: PASS' : 'DUP-TEST: FAIL');
  await browser.close();
  process.exit(posts.length === 1 ? 0 : 1);
})();

