// Service Worker の検証: 機内Wi-Fiのような遅い回線・途中切断・ログイン画面・オフラインでも、保存済みの版で
// すぐ起動し、壊れた版に置き換わらないこと。正常な回線に戻ったら新しい版が保存され、次回起動で反映されること。
const {chromium}=require('playwright');
const fs=require('fs'), path=require('path'), os=require('os'), {spawn}=require('child_process');
const repo=path.resolve(__dirname,'..'), site=fs.mkdtempSync(path.join(os.tmpdir(),'ap-site-')), modeFile=path.join(site,'.mode'), PORT=8766;
fs.mkdirSync(path.join(site,'icons'));
fs.copyFileSync(path.join(repo,'approach_planner.html'),path.join(site,'index.html'));
for(const f of fs.readdirSync(repo)) if(/^(approach_planner\.html|sw\.js|manifest\.webmanifest|map_data_.*\.js)$/.test(f)) fs.copyFileSync(path.join(repo,f),path.join(site,f));
for(const f of fs.readdirSync(path.join(repo,'icons'))) fs.copyFileSync(path.join(repo,'icons',f),path.join(site,'icons',f));
const srv=spawn('python3',[path.join(__dirname,'slow_server.py'),site,modeFile,String(PORT)],{stdio:'ignore'});
const curBuild=+/build (\d+)<\/span><\/h1>/.exec(fs.readFileSync(path.join(repo,'approach_planner.html'),'utf8'))[1];
const setMode=m=>fs.writeFileSync(modeFile,m);
const setBuild=n=>{for(const f of ['index.html','approach_planner.html']){const p=path.join(site,f);fs.writeFileSync(p,fs.readFileSync(p,'utf8').replace(/build \d+<\/span><\/h1>/,'build '+n+'</span></h1>'));}};
(async()=>{await new Promise(r=>setTimeout(r,800));const b=await chromium.launch(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{});
const ctx=await b.newContext({viewport:{width:1180,height:820}});
const errs=[];
async function openPage(label,wait=2500){
  const p=await ctx.newPage();p.on('pageerror',e=>errs.push(label+': '+e.message));
  const t0=Date.now();
  let ok=true;
  try{ await p.goto('http://127.0.0.1:8766/',{waitUntil:'domcontentloaded',timeout:15000}); }catch(e){ ok=false; }
  const tl=Date.now()-t0;
  await p.waitForTimeout(wait);
  const info=ok?await p.evaluate(async()=>{const h=document.querySelector('header h1');const c=await caches.open('ap-app-v1');const r=await c.match('./');const txt=r?await r.text():'';const m=/build (\d+)<\/span>/.exec(txt);const mc=await caches.open('ap-map-v1');
    return {shown:h?h.textContent.match(/build \d+/)[0]:null,cached:m?m[1]:null,maps:(await mc.keys()).length,img:[...document.querySelectorAll('.rotwrap img')].some(i=>i.naturalWidth>0),toast:(document.getElementById('swUpdateToast')||{}).textContent||''}}).catch(e=>({err:e.message})):{};
  console.log(label.padEnd(28),'loadMs',tl,JSON.stringify(info));
  return p;
}
setMode('normal');
let p=await openPage('1 first visit',9000); await p.close();
setBuild(curBuild+1);
setMode('slow'); p=await openPage('2 slow wifi, new build'); await p.close();
setMode('truncated'); p=await openPage('3 truncated'); await p.close();
setMode('portal302'); p=await openPage('4 captive portal 302'); await p.close();
setMode('portal200'); p=await openPage('5 captive portal 200'); await p.close();
setMode('offline'); p=await openPage('6 offline'); await p.close();
setMode('normal'); p=await openPage('7 normal -> update saved',3000); await p.close();
p=await openPage('8 reopen'); 
await p.selectOption('#airportSel','fukuoka').catch(()=>{}); await p.waitForTimeout(2000);
console.log('   fukuoka img', await p.evaluate(()=>[...document.querySelectorAll('.rotwrap img')].some(i=>i.naturalWidth>0)));
console.log('errors',errs);
await b.close(); srv.kill();})();
