#!/usr/bin/env python3
"""便名(コールサイン) → 区間 のデータベースを作る・更新する。

GitHub Actions(.github/workflows/flightdb.yml)から毎日実行される。

  baseline : FlightAware AeroAPI で国内の全空港の「明日1日分の出発予定」と、国際線のある空港の
             「到着予定」を取り、表の土台を作り直す(約450単位 ≒ 2.3ドル。8週ごと＋ダイヤ改正後)。
  daily    : OpenSky Network(無料)で前日に主要空港を発着した便のコールサインを集め、
             表に無いものだけ AeroAPI の便名検索(1便1単位)で正確な区間を調べる。
  auto     : 上のどちらを行うかを自動で決める(既定)。baseline を最後まで取れたら、同じ実行で daily も続けて行う。

ダイヤ改正(3月・10月の最終日曜)をまたぐと運航曜日や時刻が変わるので、運航曜日は「今のダイヤ期間」の運航だけから求め、
時刻がどのダイヤ期間のものかも記録する(公開ファイルの11番目の値 ts: 1=今のダイヤの時刻、0=前のダイヤの時刻)。
月の最終日(UTC)には、その月の残り単位を曜日限定便の確認(seed)に使い切る(翌月に繰り越せないため)。

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
OBS_KEEP_DAYS = 28           # 運航日の記録(obs)を残す日数。この期間に運航した曜日を「運航曜日」とする
WEEKDAY_TRUST_DAYS = 14      # OpenSky をこの日数以上続けて集めたら、主要空港発着便の運航曜日を確かとみなす
LK_TRUST_DAYS = 90           # 便名検索(前後約12日の運航が全部返る)で得た運航曜日を信じる日数(ダイヤ改正をまたいだら無効)
SEED_FILE = os.path.join(ROOT, "data", "seed_flights.txt")   # 曜日限定便などの「調べておく便名」一覧(1行1便名)
SEED_DAILY_CAP = int(os.environ.get("SEED_DAILY_CAP", "25"))  # 1回に調べる数
SEED_MIN_REMAINING = 150     # 今月の残りがこれ以下なら調べない(毎日の差分チェック用に残す)
SEED_REFRESH_DAYS = 84       # 調べてからこの日数、またはダイヤ改正をまたいだら調べ直す
SEED_MONTH_END_KEEP = 5      # 月末に使い切るときも、これだけは残す
LK_MIN_COVER_DAYS = 7        # 便名検索で「今のダイヤ期間」の運航が何日分見えていれば運航曜日を信じるか
AERO_PAST_DAYS, AERO_FUTURE_DAYS = 10, 2   # AeroAPI の便名検索が返す範囲(過去10日〜2日先)
TIME_LIMIT_MIN = float(os.environ.get("TIME_LIMIT_MIN", "165"))   # この分数を超えたら新しい問い合わせをやめて保存する
CHECKPOINT_EVERY = 40        # この単位数ごとに master を途中保存する(途中で止まっても使った分を無駄にしない)
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
T_START = time.time()


def time_left_min():
    return TIME_LIMIT_MIN - (time.time() - T_START) / 60


def out_of_time():
    return time_left_min() <= 0


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
    m.setdefault("seeded", {})       # seed_flights.txt の便名 → 最後に調べた日
    m.setdefault("airports", {})     # ICAO → [IATA, 名前]
    m.setdefault("iata", {})         # 航空会社 IATA → ICAO
    # VRS standing data(参考)は精度が低いので使わない。以前の master に残っていれば捨てる
    m["vrs"] = {}
    m["vrs_date"] = None
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
    # AeroAPI の利用額は UTC の月ごと。日本時間で数えると1日の0〜9時に前月分を今月分と取り違えるので UTC で数える
    d = d or datetime.datetime.now(datetime.timezone.utc).date()
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
        self.on_checkpoint = None

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
                    if self.on_checkpoint and self.calls % CHECKPOINT_EVERY == 0:
                        self.on_checkpoint()
                return d, None
            except urllib.error.HTTPError as e:
                body = e.read()[:300]
                if e.code == 429:
                    log("[aero] 429 rate limited; waiting 65s")
                    time.sleep(65)
                    continue
                if e.code in (400, 401, 403, 404):
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
                    ra = e.headers.get("X-Rate-Limit-Retry-After-Seconds") if e.headers else None
                    return None, f"429 回数制限（再開まで {ra} 秒）" if ra else "429 回数制限"
                if e.code == 401:
                    self.tok = None
                try:
                    body = e.read()[:200]
                except Exception:
                    body = b""
                err = f"HTTP {e.code} {body!r}"
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


def utc_parts(iso):
    """ISO時刻 → (UTCの日付 'YYYY-MM-DD', 'HHMM')"""
    if not iso:
        return None, None
    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
        return t.date().isoformat(), t.strftime("%H%M")
    except Exception:
        return None, None


def add_obs(rec, date_iso):
    """運航した日(出発予定のUTC日付)を記録する。OBS_KEEP_DAYS より古いものは捨てる。"""
    if not date_iso:
        return
    lim = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=OBS_KEEP_DAYS + 3)).isoformat()
    obs = [x for x in rec.get("obs", []) if x >= lim]
    if date_iso >= lim and date_iso not in obs:
        obs.append(date_iso)
    rec["obs"] = sorted(obs)


def display_flight_number(f, op):
    """表示用の2レター便名。子会社運航(AKX239など)は同じ番号の親会社便名(NH239)を使う。"""
    num = re.sub(r"^[A-Z]{3}", "", op)
    for ci, cc in zip(f.get("codeshares_iata") or [], f.get("codeshares") or []):
        c = canon(cc)
        if c and c[:3] != op[:3] and re.sub(r"^[A-Z]{3}", "", c) == num and ci:
            return ci
    return f.get("ident_iata") or ""


def add_flight(m, f, extra_keys=(), src="a", set_time=True):
    """AeroAPI の flight オブジェクト1件を master に取り込む。取り込んだら記録のキーを返す。
    set_time=False のときは STD/STA を書き換えない(ダイヤ改正をまたいだ別の期間の運航など)。"""
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
    sd, std = utc_parts(f.get("scheduled_out"))
    _, sta = utc_parts(f.get("scheduled_in"))
    fn = display_flight_number(f, op)
    newest = rec is None or date >= rec["last"]
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
    # STD/STA(UTC)は、いちばん新しい運航のものにする。sd = その時刻を取った運航の日(日本時間)
    if set_time and std and date >= (rec.get("sd") or ""):
        rec["std"], rec["sta"], rec["sd"] = std, (sta or ""), date
    if (newest or not rec.get("fn")) and fn:
        rec["fn"] = fn
    add_obs(rec, sd)
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
    for r in m["flights"].values():
        if r.get("obs"):
            add_obs(r, None)   # 古い運航日を捨てる
    lim3 = (today_jst() - datetime.timedelta(days=PENDING_EXPIRE_DAYS)).isoformat()
    for k in [k for k, v in m["pending"].items() if v.get("seen", "") < lim3]:
        del m["pending"][k]


# ---------------------------------------------------------------- baseline
def run_baseline(m, aero, max_units, jobs=None, full=True):
    """全国の空港の「明日1日分(日本時間)」の出発予定と、国際線のある空港の到着予定を取り込む。
    途中でエラーになったページは最後にもう一度だけ取り直し、それでも取れなければ meta.baseline_gaps に残して
    次回の daily で取り直す(4xx のような直らないエラーは残さない)。
    jobs を渡すとその (空港, 種類) だけを取る(取りこぼしの取り直し用、full=False)。"""
    t0 = datetime.datetime.combine(today_jst() + datetime.timedelta(days=1), datetime.time(0, 0), JST)
    start = t0.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (t0 + datetime.timedelta(days=1)).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"[baseline] 対象 {t0.date()}（日本時間） 上限 {max_units} 単位")
    if jobs is None:
        jobs = [(a, "scheduled_departures") for a in DEP_AIRPORTS] + [(a, "scheduled_arrivals") for a in ARR_AIRPORTS]
    used0 = aero.calls
    n_fl = 0
    stopped = False
    failed = []      # (空港, 種類, 取り直すページのURL)
    gaps = []

    def fetch(ap, kind, path):
        """path から next をたどって取り込む。戻り値 (ページ数, 便数, 失敗したURL or None, 直らないエラーか)"""
        nonlocal stopped
        pages = flights = 0
        while path:
            if aero.calls - used0 >= max_units or out_of_time():
                stopped = True
                return pages, flights, None, False
            d, err = aero.get(path)
            if d is None:
                log(f"[baseline] {ap} {kind[10:]} エラー: {err}")
                return pages, flights, path, bool(re.match(r"HTTP 4", err or ""))
            pages += 1
            for f in d.get(kind) or []:
                if add_flight(m, f):
                    flights += 1
            nxt = (d.get("links") or {}).get("next")
            path = nxt if nxt else None
        return pages, flights, None, False

    for ap, kind in jobs:
        path = f"/airports/{ap}/flights/{kind}?" + urllib.parse.urlencode(
            {"start": start, "end": end, "type": "Airline", "max_pages": 1})
        pages, flights, bad, permanent = fetch(ap, kind, path)
        n_fl += flights
        log(f"[baseline] {ap} {kind[10:]} pages={pages} flights={flights}" + (" （途中で失敗）" if bad else ""))
        if bad and not permanent:
            failed.append((ap, kind, bad))
        if stopped:
            log("[baseline] 上限（単位または時間）に達したため途中で終了")
            break
    if failed and not stopped:
        log(f"[baseline] 失敗した {len(failed)} 件を取り直す")
        time.sleep(20)
        for ap, kind, path in failed:
            pages, flights, bad, permanent = fetch(ap, kind, path)
            n_fl += flights
            log(f"[baseline] 取り直し {ap} {kind[10:]} pages={pages} flights={flights}" + (" （失敗）" if bad else ""))
            if (bad and not permanent) or stopped:
                gaps.append([ap, kind])
            if stopped:
                break
    if full and not stopped:
        m["meta"]["baseline_date"] = today_jst().isoformat()
        m["meta"]["std_ok"] = True   # STD/STA 付きで一度取り直した(以後この理由では取り直さない)
        m["meta"]["baseline_gaps"] = gaps
        if gaps:
            log(f"[baseline] 取れなかった {len(gaps)} 件は次回の daily で取り直す: {gaps}")
    elif not full:
        m["meta"]["baseline_gaps"] = gaps if not stopped else [list(j) for j in jobs]
    return {"mode": "baseline", "units": aero.calls - used0, "flights": n_fl, "complete": not stopped,
            "gaps": len(gaps)}


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
    seen, days_done, obs, dedup = {}, [], [], set()
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
                        k = (c, f.get("icao24"), f.get("firstSeen"))
                        if k not in dedup and f.get("firstSeen"):
                            dedup.add(k)
                            obs.append((c, int(f["firstSeen"]), f.get("estDepartureAirport"), f.get("estArrivalAirport")))
                log(f"[opensky] {day.date()} {ap} {kind} {n}")
        if not ok:
            log(f"[opensky] {day.date()} は取り切れなかったので次回取り直す")
            break
        days_done.append(day.date().isoformat())
        m["meta"]["os_done"] = day.date().isoformat()
        if not m["meta"].get("os_start"):
            m["meta"]["os_start"] = day.date().isoformat()
        day += datetime.timedelta(days=1)
    return seen, days_done, obs


def obs_date_for(rec, t0):
    """OpenSky で見えた時刻 t0(UNIX秒) → その便の出発予定のUTC日付。STDが分かれば、いちばん近い日のSTDに合わせる。"""
    t = datetime.datetime.fromtimestamp(t0, datetime.timezone.utc)
    std = rec.get("std")
    if not std or len(std) != 4:
        return t.date().isoformat()
    best = None
    for dd in (-1, 0, 1):
        dday = t.date() + datetime.timedelta(days=dd)
        cand = datetime.datetime(dday.year, dday.month, dday.day, int(std[:2]), int(std[2:]), tzinfo=datetime.timezone.utc)
        diff = abs((t - cand).total_seconds())
        if best is None or diff < best[0]:
            best = (diff, dday)
    return best[1].isoformat()


def apply_opensky_obs(m, obs):
    """OpenSky で実際に飛んだ便を、表の記録の運航日(obs)に加える(運航曜日を知るため)。"""
    idx = key_index(m)
    n = 0
    for c, t0, dep, arr in obs:
        recs = idx.get(c)
        if not recs:
            continue
        if len(recs) > 1:
            m1 = [r for r in recs if (dep and r["o"] == dep) or (arr and r["d"] == arr)]
            if m1:
                recs = m1
        if len(recs) > 1:
            def gap(r):
                s_ = r.get("std")
                if not s_:
                    return 10 ** 9
                t = datetime.datetime.fromtimestamp(t0, datetime.timezone.utc)
                mins = t.hour * 60 + t.minute - (int(s_[:2]) * 60 + int(s_[2:]))
                return min(abs(mins), 1440 - abs(mins))
            recs = [min(recs, key=gap)]
        for r in recs:
            add_obs(r, obs_date_for(r, t0))
            n += 1
    return n


def to_icao_designator(m, des):
    """NH619 → ANA619 のように、2レターの便名を3レターに直す(直せなければそのまま)。"""
    des = re.sub(r"\s+", "", des or "").upper()
    if canon(des):
        return canon(des)
    mm = re.match(r"^([A-Z0-9]{2})0*(\d{1,4})([A-Z]?)$", des)
    if mm and mm.group(1) in m["iata"]:
        return canon(m["iata"][mm.group(1)] + mm.group(2) + mm.group(3)) or des
    return des


def aero_lookup(m, aero, cs):
    """便名1つを AeroAPI で調べる(1単位)。戻り値 (日本発着の記録を取り込めたか, 運航曜日を確かめられたか)。
    返ってくる過去10日〜2日先の運航のうち「今のダイヤ期間」のものから、記録ごとの運航曜日(lkw)と時刻を求める。
    ダイヤ改正の直後などで今の期間の運航が LK_MIN_COVER_DAYS 日分見えないときは、運航曜日は決めない。"""
    q = urllib.parse.urlencode({"ident_type": "designator", "max_pages": 1})
    d, err = aero.get(f"/flights/{cs}?" + q)
    if d is None:
        log(f"[lookup] {cs}: {err}")
        m["miss"][cs] = today_jst().isoformat()
        return False, False
    today = today_jst()
    s0, s1 = season_bounds(today)
    s0i, s1i = s0.isoformat(), s1.isoformat()
    got = False
    masks = {}
    extra = [cs] if canon(cs) else []
    flights = d.get("flights") or []
    dates = []
    for f in flights:
        o = (f.get("origin") or {}).get("code_icao")
        de = (f.get("destination") or {}).get("code_icao")
        if not (is_jp(o) or is_jp(de)):
            continue
        jd = jst_date_of(f.get("scheduled_out") or f.get("scheduled_off"))
        if jd:
            dates.append(jd)
        in_season = bool(jd) and s0i <= jd < s1i
        rk = add_flight(m, f, extra_keys=extra, set_time=in_season)
        if rk:
            got = True
            sd, _ = utc_parts(f.get("scheduled_out"))
            if in_season and sd:
                masks[rk] = masks.get(rk, 0) | (1 << datetime.date.fromisoformat(sd).weekday())
    # 今のダイヤ期間のうち、この問い合わせで見えている日数
    w0 = max(today - datetime.timedelta(days=AERO_PAST_DAYS), s0)
    w1 = min(today + datetime.timedelta(days=AERO_FUTURE_DAYS), s1 - datetime.timedelta(days=1))
    if (d.get("links") or {}).get("next") and dates:
        # 1ページ(15件)に収まらなかったときは、返ってきた中で最も古い日までしか見えていない
        w0 = max(w0, datetime.date.fromisoformat(min(dates)))
    cover = (w1 - w0).days + 1
    cover_ok = cover >= LK_MIN_COVER_DAYS
    if cover_ok:
        for rk, w in masks.items():
            m["flights"][rk]["lk"] = today.isoformat()
            m["flights"][rk]["lkw"] = w
    if not got:
        m["miss"][cs] = today.isoformat()
    log(f"[lookup] {cs}: {'OK' if got else '見つからず'}" + ("" if cover_ok or not got else f"（今のダイヤの運航が {cover} 日分しか見えないので曜日は保留）"))
    return got, cover_ok


def schedule_changes(year):
    return sorted(last_sunday(y, mo) for y in (year - 1, year, year + 1) for mo in (3, 10))


def last_schedule_change(today=None):
    """直近のダイヤ改正日(3月・10月の最終日曜)"""
    today = today or today_jst()
    return max(c for c in schedule_changes(today.year) if c <= today)


def season_bounds(today=None):
    """今のダイヤ期間 [始まり, 次の改正日) (日本時間の日付)"""
    today = today or today_jst()
    return last_schedule_change(today), min(c for c in schedule_changes(today.year) if c > today)


def is_month_end_utc():
    """今日が UTC の月の最終日か(AeroAPI の無料枠は翌月に繰り越せない)"""
    t = datetime.datetime.now(datetime.timezone.utc).date()
    return (t + datetime.timedelta(days=1)).month != t.month


def run_seeds(m, aero, max_units):
    """data/seed_flights.txt の便名(曜日限定便など)を少しずつ AeroAPI で調べ、運航曜日と時刻を入れる。
    ふだんは1回 SEED_DAILY_CAP 件まで(今月の残りが SEED_MIN_REMAINING 以下なら調べない)。
    月の最終日(UTC)は、今月の残りを SEED_MONTH_END_KEEP だけ残して使い切る。"""
    if not aero or not os.path.exists(SEED_FILE):
        return {}
    today = today_jst()
    ch = last_schedule_change(today)
    # 改正直後は、今のダイヤ期間の運航が十分に見えない(過去10日〜2日先のうち改正後の分だけ)ので待つ
    ready = ch + datetime.timedelta(days=LK_MIN_COVER_DAYS - 1 - AERO_FUTURE_DAYS)
    if today < ready:
        log(f"[seed] ダイヤ改正（{ch}）直後なので {ready} から調べる")
        return {"seed_looked": 0}
    month_end = is_month_end_utc()
    keep = SEED_MONTH_END_KEEP if month_end else SEED_MIN_REMAINING
    if max_units <= keep:
        log(f"[seed] 今月の残りが少ないので今回は調べない（残り {max_units}）")
        return {"seed_looked": 0}
    with open(SEED_FILE, encoding="utf-8") as f:
        seeds = [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")]
    seeds = list(dict.fromkeys(seeds))
    old = max((today - datetime.timedelta(days=SEED_REFRESH_DAYS)).isoformat(), ch.isoformat())
    todo = [x for x in seeds if (m["seeded"].get(x) or "") < old]
    cap = (max_units - keep) if month_end else SEED_DAILY_CAP
    limit = max(0, min(cap, max_units - keep))    # 使ってよい単位数
    log(f"[seed] 一覧 {len(seeds)} 便名、未確認 {len(todo)} 件、今回の上限 {limit} 単位" + ("（月末なので今月の残りを使う）" if month_end else ""))
    used0 = aero.calls
    idx = key_index(m)
    ok = looked = skipped = 0
    for x in todo:
        if aero.calls - used0 >= limit:
            break
        if out_of_time():
            log("[seed] 時間切れのため残りは次回")
            break
        des = to_icao_designator(m, x)
        alt = ("ANA" + des[3:]) if des.startswith("AKX") else None   # ANAウイングス(EH)の便は ANA の便名で飛んでいることがある
        # 実際には毎日飛んでいる便(直近14日の運航日が7曜日そろう)は調べない(全国取得で時刻が入るため)
        if runs_daily(idx.get(des) or []) or (alt and runs_daily(idx.get(alt) or [])):
            m["seeded"][x] = today.isoformat()
            skipped += 1
            continue
        if alt and alt in idx and des not in idx:
            des, alt = alt, None      # 表に ANA の便名でしか無ければ、最初から ANA の便名で調べる(1単位で済む)
        got, cover_ok = aero_lookup(m, aero, des)
        if not got and alt and aero.calls - used0 < limit:
            m["miss"].pop(des, None)
            got, cover_ok = aero_lookup(m, aero, alt)
            if not got:
                m["miss"][des] = today.isoformat()
        looked += 1
        if got:
            ok += 1
        if cover_ok or not got:
            m["seeded"][x] = today.isoformat()
        idx = key_index(m) if got else idx
    left = sum(1 for x in todo if (m["seeded"].get(x) or "") < old)
    log(f"[seed] 調べた {looked}（見つかった {ok}）、毎日運航なので省いた {skipped}、残り {left}")
    return {"seed_looked": looked, "seed_found": ok, "seed_skipped_daily": skipped, "seed_left": left,
            "seed_units": aero.calls - used0}


def runs_daily(recs):
    """直近14日(今のダイヤ期間)の運航日の記録が7つの曜日すべてにある記録を含むか"""
    lim = max((datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=14)).isoformat(),
              last_schedule_change().isoformat())
    for r in recs:
        wd = {datetime.date.fromisoformat(x).weekday() for x in r.get("obs", []) if x >= lim}
        if len(wd) == 7:
            return True
    return False


def run_daily(m, aero, osn, max_units):
    res0 = {}
    if aero and m["meta"].get("baseline_gaps") and max_units > 0:
        # 前回の全国取得で取りこぼした空港だけ取り直す(明日1日分)
        gaps = m["meta"]["baseline_gaps"]
        log(f"[daily] 前回の全国取得で取れなかった {len(gaps)} 件を取り直す")
        r = run_baseline(m, aero, max_units, jobs=[tuple(g) for g in gaps], full=False)
        max_units -= r["units"]
        res0 = {"gap_units": r["units"], "gap_left": len(m["meta"].get("baseline_gaps") or [])}
    seen, days, os_obs = collect_opensky(osn, m)
    n_obs = apply_opensky_obs(m, os_obs)
    log(f"[daily] 運航日の記録に {n_obs} 件を反映")
    idx = key_index(m)
    stale = (today_jst() - datetime.timedelta(days=STALE_DAYS)).isoformat()
    today = today_jst().isoformat()
    # 表に無い(または記録が古い)便名は「調べる待ち」に積む。上限で今回調べきれなかった分は次回以降に回す
    pend = m["pending"]
    # 全国の取り直し(STD/STA付き)が済むまでは、時刻が無いだけの便は調べない(取り直しで入るため)。
    # 済んだ後は、時刻が無い記録と、時刻が前のダイヤ期間のままの記録を、見かけたら調べ直す。
    # ただしダイヤ改正後の全国取り直しがまだなら、前のダイヤの時刻のままの便は調べない(毎日運航の便は取り直しで入るため、
    # 改正直後の残り単位は曜日限定便の確認に回す)
    ch = last_schedule_change().isoformat()
    base_after_ch = (m["meta"].get("baseline_date") or "") >= ch
    need_time = lambda recs: bool(m["meta"].get("std_ok")) and not all(
        r.get("std") and ((r.get("sd") or "") >= ch or not base_after_ch) for r in recs)
    for c, n in seen.items():
        if c in m["miss"]:
            continue
        recs = idx.get(c)
        # 記録が新しく、STD/STA もそろっていれば調べない(時刻の無い古い記録は、見かけたら調べ直して時刻を入れる)
        if recs and max(r["last"] for r in recs) >= stale and not need_time(recs):
            continue
        p = pend.get(c) or {"n": 0, "first": today}
        p["n"] += n
        p["seen"] = today
        pend[c] = p
    # 未登録を優先、次に見かけた回数の多い順
    order = []
    for c, p in pend.items():
        recs = idx.get(c)
        if c in m["miss"] or (recs and max(r["last"] for r in recs) >= stale and not need_time(recs)):
            continue
        order.append((0 if not recs else 1, -p["n"], c))
    order.sort()
    # 初回や取り直しで何日分もまとめて集めたときは、その分だけ多めに調べる(最大 CATCHUP_LOOKUP_MAX 件)
    cap = min(max(DAILY_LOOKUP_CAP, DAILY_LOOKUP_CAP * len(days)), CATCHUP_LOOKUP_MAX)
    limit = min(cap, max_units)
    if is_month_end_utc():
        # 月末は今月の残りを使い切る。半分は実際に飛んでいる未登録の便名に、残りは曜日限定便の確認(seed)に
        limit = max(cap, (max_units - SEED_MONTH_END_KEEP) // 2)
        limit = max(0, min(limit, max_units - SEED_MONTH_END_KEEP))
    log(f"[daily] OpenSky {', '.join(days) or '(新しい日なし)'}: {len(seen)} 便名、調べる待ち {len(order)} 件、今回調べる上限 {limit} 件")
    used0 = aero.calls if aero else 0
    ok = 0
    looked = []
    if aero:
        for _, _, c in order[:limit]:
            if out_of_time():
                log("[daily] 時間切れのため残りは次回")
                break
            if aero_lookup(m, aero, c)[0]:
                ok += 1
            looked.append(c)
            pend.pop(c, None)
    for c in [c for c in pend if c in m["miss"]]:
        pend.pop(c, None)
    res = {**res0, "mode": "daily", "opensky_days": days, "seen": len(seen), "pending": len(order),
           "looked": len(looked), "found": ok, "left": len(pend), "units": (aero.calls - used0) if aero else 0}
    if aero:
        res.update(run_seeds(m, aero, max_units - res["units"]))
        res["units"] = aero.calls - used0 + res0.get("gap_units", 0)
    return res


# ---------------------------------------------------------------- 公開用ファイル
def build_public(m):
    recs = sorted(m["flights"].values(), key=lambda r: (r["op"], r["o"], r["d"]))
    f = []
    k = {}
    today_u = datetime.datetime.now(datetime.timezone.utc).date()
    ch = last_schedule_change().isoformat()
    # 運航日の記録は「今のダイヤ期間」のものだけ使う(改正前の曜日を持ち越さない)
    lim_o = max((today_u - datetime.timedelta(days=OBS_KEEP_DAYS)).isoformat(), ch)
    os_start = m["meta"].get("os_start")
    trust_lim = (today_u - datetime.timedelta(days=WEEKDAY_TRUST_DAYS)).isoformat()
    os_trusted = bool(os_start) and os_start <= trust_lim and ch <= trust_lim
    lk_lim = max((today_jst() - datetime.timedelta(days=LK_TRUST_DAYS)).isoformat(), ch)
    for i, r in enumerate(recs):
        # 運航曜日(出発予定のUTC日付の曜日、月=bit0)。w=0 や k=0 は「不明」としてアプリは毎日表示する
        # 運航日が2日以上記録されていて、OpenSky を十分続けて集めた(または便名検索で前後の運航を確かめた)ときだけ「確か」
        w, cnt = 0, 0
        for x in r.get("obs", []):
            if x >= lim_o:
                w |= 1 << datetime.date.fromisoformat(x).weekday()
                cnt += 1
        kn = 1 if cnt >= 2 and os_trusted and (r["o"] in OS_AIRPORTS or r["d"] in OS_AIRPORTS) else 0
        # 便名検索で前後約12日の運航を確かめた記録は、その曜日を使う(最近の OpenSky の実績も足す)
        if (r.get("lk") or "") >= lk_lim and r.get("lkw"):
            w, kn = (w | r["lkw"]), 1
        # ts: 時刻が今のダイヤ期間の運航から取ったものなら1、前のダイヤ期間のままなら0
        ts = 1 if (r.get("sd") or "") >= ch else 0
        f.append([r["op"], r["o"], r["d"], r.get("t", ""), r["last"].replace("-", ""),
                  r.get("std", ""), r.get("sta", ""), r.get("fn", ""), w, kn, ts])
        for key in r["keys"]:
            k.setdefault(key, []).append(i)
    # VRS(過去の飛行記録からの参考データ)は精度が低いので使わない(ユーザー判断)
    vr = {}
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
        "ap": ap,
        "ia": m["iata"],
        "f": f,
        "k": {kk: (vv[0] if len(vv) == 1 else vv) for kk, vv in k.items()},
        "vr": vr,
    }


# ---------------------------------------------------------------- main
def last_sunday(year, month):
    d = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else datetime.date(year, 12, 31)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def want_baseline(m):
    bd = m["meta"].get("baseline_date")
    today = today_jst()
    if not bd:
        return True, "土台がまだ無い"
    # STD/STA を記録するようになる前(build 278 より前)の記録が多いなら、時刻を入れるために取り直す
    recs = list(m["flights"].values())
    if not m["meta"].get("std_ok") and recs and sum(1 for r in recs if r.get("std")) < len(recs) * 0.5:
        return True, "STD/STA が未取得の記録が多い"
    bd = datetime.date.fromisoformat(bd)
    if (today - bd).days >= BASELINE_EVERY_DAYS:
        return True, f"前回から {(today - bd).days} 日"
    for mon in (3, 10):     # ダイヤ改正(3月・10月の最終日曜)の2〜14日後に取り直す
        ch = last_sunday(today.year, mon)
        if bd < ch and 2 <= (today - ch).days <= 14:
            return True, f"ダイヤ改正（{ch}）後"
    return False, ""


def save_all(m):
    expire(m)
    os.makedirs(os.path.dirname(MASTER), exist_ok=True)
    save_json(MASTER, m)
    pub = build_public(m)
    save_json(PUBLIC, pub, compact=True)
    log(f"[out] flightdb.json: 記録 {len(pub['f'])} 件、検索キー {len(pub['k'])} 件、"
        f"{os.path.getsize(PUBLIC)//1024} KB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "baseline", "daily", "rebuild"])
    ap.add_argument("--scheduled", action="store_true",
                    help="定期実行。同じ日(UTC)にすでに最後まで実行できていれば何もしない(予備の定期実行用)")
    a = ap.parse_args()

    m = load_master()
    mode = a.mode
    utc_today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if a.scheduled and mode != "rebuild" and any(r.get("utc") == utc_today and r.get("ok") for r in m["meta"]["runs"]):
        log(f"[skip] 今日（UTC {utc_today}）の更新は実行済みなので何もしない")
        return
    summary = {"date": today_jst().isoformat(), "utc": utc_today, "ok": False}

    if mode == "rebuild":
        save_all(m)
        return
    try:
        key = os.environ.get("AEROAPI_KEY", "").strip()
        aero = Aero(key, m) if key else None
        if not aero:
            log("[warn] AEROAPI_KEY がありません（AeroAPI は使いません）")
        else:
            aero.sync_usage()
            aero.on_checkpoint = lambda: save_json(MASTER, m)   # 途中で止まっても使った分の結果を残す
        rem = aero.remaining_units() if aero else 0
        log(f"[budget] 今月 {units_used(m)} / {MONTH_CAP_UNITS} 単位使用済み（残り {rem}）")
        cid = os.environ.get("OPENSKY_CLIENT_ID", "").strip()
        sec = os.environ.get("OPENSKY_CLIENT_SECRET", "").strip()
        then_daily = False
        if mode == "auto":
            want, why = want_baseline(m)
            if want and aero and rem >= BASELINE_EST_UNITS:
                mode = "baseline"
                then_daily = True
                log(f"[auto] baseline を実行（{why}）")
            else:
                if want:
                    log(f"[auto] baseline が必要（{why}）だが今月の残りが足りないので daily を実行")
                mode = "daily"
        if mode == "baseline":
            if not aero:
                log("[error] baseline には AEROAPI_KEY が必要"); sys.exit(1)
            rb = run_baseline(m, aero, max(0, rem))
            summary.update(rb)
            # 最後まで取れたら、同じ実行で daily(OpenSky の集計・未登録便の確認・曜日限定便の確認)も行う
            if then_daily and rb["complete"] and cid and sec:
                rem2 = aero.remaining_units()
                log(f"[auto] 続けて daily を実行（今月の残り {rem2}）")
                rd = run_daily(m, aero, OpenSky(cid, sec), max(0, rem2))
                summary["mode"] = "baseline+daily"
                summary["daily"] = rd
        else:
            if not cid or not sec:
                log("[error] OpenSky の認証情報がありません"); sys.exit(1)
            summary.update(run_daily(m, aero, OpenSky(cid, sec), max(0, rem)))
        summary["ok"] = True
    finally:
        summary["month_units"] = units_used(m)
        log("[summary]", json.dumps(summary, ensure_ascii=False))
        m["meta"]["runs"] = (m["meta"]["runs"] + [summary])[-40:]
        save_all(m)


if __name__ == "__main__":
    main()
