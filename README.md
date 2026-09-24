# Approach Planner

Visual / Circling / Traffic Pattern のパターン(Downwind・Base・Final、Cut Angle Entry、伊丹 Departure など)を、
衛星画像の上に描いて Turn 開始秒数・TOD・必要 Bank 角などを求める iPad 向けの計画ツールです。
単一の HTML(`approach_planner.html`)で動き、サーバー側の処理はありません。

> ⚠️ 個人の計画・学習用の試作です。運航判断には必ず AIS JAPAN・会社チャート等の正式な資料を使ってください。

## GitHub Pages で公開する(初回だけ)

1. GitHub で新しいリポジトリを作る(例: `approach-planner`)。
2. このフォルダをそのまま push する。
   ```sh
   git remote add origin https://github.com/<ユーザー名>/approach-planner.git
   git push -u origin main
   ```
3. リポジトリの **Settings → Pages → Build and deployment → Source** を **GitHub Actions** にする。
4. **Actions** タブで「Deploy to GitHub Pages」が緑になれば、
   `https://<ユーザー名>.github.io/approach-planner/` で開けます。

以後は `main` に push するたびに自動で公開し直されます。

## iPad で使う

- Safari で上の URL を開き、共有ボタン → **ホーム画面に追加**。ホーム画面から開くとアドレスバー無しの全画面で起動します。
- 一度オンラインで開くと、アプリ本体と 9 空港ぶんの地図データ(約 33MB)が裏で iPad に保存され、
  以後は **オフラインでも** 開けます(機内モードでも可)。
- アプリは常に iPad に保存済みの版ですぐ起動し、新しい版の確認は裏で行います。機内 Wi-Fi のような遅い回線でも起動を待たされません。
- 新しい版は最後まで受信でき、中身がアプリ本体だと確認できたときだけ保存されます(途中で切れたものや Wi-Fi のログイン画面は保存しません)。
  保存できると画面下に「新しい版(build NNN)を保存しました」と出て、アプリを開き直すと反映されます(見出し横の `build NNN` で確認)。

## ファイル構成

| パス | 内容 |
|---|---|
| `approach_planner.html` | アプリ本体(公開時は `index.html` としても配置) |
| `map_data_<空港>.js` | 各空港の衛星画像と標高データ。空港を選んだときに 1 つだけ読み込む |
| `sw.js` / `manifest.webmanifest` / `icons/` | オフライン動作とホーム画面追加(PWA)用 |
| `tile_downloader.py` | 地図データ(`map_data_*.js`)を作るスクリプト |
| `bundle_html.py` | 地図データを埋め込んだ 1 ファイル版(`approach_planner_bundled.html`)を作る。AirDrop 等で配る場合用 |
| `tools/make_artifact_body.py` | claude.ai Artifact 公開用の本文を作る |
| `tests/` | Playwright によるブラウザテスト |
| `docs/HANDOVER.md` | 設計・計算モデル・全ビルドの変更履歴 |
| `data/flap_maneuver_speed_SL.csv` | Flap Maneuver Speed の元データ |

## 地図データを作り直したとき

`python3 tile_downloader.py` で `map_data_*.js` を更新したら、`sw.js` の `MAP_CACHE` の番号を上げてから push してください
(例: `ap-map-v1` → `ap-map-v2`)。上げないと、iPad に保存済みの古い地図がそのまま使われます。

## ローカルで動かす・テストする

```sh
python3 -m http.server 8000          # http://localhost:8000/approach_planner.html
npm install && npx playwright install chromium
npm test                             # 回帰テスト + 設定パネル開閉 + 遅い回線/ログイン画面/オフラインでの起動テスト
```

`approach_planner.html` をダブルクリックで直接開いても動きます(その場合オフライン保存は働きません)。

## データの出典と利用条件

- 空港・滑走路・ILS/DME 等: AIP JAPAN AD2(各空港の EFF 日付はコード内コメント参照)。
- 衛星画像: **Esri World Imagery**(Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community)。
  リポジトリを公開すると画像データも誰でも取得できる状態になります。Esri の利用規約上、画像の再配布には制限があるため、
  公開リポジトリにする場合は利用条件を確認するか、`tile_downloader.py` の `TILE_SOURCE = "gsi"`(国土地理院 航空写真、出典明示で利用可)に切り替えて作り直すことを検討してください。
- 標高: 国土地理院 標高タイル。
