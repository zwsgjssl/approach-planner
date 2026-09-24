// Approach Planner — Service Worker(GitHub Pages 用)
//
// build219: 機内Wi-Fiのような極端に遅い/不安定な回線でも、起動が止まったり壊れた版に置き換わったり
// しないようにした。
//   - アプリ本体(HTML)は「保存済みの版を即表示」し、更新確認は裏で行う(stale-while-revalidate)。
//     回線がどれだけ遅くても起動は待たされない。新しい版は次に開いたときに反映される。
//   - 裏で取ってきた新しい版は、最後まで受信できて中身がアプリ本体だと確認できたときだけ保存する
//     (途中で切れたもの・Wi-Fiのログイン画面などに差し替わったもの・リダイレクトされたものは捨てる)。
//     保存は1ファイル単位で入れ替わるので、保存済みの版が壊れることはない。
//   - 地図データ(map_data_<空港>.js)は保存済みを優先。新しく取るときも中身を確認してから保存する。
//     地図データを作り直したときは MAP_CACHE の番号を上げること(古い保存分が消えて取り直される)。
const APP_CACHE = "ap-app-v2";   // build223: アイコン差し替えで v2 に(静的ファイルは保存版優先のため、番号を上げないと古いアイコンが残る)
const MAP_CACHE = "ap-map-v1";
const HTML_FILES = ["./", "./approach_planner.html"];
const STATIC_FILES = ["./manifest.webmanifest", "./icons/apple-touch-icon.png", "./icons/icon-192.png", "./icons/icon-512.png"];
const AIRPORT_KEYS = ["chitose","hakodate","haneda","itami","takamatsu","matsuyama","hiroshima","fukuoka","kumamoto"];
const FIRST_LOAD_TIMEOUT_MS = 30000;   // 保存済みの版が無い(初回)ときだけ、ネットワークを待つ上限

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

// ネットワークから取得し、全部受信できて確認に通ったものだけ {text, build} で返す。ダメなら null。
async function fetchValidatedHtml(url){
  try{
    const res = await fetch(url, { cache:"no-store", redirect:"follow" });
    if(!isPlainOk(res)) return null;
    const text = await res.text();              // 途中で切れたらここで例外になる
    const build = appBuildOf(text);
    return build ? { text, build } : null;
  }catch(e){ return null; }
}
function htmlResponse(text){
  return new Response(text, { status:200, headers:{ "Content-Type":"text/html; charset=utf-8" } });
}

// ---- インストール / 有効化 ---------------------------------------------------
self.addEventListener("install", (e)=>{
  e.waitUntil((async ()=>{
    const c = await caches.open(APP_CACHE);
    // HTMLは確認に通ったものだけ保存。通らなければインストール自体を失敗させ、今までの版を使い続ける
    for(const u of HTML_FILES){
      const got = await fetchValidatedHtml(u);
      if(!got) throw new Error("app html not valid: "+u);
      await c.put(u, htmlResponse(got.text));
    }
    for(const u of STATIC_FILES){
      try{ const r = await fetch(u); if(isPlainOk(r)) await c.put(u, r); }catch(err){}
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e)=>{
  e.waitUntil(caches.keys().then(keys=>Promise.all(
    keys.filter(k=>k!==APP_CACHE && k!==MAP_CACHE).map(k=>caches.delete(k))
  )).then(()=>self.clients.claim()));
});

// ---- 取得 ------------------------------------------------------------------
function isMapData(url){ return /\/map_data_([a-z]+)\.js$/.exec(url.pathname); }
function isHtml(req, url){
  return req.mode==="navigate" || url.pathname.endsWith("/") || url.pathname.endsWith(".html");
}

async function notifyClients(msg){
  const cs = await self.clients.matchAll({ includeUncontrolled:true, type:"window" });
  cs.forEach(c=>c.postMessage(msg));
}

// 裏で新しい版を取りに行き、確認に通れば保存する(表示中のページはそのまま)
async function revalidateHtml(cacheKey){
  const got = await fetchValidatedHtml(cacheKey);
  if(!got) return;
  const c = await caches.open(APP_CACHE);
  const old = await c.match(cacheKey);
  const oldBuild = old ? appBuildOf(await old.clone().text()) : null;
  await c.put(cacheKey, htmlResponse(got.text));
  // "./" と approach_planner.html は同じ中身なので両方そろえる
  for(const k of HTML_FILES){ if(k!==cacheKey) await c.put(k, htmlResponse(got.text)); }
  if(oldBuild && got.build !== oldBuild) notifyClients({ type:"app-updated", build:got.build });
}

async function serveHtml(e, url){
  const c = await caches.open(APP_CACHE);
  const key = url.pathname.endsWith("approach_planner.html") ? "./approach_planner.html" : "./";
  const hit = await c.match(key);
  if(hit){
    e.waitUntil(revalidateHtml(key));   // 起動は待たせない
    return hit;
  }
  // 保存済みの版が無い(初回や保存が消えた)ときだけネットワークを待つ
  const got = await Promise.race([
    fetchValidatedHtml(key),
    new Promise(r=>setTimeout(()=>r(null), FIRST_LOAD_TIMEOUT_MS))
  ]);
  if(got){
    for(const k of HTML_FILES) await c.put(k, htmlResponse(got.text));
    return htmlResponse(got.text);
  }
  return fetch(e.request);   // 最後の手段(ブラウザ本来のエラー表示に任せる)
}

async function serveMap(req, key){
  const c = await caches.open(MAP_CACHE);
  const hit = await c.match(req, { ignoreSearch:true });
  if(hit) return hit;
  const res = await fetch(req);
  if(isPlainOk(res)){
    const text = await res.clone().text();
    if(isValidMapData(text, key)){
      await c.put(req, new Response(text, { status:200, headers:{ "Content-Type":"text/javascript; charset=utf-8" } }));
    }
  }
  return res;
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
  if(m) e.respondWith(serveMap(req, m[1]));
  else if(isHtml(req, url)) e.respondWith(serveHtml(e, url));
  else e.respondWith(serveStatic(req));
});

// ページから {type:"precache-maps"} が来たら、まだ保存していない空港の地図データを1つずつ保存する。
// 途中で回線が切れても、確認に通った空港の分だけが残る(次回起動時に残りを再試行)。
self.addEventListener("message", (e)=>{
  if(!e.data || e.data.type!=="precache-maps") return;
  e.waitUntil((async ()=>{
    const c = await caches.open(MAP_CACHE);
    for(const k of AIRPORT_KEYS){
      const url = new URL("map_data_"+k+".js", self.registration.scope).href;
      try{
        if(await c.match(url)) continue;
        const res = await fetch(url);
        if(!isPlainOk(res)) continue;
        const text = await res.text();
        if(isValidMapData(text, k)) await c.put(url, new Response(text, { status:200, headers:{ "Content-Type":"text/javascript; charset=utf-8" } }));
      }catch(err){ /* オフライン・切断等。次回起動時に再試行 */ }
    }
  })());
});
