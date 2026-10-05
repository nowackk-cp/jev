/* Developer smoke checks against an isolated, headless demo browser.
 * NODE_PATH must point at a Playwright installation. A local docs server must
 * run at DEMO_URL (default http://127.0.0.1:8088/docs/).
 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.DEMO_URL || 'http://127.0.0.1:8088/docs/';
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.demo-test');
fs.mkdirSync(output, {recursive:true});

(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1120},deviceScaleFactor:1});
  const errors = [], network = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => {if(m.type()==='error')errors.push(m.text()+' '+JSON.stringify(m.location()));});
  page.on('requestfailed', r => console.error('Request failed:',r.url(),r.failure()));
  page.on('request', r => network.push(r.url()));
  await page.goto(base+'?step=6&paused=1');
  await page.locator('#stage .actor').first().waitFor();
  assert.equal(await page.locator('#kanban [data-col="running"] .card').count(),2);
  assert.equal(await page.locator('#kanban [data-col="pending"] .card').count(),1);
  await page.locator('#btn-plan').click();
  await page.locator('#dlg-plan[open]').waitFor();
  await page.locator('#dlg-plan').getByRole('heading',{name:/Başarı ölçütleri/}).waitFor();
  assert.match(await page.locator('#dlg-plan').innerText(),/Başarı ölçütleri/);
  await page.locator('#dlg-plan [data-close]').first().click();
  await page.locator('.plate[data-agent="sol"]').click();
  await page.locator('#dlg-agent[open]').waitFor();
  await page.locator('#dlg-agent').getByRole('heading',{name:/Sol/}).waitFor();
  assert.match(await page.locator('#dlg-agent').innerText(),/Sol/);
  await page.locator('#dlg-agent [data-close]').first().click();
  await page.locator('.card[data-task="T01"]').click();
  await page.locator('#dlg-task[open]').waitFor();
  assert.match(await page.locator('#dlg-task').innerText(),/sicaklik.py/);
  await page.locator('#dlg-task [data-close]').first().click();
  await page.locator('#btn-theme').click();
  await page.locator('#btn-theme').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  await page.locator('#btn-theme').click();
  await page.screenshot({path:path.join(root,'docs/assets/office-preview.png'),fullPage:true});
  await page.locator('#demo-speed').selectOption('4');
  await page.locator('#demo-pause').click();
  await page.locator('#kanban [data-col="verifying"] .card').first().waitFor({timeout:6000});
  await page.locator('#demo-pause').click();
  assert.match(await page.locator('#demo-step').innerText(),/duraklatıldı/);
  await page.locator('#demo-restart').click();
  await page.waitForURL(/scenario=parallel/);
  assert.equal(await page.locator('#demo-progress').getAttribute('value'),'0');
  for(const [scenario,end,total] of [['parallel',28,3],['retry',30,3],['mini',15,1]]) {
    await page.goto(base+`?scenario=${scenario}&step=${end}&paused=1`);
    await page.locator('#controls[data-kind="reported"]').waitFor();
    assert.equal(await page.locator('#kanban [data-col="done"] .card').count(),total);
    await page.locator('#btn-report').click();
    await page.locator('#dlg-report[open]').waitFor();
    await page.locator('#dlg-report').getByText('Bu rapor bir örnektir; gerçek model veya test çalıştırılmadı.',{exact:true}).waitFor();
    assert.match(await page.locator('#dlg-report').innerText(),/gerçek model veya test çalıştırılmadı/);
    await page.locator('#dlg-report [data-close]').first().click();
    if(scenario==='retry') {
      await page.locator('#tab-dec').click();
      assert.match(await page.locator('#decs').innerText(),/Silme işlemi/);
      await page.locator('.card[data-task="T01"]').click();
      assert.match(await page.locator('#dlg-task').innerText(),/Doğrulama başarısız/);
      await page.locator('#dlg-task [data-close]').first().click();
    }
    if(scenario==='mini') assert.equal(await page.locator('#btn-plan').isEnabled(),false);
  }
  await page.locator('#btn-report').click();
  await page.locator('#fix-note').fill('Kullanım örneği ekle.');
  await page.getByRole('button',{name:'Düzelt',exact:true}).click();
  await page.locator('#controls[data-kind="reported"]').waitFor({timeout:8000});
  await page.locator('#btn-report').click();
  await page.locator('#report-round').waitFor({state:'visible'});
  assert.match(await page.locator('#dlg-report-h').innerText(),/Tur 2/);
  await page.locator('#report-round').selectOption('1');
  await page.waitForFunction(()=>document.querySelector('#dlg-report-h').textContent.includes('Tur 1'));
  await page.locator('#dlg-report [data-close]').first().click();
  await page.setViewportSize({width:390,height:844});
  await page.goto(base+'?scenario=retry&step=10&paused=1');
  await page.locator('#stage .actor').first().waitFor();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile page overflows horizontally');
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  // Record the real animated scene for the README (no invented screenshots).
  await page.setViewportSize({width:1200,height:1000});
  await page.goto(base+'?scenario=parallel&speed=4');
  await page.locator('#stage .actor').first().waitFor();
  for(let i=0;i<28;i++) {
    await page.screenshot({path:path.join(output,`frame-${String(i).padStart(2,'0')}.png`)});
    await page.waitForTimeout(500);
  }
  assert.ok(network.every(u=>!new URL(u).pathname.startsWith('/api/')),'Demo contacted a backend');
  assert.deepEqual(errors,[],'Browser errors');
  console.log('PASS: parallel / retry / mini, playback, theme, agent/task dialogs, plan/report, correction, report history, mobile layout, no backend requests.');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
