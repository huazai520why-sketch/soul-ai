// Soul 网页版只读探测：登录方式 / 登录态持久化痕迹 / 接口
// 不输入任何凭据、不点击登录
const { chromium } = require('playwright');

const PROFILE = 'D:/AI/pl/_soulweb/profile';
const OUT = 'D:/AI/pl/_soulweb';

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    headless: true,
    viewport: { width: 1366, height: 900 },
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const reqs = [];
  page.on('response', (r) => {
    const u = r.url();
    if (/soulapp/.test(u)) reqs.push(r.status() + ' ' + u.replace(/^https?:\/\//, '').slice(0, 140));
  });

  let err = null;
  try {
    await page.goto('https://home.soulapp.cn/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(6000);
  } catch (e) {
    err = String(e).slice(0, 300);
  }

  let title = '', text = '', ls = [], ss = [], cookies = [], href = page.url();
  try {
    title = await page.title();
    text = (await page.innerText('body')).slice(0, 2000);
    ls = await page.evaluate(() => Object.keys(localStorage).map((k) => k + ' = ' + String(localStorage.getItem(k)).slice(0, 80)));
    ss = await page.evaluate(() => Object.keys(sessionStorage).map((k) => k + ' = ' + String(sessionStorage.getItem(k)).slice(0, 80)));
    cookies = (await ctx.cookies()).map((c) => c.name + ' @' + c.domain + ' httpOnly=' + c.httpOnly + ' exp=' + (c.expires && c.expires > 0 ? new Date(c.expires * 1000).toISOString().slice(0, 10) : 'session'));
  } catch (e) {
    err = (err ? err + ' | ' : '') + 'read:' + String(e).slice(0, 200);
  }

  try { await page.screenshot({ path: OUT + '/login.png' }); } catch (e) {}

  console.log(JSON.stringify({ href, title, text, err, localStorage: ls, sessionStorage: ss, cookies, reqs: [...new Set(reqs)].slice(0, 45) }, null, 1));
  await ctx.close();
})();
