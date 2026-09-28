#!/usr/bin/env python3
"""便名(コールサイン) → 区間 のデータベースを作る・更新する。

GitHub Actions(.github/workflows/flightdb.yml)から毎日実行される。

  baseline : FlightAware AeroAPI で国内の全空港の「明日1日分の出発予定」と、国際線のある空港の
             「到着予定」を取り、表の土台を作り直す(約450単位 ≒ 2.3ドル。8週ごと＋ダイヤ改正後)。
  daily    : OpenSky Network(無料)で前日に主要空港を発着した便のコールサインを集め、
             表に無いものだけ AeroAPI の便名検索(1便1単位)で正確な区間を調べる。
  auto     : 上のどちらを行うかを自動で決める(既定)。

出力:
  data/flightdb_master.json … 全情報(次回の更新の元。公開しない)
  flightdb.json             … アプリが読む圧縮版(GitHub Pages で公開)

使いすぎ防止: 1か月の使用単位を master に記録し、MONTH_CAP_UNITS(既定900単位≒4.5ドル)を超えそうなら
AeroAPI を呼ばない。AeroAPI の /account/usage が読めればそちらの値とも突き合わせる。
"""
import os, sys, json, time, re, datetime, urllib.request, urllib.parse, urllib.error, argparse, csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(ROOT, "data", "flightdb_master.json")
PUBLIC = os.path.join(ROOT, "flightdb.json")

AERO_BASE = os.environ.get("AEROAPI_BASE", "https://aeroapi.flightaware.com/aeroapi")
OS_BASE = os.environ.get("OPENSKY_BASE", "https://opensky-network.org/api")
OS_TOKEN_URL = os.environ.get("OPENSKY_TOKEN_URL",
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token")

UNIT_USD = 0.005
MONTH_CAP_UNITS = int(os.environ.get("MONTH_CAP_UNITS", "900"))     # 4.5ドル
BASELINE_EST_UNITS = 480
BASELINE_EVERY_DAYS = 56
DAILY_LOOKUP_CAP = int(os.environ.get("DAILY_LOOKUP_CAP", "30"))
CATCHUP_LOOKUP_MAX = int(os.environ.get("CATCHUP_LOOKUP_MAX", "150"))   # 何日分もまとめて集めたときの上限
OS_CATCHUP_DAYS = 7          # OpenSky を遡って集める最大日数(初回・取り損ねた日の取り直し)
PENDING_EXPIRE_DAYS = 30     # 「調べる待ち」に積んだまま、この日数見かけなかった便名は捨てる
AERO_MIN_INTERVAL = float(os.environ.get("AERO_MIN_INTERVAL", "6.5"))   # Personal: 10 result sets/分
OS_MIN_INTERVAL = float(os.environ.get("OS_MIN_INTERVAL", "1.0"))
MISS_RETRY_DAYS = 30        # AeroAPI で見つからなかった便名は30日は再検索しない
STALE_DAYS = 60             # この日数より古い記録しかない便名は、OpenSky で見かけたら調べ直す
EXPIRE_DAYS = 200           # この日数より古い記録は捨てる

# 出発予定を取る空港(定期便のある国内空港)
DEP_AIRPORTS = """
RJCC RJCO RJCH RJEC RJCK RJCB RJCM RJCN RJCW RJEB RJER RJEO
RJSA RJSM RJSI RJSK RJSR RJSY RJSC RJSS RJSF
RJTT RJAA RJAH RJTO RJTQ RJTH RJTF RJAN RJAZ
RJGG RJNA RJNS RJAF RJSN RJNT RJNK RJNW
RJOO RJBB RJBE RJBD RJBT
RJOB RJOA RJDC RJOI RJOW RJOC RJOR RJOH RJNO
RJOT RJOS RJOK RJOM
RJFF RJFR RJFS RJFU RJFT RJFO RJFM RJFK RJDT RJDB RJFE RJDA RJFG RJFC RJKB RJKA RJKI RJKN RORY
ROAH ROMY RORS ROIG ROYN ROMD ROKJ
""".split()
# 到着予定も取る空港(国際線のある空港。日本行きの国際便は出発予定には出てこないため)
ARR_AIRPORTS = """
RJTT RJAA RJBB RJGG RJFF RJCC ROAH
RJSS RJSN RJOA RJOT RJOM RJFT RJFK RJOB RJFU RJAH RJNS RJNK RJNT RJSA RJCH RJEC RJOH RJFS RJFR RORS ROIG
""".split()
# OpenSky で前日の発着を集める空港(受信状況が良い大空港)
OS_AIRPORTS = "RJTT RJAA RJBB RJGG RJFF RJCC ROAH RJOO".split()

CALLSIGN_RE = re.compile(r"^([A-Z]{3})0*(\d{1,4})([A-Z]{0,2})$")
JST = datetime.timezone(datetime.timedelta(hours=9))


def log(*a):
    print(*a, flush=True)


def canon(cs):
    """コールサインの正規化: 空白除去・大文字、番号の先頭の0を除く(SKY007 → SKY7)。"""
    s = re.sub(r"\s+", "", (cs or "")).upper()
    m = CALLSIGN_RE.match(s)
    if not m:
        return None
    return m.group(1) + str(int(m.group(2))) + m.group(3)


def is_jp(icao):
    return bool(icao) and icao[:2] in ("RJ", "RO")


def today_jst():
    return datetime.datetime.now(JST).date()


# ---------------------------------------------------------------- master の読み書き
def load_master():
    if os.path.exists(MASTER):
        with open(MASTER, encoding="utf-8") as f:
            m = json.load(f)
    else:
        m = {}
    m.setdefault("version", 1)
    m.setdefault("meta", {})
    m["meta"].setdefault("baseline_date", None)
    m["meta"].setdefault("usage", {})
    m["meta"].setdefault("runs", [])
    m.setdefault("flights", {})      # "OP|ORIG|DEST" → {op, o, d, keys[], t, last, first, src}
    m.setdefault("miss", {})         # canon callsign → 最後に見つからなかった日
    m.setdefault("pending", {})      # canon callsign → {n:見かけた回数, first, seen}  AeroAPI で調べる待ち
    m.setdefault("airports", {})     # ICAO → [IATA, 名前]
    m.setdefault("iata", {})         # 航空会社 IATA → ICAO
    m.setdefault("vrs", {})          # canon callsign → "RJTT-RJFF"(参考。VRS standing data, CC0)
    m.setdefault("vrs_date", None)
    return m


def save_json(path, obj, compact=False):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=0, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def month_key(d=None):
    d = d or today_jst()
    return d.strftime("%Y-%m")


def units_used(m):
    return int(m["meta"]["usage"].get(month_key(), 0))


def add_units(m, n):
    k = month_key()
    m["meta"]["usage"][k] = int(m["meta"]["usage"].get(k, 0)) + n
    # 古い月の記録は12か月分だけ残す
    for old in sorted(m["meta"]["usage"])[:-12]:
        del m["meta"]["usage"][old]


# ---------------------------------------------------------------- HTTP
class Aero:
    def __init__(self, key, master):
        self.key = key
        self.m = master
        self.last = 0.0
        self.calls = 0          # 課金対象になった(成功した)呼び出し数

    def get(self, path, billable=True):
        wait = AERO_MIN_INTERVAL - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        url = path if path.startswith("http") else AERO_BASE + path
        req = urllib.request.Request(url, headers={"x-apikey": self.key, "Accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    d = json.load(r)
                if billable:
                    self.calls += 1
                    add_units(self.m, 1)
                return d, None
            except urllib.error.HTTPError as e:
                body = e.read()[:300]
                if e.code == 429:
                    log("[aero] 429 rate limited; waiting 65s")
                    time.sleep(65)
                    continue
                if e.code in (400, 404):
                    return None, f"HTTP {e.code} {body!r}"
                err = f"HTTP {e.code} {body!r}"
            except Exception as e:
                err = repr(e)
            time.sleep(5 * (attempt + 1))
        return None, err

    def remaining_units(self):
        return MONTH_CAP_UNITS - units_used(self.m)

    def sync_usage(self):
        """AeroAPI 側の今月の利用額が読めれば、手元の記録と大きい方に合わせる。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        q = urllib.parse.urlencode({"start": now.strftime("%Y-%m-01T00:00:00Z"),
                                    "end": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
        d, err = self.get("/account/usage?" + q, billable=False)
        if not d:
            log("[aero] usage 取得できず:", err)
            return
        cost = d.get("total_cost")
        if isinstance(cost, (int, float)):
            u = int(round(cost / UNIT_USD))
            k = month_key()
            if u > int(self.m["meta"]["usage"].get(k, 0)):
                self.m["meta"]["usage"][k] = u
            log(f"[aero] 今月の利用額 ${cost:.3f}（手元記録 {units_used(self.m)} 単位）")
        else:
            log("[aero] usage 応答に total_cost なし:", json.dumps(d)[:200])


class OpenSky:
    def __init__(self, cid, secret):
        self.cid, self.secret = cid, secret
        self.tok, self.tok_t, self.last = None, 0, 0.0
        self.limited = False

    def token(self):
        if self.tok and time.time() - self.tok_t < 1500:
            return self.tok
        body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "client_id": self.cid, "client_secret": self.secret}).encode()
        with urllib.request.urlopen(urllib.request.Request(OS_TOKEN_URL, data=body), timeout=60) as r:
            self.tok = json.load(r)["access_token"]
        self.tok_t = time.time()
        return self.tok

    def get(self, path):
        if self.limited:
            return None, "rate limited"
        wait = OS_MIN_INTERVAL - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        err = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(OS_BASE + path, headers={"Authorization": "Bearer " + self.token()})
                with urllib.request.urlopen(req, timeout=90) as r:
                    return json.load(r), None
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return [], None
                if e.code == 429:
                    self.limited = True
                    return None, "429"
                if e.code == 401:
                    self.tok = None
                err = f"HTTP {e.code}"
            except Exception as e:
                err = repr(e)
            time.sleep(5 * (attempt + 1))
        return None, err


# ---------------------------------------------------------------- 記録の追加
def jst_date_of(iso):
    if not iso:
        return None
    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone(JST).date().isoformat()
    except Exception:
        return None


def remember_airport(m, a):
    if not a:
        return
    icao = a.get("code_icao") or a.get("code")
    if not icao or len(icao) != 4:
        return
    iata = a.get("code_iata") or ""
    name = (a.get("name") or "").strip()
    cur = m["airports"].get(icao)
    if not cur or (name and not cur[1]) or (iata and not cur[0]):
        m["airports"][icao] = [iata, name]


def add_flight(m, f, extra_keys=(), src="a"):
    """AeroAPI の flight オブジェクト1件を master に取り込む。取り込んだら記録のキーを返す。"""
    op = canon(f.get("ident_icao") or f.get("ident"))
    o = (f.get("origin") or {}).get("code_icao") or (f.get("origin") or {}).get("code")
    d = (f.get("destination") or {}).get("code_icao") or (f.get("destination") or {}).get("code")
    if not op or not o or not d or len(o) != 4 or len(d) != 4:
        return None
    # 欠航した便も区間(便名→出発・到着)の情報としては正しいので取り込む(その日に欠航しても表から漏れないように)。
    # 目的地変更(ダイバート)した便は到着地が本来と違う可能性があるので取り込まない
    if f.get("diverted"):
        return None
    remember_airport(m, f.get("origin"))
    remember_airport(m, f.get("destination"))
    # 航空会社の IATA → ICAO(入力欄で NH23 のような形も受け付けるため)
    oi, oc = f.get("operator_iata"), f.get("operator_icao")
    if oi and oc and len(oi) == 2 and len(oc) == 3:
        m["iata"].setdefault(oi, oc)
    for ci, cc in zip(f.get("codeshares_iata") or [], f.get("codeshares") or []):
        if len(ci) >= 3 and len(cc) >= 4 and ci[:2].isalnum() and cc[:3].isalpha():
            m["iata"].setdefault(ci[:2], cc[:3])
    # 検索キー: 運航便名＋「同じ番号」の共同運航便名(AKX239 の実際のコールサインは ANA239 など)
    num = re.sub(r"^[A-Z]{3}", "", op)
    keys = {op}
    for c in f.get("codeshares") or []:
        cc = canon(c)
        if cc and re.sub(r"^[A-Z]{3}", "", cc) == num:
            keys.add(cc)
    for k in extra_keys:
        kk = canon(k)
        if kk:
            keys.add(kk)
    date = jst_date_of(f.get("scheduled_out") or f.get("scheduled_off")) or today_jst().isoformat()
    rk = f"{op}|{o}|{d}"
    rec = m["flights"].get(rk)
    t = (f.get("aircraft_type") or "").strip()
    if rec is None:
        rec = {"op": op, "o": o, "d": d, "keys": sorted(keys), "t": t, "first": date, "last": date, "src": src}
        m["flights"][rk] = rec
    else:
        rec["keys"] = sorted(set(rec["keys"]) | keys)
        if date >= rec["last"]:
            rec["last"] = date
            if t:
                rec["t"] = t
        if date < rec.get("first", date):
            rec["first"] = date
    for k in keys:
        m["miss"].pop(k, None)
    return rk


def key_index(m):
    idx = {}
    for rk, r in m["flights"].items():
        for k in r["keys"]:
            idx.setdefault(k, []).append(r)
    return idx


def expire(m):
    lim = (today_jst() - datetime.timedelta(days=EXPIRE_DAYS)).isoformat()
    for rk in [rk for rk, r in m["flights"].items() if r["last"] < lim]:
        del m["flights"][rk]
    lim2 = (today_jst() - datetime.timedelta(days=MISS_RETRY_DAYS)).isoformat()
    for k in [k for k, v in m["miss"].items() if v < lim2]:
        del m["miss"][k]
    lim3 = (today_jst() - datetime.timedelta(days=PENDING_EXPIRE_DAYS)).isoformat()
    for k in [k for k, v in m["pending"].items() if v.get("seen", "") < lim3]:
        del m["pending"][k]


# ---------------------------------------------------------------- baseline
def run_baseline(m, aero, max_units):
    t0 = datetime.datetime.combine(today_jst() + datetime.timedelta(days=1), datetime.time(0, 0), JST)
    start = t0.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (t0 + datetime.timedelta(days=1)).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"[baseline] 対象 {t0.date()}（日本時間） 上限 {max_units} 単位")
    jobs = [(a, "scheduled_departures") for a in DEP_AIRPORTS] + [(a, "scheduled_arrivals") for a in ARR_AIRPORTS]
    used0 = aero.calls
    n_fl = 0
    stopped = False
    for ap, kind in jobs:
        path = f"/airports/{ap}/flights/{kind}?" + urllib.parse.urlencode(
            {"start": start, "end": end, "type": "Airline", "max_pages": 1})
        pages = flights = 0
        while path:
            if aero.calls - used0 >= max_units:
                stopped = True
                break
            d, err = aero.get(path)
            if d is None:
                log(f"[baseline] {ap} {kind} エラー: {err}")
                break
            pages += 1
            for f in d.get(kind) or []:
                if add_flight(m, f):
                    flights += 1
            nxt = (d.get("links") or {}).get("next")
            path = nxt if nxt else None
        n_fl += flights
        log(f"[baseline] {ap} {kind[10:]} pages={pages} flights={flights}")
        if stopped:
            log("[baseline] 上限に達したため途中で終了")
            break
    if not stopped:
        m["meta"]["baseline_date"] = today_jst().isoformat()
    return {"mode": "baseline", "units": aero.calls - used0, "flights": n_fl, "complete": not stopped}


# ---------------------------------------------------------------- daily
def collect_opensky(osn, m):
    """主要空港を発着した便のコールサイン → 回数。
    前回までに集め終えた日(meta.os_done)の翌日から前日(UTC)までを1日ずつ集める(最大 OS_CATCHUP_DAYS 日)。
    初回は過去7日分をまとめて集め、毎日運航ではない便(週数便の国際線など)も最初の実行で洗い出す。
    実行が失敗した日や、回数制限で途中までしか取れなかった日は、次回の実行で取り直す。"""
    today0 = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    last = today0 - datetime.timedelta(days=1)
    first = today0 - datetime.timedelta(days=OS_CATCHUP_DAYS)
    done = m["meta"].get("os_done")
    if done:
        nxt = datetime.datetime.fromisoformat(done).replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(days=1)
        if nxt > first:
            first = nxt
    seen, days_done = {}, []
    day = first
    while day <= last:
        begin = int(day.timestamp())
        end = begin + 86400
        ok = True
        for ap in OS_AIRPORTS:
            for kind in ("departure", "arrival"):
                d, err = osn.get(f"/flights/{kind}?airport={ap}&begin={begin}&end={end}")
                if d is None:
                    log(f"[opensky] {day.date()} {ap} {kind} エラー: {err}")
                    ok = False
                    continue
                n = 0
                for f in d:
                    c = canon(f.get("callsign"))
                    if c:
                        seen[c] = seen.get(c, 0) + 1
                        n += 1
                log(f"[opensky] {day.date()} {ap} {kind} {n}")
        if not ok:
            log(f"[opensky] {day.date()} は取り切れなかったので次回取り直す")
            break
        days_done.append(day.date().isoformat())
        m["meta"]["os_done"] = day.date().isoformat()
        day += datetime.timedelta(days=1)
    return seen, days_done


def aero_lookup(m, aero, cs):
    """便名1つを AeroAPI で調べる(1単位)。日本発着の記録を取り込めたら True。"""
    q = urllib.parse.urlencode({"ident_type": "designator", "max_pages": 1})
    d, err = aero.get(f"/flights/{cs}?" + q)
    if d is None:
        log(f"[lookup] {cs}: {err}")
        m["miss"][cs] = today_jst().isoformat()
        return False
    got = False
    for f in d.get("flights") or []:
        o = (f.get("origin") or {}).get("code_icao")
        de = (f.get("destination") or {}).get("code_icao")
        if not (is_jp(o) or is_jp(de)):
            continue
        if add_flight(m, f, extra_keys=[cs]):
            got = True
    if not got:
        m["miss"][cs] = today_jst().isoformat()
    log(f"[lookup] {cs}: {'OK' if got else '見つからず'}")
    return got


def run_daily(m, aero, osn, max_units):
    seen, days = collect_opensky(osn, m)
    idx = key_index(m)
    stale = (today_jst() - datetime.timedelta(days=STALE_DAYS)).isoformat()
    today = today_jst().isoformat()
    # 表に無い(または記録が古い)便名は「調べる待ち」に積む。上限で今回調べきれなかった分は次回以降に回す
    pend = m["pending"]
    for c, n in seen.items():
        if c in m["miss"]:
            continue
        recs = idx.get(c)
        if recs and max(r["last"] for r in recs) >= stale:
            continue
        p = pend.get(c) or {"n": 0, "first": today}
        p["n"] += n
        p["seen"] = today
        pend[c] = p
    # 未登録を優先、次に見かけた回数の多い順
    order = []
    for c, p in pend.items():
        recs = idx.get(c)
        if c in m["miss"] or (recs and max(r["last"] for r in recs) >= stale):
            continue
        order.append((0 if not recs else 1, -p["n"], c))
    order.sort()
    # 初回や取り直しで何日分もまとめて集めたときは、その分だけ多めに調べる(最大 CATCHUP_LOOKUP_MAX 件)
    cap = min(max(DAILY_LOOKUP_CAP, DAILY_LOOKUP_CAP * len(days)), CATCHUP_LOOKUP_MAX)
    limit = min(cap, max_units)
    log(f"[daily] OpenSky {', '.join(days) or '(新しい日なし)'}: {len(seen)} 便名、調べる待ち {len(order)} 件、今回調べる上限 {limit} 件")
    used0 = aero.calls if aero else 0
    ok = 0
    looked = []
    if aero:
        for _, _, c in order[:limit]:
            if aero_lookup(m, aero, c):
                ok += 1
            looked.append(c)
            pend.pop(c, None)
    for c in [c for c in pend if c in m["miss"]]:
        pend.pop(c, None)
    return {"mode": "daily", "opensky_days": days, "seen": len(seen), "pending": len(order),
            "looked": len(looked), "found": ok, "left": len(pend), "units": (aero.calls - used0) if aero else 0}


# ---------------------------------------------------------------- 公開用ファイル
def build_public(m):
    recs = sorted(m["flights"].values(), key=lambda r: (r["op"], r["o"], r["d"]))
    f = []
    k = {}
    for i, r in enumerate(recs):
        f.append([r["op"], r["o"], r["d"], r.get("t", ""), r["last"].replace("-", "")])
        for key in r["keys"]:
            k.setdefault(key, []).append(i)
    # VRS(参考)は AeroAPI に無い便名だけ
    vr = {c: rt for c, rt in m["vrs"].items() if c not in k}
    used_ap = set()
    for r in recs:
        used_ap.update((r["o"], r["d"]))
    for rt in vr.values():
        used_ap.update(rt.split("-"))
    ap = {c: v for c, v in m["airports"].items() if c in used_ap}
    return {
        "v": 1,
        "updated": today_jst().isoformat(),
        "base": m["meta"].get("baseline_date"),
        "vrsDate": m.get("vrs_date"),
        "ap": ap,
        "ia": m["iata"],
        "f": f,
        "k": {kk: (vv[0] if len(vv) == 1 else vv) for kk, vv in k.items()},
        "vr": vr,
    }


# ---------------------------------------------------------------- VRS(参考)の取り込み
def import_vrs(m, path, date):
    """VRS standing data の routes.csv から日本発着の便だけ取り込む(参考用、CC0)。"""
    n = 0
    vrs = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rt = row.get("AirportCodes") or ""
            aps = rt.split("-")
            if not any(is_jp(a) for a in aps):
                continue
            c = canon(row.get("Callsign"))
            if c:
                vrs[c] = rt
                n += 1
    m["vrs"] = vrs
    m["vrs_date"] = date
    log(f"[vrs] {n} 便名を取り込み（{date}）")


# ---------------------------------------------------------------- main
def last_sunday(year, month):
    d = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else datetime.date(year, 12, 31)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def want_baseline(m):
    bd = m["meta"].get("baseline_date")
    today = today_jst()
    if not bd:
        return True, "土台がまだ無い"
    bd = datetime.date.fromisoformat(bd)
    if (today - bd).days >= BASELINE_EVERY_DAYS:
        return True, f"前回から {(today - bd).days} 日"
    for mon in (3, 10):     # ダイヤ改正(3月・10月の最終日曜)の2〜14日後に取り直す
        ch = last_sunday(today.year, mon)
        if bd < ch and 2 <= (today - ch).days <= 14:
            return True, f"ダイヤ改正（{ch}）後"
    return False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "baseline", "daily", "rebuild", "import-vrs"])
    ap.add_argument("--vrs", help="import-vrs: routes.csv のパス")
    ap.add_argument("--vrs-date", help="import-vrs: データの日付")
    a = ap.parse_args()

    m = load_master()
    mode = a.mode
    summary = {"date": today_jst().isoformat()}

    if mode == "import-vrs":
        import_vrs(m, a.vrs, a.vrs_date or today_jst().isoformat())
    elif mode == "rebuild":
        pass
    else:
        key = os.environ.get("AEROAPI_KEY", "").strip()
        aero = Aero(key, m) if key else None
        if not aero:
            log("[warn] AEROAPI_KEY がありません（AeroAPI は使いません）")
        else:
            aero.sync_usage()
        rem = aero.remaining_units() if aero else 0
        log(f"[budget] 今月 {units_used(m)} / {MONTH_CAP_UNITS} 単位使用済み（残り {rem}）")
        if mode == "auto":
            want, why = want_baseline(m)
            if want and aero and rem >= BASELINE_EST_UNITS:
                mode = "baseline"
                log(f"[auto] baseline を実行（{why}）")
            else:
                if want:
                    log(f"[auto] baseline が必要（{why}）だが今月の残りが足りないので daily を実行")
                mode = "daily"
        if mode == "baseline":
            if not aero:
                log("[error] baseline には AEROAPI_KEY が必要"); sys.exit(1)
            summary.update(run_baseline(m, aero, max(0, rem)))
        else:
            cid = os.environ.get("OPENSKY_CLIENT_ID", "").strip()
            sec = os.environ.get("OPENSKY_CLIENT_SECRET", "").strip()
            if not cid or not sec:
                log("[error] OpenSky の認証情報がありません"); sys.exit(1)
            summary.update(run_daily(m, aero, OpenSky(cid, sec), max(0, rem)))
        summary["month_units"] = units_used(m)
        log("[summary]", json.dumps(summary, ensure_ascii=False))
        m["meta"]["runs"] = (m["meta"]["runs"] + [summary])[-40:]

    expire(m)
    os.makedirs(os.path.dirname(MASTER), exist_ok=True)
    save_json(MASTER, m)
    pub = build_public(m)
    save_json(PUBLIC, pub, compact=True)
    log(f"[out] flightdb.json: 記録 {len(pub['f'])} 件、検索キー {len(pub['k'])} 件、参考(VRS) {len(pub['vr'])} 件、"
        f"{os.path.getsize(PUBLIC)//1024} KB")


if __name__ == "__main__":
    main()
