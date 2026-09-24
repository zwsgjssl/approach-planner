#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bundle_html.py
================
approach_planner.html 内の
    <script src="map_data_XXX.js"></script>
を、実際にダウンロード済みの map_data_XXX.js の中身でその場に埋め込み、
外部ファイルに依存しない1個のHTMLファイル（approach_planner_bundled.html）
を作るスクリプト。

【使い方】
1. approach_planner.html と、tile_downloader.pyで取得した map_data_*.js を
   すべて同じフォルダに置く（未取得の空港があっても構わない）。
2. そのフォルダで実行:
     python3 bundle_html.py
3. 同じフォルダに approach_planner_bundled.html が生成される。
   これ1個をiPadに送るだけでOK（別ファイル管理が不要になる）。

【どのデータが埋め込まれるか（build 198〜）】
  approach_planner.html の <!-- MAP_DATA_EMBED --> の位置に、フォルダにある
  個別版 map_data_<空港>.js を全部埋め込む。個別版が無い空港は、統合版
  map_data_all.js に入っていればそこから補う（同じ空港は1回だけ）。
  （build 175〜197のHTMLは <script src="map_data_*.js"> タグを置き換える旧方式で処理する。
   build 197以前のこのスクリプトは、統合版が残っているとHTMLが読まない統合版を理由に
   個別ファイルを全部飛ばし、地図データ0件のHTMLをエラー無しで作ることがあった。）

【注意】
- 取得済みの空港が多いほど、出来上がるファイルは大きくなる
  （1空港あたり数MB〜10数MB程度）。あまり多くの空港を1ファイルに
  詰め込みすぎると、ファイル自体は大きくなる（ダウンロード・保存に時間がかかる）。
  必要な空港だけ先にダウンロードしてからまとめるのがおすすめ。
- 元の approach_planner.html 自体は書き換えない（別ファイルとして出力）。
  データを更新したときは、このスクリプトを再実行すれば良い。

【build 157での変更点：埋め込み方式の軽量化】
  以前は各空港のデータを実行可能なJSの代入文
      var MAP_DATA_<KEY> = {...(数MB〜数十MBのbase64文字列)...};
  としてそのまま <script>...</script> で埋め込んでいた。この形式だと、
  HTMLを開いた瞬間に「今表示していない空港ぶんも含めて全部」JSエンジンが
  オブジェクトとして生成し終えるまでページが固まってしまう
  （空港数が多い/画像が大きいほど起動が重くなる主因だった）。
  build 157からは、各空港のデータを
      <script type="application/json" id="mapdata-<key>">{...}</script>
  という「ブラウザに実行されないテキストブロック」として埋め込むよう変更した。
  HTMLパーサはこの中身をただの文字列として保持するだけで、JSオブジェクト化は
  一切発生しない。approach_planner.html 側（build 157以降）は、ユーザーが
  その空港を選んだ**その時だけ** JSON.parse する（getAirportMapData()参照）。
  → 起動時に重い処理が走るのは選択中の1空港ぶんだけになり、体感の起動速度が
    空港数にほとんど依存しなくなる。
  ※ approach_planner.html が build 156以前（getAirportMapData()が無い版）の場合、
    この新しい埋め込み形式を読めないので、必ず build 157以降と組み合わせて使うこと。
"""

import re
import os
import sys

SRC_HTML = "approach_planner.html"
OUT_HTML = "approach_planner_bundled.html"
MERGED_NAME = "map_data_all.js"

SCRIPT_TAG_PATTERN = re.compile(r'<script src="(map_data_[a-zA-Z0-9_]+\.js)"></script>')
# 統合版の中にどの空港が入っているかを調べるための目印（tile_downloader.py が書く形式）
MAP_DATA_VAR_PATTERN = re.compile(r'var\s+MAP_DATA_([A-Za-z0-9_]+)\s*=')
# map_data_*.js の中身から「1空港＝1行」の代入文を取り出すためのパターン（値の部分をキャプチャ）
MAP_DATA_LINE_PATTERN = re.compile(r'var\s+MAP_DATA_([A-Za-z0-9_]+)\s*=\s*(.*?);\s*$', re.M)


def _to_inert_json_blocks(js_content, log_func=print):
    """map_data_*.js の 'var MAP_DATA_<KEY> = {...};' 形式を、
    '<script type="application/json" id="mapdata-<key>">{...}</script>' という
    非実行のテキストブロックに変換する（build 157の軽量化。モジュールdocstring参照）。

    JSON中に(まず起こらないが理論上)"</script"という並びが含まれていても
    HTMLパーサがそこでブロックを終端してしまわないよう、"/" を JSON として妥当な
    エスケープ "\\/" に置き換えておく（JSON.parse側で自動的に"/"へ戻る）。
    """
    blocks = []
    keys = []
    for m in MAP_DATA_LINE_PATTERN.finditer(js_content):
        key = m.group(1).lower()
        json_text = m.group(2)
        safe = re.sub(r'</(script)', r'<\\/\1', json_text, flags=re.I)
        blocks.append(f'<script type="application/json" id="mapdata-{key}">{safe}</script>')
        keys.append(key)
    return "\n".join(blocks), keys


def keys_in_merged(path=MERGED_NAME):
    """統合版ファイルに含まれている空港キーの集合を返す（無ければ空集合）。"""
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return set()
    return {m.group(1).lower() for m in MAP_DATA_VAR_PATTERN.finditer(text)}


EMBED_MARKER = "<!-- MAP_DATA_EMBED -->"
INDIVIDUAL_PATTERN = re.compile(r'^map_data_([a-zA-Z0-9_]+)\.js$')


def _collect_all_blocks(log_func):
    """フォルダ内の map_data_<空港>.js（個別版）を全部、非実行JSONブロックにする。
    個別版が無い空港は、統合版 map_data_all.js に入っていればそこから補う（同じ空港は1回だけ）。"""
    blocks, found, have = [], [], set()
    for fn in sorted(os.listdir(".")):
        m = INDIVIDUAL_PATTERN.match(fn)
        if not m or fn == MERGED_NAME:
            continue
        with open(fn, encoding="utf-8") as jf:
            content = jf.read()
        b, keys = _to_inert_json_blocks(content, log_func)
        if not keys:
            log_func(f"  [警告] {fn} から MAP_DATA_* を1つも読み取れませんでした（形式が想定と異なる可能性）。")
            continue
        blocks.append(b); have.update(keys)
        found.append((fn, os.path.getsize(fn) / 1024 / 1024))
    if os.path.exists(MERGED_NAME):
        with open(MERGED_NAME, encoding="utf-8") as jf:
            content = jf.read()
        extra = []
        for mm in MAP_DATA_LINE_PATTERN.finditer(content):
            key = mm.group(1).lower()
            if key in have:
                continue
            safe = re.sub(r'</(script)', r'<\\/\1', mm.group(2), flags=re.I)
            extra.append(f'<script type="application/json" id="mapdata-{key}">{safe}</script>')
            have.add(key)
        if extra:
            blocks.append("\n".join(extra))
            found.append((MERGED_NAME + f"（個別版の無い{len(extra)}空港ぶん）", os.path.getsize(MERGED_NAME) / 1024 / 1024))
    return "\n".join(blocks), found


def bundle(src_html=SRC_HTML, out_html=OUT_HTML, log_func=print):
    if not os.path.exists(src_html):
        log_func(f"[エラー] {src_html} が見つかりません。同じフォルダに置いて実行してください。")
        return False

    with open(src_html, encoding="utf-8") as f:
        html = f.read()

    found = []
    missing = []
    skipped_dup = []

    if EMBED_MARKER in html:
        # build198以降のHTML: 目印の位置に、フォルダにある全空港ぶんを埋め込む
        blocks, found = _collect_all_blocks(log_func)
        new_html = html.replace(EMBED_MARKER, blocks, 1)
    else:
        # 旧HTML(<script src="map_data_*.js">で読む版)。統合版に収録済みの空港の個別ファイルを
        # 飛ばすのは、HTML自体が統合版も読み込んでいる場合だけ(二重埋め込み防止)。
        # 旧版はHTMLが統合版を読まないのに個別ファイルを全部飛ばし、地図データ0件の出力を
        # エラー無しで作ってしまうことがあった(build198で修正)。
        html_reads_merged = f'<script src="{MERGED_NAME}"></script>' in html
        merged_keys = keys_in_merged() if html_reads_merged else set()

        def replace(m):
            js_path = m.group(1)
            if js_path != MERGED_NAME:
                key = js_path[len("map_data_"):-len(".js")].lower()
                if key in merged_keys:
                    skipped_dup.append(js_path)
                    return ""
            if os.path.exists(js_path):
                with open(js_path, encoding="utf-8") as jf:
                    content = jf.read()
                size_mb = os.path.getsize(js_path) / 1024 / 1024
                found.append((js_path, size_mb))
                blocks, keys = _to_inert_json_blocks(content, log_func)
                if not keys:
                    log_func(f"  [警告] {js_path} から MAP_DATA_* を1つも読み取れませんでした"
                             f"（形式が想定と異なる可能性）。")
                return blocks
            else:
                missing.append(js_path)
                return ""  # 未取得のものはタグごと削除(読み込みエラーを避ける)

        new_html, count = SCRIPT_TAG_PATTERN.subn(replace, html)

    for js_path, size_mb in found:
        log_func(f"  埋め込み: {js_path} ({size_mb:.1f}MB)")
    for js_path in skipped_dup:
        log_func(f"  スキップ（統合版に収録済み）: {js_path}")
    for js_path in missing:
        log_func(f"  スキップ（未取得）: {js_path}")

    if not found:
        log_func("[エラー] 埋め込めた map_data_*.js が1つもありません。"
                 "tile_downloader.py で画像を取得し、approach_planner.html と同じフォルダに置いてから実行してください。")
        return False

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(new_html)

    # 念のため、同じ空港が2回入っていないか出力を検査する
    # (build 157以降の出力形式は <script type="application/json" id="mapdata-<key>"> なので
    #  そちらを数える。念のため旧形式の var MAP_DATA_* も合わせて拾っておく)
    dup = []
    counts = {}
    for m in re.finditer(r'id="mapdata-([a-zA-Z0-9_]+)"', new_html):
        k = m.group(1).lower()
        counts[k] = counts.get(k, 0) + 1
    for m in MAP_DATA_VAR_PATTERN.finditer(new_html):
        k = m.group(1).lower()
        counts[k] = counts.get(k, 0) + 1
    for k, n in sorted(counts.items()):
        if n > 1:
            dup.append(f"{k}×{n}")
    if dup:
        log_func(f"[警告] 同じ空港のデータが複数回埋め込まれています: {', '.join(dup)}")
    else:
        log_func(f"  収録空港: {len(counts)}件（重複なし）")

    out_size_mb = os.path.getsize(out_html) / 1024 / 1024
    log_func(f"\n完成: {out_html} ({out_size_mb:.1f}MB)")
    log_func("このファイル1つだけをiPadに送ればOKです。")
    return True


if __name__ == "__main__":
    bundle()
