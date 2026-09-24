#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tile_downloader.py
===================
国土地理院（GSI）の航空写真タイル（seamlessphoto）を、指定した空港周辺の
範囲だけ取得・合成して、1枚の画像＋緯度経度の対応情報を作るスクリプト。

【使い方】
1. インターネットに接続されたPC（会社PC・自宅PC等）でこのスクリプトを実行する。
     pip install requests pillow --break-system-packages   (環境により不要な場合あり)

   A) GUIで使う場合（推奨・追加インストール不要、Python標準のtkinter使用）
        python3 tile_downloader.py
      空港をチェックボックスで選択し、「ダウンロード開始」ボタンを押すだけ。
      進行状況（空港ごと・タイルごと）がプログレスバーとログで確認できる。

   B) コマンドラインで使う場合
        python3 tile_downloader.py hakodate
      複数まとめて取りたい場合は
        python3 tile_downloader.py all

2. 実行が終わると、同じフォルダに空港ごとに
     map_data_<空港キー>.js   （例: map_data_haneda.js）
   ができる（build 175でこの個別ファイル方式に戻した。経緯は下記【統合版について】参照）。
   これを approach_planner.html と同じフォルダに置けば衛星画像が表示される。
   同じ空港を取り直すと、その空港のファイルだけが差し替わる（他空港には影響しない）。

   ※ build 126〜173で採用していた統合版 map_data_all.js が手元に残っている場合は
        python3 tile_downloader.py split
     で空港ごとの個別ファイルに分割できる（分割後も map_data_all.js 自体は残る。
     消したい場合は `split --delete-merged`）。逆に個別ファイルをまとめたい場合は
     従来どおり `python3 tile_downloader.py merge` が使える。

   【統合版について（build 126〜173の経緯）】
     以前は全空港を1つの map_data_all.js にまとめる方式だった（起動時の二重読み込み防止が
     目的）。しかしオンライン配信を検討する中で、hosting各社のファイルサイズ上限
     （Cloudflare Pages 25MiB/ファイル、GitHub 100MB/ファイル等）に対し、9空港分をまとめた
     単一ファイルは数十MB〜100MB級になり通らないことが判明。空港ごとに分割して必要な分だけ
     読む設計に戻す必要が生じたため、build 175でtile_downloader.pyの既定出力を
     空港ごとの個別ファイルに戻した（approach_planner.html側も対応する
     <script src="map_data_<空港キー>.js"> を空港数ぶん読み込むよう変更済み）。

3. 取得には空港1つあたり数分かかる。**取得中はPCがスリープしないよう自動で抑止する**
   （Windows: SetThreadExecutionState / macOS: caffeinate / Linux: systemd-inhibit）。
   抑止するのはシステムのアイドルスリープだけで、画面は消えてよい。
   フタを閉じた場合や手動スリープは抑止できない。

【取得の速さについて】
  タイルは同時 MAX_WORKERS 本で並列に取得し、全体のリクエスト毎秒を MAX_RPS で頭打ちにする。
  所要時間はおおよそ「総タイル数 ÷ MAX_RPS」。9空港ぶん(画像5,029枚+標高1,498枚)なら
  標準の25rpsで約4〜5分。回線速度はまず律速しない（転送量780MBは500Mbpsなら13秒ぶん）。
  GUIの「取得速度」、またはコマンドラインの --rps=N / --workers=N で変更できる。
  上げるほど相手サーバー(国土地理院・Esri)への負荷が増えるので、常識的な範囲で。

【注意】
- 地理院タイルの利用は国土地理院の利用規約に従うこと（出典表示等）。
  https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html
- Googleマップの衛星画像はオフライン保存・再配布がAPI利用規約で禁止されているため、
  このツールでは使用していない。
- AIRPORTS辞書の緯度経度・ズームレベルは「ダウンロード範囲を決めるための概算値」。
  取得した画像が実際の空港と正しく重なっているかは、必ず目視で確認すること。
- 滑走路の正確な閾値座標・方位は、このスクリプトでは扱っていない
  （それは approach_planner.html 側のAIRPORTSで別途管理する）。
  運航judgmentに使う場合は、必ず現行AIP/会社チャートの数値で置き換えて検証すること。
"""

import os
import re
import sys
import math
import base64
import json
import time
import platform
import subprocess
import contextlib
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

try:
    import requests
except ImportError:
    print("requestsが見つかりません。 pip install requests --break-system-packages を実行してください。")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("Pillowが見つかりません。 pip install pillow --break-system-packages を実行してください。")
    sys.exit(1)


# ------------------------------------------------------------------
# 空港ごとの「ダウンロード範囲の中心座標」（空港基準点(ARP)付近の座標）
# 半径は「その空港の滑走路パターン全体が入る」ように広めに設定。
# 必要なら後で自由に追加・修正してよい。
# ------------------------------------------------------------------
AIRPORTS = {
    "hakodate": {
        "name": "函館空港 (RJCH)",
        "center_lat": 41.770000,   # 41°46'12"N (AIP RJCH AD2 ARP 414612N準拠、確認済み)
        "center_lon": 140.821944,  # 140°49'19"E (AIP RJCH AD2 ARP 1404919E準拠、確認済み)
        "radius_nm": 6.0,
    },
    "chitose": {
        "name": "新千歳空港 (RJCC)",
        "center_lat": 42.775278,   # 42°46'31"N (AIP RJCC AD2 ARP準拠、確認済み)
        "center_lon": 141.692500,  # 141°41'33"E (AIP RJCC AD2 ARP準拠、確認済み)
        "radius_nm": 6.0,
    },
    "hiroshima": {
        "name": "広島空港 (RJOA)",
        "center_lat": 34.436111,   # 34°26'10"N (AIP RJOA AD2 ARP 342610N準拠、確認済み)
        "center_lon": 132.919444,  # 132°55'10"E (AIP RJOA AD2 ARP 1325510E準拠、確認済み)
        "radius_nm": 6.0,
    },
    "takamatsu": {
        "name": "高松空港 (RJOT)",
        "center_lat": 34.214167,   # 34°12'51"N (AIP RJOT AD2 ARP 341251N準拠、確認済み)
        "center_lon": 134.015556,  # 134°00'56"E (AIP RJOT AD2 ARP 1340056E準拠、確認済み)
        "radius_nm": 6.0,
    },
    "matsuyama": {
        "name": "松山空港 (RJOM)",
        "center_lat": 33.827222,   # 33°49'38"N (確認済み)
        "center_lon": 132.699722,  # 132°41'59"E (確認済み)
        "radius_nm": 6.0,
    },
    "fukuoka": {
        "name": "福岡空港 (RJFF)",
        "center_lat": 33.584444,   # 33°35'04"N (確認済み)
        "center_lon": 130.451667,  # 130°27'06"E (確認済み)
        "radius_nm": 6.0,
    },
    "haneda": {
        "name": "羽田空港 (RJTT)",
        "center_lat": 35.553333,   # 35°33'12"N (確認済み)
        "center_lon": 139.781111,  # 139°46'52"E (確認済み)
        "radius_nm": 6.0,
    },
    "itami": {
        "name": "伊丹空港 (RJOO)",
        "center_lat": 34.784444,   # 34°47'04"N (確認済み)
        "center_lon": 135.439167,  # 135°26'21"E (確認済み)
        "radius_nm": 6.0,
    },
    "kumamoto": {
        "name": "熊本空港 (RJFT)",
        "center_lat": 32.837222,   # 32°50'14"N (AIP RJFT AD2 ARP 325014N準拠、確認済み)
        "center_lon": 130.855278,  # 130°51'19"E (AIP RJFT AD2 ARP 1305119E準拠、確認済み)
        "radius_nm": 6.0,
    },
}
# ※ 衛星画像のズームレベルは空港ごとの個別値ではなく、下の IMAGE_ZOOM で
#   全空港共通に指定する（build 160。標高のDEM_SOURCE選択と同じ考え方）。

TILE_SOURCES = {
    "esri": {
        "url": "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    },
    "gsi": {
        "url": "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
        "attribution": "地図: 国土地理院 航空写真タイル (seamlessphoto)",
    },
}
TILE_SOURCE = "esri"  # "esri" または "gsi" に切り替え可能
# 衛星画像の保存形式。"jpeg"(既定・従来どおり)か "webp"(build 159で追加)。
# WebPは同じ画質設定でJPEGより一般に20〜35%ほど小さくなる(業界共通のベンチマークによる。
# 実際の圧縮率は写真の内容に依存するため、まずは1空港だけ--webpで試して
# ファイルサイズを比較してから全空港に適用することを推奨)。
# iOS 14以降のSafari（現行の全iPadで対応）ならWebPも通常のJPEGと同様にネイティブ表示できる。
IMAGE_FORMAT = "jpeg"
# 衛星画像のズームレベル。DEMの dem/dem5a 選択と同じ発想で、全空港共通で選べるようにしてある(build 160)。
#   15: 既定・高精細（滑走路縁や誘導路標示までくっきり見える）
#   14: 軽量・ズーム1段階下げるとタイル数(=データ量)はおおよそ1/4になる
#       （JPEG/WebPの画質調整による20〜35%減よりずっと効果が大きい）。
#       ただし解像度は明確に落ちる（目視でもややぼやける）。進入経路確認用途で
#       どこまで粗くても許容できるかは、実際に1空港試し取りして見比べてから判断すること。
# タイルベースの地図は日本の空港周辺（市街地・平地）ならどのズームでも普通にタイルが
# 存在するため、DEMのdem5aのような「無い場所は欠測になる」というカバレッジの制約は無い。
# 純粋に「鮮明さ」対「ファイルサイズ」のトレードオフ。
IMAGE_ZOOM = 15
# 注意: EsriのWorld Imageryは個人利用の範囲では一般的に許容されているが、
# 大量ダウンロード・再配布には制限があるAPI利用規約に基づく。
# 商用利用や広範囲配布を行う場合は必ずEsriの利用規約を確認すること。
TILE_SIZE = 256
NM_IN_M = 1852.0

# ------------------------------------------------------------------
# タイル取得の並列化とレート制限
# ------------------------------------------------------------------
# 以前は「1枚ずつ直列 + 1枚ごとに0.05秒スリープ」だったため、回線が速くても
# 所要時間は往復遅延(RTT)と枚数でほぼ決まっていた（9空港で10〜17分）。
# 転送量780MBは500Mbpsなら13秒ぶんでしかなく、**帯域は全く律速していない**。
#
# そこで「同時に複数枚取る」+「全体のリクエスト毎秒(RPS)に上限を掛ける」形に変えた。
# 並列にしてもRPSで頭打ちにするので、サーバーへの負荷は上限値そのもので決まり、
# RTTが大きい環境でも小さい環境でも同じ負荷・同じ所要時間に収束する。
#
#   所要時間の目安 ≒ 総タイル数 / MAX_RPS
#   9空港ぶん6,527枚 なら 25rps で約4.4分（従来の1/2〜1/4）。
#
# MAX_RPSを上げればさらに速くなるが、それはそのまま相手サーバーへの負荷なので、
# 個人が一度だけ取る用途として常識的な範囲に留めてある。
MAX_WORKERS = 8        # 同時接続数
MAX_RPS = 25.0         # 全スレッド合計のリクエスト毎秒の上限（0以下で無制限）


class _RateLimiter:
    """全スレッド共通で「リクエスト開始の間隔」を空ける。"""
    def __init__(self, rps):
        self.interval = (1.0 / rps) if rps and rps > 0 else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        if self.interval <= 0:
            return
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        delay = at - time.monotonic()
        if delay > 0:
            time.sleep(delay)


_tls = threading.local()


def _session():
    """requests.Session はスレッドごとに持つ（コネクション再利用のため）。"""
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "flight-planning-tool/1.0 (personal offline use)"})
        # 同時接続数ぶんのコネクションプールを確保しておく
        try:
            ad = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS,
                                               pool_maxsize=MAX_WORKERS)
            s.mount("https://", ad); s.mount("http://", ad)
        except Exception:
            pass
        _tls.session = s
    return s


def _fetch_bytes(url, limiter, log_func, label="タイル"):
    """1枚取る。取れなければ None（404＝データ無しの区画も None）。"""
    for attempt in range(3):
        limiter.wait()
        try:
            resp = _session().get(url, timeout=20)
            if resp.status_code == 200 and resp.content:
                return resp.content
            if resp.status_code == 404:
                return None            # 海上など、そもそもデータが無い
            log_func(f"    [警告] {label}欠落 {url} status={resp.status_code}")
            return None
        except Exception as e:
            if attempt == 2:
                log_func(f"    [失敗] {url} -> {e}")
            else:
                log_func(f"    [リトライ {attempt+1}/3] {url} -> {e}")
                time.sleep(0.4 * (attempt + 1))
    return None


def _run_parallel(jobs, work, chunk, on_result, progress_func=None):
    """jobsをチャンクに切って並列実行する。結果は投入順に on_result へ渡す。

    一括で全部投げると、デコード済みタイルがメモリに山積みになる（画像1GB級）。
    チャンクに区切って「取る→使う→捨てる」を繰り返すことで使用量を抑える。
    """
    total = len(jobs)
    done = 0
    if progress_func: progress_func(0, total)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for i in range(0, total, chunk):
            part = jobs[i:i+chunk]
            for job, result in zip(part, ex.map(work, part)):
                on_result(job, result)
                done += 1
            if progress_func: progress_func(done, total)
    if progress_func: progress_func(total, total)


def deg2num(lat_deg, lon_deg, zoom):
    """緯度経度 -> タイル番号（Webメルカトル / スリッピーマップ方式）"""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = (lon_deg + 180.0) / 360.0 * n
    ytile = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def num2deg(xtile, ytile, zoom):
    """タイル番号 -> タイル左上角の緯度経度"""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def bbox_from_center(center_lat, center_lon, radius_nm):
    """中心座標＋半径(NM)から、簡易的な緯度経度の矩形範囲を作る"""
    dlat = (radius_nm * NM_IN_M) / 111320.0  # 緯度1度 ≈ 111.32km
    dlon = dlat / math.cos(math.radians(center_lat))
    return {
        "north": center_lat + dlat,
        "south": center_lat - dlat,
        "east": center_lon + dlon,
        "west": center_lon - dlon,
    }


def fetch_and_stitch(bbox, zoom, log_func=print, progress_func=None):
    x_min_f, y_min_f = deg2num(bbox["north"], bbox["west"], zoom)
    x_max_f, y_max_f = deg2num(bbox["south"], bbox["east"], zoom)

    x_min, x_max = int(math.floor(x_min_f)), int(math.floor(x_max_f))
    y_min, y_max = int(math.floor(y_min_f)), int(math.floor(y_max_f))

    n_cols = x_max - x_min + 1
    n_rows = y_max - y_min + 1
    total = n_cols * n_rows
    log_func(f"  タイル数: {n_cols} x {n_rows} = {total} 枚 (zoom={zoom}) "
             f"／ 同時{MAX_WORKERS}本・最大{MAX_RPS:.0f}rps → 約{total/max(MAX_RPS,1)/60:.1f}分")
    if total > 1500:
        log_func(f"  [警告] タイル数が多すぎます({total}枚)。zoomを下げるかradius_nmを小さくすることを推奨します。")
        log_func("  10秒後に開始します。中断する場合は Ctrl+C を押してください...")
        time.sleep(10)

    canvas = Image.new("RGB", (n_cols * TILE_SIZE, n_rows * TILE_SIZE), (40, 40, 40))

    limiter = _RateLimiter(MAX_RPS)
    jobs = [(row, col, xtile, ytile)
            for row, ytile in enumerate(range(y_min, y_max + 1))
            for col, xtile in enumerate(range(x_min, x_max + 1))]

    def work(job):
        _, _, xtile, ytile = job
        url = TILE_SOURCES[TILE_SOURCE]["url"].format(z=zoom, x=xtile, y=ytile)
        data = _fetch_bytes(url, limiter, log_func, "画像タイル")
        if data is None:
            return None
        try:
            return Image.open(BytesIO(data)).convert("RGB")
        except Exception as e:
            log_func(f"    [警告] 画像を開けません z={zoom} x={xtile} y={ytile} -> {e}")
            return None

    missing = [0]
    def on_result(job, tile_img):
        row, col, _, _ = job
        if tile_img is None:
            missing[0] += 1
            return
        # PILのpasteはメインスレッドだけで行う（スレッド安全でないため）
        canvas.paste(tile_img, (col * TILE_SIZE, row * TILE_SIZE))

    _run_parallel(jobs, work, MAX_WORKERS * 8, on_result, progress_func)
    if missing[0]:
        log_func(f"  [警告] 取得できなかった画像タイル {missing[0]} 枚（その部分は灰色のまま）")

    # 実際に取得した範囲の正確な緯度経度境界（タイル格子の外周）
    north_actual, west_actual = num2deg(x_min, y_min, zoom)
    south_actual, east_actual = num2deg(x_max + 1, y_max + 1, zoom)

    actual_bounds = {
        "north": north_actual,
        "south": south_actual,
        "east": east_actual,
        "west": west_actual,
    }
    return canvas, actual_bounds


# ==================================================================
# 標高データ（DEM）の取得
# ==================================================================
# 出典：国土地理院「標高タイル」 https://maps.gsi.go.jp/development/demtile.html
#   dem    : 10mメッシュ (DEM10B) / zoom 14 / 日本全国をカバー → 既定
#   dem5a  : 5mメッシュ / zoom 15 / 整備済み区域のみ（無い所は欠測になる）
# 形式は .txt（256行×256列のカンマ区切り、単位m、欠測は "e"）。
#
# 用途は「進入経路まわりの山を見る」ことなので、10mメッシュで十分。
# 5mを使うとタイル数が4倍になり取得時間も4倍になる点に注意。
DEM_SOURCES = {
    "dem":   {"url": "https://cyberjapandata.gsi.go.jp/xyz/dem/{z}/{x}/{y}.txt",   "zoom": 14,
              "label": "10mメッシュ (全国)"},
    "dem5a": {"url": "https://cyberjapandata.gsi.go.jp/xyz/dem5a/{z}/{x}/{y}.txt", "zoom": 15,
              "label": "5mメッシュ (整備済み区域のみ)"},
}
DEM_SOURCE = "dem"
DEM_ATTRIBUTION = "標高: 国土地理院 標高タイル"
DEM_GRID_SIZE = 512          # 出力グリッドの一辺。12NM四方なら約43m間隔になる
DEM_OFFSET_M = 1000.0        # 符号化のための下駄（-1000m まで表現できるように）
DEM_SCALE = 10.0             # 0.1m 刻みで符号化する


def _parse_dem_tile(text):
    """標高タイル(.txt)を 256x256 の二次元配列にする。欠測("e")は None。"""
    rows = []
    for line in text.strip().split("\n"):
        vals = []
        for s in line.split(","):
            s = s.strip()
            if s == "" or s == "e":
                vals.append(None)
            else:
                try:
                    vals.append(float(s))
                except ValueError:
                    vals.append(None)
        rows.append(vals)
    return rows


def fetch_dem_grid(bounds, log_func=print, progress_func=None, size=DEM_GRID_SIZE):
    """画像と同じ bounds の範囲を、size x size の標高グリッドにして返す。

    グリッドの並びは approach_planner.html の latLonToFraction と同じ
    「緯度・経度に対して線形」。衛星画像のレイヤーとそのまま重なるようにするため。

    各出力セルには、そこに落ちる入力ピクセルの **最大値** を入れる。
    平均だと山頂が均されて消えるため、地形を見る用途では最大のほうが安全で見栄えも良い。
    """
    src = DEM_SOURCES[DEM_SOURCE]
    zoom = src["zoom"]
    x_min_f, y_min_f = deg2num(bounds["north"], bounds["west"], zoom)
    x_max_f, y_max_f = deg2num(bounds["south"], bounds["east"], zoom)
    x_min, x_max = int(math.floor(x_min_f)), int(math.floor(x_max_f))
    y_min, y_max = int(math.floor(y_min_f)), int(math.floor(y_max_f))
    n_cols, n_rows = x_max - x_min + 1, y_max - y_min + 1
    total = n_cols * n_rows
    log_func(f"  標高タイル: {n_cols} x {n_rows} = {total} 枚 "
             f"({DEM_SOURCE} / {src['label']} / zoom={zoom}) "
             f"→ 約{total/max(MAX_RPS,1)/60:.1f}分")

    grid = [[None] * size for _ in range(size)]
    north, south = bounds["north"], bounds["south"]
    west, east = bounds["west"], bounds["east"]
    dlat = north - south
    dlon = east - west
    if dlat <= 0 or dlon <= 0:
        log_func("  [エラー] boundsが不正です。標高の取得を中止します。")
        return None, {}

    limiter = _RateLimiter(MAX_RPS)
    jobs = [(xtile, ytile) for ytile in range(y_min, y_max + 1)
                           for xtile in range(x_min, x_max + 1)]

    def work(job):
        xtile, ytile = job
        url = src["url"].format(z=zoom, x=xtile, y=ytile)
        data = _fetch_bytes(url, limiter, log_func, "標高タイル")
        if data is None:
            return None
        try:
            return _parse_dem_tile(data.decode("utf-8", "replace"))
        except Exception as e:
            log_func(f"    [警告] 標高タイルを解釈できません z={zoom} x={xtile} y={ytile} -> {e}")
            return None

    missing_tiles = [0]
    def on_result(job, tile):
        xtile, ytile = job
        if tile is None:
            missing_tiles[0] += 1
            return
        lat_top, lon_left = num2deg(xtile, ytile, zoom)
        lat_bot, lon_right = num2deg(xtile + 1, ytile + 1, zoom)
        nrow = len(tile)
        ncol = len(tile[0]) if nrow else 0
        if not ncol:
            return
        # 出力セル番号はタイル内で行・列ごとに共通なので、先に一度だけ表にしておく。
        # 画素ごとに緯度経度を計算していたのを、この表引きに置き換えて内側ループを軽くした。
        gx_of = []
        for px in range(ncol):
            lon = lon_left + (lon_right - lon_left) * (px + 0.5) / ncol
            fx = (lon - west) / dlon
            gx_of.append(int(fx * size) if 0 <= fx < 1 else -1)
        for py in range(nrow):
            # タイル内のY方向はWebメルカトルなので厳密には非線形だが、
            # 1タイル(z14で約2km)の中では線形近似の誤差は1m未満で無視できる
            lat = lat_top + (lat_bot - lat_top) * (py + 0.5) / nrow
            fy = (north - lat) / dlat
            if not (0 <= fy < 1):
                continue
            grow = grid[int(fy * size)]
            row = tile[py]
            for px in range(ncol):
                h = row[px]
                if h is None:
                    continue
                gx = gx_of[px]
                if gx < 0:
                    continue
                cur = grow[gx]
                if cur is None or h > cur:
                    grow[gx] = h

    _run_parallel(jobs, work, max(8, MAX_WORKERS * 3), on_result, progress_func)
    missing_tiles = missing_tiles[0]

    filled = sum(1 for r in grid for v in r if v is not None)
    stats = {"cells": size * size, "filled": filled,
             "missingTiles": missing_tiles, "tiles": total}
    if filled == 0:
        log_func("  [エラー] 標高データが1点も取得できませんでした。")
        return None, stats
    log_func(f"  標高グリッド {size}x{size}: 有効 {filled} 点 "
             f"({filled*100.0/(size*size):.1f}%) / 取得できなかったタイル {missing_tiles} 枚")
    return grid, stats


def dem_grid_to_png_datauri(grid):
    """標高グリッドをPNGのdata URIにする。

    符号化: v = round((h + 1000) * 10)   → 0.1m刻み / -1000〜+5553m
            R = v >> 8, G = v & 255, B = 255(有効) / 0(欠測)
    ブラウザがPNGをネイティブに復号できるので、アプリ側は canvas に描いて
    getImageData で読むだけでよい（外部ライブラリ不要・完全オフライン）。

    ※ 検討はしたが不採用にした案（build159相当の検討時にHANDOVER 8.0zr参照）:
    Bチャンネル(欠測フラグのためだけの1バイト)を"LA"(グレースケール+アルファ)モードで
    削減する案を試したが、alpha<255のピクセルはブラウザがcanvas内部で色をアルファ
    「事前乗算」してから保持するため、getImageData()で読み戻すとRGB値が変化してしまう
    (Playwrightで実測: L=200,A=30 → 読み戻すとL=204に化ける等)。標高データの改ざんに
    直結する致命的な罠のため、この案は採用していない。DEMは9空港合計でも数MB程度と
    全体(100MB超)に占める割合が小さく、複雑さに見合わないとも判断した。
    """
    size = len(grid)
    img = Image.new("RGB", (size, size), (0, 0, 0))
    px = img.load()
    vmin, vmax = None, None
    for y in range(size):
        row = grid[y]
        for x in range(size):
            h = row[x]
            if h is None:
                px[x, y] = (0, 0, 0)       # B=0 が欠測の印
                continue
            if vmin is None or h < vmin: vmin = h
            if vmax is None or h > vmax: vmax = h
            v = int(round((h + DEM_OFFSET_M) * DEM_SCALE))
            v = max(0, min(65535, v))
            px[x, y] = (v >> 8, v & 255, 255)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)   # 可逆。標高は誤差が出ては困るのでJPEG不可
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}", vmin, vmax, len(buf.getvalue())


# ------------------------------------------------------------------
# build 175: 出力は空港ごとに map_data_<key>.js（1ファイル1空港）に戻した。
# 経緯：build 126〜173では全空港を map_data_all.js 1つにまとめていたが、
# オンライン配信を検討する中で各hostingのファイルサイズ上限
# （Cloudflare Pages 25MiB/ファイル、GitHub 100MB/ファイル等）に対し
# 9空港分の単一ファイルは数十MB〜100MB級になり通らないことが判明。
# 空港ごとに分割して必要な分だけ読み込む設計に戻すため、既定の出力先を
# 空港ごとの個別ファイルに戻した（approach_planner.html側の<script src>も対応済み）。
# 中身は旧来どおり「1空港＝1行」の
#     var MAP_DATA_<KEY> = {...};
# 形式なので、approach_planner.html / bundle_html.py 側は変更不要
# （どちらも元々この1行形式をキーごとに読む作りだったため）。
#
# map_data_all.js（統合版）を読み書きする load_merged/write_merged は、
# 過去に統合版で貯めたデータを個別ファイルに戻す split()、および逆方向の
# merge()（個別→統合、他用途で欲しい場合向けに残置）のために引き続き使う。
# ------------------------------------------------------------------
MERGED_NAME = "map_data_all.js"
MAP_DATA_LINE_PATTERN = re.compile(r'\s*var\s+MAP_DATA_([A-Za-z0-9_]+)\s*=')


def _entry_path(key, out_dir="."):
    return os.path.join(out_dir, f"map_data_{key.lower()}.js")


def _atomic_write_text(path, text):
    """一時ファイルに書いてから置き換える（途中失敗で既存データを失わないため）。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)   # 書き切れたときだけ差し替える
    except PermissionError as e:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
        raise PermissionError(
            f"{path} に書き込めませんでした。考えられる原因：\n"
            f"    1) このスクリプトが C:\\Program Files 等、書き込み権限のないフォルダに置かれている\n"
            f"       → デスクトップやドキュメント内の普通のフォルダに移動してください\n"
            f"    2) 同名ファイルが既に開かれている（エディタ等）\n"
            f"    3) OneDrive等の同期フォルダでロックされている\n"
            f"    (元エラー: {e})"
        ) from e


def load_merged(path=None, out_dir="."):
    """map_data_all.js（統合版）を読み、{空港キー: その行} の辞書にして返す（無ければ空）。
    split()/merge() の材料読み込み用。"""
    path = path or os.path.join(out_dir, MERGED_NAME)
    entries = {}
    if not os.path.exists(path):
        return entries
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = MAP_DATA_LINE_PATTERN.match(line)
            if m:
                entries[m.group(1).lower()] = line.rstrip("\n")
    return entries


def write_merged(entries, path=None, out_dir="."):
    """辞書を map_data_all.js（統合版）として書き出す。merge()用。"""
    path = path or os.path.join(out_dir, MERGED_NAME)
    text = "// 衛星画像データ（tile_downloader.py merge により生成した統合版）\n"
    text += f"// 収録: {len(entries)} 空港 ({', '.join(sorted(entries))})\n"
    for key in sorted(entries):
        text += entries[key] + "\n"
    _atomic_write_text(path, text)


ENTRY_OBJ_PATTERN = re.compile(r'^\s*var\s+MAP_DATA_[A-Za-z0-9_]+\s*=\s*(.*);\s*$', re.S)


def _entry_to_obj(line):
    """'var MAP_DATA_<KEY> = {...};' の1行を dict に戻す（読めなければ None）。"""
    if not line:
        return None
    m = ENTRY_OBJ_PATTERN.match(line)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _obj_to_entry(key, obj):
    # constだとwindow経由で参照できないためvarにする
    return f"var MAP_DATA_{key.upper()} = " + json.dumps(obj, ensure_ascii=False) + ";"


def load_entry(key, out_dir="."):
    """map_data_<key>.js を読み、dictにして返す（無ければNone）。"""
    path = _entry_path(key, out_dir)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = MAP_DATA_LINE_PATTERN.match(line)
            if m:
                return _entry_to_obj(line.rstrip("\n"))
    return None


def write_entry(key, obj, out_dir="."):
    """1空港ぶんの dict を map_data_<key>.js として書き出す。"""
    path = _entry_path(key, out_dir)
    text = f"// 衛星画像データ（tile_downloader.py により自動生成。{key}専用の個別ファイル）\n"
    text += _obj_to_entry(key, obj) + "\n"
    _atomic_write_text(path, text)
    return path


def update_entry(key, fields, out_dir=".", log_func=print):
    """1空港ぶんのエントリ(map_data_<key>.js)に fields をマージする（既存の他フィールドは残す）。

    画像と標高を別々のタイミングで取得できるようにするための仕組み。
    画像を取り直しても既に取得済みの標高データが消えない（逆も同じ）。
    """
    existing = load_entry(key, out_dir)
    if existing is None and os.path.exists(_entry_path(key, out_dir)):
        log_func(f"  [警告] {key} の既存データを解釈できなかったので作り直します"
                 f"（以前のフィールドは失われます）")
    obj = existing or {}
    obj.update(fields)
    path = write_entry(key, obj, out_dir)
    return obj, (existing is not None), path, os.path.getsize(path)


def save_to_merged(key, airport, image, bounds, out_dir=".", log_func=print):
    """取得した1空港ぶんの衛星画像を map_data_<key>.js に保存（既にあれば差し替え）する。"""
    buf = BytesIO()
    if IMAGE_FORMAT == "webp":
        image.save(buf, format="WEBP", quality=85, method=6)   # method=6: 圧縮率優先(やや遅い)
        mime = "image/webp"
    else:
        # optimize=True: 画質はquality=85のまま、ハフマン符号化を最適化するだけで
        # 数%ファイルサイズが減る(画質の劣化は一切ない、無条件に有効な最適化)
        image.save(buf, format="JPEG", quality=85, optimize=True)
        mime = "image/jpeg"
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    fields = {
        "name": airport["name"],
        "bounds": bounds,
        "imageDataURI": f"data:{mime};base64,{b64}",
        "attribution": TILE_SOURCES[TILE_SOURCE]["attribution"],
    }
    obj, replaced, path, total_bytes = update_entry(
        key, fields, out_dir=out_dir, log_func=log_func)

    size_mb = len(b64) * 3 / 4 / 1024 / 1024
    kept = "（標高データは保持）" if "dem" in obj else ""
    log_func(f"  -> {os.path.basename(path)} に{'差し替え' if replaced else '保存'}{kept}"
             f"（画像 約{size_mb:.1f}MB / ファイル全体 {total_bytes/1024/1024:.1f}MB）")


def save_dem_to_merged(key, airport, grid, bounds, stats, out_dir=".", log_func=print):
    """取得した標高グリッドを、その空港の map_data_<key>.js に追記する。"""
    uri, vmin, vmax, nbytes = dem_grid_to_png_datauri(grid)
    fields = {
        "name": airport["name"],
        "bounds": bounds,          # 標高は画像と同じboundsに揃えてある
        "dem": {
            "dataURI": uri,
            "size": len(grid),
            "minM": None if vmin is None else round(vmin, 2),
            "maxM": None if vmax is None else round(vmax, 2),
            "offsetM": DEM_OFFSET_M,
            "scale": DEM_SCALE,
            "layer": DEM_SOURCE,
            "zoom": DEM_SOURCES[DEM_SOURCE]["zoom"],
            "attribution": DEM_ATTRIBUTION,
            # 復号: v = R*256 + G ; h_m = v/scale - offsetM ; B=0 は欠測
            "encoding": "h_m = ((R*256+G)/scale) - offsetM ; B=0 means no data",
            "stats": stats,
        },
    }
    obj, replaced, path, total_bytes = update_entry(
        key, fields, out_dir=out_dir, log_func=log_func)
    has_img = "imageDataURI" in obj
    log_func(f"  -> 標高を{os.path.basename(path)}に{'差し替え' if replaced else '追加'}"
             f"（PNG {nbytes/1024:.0f}KB / 標高 {vmin:.0f}〜{vmax:.0f}m"
             f"{'' if has_img else ' / この空港はまだ衛星画像なし'}）")


# ------------------------------------------------------------------
# ダウンロード中だけPCがスリープしないようにする。
# 空港1つでもタイル数百枚＝数分かかるので、放置している間に
# スリープに入って取得が途中で止まるのを防ぐ。
# 抑止するのは「システムのアイドルスリープ」だけで、画面は消えてよい。
# 手動スリープやフタを閉じた場合は抑止できない（OSの仕様）。
# 設定に失敗しても取得自体は続行する。
#
# 【build 161で追加：スリープ検出ウォッチドッグ】
# 「抑止できた旨のログは出ていたのに、実際にはWindowsがスリープしてしまった」
# という報告があった。SetThreadExecutionState/caffeinate/systemd-inhibitは
# いずれもOSへの「お願い」であり、OEM製の独自電源管理ソフトやグループポリシー等
# によってOS側の判断で上書きされる(=抑止が効かない)ケースが実際にある。
# これを検知できないと、スリープ中に失敗したタイル取得がただの「404相当の欠測」
# として静かに処理され、画像が虫食いになったことに気づけない。
# そこで、抑止の成否によらず常に「一定間隔で待って、実際の経過時間が
# 想定よりずっと長ければ(=その間スリープしていた可能性が高い)警告を出す」
# ウォッチドッグを追加した。あわせてWindowsでは、1回きりの要求よりも確実という
# 報告があるため、このウォッチドッグの中でSetThreadExecutionStateを
# 定期的に(20秒おきに)再要求する。
# ------------------------------------------------------------------
_SLEEP_WATCHDOG_INTERVAL_S = 20.0


def _sleep_watchdog(stop_event, log_func, system, status_func):
    """一定間隔ごとに実経過時間を測り、想定よりずっと長ければ
    「その間PCがスリープしていた」とみなして警告する。
    Windowsでは、ついでにSetThreadExecutionStateを定期的に再要求する
    （1回だけの要求より確実という報告があるため。実害はないので他OSでも
    害はないが、他OSでは不要なので行わない）。
    """
    warned = False
    last = time.time()
    while not stop_event.wait(_SLEEP_WATCHDOG_INTERVAL_S):
        now = time.time()
        gap = now - last
        last = now
        if system == "Windows":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            except Exception:
                pass
        # 多少のOS/GCの遅延は許容し、想定間隔の2.5倍以上ずれていたらスリープとみなす
        if gap > _SLEEP_WATCHDOG_INTERVAL_S * 2.5:
            warned = True
            log_func(f"  [警告] 直前の約{gap:.0f}秒間、PCがスリープ状態だった可能性があります"
                     f"（スリープ抑止が効いていなかった疑いがあります）。この間に取得しようと"
                     f"していたタイルは、取得失敗＝海上等のデータ無しと区別がつかないまま"
                     f"欠測扱いになっている可能性があります。取得完了後、念のためこの実行で"
                     f"扱っていた空港を再取得することをおすすめします。")
            status_func("warn", f"⚠ スリープを検出（約{gap:.0f}秒間）。この空港は再取得推奨")
    return warned


@contextlib.contextmanager
def keep_awake(log_func=print, status_func=None):
    """status_func(state, text) は state in {"ok","fail","warn"} で
    現在のスリープ抑止状態を通知する（GUIの常時表示ラベル用。省略時は何もしない）。
    ログ(log_func)はスクロールして流れて見えなくなるため、GUIでは常時見える
    状態表示を別途用意できるようにした（build 161）。
    """
    if status_func is None:
        status_func = lambda state, text: None
    system = platform.system()
    release = None
    watchdog_stop = threading.Event()
    watchdog_thread = threading.Thread(
        target=_sleep_watchdog, args=(watchdog_stop, log_func, system, status_func), daemon=True)
    watchdog_thread.start()
    try:
        if system == "Windows":
            try:
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                # 注意: SetThreadExecutionStateはスレッド単位。
                # GUIは別スレッドで取得するので、必ず取得を行うスレッドから呼ぶこと。
                kernel32 = ctypes.windll.kernel32
                if kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                    release = lambda: kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                    log_func("  (取得中はPCがスリープしないようにしています)")
                    status_func("ok", "スリープ抑止: 有効（Windows）")
                else:
                    log_func("  [注意] スリープ抑止を設定できませんでした（取得は続行します）")
                    status_func("fail", "スリープ抑止: 設定できませんでした")
            except Exception as e:
                log_func(f"  [注意] スリープ抑止を設定できませんでした（取得は続行します）: {e}")
                status_func("fail", "スリープ抑止: 設定できませんでした")
        else:
            # macOS/Linuxは外部コマンドに任せる。コマンドが存在しても
            # 即座に失敗することがある(例: systemdセッションの無い環境で
            # "Failed to connect to bus")ので、起動直後に生存確認してから
            # 「抑止できた」と表示する。できていないのに出来たと言わないため。
            if system == "Darwin":
                cmd = ["caffeinate", "-i", "-w", str(os.getpid())]
            else:
                # "sleep infinity" ではなく自分のPIDを見張らせる。
                # GUIの取得はdaemonスレッドで動くため、ウィンドウを閉じて
                # プロセスが落ちるとfinallyが走らないことがある。その場合でも
                # この子プロセスが自分で終了して抑止が解ける。
                watch = f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 5; done"
                cmd = ["systemd-inhibit", "--what=idle", "--who=tile_downloader",
                       "--why=satellite image download", "--mode=block", "sh", "-c", watch]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, text=True)
                time.sleep(0.3)   # すぐ落ちないか見る
                if proc.poll() is None:
                    release = proc.terminate
                    log_func("  (取得中はPCがスリープしないようにしています)")
                    status_func("ok", f"スリープ抑止: 有効（{cmd[0]}）")
                else:
                    err = (proc.stderr.read() or "").strip().splitlines()
                    why = err[0] if err else f"{cmd[0]} が終了コード{proc.returncode}で終了"
                    log_func(f"  (スリープ抑止は効きませんでした: {why[:80]} / 取得は続行します)")
                    status_func("fail", f"スリープ抑止: 効きませんでした（{why[:40]}）")
            except FileNotFoundError:
                log_func(f"  (この環境では{cmd[0]}が無くスリープ抑止に未対応です。"
                         f"必要なら電源設定で調整してください)")
                status_func("fail", f"スリープ抑止: {cmd[0]}が見つかりません")
            except Exception as e:
                log_func(f"  (スリープ抑止を設定できませんでした: {e} / 取得は続行します)")
                status_func("fail", "スリープ抑止: 設定できませんでした")
        yield
    finally:
        watchdog_stop.set()
        if release is not None:
            try:
                release()
            except Exception:
                pass


def _existing_bounds(key, out_dir="."):
    """既に取得済みのboundsを返す（無ければNone）。標高だけ後から足すときに使う。"""
    obj = load_entry(key, out_dir)
    return obj.get("bounds") if obj else None


def process(key, log_func=print, progress_func=None, with_image=True, with_dem=True):
    """1空港ぶんを取得する。画像と標高はそれぞれ独立にON/OFFできる。

    標高だけを後から足す場合(with_image=False)は、既に保存されているboundsを使って
    画像と完全に同じ範囲でグリッドを作る。boundsが無ければ画像と同じ計算で求める。
    """
    if key not in AIRPORTS:
        log_func(f"未登録の空港キー: {key}  (登録済み: {', '.join(AIRPORTS.keys())})")
        return
    airport = AIRPORTS[key]
    bbox = bbox_from_center(airport["center_lat"], airport["center_lon"], airport["radius_nm"])

    actual_bounds = None
    if with_image:
        log_func(f"[{key}] {airport['name']} の画像を取得中...")
        image, actual_bounds = fetch_and_stitch(bbox, IMAGE_ZOOM,
                                                log_func=log_func, progress_func=progress_func)
        save_to_merged(key, airport, image, actual_bounds, log_func=log_func)

    if with_dem:
        if actual_bounds is None:
            actual_bounds = _existing_bounds(key)
            if actual_bounds is None:
                # 画像がまだ無い場合は、画像を取ったときと同じタイル格子の外周を計算で求める
                z = IMAGE_ZOOM
                x0, y0 = deg2num(bbox["north"], bbox["west"], z)
                x1, y1 = deg2num(bbox["south"], bbox["east"], z)
                n0, w0 = num2deg(int(math.floor(x0)), int(math.floor(y0)), z)
                s1, e1 = num2deg(int(math.floor(x1)) + 1, int(math.floor(y1)) + 1, z)
                actual_bounds = {"north": n0, "south": s1, "east": e1, "west": w0}
                log_func(f"[{key}] 衛星画像が未取得のため、boundsを計算で求めました")
        log_func(f"[{key}] {airport['name']} の標高データを取得中...")
        grid, stats = fetch_dem_grid(actual_bounds, log_func=log_func, progress_func=progress_func)
        if grid:
            save_dem_to_merged(key, airport, grid, actual_bounds, stats, log_func=log_func)
        else:
            log_func(f"[{key}] 標高データの取得に失敗しました（画像は上記のとおり）")

    log_func(f"[{key}] 完了。 bounds = {json.dumps(actual_bounds, ensure_ascii=False)}")


# ==================================================================
# GUI（tkinter・標準ライブラリのみ、追加インストール不要）
# ==================================================================
def launch_gui():
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk, scrolledtext

    root = tk.Tk()
    root.title("衛星画像ダウンローダー")
    root.geometry("680x560")

    q = queue.Queue()
    vars_by_key = {}

    top = tk.Frame(root, padx=10, pady=8)
    top.pack(fill="x")
    tk.Label(top, text="ダウンロードする空港を選択：", font=("", 11, "bold")).pack(anchor="w")

    grid = tk.Frame(top)
    grid.pack(fill="x", pady=4)
    keys = list(AIRPORTS.keys())
    for i, key in enumerate(keys):
        var = tk.BooleanVar(value=True)
        vars_by_key[key] = var
        tk.Checkbutton(grid, text=AIRPORTS[key]["name"], variable=var).grid(
            row=i // 2, column=i % 2, sticky="w", padx=4, pady=1)

    btnrow = tk.Frame(top)
    btnrow.pack(fill="x", pady=4)
    tk.Button(btnrow, text="全選択", command=lambda: [v.set(True) for v in vars_by_key.values()]).pack(side="left", padx=3)
    tk.Button(btnrow, text="全解除", command=lambda: [v.set(False) for v in vars_by_key.values()]).pack(side="left", padx=3)

    tk.Label(top, text="地図ソース：", font=("", 11, "bold")).pack(anchor="w", pady=(8, 0))
    source_var = tk.StringVar(value=TILE_SOURCE)
    source_row = tk.Frame(top)
    source_row.pack(fill="x", pady=2)
    tk.Radiobutton(source_row, text="Esri World Imagery", variable=source_var, value="esri").pack(side="left", padx=4)
    tk.Radiobutton(source_row, text="国土地理院 (GSI)", variable=source_var, value="gsi").pack(side="left", padx=4)

    def apply_source():
        global TILE_SOURCE
        TILE_SOURCE = source_var.get()

    dem_var = tk.BooleanVar(value=True)
    dem5_var = tk.BooleanVar(value=False)
    dem_row = tk.Frame(top)
    dem_row.pack(fill="x", pady=2)
    tk.Checkbutton(dem_row, text="標高データも取得（山の可視化用）", variable=dem_var).pack(side="left", padx=4)
    tk.Checkbutton(dem_row, text="5mメッシュ（高精細・取得4倍）", variable=dem5_var).pack(side="left", padx=4)

    webp_var = tk.BooleanVar(value=False)
    webp_row = tk.Frame(top)
    webp_row.pack(fill="x", pady=2)
    tk.Checkbutton(webp_row, text="衛星画像をWebP形式で保存（同じ画質でファイルサイズ削減・iOS14+）",
                   variable=webp_var).pack(side="left", padx=4)

    zoom_var = tk.StringVar(value="15")
    zoom_row = tk.Frame(top)
    zoom_row.pack(fill="x", pady=2)
    tk.Label(zoom_row, text="衛星画像の解像度:").pack(side="left", padx=(4, 2))
    tk.Radiobutton(zoom_row, text="標準・高精細 (zoom15)", variable=zoom_var, value="15").pack(side="left")
    tk.Radiobutton(zoom_row, text="軽量 (zoom14・データ量約1/4・やや粗い)", variable=zoom_var, value="14").pack(side="left")

    speed_var = tk.StringVar(value="normal")
    speed_row = tk.Frame(top)
    speed_row.pack(fill="x", pady=2)
    tk.Label(speed_row, text="取得速度:").pack(side="left", padx=(4,2))
    tk.Radiobutton(speed_row, text="控えめ (10rps)", variable=speed_var, value="slow").pack(side="left")
    tk.Radiobutton(speed_row, text="標準 (25rps)", variable=speed_var, value="normal").pack(side="left")
    tk.Radiobutton(speed_row, text="速い (50rps)", variable=speed_var, value="fast").pack(side="left")

    start_btn = tk.Button(root, text="ダウンロード開始", font=("", 12, "bold"),
                           bg="#2e7d32", fg="white", height=2)
    start_btn.pack(fill="x", padx=10, pady=6)

    # スリープ抑止の状態を常時表示するラベル（build 161）。
    # ログ欄はスクロールして流れてしまい見落とされるため、別枠で常に見える表示にした。
    sleep_status_label = tk.Label(root, text="スリープ抑止: -", anchor="w", fg="#666666")
    sleep_status_label.pack(fill="x", padx=10)

    overall_label = tk.Label(root, text="空港: -/-", anchor="w")
    overall_label.pack(fill="x", padx=10)
    overall_bar = ttk.Progressbar(root, mode="determinate")
    overall_bar.pack(fill="x", padx=10, pady=(0, 8))

    tile_label = tk.Label(root, text="タイル: -/-", anchor="w")
    tile_label.pack(fill="x", padx=10)
    tile_bar = ttk.Progressbar(root, mode="determinate")
    tile_bar.pack(fill="x", padx=10, pady=(0, 8))

    log_box = scrolledtext.ScrolledText(root, height=16, state="disabled", font=("Consolas", 9))
    log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def log_from_thread(msg):
        q.put(("log", msg))

    def progress_from_thread(cur, tot):
        q.put(("tile", cur, tot))

    def sleep_status_from_thread(state, text):
        q.put(("sleep_status", state, text))

    def run_downloads(selected_keys):
        total = len(selected_keys)
        ok = 0
        # keep_awakeはこのワーカースレッド内で入ること
        # (WindowsのSetThreadExecutionStateはスレッド単位のため)
        with keep_awake(log_func=log_from_thread, status_func=sleep_status_from_thread):
            for i, key in enumerate(selected_keys):
                q.put(("overall", i, total, AIRPORTS[key]["name"]))
                try:
                    process(key, log_func=log_from_thread, progress_func=progress_from_thread,
                            with_dem=dem_var.get())
                    ok += 1
                except Exception as e:
                    log_from_thread(f"[エラー] {key}: {e}")
        # build 175: process()は取得のたびに空港ごとのmap_data_<key>.jsを直接更新する
        # (以前のmap_data_all.js統合は行わない)。
        if ok:
            log_from_thread("")
            log_from_thread(f"完了: 取得した{ok}空港ぶんの map_data_<空港キー>.js を更新しました"
                             "（approach_planner.htmlと同じフォルダに置けばOK）。")
        q.put(("overall", total, total, "完了"))
        q.put(("done", None))

    def on_start():
        selected = [k for k in keys if vars_by_key[k].get()]
        if not selected:
            log_from_thread("空港が選択されていません。")
            return
        apply_source()
        sleep_status_label.config(text="スリープ抑止: 設定中…", fg="#666666")
        global DEM_SOURCE, MAX_RPS, IMAGE_FORMAT, IMAGE_ZOOM
        DEM_SOURCE = "dem5a" if dem5_var.get() else "dem"
        IMAGE_FORMAT = "webp" if webp_var.get() else "jpeg"
        IMAGE_ZOOM = int(zoom_var.get())
        MAX_RPS = {"slow":10.0, "normal":25.0, "fast":50.0}.get(speed_var.get(), 25.0)
        log_from_thread(f"取得速度: 同時{MAX_WORKERS}本 / 最大{MAX_RPS:.0f} リクエスト毎秒")
        log_from_thread(f"地図ソース: {source_var.get()}")
        log_from_thread(f"衛星画像形式: {IMAGE_FORMAT} / zoom={IMAGE_ZOOM}")
        if dem_var.get():
            log_from_thread(f"標高ソース: {DEM_SOURCE} ({DEM_SOURCES[DEM_SOURCE]['label']})")
        start_btn.config(state="disabled", text="ダウンロード中…")
        threading.Thread(target=run_downloads, args=(selected,), daemon=True).start()

    start_btn.config(command=on_start)

    def poll():
        try:
            while True:
                item = q.get_nowait()
                kind = item[0]
                if kind == "log":
                    log_box.config(state="normal")
                    log_box.insert("end", item[1] + "\n")
                    log_box.see("end")
                    log_box.config(state="disabled")
                elif kind == "overall":
                    _, i, total, name = item
                    overall_bar["maximum"] = max(total, 1)
                    overall_bar["value"] = i
                    overall_label.config(text=f"空港: {i}/{total}  {name}")
                elif kind == "tile":
                    _, cur, tot = item
                    tile_bar["maximum"] = max(tot, 1)
                    tile_bar["value"] = cur
                    tile_label.config(text=f"タイル: {cur}/{tot}")
                elif kind == "sleep_status":
                    _, state, text = item
                    color = {"ok": "#2e7d32", "fail": "#c62828", "warn": "#e65100"}.get(state, "#666666")
                    sleep_status_label.config(text=f"スリープ抑止: {text}", fg=color)
                elif kind == "done":
                    start_btn.config(state="normal", text="ダウンロード開始")
        except queue.Empty:
            pass
        root.after(100, poll)

    root.after(100, poll)
    root.mainloop()


def merge(out_dir=".", log_func=print, delete_parts=False):
    """空港ごとの個別ファイル map_data_<key>.js を map_data_all.js（統合版）にまとめる。

    build 175時点では個別ファイルが既定の出力形式で、approach_planner.htmlも
    個別ファイルを読む作りに戻っている。この関数は「あえて1ファイルにまとめたい」
    （例: PCで直接<script src>読み込みして手早く動作確認したい等）場合向けの補助機能。
    個別ファイルは approach_planner.html が引き続き読む現役データなので、
    既定では削除しない（削除したい場合のみ --delete を明示指定する）。
    """
    import glob

    out_path = os.path.join(out_dir, MERGED_NAME)
    entries = load_merged(out_path)          # 既存の統合版の中身は保持する
    before = len(entries)

    paths = sorted(glob.glob(os.path.join(out_dir, "map_data_*.js")))
    paths = [q for q in paths if os.path.basename(q) != MERGED_NAME]
    if not paths:
        if entries:
            log_func(f"取り込む個別ファイルはありません（{MERGED_NAME} に{before}空港収録済み: "
                     f"{', '.join(sorted(entries))}）。")
        else:
            log_func(f"{MERGED_NAME} も個別ファイルも見つかりませんでした。")
            log_func("先に空港データをダウンロードしてください。")
        return out_path if entries else None

    taken = []
    for q in paths:
        name = os.path.basename(q)
        with open(q, encoding="utf-8") as f:
            for line in f:
                m = MAP_DATA_LINE_PATTERN.match(line)
                if m:
                    key = m.group(1).lower()
                    entries[key] = line.rstrip("\n")
                    taken.append(key)
        log_func(f"  + {name} を取り込み ({os.path.getsize(q)/1024/1024:.1f}MB)")

    if not taken:
        log_func("[警告] 個別ファイルから MAP_DATA_* を1つも読み取れませんでした。削除は行いません。")
        return out_path

    write_merged(entries, out_path)
    log_func(f"-> {out_path}  ({before}空港 → {len(entries)}空港 / "
             f"{os.path.getsize(out_path)/1024/1024:.1f}MB)")

    if delete_parts:
        for q in paths:
            try:
                os.remove(q)
            except OSError as e:
                log_func(f"  ! {os.path.basename(q)} を削除できませんでした: {e}")
        log_func("取り込み済みの個別ファイルを削除しました（--delete 指定）。"
                 "approach_planner.htmlは個別ファイルを読む作りなので、削除後は"
                 f"{MERGED_NAME} を個別ファイルの代わりに使う運用に切り替える必要がある点に注意。")
    else:
        log_func("個別ファイルはそのまま残しています（approach_planner.htmlが読む現役データのため）。")

    return out_path


def split(out_dir=".", log_func=print, delete_merged=False):
    """統合版 map_data_all.js を、空港ごとの個別ファイル map_data_<key>.js に分割する。

    build 126〜173で作った統合版が手元に残っている場合の移行用（merge()の逆方向）。
    分割後、approach_planner.htmlは個別ファイルの方を読むので、統合版は既定では
    削除せずそのまま残す（--delete-merged を指定すると削除する）。
    """
    merged_path = os.path.join(out_dir, MERGED_NAME)
    entries = load_merged(merged_path)
    if not entries:
        log_func(f"{MERGED_NAME} が見つからないか、空です。分割するデータがありません。")
        return []

    written = []
    for key, line in sorted(entries.items()):
        obj = _entry_to_obj(line)
        if obj is None:
            log_func(f"  [警告] {key} の行を解釈できなかったのでスキップしました。")
            continue
        path = write_entry(key, obj, out_dir=out_dir)
        written.append(key)
        log_func(f"  + {os.path.basename(path)} を書き出し ({os.path.getsize(path)/1024/1024:.1f}MB)")

    log_func(f"-> {len(written)}空港ぶんの個別ファイルを書き出しました。")

    if delete_merged:
        try:
            os.remove(merged_path)
            log_func(f"{MERGED_NAME} を削除しました（--delete-merged 指定）。")
        except OSError as e:
            log_func(f"  ! {MERGED_NAME} を削除できませんでした: {e}")
    else:
        log_func(f"{MERGED_NAME} 自体は残しています（--delete-merged で削除可能）。")

    return written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # 引数なしで実行された場合はGUIを起動
        try:
            launch_gui()
        except Exception as e:
            print(f"GUIの起動に失敗しました: {e}")
            print(f"コマンドラインで使う場合: python3 {sys.argv[0]} <空港キー|all>")
            print(f"登録済み空港キー: {', '.join(AIRPORTS.keys())}")
            print(f"個別ファイルを1つに統合: python3 {sys.argv[0]} merge  [--delete]")
            print(f"統合版を個別ファイルに分割: python3 {sys.argv[0]} split  [--delete-merged]")
        sys.exit(0)

    args = sys.argv[1:]
    target = args[0]
    # --no-dem : 標高を取らない（従来どおり画像だけ）
    # --dem-only: 標高だけ取る（画像は既存のものを残す）
    # --dem5a  : 5mメッシュを使う（整備済み区域のみ・タイル数4倍）
    # --webp   : 衛星画像をWebP形式で保存する（同じ画質設定でJPEGより小さくなる。build 159）
    # --zoom=N : 衛星画像のズームレベル（既定15。14にするとデータ量が約1/4になる。build 160）
    with_dem = "--no-dem" not in args
    with_image = "--dem-only" not in args
    if "--dem5a" in args:
        DEM_SOURCE = "dem5a"
    if "--webp" in args:
        IMAGE_FORMAT = "webp"
    # --rps=N / --workers=N で取得の速さを調整（上げるほど相手サーバーへの負荷が増える）
    for a in args:
        if a.startswith("--rps="):
            try: MAX_RPS = float(a.split("=",1)[1])
            except ValueError: print(f"--rps の値が不正です: {a}")
        elif a.startswith("--workers="):
            try: MAX_WORKERS = max(1, int(a.split("=",1)[1]))
            except ValueError: print(f"--workers の値が不正です: {a}")
        elif a.startswith("--zoom="):
            try: IMAGE_ZOOM = int(a.split("=",1)[1])
            except ValueError: print(f"--zoom の値が不正です: {a}")

    if target == "merge":
        # 個別ファイルを1つの統合版にまとめる（既定では個別ファイルは残す。--delete で削除）
        merge(delete_parts=("--delete" in sys.argv))
    elif target == "split":
        # 統合版を空港ごとの個別ファイルに分割する（既定では統合版は残す。--delete-merged で削除）
        split(delete_merged=("--delete-merged" in sys.argv))
    elif target == "all":
        with keep_awake():
            for k in AIRPORTS:
                process(k, with_image=with_image, with_dem=with_dem)
    else:
        with keep_awake():
            process(target, with_image=with_image, with_dem=with_dem)
