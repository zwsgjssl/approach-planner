// 全空港×全滑走路×全Modeで計算結果ボードが空にならないこと、ページエラーが無いことを確認する回帰テスト
// 実行: npm install && npx playwright install chromium && node tests/regress.js
const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('file://' + path.resolve(__dirname, '..', 'approach_planner.html'), { waitUntil: 'load' });
  await page.waitForTimeout(300);
  await page.click('#menuBtn');
  await page.waitForTimeout(200);

  const airports = await page.$$eval('#airportSel option', opts => opts.map(o=>o.value));
  let checks = 0, failures = [];

  for(const ap of airports){
    await page.selectOption('#airportSel', ap);
    await page.waitForTimeout(80);
    const rwyOpts = await page.$$eval('#rwyEnd option', opts => opts.map(o=>o.value));
    for(const rwy of rwyOpts){
      await page.selectOption('#rwyEnd', rwy);
      await page.waitForTimeout(40);
      for(const modeSel of ['approach','pattern','departure']){
        const modeBtn = await page.$('#opMode button[data-v="'+modeSel+'"]');
        const visible = modeBtn ? await modeBtn.isVisible() : false;
        if(!visible) continue;
        const enabled = await modeBtn.isEnabled();
        if(enabled) await modeBtn.click();   // build187: LDA選択中はModeボタン無効(その状態のまま描画を確認)
        await page.waitForTimeout(40);
        checks++;
        const readoutHTML = await page.$eval('#readout', el=>el.innerHTML);
        if(readoutHTML.trim()===''){
          failures.push(`EMPTY READOUT: ap=${ap} rwy=${rwy} mode=${modeSel}`);
        }
      }
    }
  }
  console.log('total checks:', checks);
  console.log('failures:', failures.length);
  failures.slice(0,20).forEach(f=>console.log(f));
  console.log('pageerrors:', errors.length);
  if(failures.length || errors.length) process.exitCode = 1;
  errors.slice(0,20).forEach(e=>console.log(e));
  await browser.close();
})();
