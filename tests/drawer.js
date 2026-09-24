// 設定パネル(☰)の開閉と、claude.ai Artifact 公開用本文(tools/make_artifact_body.py の出力)の健全性を確認する。
// build213〜215で公開用本文から <header> が消え、☰が隠れる/アプリが止まる不具合があったための再発防止。
const { chromium } = require('playwright');
const path = require('path'), fs = require('fs'), os = require('os'), { execFileSync } = require('child_process');
(async () => {
  const root = path.resolve(__dirname, '..');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ap-'));
  const body = path.join(tmp, 'artifact_body.html');
  execFileSync('python3', [path.join(root, 'tools', 'make_artifact_body.py'), body]);
  // Artifact と同じく doctype/head/body の骨組みで包む。地図データは元フォルダから読むよう <base> を置く
  const skel = path.join(tmp, 'skel.html');
  fs.writeFileSync(skel, '<!doctype html><html><head><meta charset="utf-8"><base href="file://' + root + '/"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>' + fs.readFileSync(body, 'utf8') + '</body></html>');
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  let fail = 0;
  for (const file of [path.join(root, 'approach_planner.html'), skel]) {
    for (const vp of [{ width: 1180, height: 820 }, { width: 820, height: 1180 }]) {
      const p = await browser.newPage({ viewport: vp, hasTouch: true, isMobile: true });
      const errs = []; p.on('pageerror', e => errs.push(e.message));
      await p.goto('file://' + file); await p.waitForTimeout(1500);
      await p.click('#menuBtn'); await p.waitForTimeout(500);
      const r = await p.evaluate(() => {
        const c = document.getElementById('controls'), bt = document.getElementById('menuBtn').getBoundingClientRect();
        const hit = document.elementFromPoint(bt.x + bt.width / 2, bt.y + bt.height / 2);
        return { header: !!document.querySelector('header'), visible: getComputedStyle(c).visibility === 'visible',
                 menuOnTop: !!hit && hit.id === 'menuBtn', img: [...document.querySelectorAll('.rotwrap img')].some(i => i.naturalWidth > 0) };
      });
      await p.click('#menuBtn'); await p.waitForTimeout(400);
      r.closed = !(await p.evaluate(() => document.getElementById('controls').classList.contains('open')));
      const ok = r.header && r.visible && r.menuOnTop && r.closed && r.img && !errs.length;
      if (!ok) fail++;
      console.log(ok ? 'OK  ' : 'FAIL', path.basename(file), vp.width + 'x' + vp.height, JSON.stringify(r), errs.join(' | '));
      await p.close();
    }
  }
  await browser.close();
  console.log('failures:', fail);
  if (fail) process.exitCode = 1;
})();
