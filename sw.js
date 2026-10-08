// Approach Planner — Service Worker(GitHub Pages 用)
//
// build219: 機内Wi-Fiのような極端に遅い/不安定な回線でも、起動が止まったり壊れた版に置き換わったり
// しないようにした。
//   - アプリ本体(HTML)は「保存済みの版を即表示」し、更新確認は裏で行う(stale-while-revalidate)。
//     回線がどれだけ遅くても起動は待たされない。
//   - 裏で取ってきた新しい版は、最後まで受信できて中身がアプリ本体だと確認できたときだけ保存する
//     (途中で切れたもの・Wi-Fiのログイン画面などに差し替わったもの・リダイレクトされたものは捨てる)。
//   - 地図データ(map_data_<空港>.js)は保存済みを優先。新しく取るときも中身を確認してから保存する。
//     地図データを作り直したときは MAP_CACHE の番号を上げる。
// build236:
//   - 地図の保存番号を上げても、新しい地図が全部そろうまでは古い地図を消さずに使う
//     (以前は番号を上げた瞬間に古い地図を全部消していたため、取り直し中にオフラインになると地図が出なかった)。
//   - HTMLの更新確認は同時に1本だけ・10秒に1回まで。条件付き取得(cache:"no-cache")で、変わっていなければ
//     本体(約400KB)を取り直さない(以前は起動ごとに2〜4回、丸ごと取り直していた)。
//   - 地図の保存状況をページに返す(maps-status)。
//   - 新しい版を保存したときは、アイコン・manifestも取り直す。
// build302: 降下プランナー(descent_planner.html)を保存して、機内モードでも開けるようにした(一度オンラインで開いた後)。
const APP_CACHE = "ap-app-v2";
const MAP_CACHE = "ap-map-v1";
const MAP_CACHE_PREFIX = "ap-map-";
const DATA_CACHE = "ap-data-v1";
const DP_CACHE = "ap-dp-v1";       // build302: 降下プランナー(descent_planner.html)。アプリ本体の版と関係なく更新される   // 便名検索のデータ(flightdb.json)。アプリ本体の版と関係なく毎日更新される
const HTML_FILES = ["./", "./approach_planner.html"];
const STATIC_FILES = ["./manifest.webmanifest", "./icons/apple-touch-icon.png", "./icons/icon-192.png", "./icons/icon-512.png", "./icons/favicon-32.png", "./icons/favicon-16.png"];
const AIRPORT_KEYS = ["chitose","hakodate","haneda","itami","takamatsu","matsuyama","hiroshima","fukuoka","kumamoto"];
const FIRST_LOAD_TIMEOUT_MS = 30000;   // 保存済みの版が無い(初回)ときだけ、ネットワークを待つ上限
const REVALIDATE_MIN_INTERVAL_MS = 10000;   // 起動時の二重確認を防ぐための最小間隔
// build297: 機内モード(回線なし)のときは裏の通信をしない。iPadで機内モードのままアプリを開くたびに
// 「データにアクセスするには、機内モードをオフにするか…」と出ていたため。保存済みのものだけで表示する。
const isOffline = ()=>{ try{ return self.navigator && self.navigator.onLine===false; }catch(e){ return false; } };

// ---- 中身の確認 ------------------------------------------------------------
// アプリ本体: 見出しと build 番号があり、</html> で終わっている(=最後まで受信できている)こと
function appBuildOf(text){
  const m = /APPROACH PLANNER[\s\S]*?build (\d+)<\/span>[\s\S]*<\/html>\s*$/.exec(text);
  return m ? +m[1] : null;
}
// 地図データ: 空港キーに対応する代入文で始まり、閉じ括弧で終わっていること
function isValidMapData(text, key){
  return text.indexOf("var MAP_DATA_"+key.toUpperCase()+" = {") >= 0 && /\}\s*;?\s*$/.test(text);
}
function isPlainOk(res){ return res && res.ok && res.status===200 && !res.redirected && res.type!=="opaqueredirect"; }
function jsResponse(text){ return new Response(text, { status:200, headers:{ "Content-Type":"text/javascript; charset=utf-8" } }); }
function htmlResponse(text){ return new Response(text, { status:200, headers:{ "Content-Type":"text/html; charset=utf-8" } }); }

// ネットワークから取得し、全部受信できて確認に通ったものだけ {text, build} で返す。ダメなら null。
// cache:"no-cache" = 必ずサーバーに確認するが、変わっていなければ(304)端末のHTTPキャッシュの本体を使う。
async function fetchValidatedHtml(url, signal){
  try{
    const res = await fetch(url, { cache:"no-cache", redirect:"follow", signal });
    if(!isPlainOk(res)) return null;
    const text = await res.text();              // 途中で切れたらここで例外になる
    const build = appBuildOf(text);
    return build ? { text, build } : null;
  }catch(e){ return null; }
}

async function refreshStaticFiles(){
  const c = await caches.open(APP_CACHE);
  for(const u of STATIC_FILES){
    try{ const r = await fetch(u, { cache:"no-cache" }); if(isPlainOk(r)) await c.put(u, r); }catch(err){}
  }
}

// ---- インストール / 有効化 ---------------------------------------------------
self.addEventListener("install", (e)=>{
  e.waitUntil((async ()=>{
    const c = await caches.open(APP_CACHE);
    // HTMLは確認に通ったものだけ保存。通らなければインストール自体を失敗させ、今までの版を使い続ける
    // ("./" と approach_planner.html は同じ中身なので、1回だけ取得して両方に保存する)
    const got = await fetchValidatedHtml("./");
    if(!got) throw new Error("app html not valid");
    for(const k of HTML_FILES) await c.put(k, htmlResponse(got.text));
    await refreshStaticFiles();
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e)=>{
  // 古いアプリ本体の保存分は消す。地図の保存分(ap-map-*)は、新しい地図がそろうまで消さない(build236)
  e.waitUntil(caches.keys().then(keys=>Promise.all(
    keys.filter(k=>k!==APP_CACHE && k!==DATA_CACHE && k!==DP_CACHE && !k.startsWith(MAP_CACHE_PREFIX)).map(k=>caches.delete(k))
  )).then(()=>self.clients.claim()));
});

// ---- 更新確認(HTML) ---------------------------------------------------------
async function notifyClients(msg){
  const cs = await self.clients.matchAll({ includeUncontrolled:true, type:"window" });
  cs.forEach(c=>c.postMessage(msg));
}
let revalInflight = null, revalStartedAt = 0, revalCtrl = null, revalOkAt = 0;
// 裏で新しい版を取りに行き、確認に通れば保存する(表示中のページはそのまま)。
//  - 10秒以内に始めた確認が実行中なら、それを共有する(起動時の二重取得を防ぐ)
//  - それより古い確認がまだ終わっていない(極端に遅い回線など)ときは打ち切って取り直す
//    (遅い回線で始まった確認が、回線が良くなった後の確認を塞がないように)
//  - 最後に成功してから10秒以内は確認しない
function revalidateHtml(force){
  if(isOffline()) return Promise.resolve(null);
  if(revalInflight && Date.now()-revalStartedAt < 10000) return revalInflight;
  if(!force && Date.now()-revalOkAt < REVALIDATE_MIN_INTERVAL_MS) return Promise.resolve(null);
  if(revalCtrl){ try{ revalCtrl.abort(); }catch(err){} }
  const ctrl = (typeof AbortController!=="undefined") ? new AbortController() : null;
  revalCtrl = ctrl; revalStartedAt = Date.now();
  const p = (async ()=>{
    const got = await fetchValidatedHtml("./", ctrl ? ctrl.signal : undefined);
    if(!got) return null;
    revalOkAt = Date.now();
    const c = await caches.open(APP_CACHE);
    const old = await c.match("./");
    const oldBuild = old ? appBuildOf(await old.text()) : null;
    if(oldBuild !== got.build){
      for(const k of HTML_FILES) await c.put(k, htmlResponse(got.text));
      if(oldBuild){
        notifyClients({ type:"app-updated", build:got.build });
        refreshStaticFiles();   // 新しい版ではアイコン・manifestも変わっている可能性がある
      }
    }
    return got;
  })().finally(()=>{ if(revalInflight===p){ revalInflight = null; revalCtrl = null; } });
  revalInflight = p;
  return p;
}

// ---- 取得 ------------------------------------------------------------------
function isMapData(url){ return /\/map_data_([a-z]+)\.js$/.exec(url.pathname); }
function isHtml(req, url){
  return req.mode==="navigate" || url.pathname.endsWith("/") || url.pathname.endsWith(".html");
}
function htmlKeyOf(url){ return url.pathname.endsWith("approach_planner.html") ? "./approach_planner.html" : "./"; }

async function serveHtml(e, url){
  const c = await caches.open(APP_CACHE);
  const key = htmlKeyOf(url);
  const hit = await c.match(key);
  if(hit) return hit;   // 起動は待たせない(更新確認は fetch ハンドラで裏に回してある)
  // 保存済みの版が無い(初回や保存が消えた)ときだけネットワークを待つ
  const got = await Promise.race([
    revalidateHtml(true),   // 実行中の確認があればそれを待つ(無ければ間隔制限なしで取りに行く)
    new Promise(r=>setTimeout(()=>r(null), FIRST_LOAD_TIMEOUT_MS))
  ]);
  const hit2 = await c.match(key);
  if(hit2) return hit2;
  if(got) return htmlResponse(got.text);
  return fetch(e.request);   // 最後の手段(ブラウザ本来のエラー表示に任せる)
}

// 地図: 今の保存番号の分 → 古い保存番号の分 → ネットワーク の順に探す
async function matchAnyMap(req){
  const cur = await caches.open(MAP_CACHE);
  const hit = await cur.match(req, { ignoreSearch:true });
  if(hit) return { hit, current:true };
  const keys = (await caches.keys()).filter(k=>k.startsWith(MAP_CACHE_PREFIX) && k!==MAP_CACHE);
  for(const k of keys){
    const h = await (await caches.open(k)).match(req, { ignoreSearch:true });
    if(h) return { hit:h, current:false };
  }
  return { hit:null, current:false };
}
const mapInflight = new Map();   // 同じ地図を同時に2回取りに行かない
function fetchAndStoreMap(key){
  if(mapInflight.has(key)) return mapInflight.get(key);
  const url = new URL("map_data_"+key+".js", self.registration.scope).href;
  const p = (async ()=>{
    try{
      const res = await fetch(url, { cache:"no-cache" });
      if(!isPlainOk(res)) return null;
      const text = await res.text();
      if(!isValidMapData(text, key)) return null;
      await (await caches.open(MAP_CACHE)).put(url, jsResponse(text));
      return text;
    }catch(err){ return null; }
  })().finally(()=>mapInflight.delete(key));
  mapInflight.set(key, p);
  return p;
}
async function serveMap(e, req, key){
  const found = await matchAnyMap(req);
  if(found.hit){
    if(!found.current && !isOffline()) e.waitUntil(fetchAndStoreMap(key).then(()=>cleanupOldMapsIfComplete()));   // 古い地図を出しつつ、新しい地図を裏で取得
    return found.hit;
  }
  const text = await fetchAndStoreMap(key);
  if(text) return jsResponse(text);
  return fetch(req);
}
// 新しい保存番号の地図が全空港そろったら、古い保存番号の地図を消す
async function cleanupOldMapsIfComplete(){
  const cur = await caches.open(MAP_CACHE);
  for(const k of AIRPORT_KEYS){
    const url = new URL("map_data_"+k+".js", self.registration.scope).href;
    if(!(await cur.match(url))) return false;
  }
  const olds = (await caches.keys()).filter(k=>k.startsWith(MAP_CACHE_PREFIX) && k!==MAP_CACHE);
  await Promise.all(olds.map(k=>caches.delete(k)));
  return true;
}

// 便名検索のデータ: 保存済みを即返し、裏で新しい版を取って保存する(stale-while-revalidate)。
// 最後まで受信できて JSON として読めたものだけ保存する。
let flightDbInflight = null;
function fetchAndStoreFlightDb(){
  if(flightDbInflight) return flightDbInflight;
  const url = new URL("flightdb.json", self.registration.scope).href;
  flightDbInflight = (async ()=>{
    try{
      const res = await fetch(url, { cache:"no-cache" });
      if(!isPlainOk(res)) return null;
      const text = await res.text();
      const d = JSON.parse(text);
      if(!d || d.v!==1 || !d.k) return null;
      await (await caches.open(DATA_CACHE)).put(url, new Response(text, { status:200, headers:{ "Content-Type":"application/json; charset=utf-8" } }));
      return text;
    }catch(err){ return null; }
  })().finally(()=>{ flightDbInflight = null; });
  return flightDbInflight;
}
async function serveFlightDb(e, req){
  const c = await caches.open(DATA_CACHE);
  const hit = await c.match(req, { ignoreSearch:true });
  if(hit && isOffline()) return hit;
  const net = fetchAndStoreFlightDb();
  if(hit){ try{ e.waitUntil(net); }catch(err){} return hit; }
  const text = await net;
  if(text) return new Response(text, { status:200, headers:{ "Content-Type":"application/json; charset=utf-8" } });
  return fetch(req);
}

// build302: 降下プランナー(descent_planner.html)。オンラインなら新しい版を最大3秒待ち、間に合わなければ保存済みを返す
// (裏で取得は続け、最後まで受信できて中身が降下プランナーだと確認できたときだけ保存)。機内モードでは保存済みだけ。
function isValidDp(text){ return text.indexOf("<title>降下プランナー</title>") >= 0 && /<\/html>\s*$/.test(text); }
let dpInflight = null;
function fetchAndStoreDp(){
  if(dpInflight) return dpInflight;
  const url = new URL("descent_planner.html", self.registration.scope).href;
  dpInflight = (async ()=>{
    try{
      const res = await fetch(url, { cache:"no-cache" });
      if(!isPlainOk(res)) return null;
      const text = await res.text();
      if(!isValidDp(text)) return null;
      await (await caches.open(DP_CACHE)).put(url, htmlResponse(text));
      return text;
    }catch(err){ return null; }
  })().finally(()=>{ dpInflight = null; });
  return dpInflight;
}
async function serveDp(req, net){
  const c = await caches.open(DP_CACHE);
  const hit = await c.match(req, { ignoreSearch:true });
  if(!net) return hit || fetch(req);
  if(hit){
    const t = await Promise.race([net, new Promise(r=>setTimeout(()=>r(null), 3000))]);
    return t ? htmlResponse(t) : hit;
  }
  const text = await net;
  if(text) return htmlResponse(text);
  return fetch(req);
}

async function serveStatic(req){
  const c = await caches.open(APP_CACHE);
  const hit = await c.match(req, { ignoreSearch:true });
  if(hit) return hit;
  return fetch(req);
}

self.addEventListener("fetch", (e)=>{
  const req = e.request;
  if(req.method!=="GET") return;
  const url = new URL(req.url);
  if(url.origin!==self.location.origin) return;
  const m = isMapData(url);
  if(m){ e.respondWith(serveMap(e, req, m[1])); return; }
  if(url.pathname.endsWith("/flightdb.json")){ e.respondWith(serveFlightDb(e, req)); return; }
  if(url.pathname.endsWith("/descent_planner.html")){
    // アプリ本体の HTML より先に判定する。取得は同期的に開始して waitUntil に渡す(後から呼ぶと Safari で失敗する可能性があるため)
    const net = isOffline() ? null : fetchAndStoreDp();
    if(net){ try{ e.waitUntil(net); }catch(err){} }
    e.respondWith(serveDp(req, net));
    return;
  }
  if(isHtml(req, url)){
    // 更新確認はここで同期的に裏へ回す(waitUntilを後から呼ぶとSafariで失敗する可能性があるため)
    const reval = revalidateHtml(false);
    try{ e.waitUntil(reval); }catch(err){}
    e.respondWith(serveHtml(e, url));
    return;
  }
  e.respondWith(serveStatic(req));
});

// ---- ページからの依頼 ---------------------------------------------------------
async function mapsStatus(){
  let saved = 0, current = 0;
  for(const k of AIRPORT_KEYS){
    const url = new URL("map_data_"+k+".js", self.registration.scope).href;
    const f = await matchAnyMap(new Request(url));
    if(f.hit){ saved++; if(f.current) current++; }
  }
  return { type:"maps-status", saved, current, total:AIRPORT_KEYS.length };
}
self.addEventListener("message", (e)=>{
  const d = e.data || {};
  const src = e.source;
  if(d.type==="check-update"){
    // build224: 裏でGitHubの最新版を確認・保存したうえで、保存済みの版の番号を返す(ページが「今すぐ更新しますか？」を出す)
    e.waitUntil((async ()=>{
      await revalidateHtml(false);
      const hit = await (await caches.open(APP_CACHE)).match("./");
      const build = hit ? appBuildOf(await hit.text()) : null;
      if(build && src) src.postMessage({ type:"app-version", build });
    })());
  } else if(d.type==="precache-maps"){
    // まだ今の保存番号で保存していない空港の地図を1つずつ保存する。途中で切れても保存できた分は残る。
    // 全部そろったら古い保存番号の地図を消す。進み具合はページに知らせる。
    if(isOffline()) return;
    e.waitUntil((async ()=>{
      const cur = await caches.open(MAP_CACHE);
      for(const k of AIRPORT_KEYS){
        const url = new URL("map_data_"+k+".js", self.registration.scope).href;
        if(await cur.match(url)) continue;
        await fetchAndStoreMap(k);
        notifyClients(await mapsStatus());
      }
      await cleanupOldMapsIfComplete();
      notifyClients(await mapsStatus());
    })());
  } else if(d.type==="maps-status"){
    e.waitUntil(mapsStatus().then(st=>{ if(src) src.postMessage(st); }));
  }
});
