# Approach Planner — 引き継ぎ資料

最終ビルド: **build 219** / ファイル: `approach_planner.html`（単一HTML・完全オフライン動作）

---

## 1. これは何か

日本の空港向けの、Visual / Circling アプローチのパターンを計算・作図する iPad 用 PWA。
衛星画像の上にパターンを重ね、TOD・Abeam・旋回開始点などの距離/高度/秒数を算出する。

**重要な制約**：外部ライブラリ・CDN・ネットワークを一切使わない。すべて単一HTMLに自己完結（機内・オフライン前提）。
3D表示も three.js 等を使わず CSS 3D transform と SVG の自前計算で実装している。この方針は維持すること。

---

## 2. 関連ファイル

| ファイル | 役割 |
|---|---|
| `approach_planner.html` | 本体（これだけで動く） |
| `tile_downloader.py` | 衛星画像タイル取得（Esri/GSI、GUI+CLI、**半径6NM**） |
| `bundle_html.py` | `map_data_*.js` を本体HTMLに埋め込んで1ファイル化 |
| `run_downloader.bat` / `run_bundle.bat` | Windows用ランチャ |
| `map_data_<空港キー>.js` | 空港ごとの衛星画像データ（tile_downloader.py が生成。build 126〜173は統合版`map_data_all.js`1つのみだったが、build 175でオンライン配信のファイルサイズ上限対応のため空港ごとの個別ファイルに戻した） |

---

## 3. 作業のお作法（重要）

### 3.1 検証なしでデプロイしない
このプロジェクトでは過去に**符号ミス・参照漏れ・スケール補正漏れ**などのバグを何度も出している。
変更したら必ず以下を通すこと。

```bash
# 1) HTMLからJSを抽出して構文チェック
python3 -c "
import re
html = open('approach_planner.html', encoding='utf-8').read()
m = re.findall(r'<script>(.*?)</script>', html, re.S)
open('ex.js','w',encoding='utf-8').write(m[0])
"
node --check ex.js

# 2) DOM非依存部分を切り出して数値検証（下記3.2）
# 3) jsdom で実際に読み込んで初期化エラーが無いか確認
npm install jsdom --silent
```

**jsdom での動作確認は特に有効**。過去に「削除した要素への addEventListener」が原因で
スクリプト全体が停止し、一見無関係な機能が壊れる事故があった。

### 3.2 数値検証の型
DOM非依存部分（定数〜`computeFullPatternGeometry`まで）を切り出して Node で実行し、
以下を必ず確認する。

- **NaN が無いこと**（全滑走路 × Left/Right × Visual/Circling × Cut角 × 風況）
- **着地精度**：Final延長線への cross-track 誤差（許容 < 3m、通常は 0.0m）
- **Entry の Downwind offset 到達誤差**（通常 0.000m）
- **順序関係**：Turn開始 < Abeam、TOD < Abeam
- **Overshoot**：旋回経路が Final 延長線を越えて反対側に出ていないこと
- **Bank角**が発散していないこと

build 115時点で全9空港・全26滑走路端、build 117で函館追加後は全27滑走路端について、
× Left/Right × Visual/Circling × Cut角(NIL/45/60) × 3D ON/OFF × 風況(無風/追い風/横風)
の計1944通りを jsdom で機械的に走らせ、NaN・console/初期化エラーともに0件を確認済み。

### 3.3 バージョン
変更したら `build NNN` の数字を上げる（HTML冒頭のヘッダ内にある）。

### 3.4 検証の適用範囲（build 120でユーザーと合意した軽量化ルール）
上記3.2の1944通りフルスイープは**計算ロジック自体を変更したとき**にのみ必要。
空港ごとに違うのは主に座標などの**データ**であり、計算ロジックは全空港共通なので、
データ追加・修正だけの変更にフルスイープを毎回回すのは過剰（build 120でユーザー指摘、合意済み）。

- **計算ロジックの変更**（`computePattern`等の共通関数を触った）→ 代表空港1つ（例：羽田）で
  フルスイープを実施すれば十分。全空港で回す必要はない。
- **空港固有データの追加・修正**（座標・標高・デフォルト滑走路など）→ その空港について
  NaNが出ないこと等の軽い確認（例：全空港を切り替えてreadout/svgに`NaN`が出ないか）で十分。
- **例外**：羽田のVOR専用ルート（`VOR34L→16R`, `VORA16L`）や、福岡の2滑走路配列・
  羽田の`notLandable`のようにデータ構造自体が他と異なる箇所は、通常のデータ追加とは
  別のコードパスを通るため、そこだけ個別に軽くチェックする。

> **build 124でも再発した**：幾何の成立判定（全空港共通のロジック）を追加した際に、
> 全空港744通りを回してユーザーに止められた。**空港ごとに変わるのは座標だけで、
> 判定ロジックは共通**なので、羽田の条件振り（offset・IAS・Cut角・風）だけで足りる。
> jsdom経由の全空港スイープは10分以上かかるうえ得られる情報が増えない。やらないこと。

---

## 4. 計算モデルの要点

### 4.1 座標・符号の規約
- `magVarDeg` は **西偏を正**。`磁方位 = 真方位 + magVarDeg`
- `pat.ux, uy`：Final方向の単位ベクトル。**along = -((P-thr)·u)** で「THRからの手前方向の距離」
- `pat.nx, ny`：Downwind側を正とする法線。**cross = (P-thr)·n**
- `turnSign = -pat.side`

### 4.2 旋回
- 空力（旋回半径 R=V²/(g·tanβ)）は **TAS** 基準
- 位置・軌跡は TAS ベクトル＋風ベクトルを毎ステップ積分（実質 **G/S**）
- **Roll rate 3°/sec**（`ROLL_RATE_DEG_PER_SEC`）。Roll-in/out 中も旋回率が変化する
- **WCA は旋回の開始側だけでなく終了側にも適用**。Roll out 時に Track（HDGではない）が
  目標に一致するよう、実際に掃引する角度を調整している
- Entry の旋回は固定コーナー幾何にスケール補正せず、**自然なシミュレーション結果をそのまま使う**
  （Roll rate を変えると旋回の実位置が動く）

### 4.3 速度プロファイル
- Abeam 通過後、**0.7 kt/sec の一定減速**で Target APP SPD まで減速
- 等加速度の式 `V² = V₀² - 2as` で距離の関数に変換（TOD位置に依存しない）

### 4.4 Base Turn のタイミング
| モード | 基準 |
|---|---|
| Visual「距離固定」 | 入力した Base Leg Distance（既定 2.5NM） |
| Visual「時間」 | Abeam から **35秒**（既定） |
| Circling | Abeam から **20秒** |
| VOR A→16L | **AOMI 上空を通る**よう Base 距離を三分探索で逆算 |

- 時間基準は **G/S で距離換算**（TASだと風で距離が変わらない不自然な結果になる）
- 風補正：**Tailwind で 0.4秒/kt 早く、Headwind で 0.4秒/kt 遅く**
- 「Abeamから N秒」は**実際に N秒飛んだ地点**になるよう反復で解いている
  （旋回半径分が先に消費されるため、単純な `速度×秒数` ではズレる）

> **未解決の論点**：0.4秒/kt という係数は、G/S による自然な距離変化を打ち消すどころか
> 逆転させるほど強く効いている（30kt追い風で Base 距離が無風時の半分以下になる）。
> 0.2秒/kt 程度が妥当かもしれないが、実運用の根拠が不明なため保留中。

### 4.5 周回進入区域
**4本すべての滑走路それぞれから2.5NMのスタジアム形状の和集合**（重なる内側は無視、最も外側の輪郭）。
120方向の角度スキャン＋半径方向の二分探索で求めている（誤差0m）。
境界の担当滑走路が切り替わる箇所には、その滑走路上の最近接点から補助線を引いている
（切り替わり位置も角度方向の二分探索で精密化済み）。

---

## 5. 空港データ

### 5.1 データ構造
```javascript
haneda: {
  key:"haneda", name:"羽田空港 (RJTT)",
  elevationFt: 21,
  arp:{ lat:35.553333, lon:139.781111 },
  navaids:{ TTE:{ name:"TOKYO VOR/DME (TTE)", freq:"117.4MHz", lat:..., lon:... },
            AOMI:{ name:"AOMI Landmark Bcn", lat:35.610556, lon:139.785833 } }, // AIP実測値
  runways:[
    { ident:"16L/34R", group:"C", headingTrueDegAtoB:150, magVarDeg:8, lengthM:3360,
      slopePercent: 0.00,   // (build 121で追加) A→B方向の平均勾配%。+は上り/-は下り
      thresholds:{ ident:"16L/34R",
        "16L":{ label:"16L", lat:..., lon:..., elevFt:19.2 },   // Displaced THR があればその座標。elevFtはTHR ELEV(build 121で追加)
        "34R":{ label:"34R", lat:..., lon:..., elevFt:19.7 } },
      notLandable:["05"] }   // 着陸に使えない端があれば指定
  ]
}
```

**`slopePercent`/`elevFt`（build 121で追加）**：`elevFt`はAD2.12のTHR ELEV列（表形式・信頼できる）
をそのまま転記。`slopePercent`は`(elevFt[B]-elevFt[A])*0.3048/lengthM*100`で算出した
**THR間の単純平均勾配**（ident記載順=A→B方向）。AIPのAD2.12には区間ごとの詳細な縦断勾配図
（LONGITUDINAL PROFILE OF RUNWAY）もあるが、テキスト抽出では文字化けや数値対応の不整合が
確認されており（下記5.7参照）信頼できないため採用していない。滑走路内の凹凸までは表現しない
簡易値である点に注意。

### 5.2 現状
| 空港 | 状態 |
|---|---|
| **羽田 (RJTT)** | **AIP実測値で完備**（4本すべて、Displaced THR 反映済み。AOMIもbuild 116でAIP実測値化） |
| **新千歳・広島・高松・松山・福岡・熊本・伊丹・函館** | **AIP実測値で完備**（AD2.2/AD2.12より転記。函館はbuild 117で追加、他はbuild 115。下記5.4/5.6参照） |

全9空港・全15滑走路(計30滑走路端、うち羽田05は着陸不可のため選択肢としては29端)が
AIP実測値で完備済み（プレースホルダーの空港はもう無い）。
build 121で全滑走路に`elevFt`（THR標高）と`slopePercent`（平均勾配）を追加済み（5.7参照）。
build 122で新千歳(01L/19R)・伊丹(14L/32R)の残り滑走路も追加し、モデル化している全空港で
実際に着陸できる滑走路をすべて選択できるようになった（5.8参照）。

### 5.3 追加手順
1. AIP の **AD 2.2（ARP・標高）** と **AD 2.12（滑走路諸元・閾値座標）** を用意
2. PDFが画像の場合は OCR で読む（羽田でやった手順）：
   ```bash
   pdftoppm -png -r 100 input.pdf pages/page
   tesseract pages/page-30.png -   # ページごとにOCR
   ```
   数値は必ず**画像を目視**して確認する（OCRの誤読が起きる）。
   PDFがネイティブテキスト（ベクター）形式で、画像化されていない場合は
   OCR誤読のリスクそのものが存在しない。その場合はテキスト抽出値をそのまま使ってよいが、
   代わりに**両THR座標から球面三角法で距離・方位を逆算し、AIPのRWY長・TRUE BRGと
   数m/0.2°以内で一致するか**を必ず確認すること（build 115ではこの方法で7空港を検証）。
   なお、Projectのファイルアップロード経由のPDFは、この環境のツール上ではテキスト抽出のみ
   可能で、ページを画像として開いて目視確認することができない（build 115作業時に判明）。
   本当に画像目視確認までしたい場合は、対象PDFをこの会話に直接添付する必要がある
   （そうすればローカルファイルとして扱え、pdftoppmが使える）。
3. `thresholds` に **Displaced THR があればそちらの座標**を入れる
4. `headingTrueDegAtoB` は AIP の **TRUE BRG** を使う（滑走路番号からの概算で済ませない）
5. `notLandable` に着陸不可の端を列挙（例：羽田のRWY05）
6. 追加後は **3.2 の数値検証**を必ず実行
7. 衛星画像は `python tile_downloader.py <key>` で取得（半径6NM）。
   `tile_downloader.py`側の`center_lat/lon`もAIPのARP座標に合わせて更新すること
   （滑走路の閾値座標とは別管理なので、片方だけ直して忘れがち）

> **原則**：推測値を入れない。AIPで確認できない数値は入れず、プレースホルダのままにする。

### 5.4 build 117 での追加分に関する注記
- **函館空港 (RJCH) のAIP実測値を追加**（RJCH AD2.2/AD2.12より）。
  ARP 414612N/1404919E, 標高111.9ft, MAG VAR 9°W(2009)/1.2'E。
  滑走路12/30: TRUE BRG 107.98°/287.98°(整数化して108を採用)、3000×45m、
  THR座標 12:414627.62N/1404817.61E、30:414557.54N/1405021.00E。
  両THR座標から逆算した距離は2990.5m(AIP記載3000mとの差9.5m)、方位は108.09°/288.11°
  (AIP記載107.98°/287.98°との差0.11-0.13°)で、他空港と同水準の許容誤差内。
  これで函館以外も含め全9空港がプレースホルダーなしになった。
- 更新後、全9空港・全27滑走路端 × Left/Right × Visual/Circling × Cut角(NIL/45/60)
  × 3D ON/OFF × 風況3パターンの計1944通りをjsdomで再検証し、NaN・エラーともに0件を確認済み。

### 5.5 build 116 での追加分に関する注記
- **AOMI Landmark Bcn の座標をAIP実測値に更新**。RJTT AD2.24 (Noise Abatement chart) に
  「AOMI LANDMARK BEACON (FLASHING WHITE) 353638N 1394709E」と明記されているのを
  テキスト検索で直接確認（ユーザー提示の数値と完全一致）。10進化すると
  `lat:35.610556, lon:139.785833`。旧値（Jeppesenチャート画像からの目測推定、
  `35.613078, 139.784257`）との誤差は**約315m**（緯度側+281m、経度側-143m相当）。
  VOR A→16Lの「AOMI上空通過」を狙うBase距離の三分探索ロジックに直接影響する値のため、
  AIP実測値に差し替えた。jsdomでVOR A→16Lの全Cut角(NIL/45/60)×3D ON/OFF×風況4パターン
  （計24通り）を再検証し、NaN・エラーともに0件を確認済み。build 116。
- 函館(RJCH)・羽田(RJTT)のAIP PDFがプロジェクトにアップロードされた。羽田はAOMIの
  座標確認にのみ使用（上記）。函館の滑走路データ解析はユーザーの指示待ちでまだ未着手。
- `tile_downloader.py`の函館(hakodate)のcenter_lat/lonを、RJCH AD2.2のARP座標
  (414612N/1404919E → 41.770000, 140.821944)に合わせて精度を上げ、「確認済み」に更新。
  旧値(41.7700, 140.8219)は概算だったが、実際には数m以内の差で元々ほぼ正確だった。
  羽田は元々AIP値と完全一致していたため変更なし。
  なお`approach_planner.html`側の函館の滑走路データ（閾値座標等）はまだプレースホルダーのまま
  （ユーザーの指示待ち）。

### 5.6 build 115 での追加分に関する注記
- ユーザーがアップロードしたAIP AD2章PDF（新千歳RJCC・広島RJOA・高松RJOT・松山RJOM・
  福岡RJFF・熊本RJFT・伊丹RJOO）から、ARP座標・標高・磁気偏差・滑走路TRUE BRG・
  閾値座標を転記。いずれもネイティブテキストPDF（スキャン画像ではない）で、
  この環境のPDF読み込みでは画像として開いて目視確認する手段がなかったため、
  上記5.3の「両THR座標からの逆算チェック」で代替検証している（全て許容誤差内で一致）。
  **運航判断に使う前に、必ずAIS JAPAN／会社チャートの正式な数値で最終確認すること。**
- **福岡空港 (RJFF) は平行滑走路2本**であることが今回のAIPで判明（16L/34R 2800m ILS、
  16R/34L 2500m LOCのみ）。旧版は16/34の単一滑走路（推定値）だったため、実際の2本構成
  （`fukuoka`キー内に `16L/34R` と `16R/34L` の2エントリ）に置き換えた。
  これによりUIの滑走路選択が2択→4択に変わっている。
- 函館 (RJCH) はこの時点ではAIP未取得のためプレースホルダーのままだったが、
  build 117で実測値化済み（5.4参照）。

### 5.7 build 121 での追加分に関する注記（滑走路勾配データ）
- ユーザー要望「渡したデータに滑走路勾配の情報を加えてほしい」に対応。全13滑走路エントリ
  （新千歳・函館・羽田×4・伊丹・広島・高松・松山・福岡×2・熊本）に `elevFt`（各THRの
  AD2.12記載THR ELEV、羽田のDisplaced THRがある滑走路はDisplaced基準で統一）と、
  そこから算出した `slopePercent`（THR間の平均勾配%、ident記載順=A→B方向、+は上り）を追加。
- **採用しなかった方法とその理由**：AD2.12には区間ごとの詳細な縦断勾配図
  （LONGITUDINAL PROFILE OF RUNWAY、距離ごとの%勾配とブレークポイント標高）も掲載されている。
  しかしテキスト抽出で検証したところ、(a) 松山(RJOM)の図は私用領域Unicodeの文字化けで
  数値が読み取れず、(b) 広島(RJOA)は図から逆算した勾配(約0.95%)がAIP記載の
  「0.3%／0.5%」という数値と一致しなかった（2D図をテキスト抽出で線形化する際に
  数値の対応関係がズレるリスクを示唆）。このため詳細プロファイルは採用せず、
  **信頼できる表形式のTHR ELEVから算出した単純平均勾配**のみをデータ化した
  （＝滑走路内の凹凸までは表現できない簡易値。ユーザーへの提示時にこの制約を明記済み）。
- 算出値（A→B方向、+は上り/-は下り）：
  新千歳01R/19L +0.20%、函館12/30 +0.60%、羽田16L/34R +0.00%、羽田16R/34L +0.02%、
  羽田04/22 +0.20%、羽田05/23 +0.11%、伊丹14R/32L -0.15%、広島10/28 -0.05%、
  高松08/26 +0.04%、松山14/32 -0.10%、福岡16L/34R +0.19%、福岡16R/34L +0.21%、
  熊本07/25 +0.42%。
- 検証：`node --check`で構文確認、jsdomで全9空港を切り替えて`NaN`が出ないことを確認
  （build 120で合意した軽量検証ルール＝3.4参照。今回は既存の計算ロジックには一切
  組み込んでいない純粋なデータ追加のため、フルスイープは不要と判断）。

### 5.8 build 122 での変更点（データ巻き戻り事故の復旧＋ハンバーガーメニュー／map_data統合＋残り滑走路追加）

**事故の経緯**：ユーザーがハンバーガーメニュー機能とmap_data統合機能(`map_data_all.js`)を
`approach_planner.html`に追加してプロジェクトにアップロードしたところ、その作業のベースが
build115より前の、AIP実測値を反映する前の推定値版だったため、build115〜121で積み上げた
AIP実測値・AOMI座標修正・空港ごとのデフォルト設定・空港並び替え・表示パン中心の変更・
滑走路勾配データが丸ごと消えた状態でアップロードされてしまった。さらに別途「build112」の
古いローカルコピー（ハンバーガーメニューのみ、データはさらに古い推定値）も別途存在していた
ことが判明。ユーザーに状況を報告し確認した結果、**build119（デフォルト設定＋並び替えまで
反映済みの版）をベースに、build120・121相当のデータ修正を再適用し、そこにハンバーガー
メニューとmap_data統合機能を移植する**方針で合意し、build 122として復旧した。
- 教訓：この種のツールは複数の版が別々の場所（プロジェクトドキュメント／会話への直接添付／
  ユーザーのローカルコピー）に並行して存在しうる。ファイルを受け取ったら、まず`build NNN`と
  データの中身（`thresholds:null`が残っていないか等）を確認してから作業を始めること。

**復旧・統合した内容**：
- build 120相当：熊本のデフォルト設定(`AIRPORT_DEFAULTS.kumamoto = {rwy:"07", side:"left"}`)を再追加。
- build 121相当：全滑走路への`elevFt`/`slopePercent`（滑走路勾配データ）を再追加。
- **ハンバーガーメニュー**：設定パネルを既定で隠し、ヘッダーの☰ボタンで開閉する引き出しに変更
  （`.menuBtn`, `.controls`(position:fixed + transform:translateX)、`body.drawer-open`）。
  地図側はパネルを開いている間だけ幅を縮める(`.layout{margin-left}`)。
- **map_data統合**：`<script src="map_data_all.js"></script>` を個別ファイルより先に読み込むよう追加
  （`tile_downloader.py merge`で生成する統合版。個別ファイルは統合版が無い環境向けのフォールバック
  として残す）。`tile_downloader.py`に`merge()`関数・`MERGED_NAME`定数・CLI引数`merge`
  （`--delete`で個別ファイル削除）を追加。`all`実行時も自動でmergeするよう変更。
- **新千歳(01L/19R)・伊丹(14L/32R)の残り滑走路を追加**（ユーザー要望「滑走路全てに変更したい」）。
  デフォルトの滑走路/Downwind側(`AIRPORT_DEFAULTS`)は変更していない（新千歳=19L Left、
  伊丹=14R Right のまま）。
  - 新千歳01L/19R：RJCC AD2.12より TRUE BRG 352.62°(整数化353)、3000×60m、
    THR座標 01L:424541.90N/1414134.17E(ELEV 62ft)、19R:424718.36N/1414117.18E(ELEV 82ft)。
    平均勾配 +0.20%(01L→19R)。
  - 伊丹14L/32R(A滑走路)：RJOO AD2.12より TRUE BRG 135°/315°、1828×45m、
    THR座標 14L:344742.97N/1352543.27E(ELEV 50ft)、32R:344701.02N/1352634.11E(ELEV 34ft)。
    平均勾配 -0.27%(14L→32R)。
- 検証：`node --check`で構文確認、jsdomで全9空港×各空港の全滑走路選択肢を機械的に切り替えて
  `NaN`が出ないこと、および各空港のデフォルト値が変更前と一致することを確認。
  ハンバーガーメニューの開閉(`menuBtn`クリック→`.controls.open`/`body.drawer-open`の
  トグル)も動作確認済み。

---

## 6. 特殊進入方式（羽田専用）

### 6.1 VOR 34L→16R (Circling)
- TTE の **333°M** で進入 → 周回進入区域の境界 → 標準の **45°/60° Cut Angle S字進入** → 1.5NM Downwind → Circling で 16R
- Downwind **730ft MSL**、Approach/Side は Circling/Left に固定

### 6.2 VOR A→16L (Circling)
- チャート：**VOR A (13-1)** と **Noise Abatement (10-4E)**
- TTE の **274°M（R-094 inbound）** で進入
- 周回進入区域の境界で**右64°旋回**して 2.5NM Downwind（≒337°M）へ直接合流
  （274°とDownwindの差が十分あるので S字の Cut Angle は使わない）
- そのまま **Left Downwind → 左180° Circling** で 16L
- **騒音軽減のため AOMI Landmark Bcn 上空を通る**必要がある
  → Base 距離を三分探索で逆算
- Downwind **1800 / 1500 ft MSL の選択式**、**147kt**、offset 2.5NM
- 最終旋回の Bank は 25°固定ではなく**解いた値**
- SULUL (D5.7) をプロット。**DARKS (D10.7) は描画範囲外**（ARPから9.95NM、画像は6NM四方）

> **AOMI の座標は build 116 でAIP実測値に更新済み**（RJTT AD2.24より
> `353638N 1394709E` = 35.610556, 139.785833）。旧値はJeppesenチャート画像からの
> 目測推定で、約315mの誤差があった（5.5参照）。

---

## 7. UI の現状

- **設定パネル**：ハンバーガーメニュー化（build 122）。既定で非表示、ヘッダー左の☰ボタンで
  開閉する引き出し(`#controls`, `position:fixed`)。地図側(`.layout`)は開いている間だけ
  幅を縮める。開閉ロジックは`menuBtn`クリックのイベントリスナ1箇所（IIFE）にまとめてある。
- **衛星画像データの読み込み**：`map_data_all.js`（`tile_downloader.py merge`で生成する統合版）
  を優先して読み込み、個別の`map_data_<key>.js`はフォールバックとして残す（build 122）。
- **風入力**：`010/15` 形式の1欄に統合（空欄＝無風、50kt上限、`/`でもスペースでも可）
- **滑走路切替時**：VOR方式で上書きされた入力欄を `applyApproachDefaults()` でデフォルトに戻す
- **色分け**：**TODを境に** 緑＝降下中／シアン＝Level flight。Entry＝アンバー、境界＝赤破線、着陸滑走路＝黄
- **高度表示**：すべて 10ft 単位
- **Aiming Point**：既定1312ft（THRから）だが任意で変更可能（build 115で固定値から変更）
- **3D表示**：ON/OFF の2択。ONで衛星画像ごと **55°固定で傾斜**、ドラッグで**方位のみ**回転。
  航跡は高度分だけ浮き（**4倍に強調**）、地面に投射される線（影）は元の色を薄くするのではなく
  **単純な薄いグレー(`--groundline`)** で表示（build 113で変更。`groundLineAttrs()`参照）
- **地図上の距離/高度ラベル**：デフォルトOFF
- **画像の自動パン中心**：THR(along=0, cross=0)とAbeam点(along=0, cross=downwindOffset)の
  中点に自動でパンする（build 118で変更。旧: THR〜Base旋回位置の中点×RWY〜Downwindの中点。
  該当コードは`render()`内の`centerAlongM`/`centerCrossM`）
- **空港切替時のデフォルト滑走路/Downwind側**：`AIRPORT_DEFAULTS`（`setupAirport()`直前に定義）
  で空港ごとに指定（build 119で追加）。
  | 空港 | デフォルト滑走路 | Downwind側 |
  |---|---|---|
  | 羽田 | 34R | (未指定、現在の選択を維持) |
  | 新千歳 | 19L | Left |
  | 広島 | 10 | Right |
  | 高松 | 08 | Left |
  | 松山 | 32 | Left |
  | 福岡 | 34R | Left |
  | 伊丹 | 14R | Right |
  | 熊本 | 07 | Left（build 120で追加） |
  | 函館 | 未指定（`landableEnds[0]`のまま） | 未指定 |
- **空港選択の並び順**：ARPの緯度が高い順（北から南）に変更（build 119）。
  新千歳→函館→羽田→伊丹→広島→高松→松山→福岡→熊本。
  （経度による東西の違いは、この9空港では緯度順と矛盾しないため単純な緯度降順で実装）

---

## 8. 未対応・保留中の課題

### 8.0 build 122 時点の精査で判明した不具合（2026-09-21、未修正）

全空港×全滑走路×Left/Right×Visual/Circling×Cut角×風況 の1740ケースを計算層で、
1116ケース＋VOR方式/3D表示を含む代表20ケースを描画層(jsdom)で走らせて確認した結果。
**以下はすべて再現手順つきで裏取り済み。修正はユーザーの指示待ち。**

**A. 数値に影響するもの**

| # | 症状 | 再現 | 原因 |
|---|---|---|---|
| A1 | ~~空港を切り替えるとVOR方式の強制値が新空港に残る~~ → **build 128で修正済み（8.0g）** | 羽田→RWY「VOR A→16L」→Airport「新千歳」でDownwind Alt 1779ft AFE / IAS 147kt / Circling が残る | `setupAirport()`が`applyApproachDefaults()`を呼んでいない（`#rwyEnd`のchangeハンドラには同じ対策があるのに空港切替経路だけ抜け） |
| A2 | 強い追い風で、達成できない目標秒数がそのまま表示される | 広島RWY28 Circling Left wind280/45 →「Turn開始 2.85NM **(Abeam+2.0s)**」と「Abeam→Turn開始 **12 sec**」が同時表示（実測12.3s） | 目標秒数 20−0.4×45=2.0s が物理的に到達不能で二分探索のブラケットが不成立→素朴な初期推定にフォールバックするが、フォールバックしたことが表示に出ない。4.4の「0.4秒/kt効きすぎ」問題の顕在化 |
| A3 | ~~Visualの「Turn終了(Final)高度」が実Roll out高度より約350ft高い~~ → **build 123で修正済み（下記8.0b）** | 羽田34R Visual無風: 表示890ft MSL / 実際にwings levelになる1.41NM地点は540ft MSL。熊本07は360ft差 | `baseFinalAltFt = altAtDist(baseDistM)`はBase延長線とFinalの交点(2.50NM)の高度。実Roll outは旋回の接線長ぶん手前。**Circlingは180°連続旋回が交点で終わるため差0ft＝Visual限定** |
| A4 | ~~Downwind Offsetを狭めると経路が自分自身に折り返す~~ → **build 124で対応済み（8.0c）** | Visual + offset 1.5NM + IAS182 → Base legが −1272m。警告もNaNも出ない | offset < 2×R（R=Bank25°の旋回半径）で2つの90°旋回の接線が重なる。既定2.5NM/182ktでも余裕580mのみ、IAS195kt超で破綻。Entry(S字)でも11/928ケース（Circling+Cut60+45kt風）で−56〜−128m |

**B. UI・表示の不具合**

| # | 症状 | 再現 |
|---|---|---|
| B1 | ~~衛星画像クレジット(.attribution)が空港切替のたびに増殖~~ → **build 126で修正済み** | 羽田→新千歳→伊丹で3枚重なる。画像なし空港へ移ると1枚しか消えず他空港のクレジットが残る |
| B2 | ~~VOR方式でCut Angleボタンが押せるのに効かない~~ → **build 129で修正済み（8.0h）** | VOR A→16Lで45/60を押しても常にNIL、VOR 34L→16RでNILを押しても戻らない。他の欄はグレーアウトされるのにCut Angleだけ明示的に解除されている |
| B3 | 滑走路を変えるだけで入力7項目がリセット → **仕様として確定（ユーザー判断「都度、既定値に」／修正しない）** | baseDist/glideAngle/targetAppSpd/downwindAlt/downwindOffset/baseTime が既定値に戻る（wind/oatは残る）。VOR方式未使用でも発生 |
| B4 | ~~VOR方式から通常滑走路に戻してもApproach/Downwind側が戻らない~~ → **build 128で修正済み（8.0g）** | Visual/Rightで作業中にVORを一瞬選ぶとCircling/Leftのまま。B3と合わさり入力欄もCircling既定値になる |
| B5 | ~~「旋回半径 (Bank 25°, TAS)」がどの旋回の半径でもない~~ → **build 123で修正済み（下記8.0b）** | 表示1.09NM(Downwind TAS基準) / 実際に描画されるFinal turnは約1604m / 物理的に正しい減速後の値は1321m(0.71NM)。コーナー幾何のRだけ減速プロファイル未反映で、描画旋回が約20%大きい |
| B6 | ~~地図左上のコンパス表示だけゼロ埋めされない~~ → **build 135で修正済み（8.0n）** | 選択肢は"RWY 01R (003°M)"だが地図は"RWY 01R ↑ 3°M" |
| B7 | ~~TODのセグメント名が実経路と食い違うことがある~~ → **build 136で修正済み（8.0p）** | 理想コーナー幾何の距離で判定しているため。offset1.5NMのVisualで実際はBase旋回中なのに"Downwind"表示 |
| B8 | ~~風入力のバリデーションが緩い~~ → **build 129で修正済み（8.0h）** | "999/99"が279°M/50ktとして受理（360超を暗黙に剰余）。不正入力時に矢印は消えるがテキストに直前値が残る |
| B9 | ~~`convexHull`/`bufferedHullOutline`がデッドコード~~ → **build 129で削除済み（8.0h）** | 呼び出し元ゼロ（約60行）。実際は`unionStadiumOutline`のみ使用 |

**C. 検証して問題なしと確認できた項目**（今後の再検証の手間を省くため記録）

- AIPデータ整合: 全15滑走路でTHR座標から逆算した距離が公称値と −9.5〜+8.8m、真方位0.34°以内（羽田のDisplaced THR 2本は仕様どおり公称長より短い）
- 着地精度: 全1740ケースでRoll out点のFinal延長線からの横ズレ最大 **0.05m**
- Roll out時のTrack誤差: 最大 **0.0008°**（WCA処理は正しく効いている）
- Entry(S字)のDownwind offset到達精度: 最大 **0.00m**
- 旋回の物理モデル: ω=g·tanφ/V、WCA式、座標系の符号、二分法の収束方向すべて正。積分ステップ0.15秒でRK4比の位置誤差2.6cm
- 平面近似(toXY): 6NM範囲で最大30m(0.27%)、方位0.17°
- IAS→TAS、真高度補正: 標準式と一致
- NaN/Infinity: 地図SVG・VSD・readoutを1116＋20ケースで走査し0件。極端な入力（offset 0、高度−500、glide 0/−3/90、速度0/500、Aiming Point −1000等）でもNaNなし

### 8.0g build 128：VOR方式の強制設定が別空港に持ち越される不具合を修正（監査A1・B4）

**症状(A1)**：羽田でVOR A→16Lを選んでから新千歳へ切り替えると、新千歳なのに
Circling固定・Downwind高度が羽田基準の1779ft AFE・147ktのまま適用されていた。
**症状(B4)**：VOR方式から通常滑走路に戻してもApproach Type/Downwind側が復元されなかった。

**方針**：`setupAirport()`で毎回`applyApproachDefaults()`を呼ぶと直るが、
**空港を切り替えるたびに入力欄が全部リセット**される副作用が出る（B3と同じ挙動）。
そこで「VOR方式から離れるときだけ戻す」形にした。

**実装**
- `vorSavedState`（モジュールスコープ）に、VOR強制に入る**前**のApproach/Sideを退避。
  `saveBeforeVorForcing()` / `clearVorForcing()`（戻すものがあればtrueを返す）。
- `#rwyEnd`ハンドラ：VOR方式を選んだら`saveBeforeVorForcing()`→`state.approach="circling"`。
  通常滑走路を選んだら`clearVorForcing()`で元に戻す（これがB4の修正）。
- `setupAirport()`：**冒頭**で`clearVorForcing()`（AIRPORT_DEFAULTSのside適用より前に置くこと。
  後だと空港ごとの既定sideが上書きされる）。VORから離れた場合のみ`applyApproachDefaults()`。
- `render()`の非VOR分岐で、Approach/Sideボタンの`active`をstateに同期（復帰後の見た目合わせ）。

> **踏んだ罠**：退避を`state.approach="circling"`の**後**に置くと、render()が退避する時点で
> 既にcirclingになっており、解除しても元のApproach Typeに戻らない。必ず倒す前に退避すること。

**検証（全項目パス）**
| 操作 | 結果 |
|---|---|
| 羽田Visual/Right → VOR A→16L → 新千歳 | 19L / Left / Visual / 2.5NM / 182kt / 1500ft |
| 羽田Visual/Right → VOR 34L→16R → 伊丹 | 14R / Right / Visual / 1500ft |
| VOR A→16L → 34Rに戻す | Visual / Right に復帰 |
| Circlingで入って→VOR→34Rに戻す | Circlingに復帰（ユーザーの選択を尊重） |
| VOR未経由で空港切替 | baseDist/glideAngle等の調整値を**保持**（リセットしない） |
| 手動Circling中に空港切替 | Circling維持 |
| VOR方式の値 | 34L→16R=1.5NM/147kt、A→16L=2.5NM/147kt（build127を維持） |

羽田VOR方式×Cut角×風×3Dの36件、空港切替を挟む連続操作8件、全空港×Visual/Circling36件で
異常0件（福岡34R Visual/45kt風の幾何エラーはbuild 124の意図的挙動）。

### 8.0f build 127：VOR 34L→16R のDownwind offset/速度が操作順で変わる不具合を修正

8.0eの検証で判明した件。**同じVOR 34L→16Rを選んでいるのに、それまでの操作順で
表示される数値が全部変わっていた**（起動直後に選ぶと offset2.5NM/182kt、
先にCirclingボタンを押してから選ぶと 1.5NM/147kt）。

| | 修正前・起動直後にVOR | 修正前・Circling後にVOR | 修正後(全経路) |
|---|---|---|---|
| offset / 速度 | 2.5NM / 182kt | 1.5NM / 147kt | **1.5NM / 147kt** |
| Turn開始 距離 | 5.11 NM | 3.50 NM | 3.50 NM |
| TOD | Abeam+**91秒** | Abeam+53秒 | Abeam+53秒 |
| Turn終了 高度 | 400ft MSL (0.99NM) | 350ft MSL (0.83NM) | 350ft MSL (0.83NM) |
| 必要Bank | 14.5° | 23.5° | 23.5° |

**原因**：滑走路選択時の処理が2段階で、その間で`state.approach`がずれていた。
1. `#rwyEnd`のchangeハンドラが`applyApproachDefaults()`を呼ぶ。この時点の`state.approach`は
   **選択前の値**（起動直後ならvisual）なので、Visualの既定値 2.5NM/1500ft/182kt が書き込まれる
2. そのあと`render()`がVOR方式と判定して`state.approach="circling"`に上書きし、
   **Downwind Altitudeだけ**730ft MSL固定にする
3. offsetと速度は誰も直さず、1で入ったVisualの値が残る

`isVorA16L`側は`downwindOffset`/`downwindSpd`を明示的に書いていたため無事だった。
`isVor34L`だけ書き忘れ、という形。**何も考えず滑走路を選ぶという一番自然な操作で
手順書と違う値が出る**向きだったのが厄介だった（警告も出ず数字はそれらしく揃う）。

**修正（2箇所）**
1. 根本原因：`#rwyEnd`のchangeハンドラで、`applyApproachDefaults()`を呼ぶ**前に**
   VOR方式なら`state.approach="circling"`にしておく。
2. 明示指定：`isVor34L`ブロックでも`downwindOffset="1.5"` / `downwindSpd="147"`を設定
   （`isVorA16L`と同じ書き方に統一）。`render()`がどの経路から呼ばれても自己修復する。

**ユーザー確認済みの既定値**：Visual = 2.5NM / 182kt、Circling = 1.5NM / 147kt。
VOR A→16Lは**Circlingだが手順固有で2.5NM/147kt**（AOMI上空通過のため）なのでそのまま。

**検証**：操作順5パターン(起動直後/Circling後/Visual後/VOR→34R→VOR/VOR A→VOR34L)で
すべて同一値になることを確認。VOR A→16Lが2.5NM/147ktのままであること、
通常Circlingが1.5NM/147kt・通常Visualが2.5NM/182ktであることも確認。
羽田VOR方式×Cut角×風×3Dの36ケースで異常0件。
（福岡34R Visual/45kt風で幾何エラーが出るのはbuild 124の意図的な挙動で、本修正の回帰ではない）

### 8.0e 人間が飛んだ場合の検証（モンテカルロ、2026-09-21・未修正）

「アプリの数値どおりに人間が飛んだらどこに出るか」を、アプリとは**独立に実装した**
点質量シミュレータ（x=東/y=北、ω=g·tanφ/V、風ベクトル加算、dt=0.05s）で各4000回試行。
人間のばらつきは abeam判定±1.5s / 操作の反応遅れ1.2±0.4s / Roll rate 3.2±0.6°/s /
Bank ±2.5°+ドリフト±1.5° / IAS±3kt / 減速率0.7±0.15kt/s / V/S確立6±2s・±60ft/min /
Downwind offset±0.15NM / 機首方位±2° / Final turn開始の目測±10%。

**独立実装の裏取り**：完璧に飛んだ場合のRoll out距離が Visual 1.62NM（アプリ表示1.60NM）、
横ズレ−3m・高低差−10ft。アプリの幾何と独立計算がほぼ一致しており、シミュレータ側は信頼できる。
またFinal turnの開始目測係数を較正すると k≒1.27（cross≦1.27Rで旋回開始すると正対）となり、
build 123で判明した「Roll-in/out込みの横消費は定常半径の約1.2倍」と独立に一致した。

| ケース | 理想(完璧) | 人間: 横ズレσ | 人間: 90%区間 | 必要修正角(90%) | 横ズレ>300m |
|---|---|---|---|---|---|
| Visual 34R (Bank25°, 2旋回) | −3m / −10ft | **184m** | ±390m | 6.0° | 14.5% |
| VOR34L→16R 現状設定 (offset2.5NM/182kt→Bank14.5°) | −76m / −95ft | **1041m** | −2119〜+1125m | 36.3° | 73.1% |
| VOR34L→16R 手順書どおり (offset1.5NM/147kt→Bank23.5°) | +1m / −10ft | **488m** | ±800m | 26.5° | 53.4% |

**分かったこと**
1. **Visualは人間が飛んでも十分実用的**。2つの90°旋回の間にBase legがあるため、
   Base turn開始のタイミング誤差がBase legの長さで吸収され、Final turnは滑走路を見て
   目測で入る＝閉ループになる。実際、abeam判定±1.5sの寄与は横ズレσでわずか**1.5m**。
   横ズレの支配要因はFinal turn開始の目測(σ132m) > Bank誤差(88m) > Roll rate差(80m)。
2. **Circlingの連続180°旋回は開ループで、誤差が積分される**。途中に修正の機会が無く、
   Bank角の誤差がそのまま旋回半径→横ズレに効く。しかも**浅いBankほど脆い**：
   Bank誤差1°あたりの旋回半径変化は 14.5°で7.2% / 23.5°で4.8% / 25°で4.6%。
   180°旋回ではこれが2倍の横ズレになる。
3. **高度は人間が飛ぶと低めに出る傾向**（Visual平均−42ft、Circling現状設定−154ft）。
   高低差の支配要因は機首方位保持±2°(σ71ft)とDownwind offset誤差(σ48ft)。
   どちらも経路長が変わることで、同じV/Sでも到達高度がずれる。

**要判断：VOR 34L→16R のDownwind offsetと速度が手順書と食い違っている**
`render()`の`isVor34L`ブロックは**Downwind Altitude(730ft MSL)しか強制していない**。
一方`isVorA16L`ブロックはoffset(2.5NM)とDownwind IAS(147kt)も明示的に設定している。
このためVOR 34L→16Rを選ぶと、直前の`applyApproachDefaults()`が入れたVisualの既定値
（offset 2.5NM / 182kt）がそのまま残り、Circlingなのに182ktのDownwindになる。
HANDOVER 6.1の記述は「**1.5NM Downwind**」なので食い違っている。
影響は表のとおりで、現状設定だと理想的に飛んでも−76m/−95ftずれ、人間が飛ぶと横ズレσが
手順書どおりの設定の**2倍以上**になる。`isVorA16L`と同じようにoffsetと速度も
強制するのが筋だが、1.5NM/147ktで正しいかは運航側の確認が必要なため未修正。

### 8.0b build 123 で修正した分（A3 / B5）

**症状**：Visualの「Turn終了(Final)高度」が実Roll out高度より約350ft高く、
「旋回半径」の表示がどの旋回の実半径とも一致しなかった（上記A3・B5）。

**根本原因は2段構え**だった。ユーザー（現役パイロット）の指摘で判明。

1. **Final turnの旋回半径をDownwind IASで計算していた**（`R_visual = turnRadiusM(downwindTAS, 25)`）。
   実測すると Abeam(6.73NM)から0.7kt/secで減速して**4.32NM地点で減速完了**、
   Final turnは3.21→1.41NMなので**全区間がTarget APP TASで回っている**。
   Base turn(5.32→3.52NM)も開始167kt→終了151ktでDownwind TASではない。
   → `tasProfileFn`から**各旋回のコーナー位置のTAS**で半径を取るように変更。Base/Finalで別々の半径になる。
2. **コーナー幾何が純粋な円弧を前提にしていた**。接線長を`R·tan(θ/2)`で置いていたが、
   実際の経路はRoll-in/Roll-out(3°/sec、25°なら各8.33秒)の間ほぼ直進するため横に広がり、
   同じ定常旋回半径でも正味の変位が約2割大きい。この差を`scale`が吸収して、
   **積分した正しい形が引き伸ばされて描かれていた**（1.を直しただけだと今度は79%に縮んだ）。
   → `computeTurnArc`内で、シミュレート済み経路の正味変位`dnet`から
   **`Rfit = dnet / (2·sin(θ/2))`** を逆算してコーナーに充てる方式に変更。
   これで`scale`が全ケースで厳密に**1.000000**になり、積分した形がそのまま描かれる。

**表示側の変更**
- 「Turn終了(Final) 高度」= 実際のRoll out地点(`arc2`/`cont`の終点)の高度。距離もカッコ内に併記
  （例: `600 ft MSL (1.60 NM)`）。Visual/Circling で定義を統一。
- 「旋回半径」= `Base 0.81 / Final 0.71 NM`（各旋回時のTASでの定常旋回半径）。

**検証**（3.4の「計算ロジック変更→代表空港でフルスイープ」に従い羽田420通り＋全空港744通り）
- NaN 0件 / 着地cross-track誤差 最大0.047m / Roll out Track誤差 最大0.0009°
- Entry offset誤差 最大0.000m / Overshoot 0件 / Bank最大32.3°(45°超0件)
- `scale`が全ケース1.000000（伸縮なし）／無風時の描画Final turn半径が物理値の**100%**
- 羽田VOR 34L→16R・VOR A→16L（別コードパス）も無風/45kt風で正常
- Circlingの「Turn終了高度」は修正前後で不変（元々Roll out高度と一致していたため）

**副作用として認識しておくこと**：コーナー幾何が正直になったぶん、1旋回が消費する
Downwind offsetは`Rfit`（≒物理半径の1.2倍、Target APP速度で約1580m）になった。
つまり**Visualの正方形パターンはoffsetが約1.75NM以上ないと2つの90°旋回が収まらない**。
既定の2.5NMは余裕あり（Base leg 1262m）だが、offsetを1.5NMに詰めるとBase legが負になり
経路が折り返す（A4の警告未実装の件は据え置き）。これは物理的に正しい挙動で、
旧版が「収まっているように見えていた」のが誤り。

### 8.0d map_data統合まわりの不具合（build 124と同時に修正、HTML本体は変更なし）

ユーザーから「map dataが統合されていない気がする」との指摘。調べたら**2箇所**壊れていた。
どちらも`approach_planner.html`側ではなくツール側（`tile_downloader.py` / `bundle_html.py`）。

**① GUIでダウンロードしても`map_data_all.js`が作られなかった**
`launch_gui()`内の`run_downloads()`は`process(key)`を呼ぶだけで`merge()`を呼んでいなかった。
mergeしていたのはCLIの`all`だけ。ところがdocstringはGUIを「推奨」としているので、
推奨どおりに使うと統合版ができない、という状態だった。
→ `run_downloads()`の最後に`merge()`を呼ぶよう修正（1空港でも成功していれば実行）。

**② `bundle_html.py`が統合版と個別ファイルを二重に埋め込んでいた**
HTMLは統合版→個別ファイルの順に読む（統合版が無い環境へのフォールバック）作りなので、
`bundle_html.py`の正規表現`map_data_[a-zA-Z0-9_]+\.js`は両方にマッチし、
**同じ空港のデータが2回埋め込まれていた**。ダミー3空港で実測して 1.29MB → 本来0.70MB、
つまり**ほぼ2倍**。実データは1空港数MB〜10数MBなので9空港なら数十MBの無駄になり、
iPad Safariの読み込みが重くなる。
→ `map_data_all.js`の中身を`var MAP_DATA_(\w+)`で走査して収録済みの空港キーを把握し、
  **統合版に入っている空港の個別ファイルは埋め込まずスキップ**するよう修正。
  統合版に無い空港（統合後に追加DLした空港）は従来どおり個別に埋め込む。
  出力後に同一空港の重複が無いか自己検査してログに出す。

**検証した3ケース**（ダミーデータで実測）
| 状況 | 結果 |
|---|---|
| 統合版あり + 個別ファイルあり | 統合版のみ埋め込み、重複0、1.29→0.70MB |
| 統合版なし・個別のみ | 従来どおり個別を埋め込み（3空港・重複0） |
| 統合版が古く熊本だけ後から追加 | 統合版3空港＋熊本の個別を埋め込み、計4空港・重複0 |

**③ `tile_downloader.py`の空港名が全滑走路対応前のままだった**
GUIのチェックボックス表示・ログ・生成される`map_data_<key>.js`の`name`フィールドに使われる名前が、
本体側で全滑走路をモデル化したあとも滑走路限定の表記のままだった。
- 新千歳: `新千歳空港 (RJCC) 01R/19L` → `新千歳空港 (RJCC)`（build 122で01L/19Rも追加済み）
- 羽田: `羽田空港 (RJTT) C滑走路 16L/34R` → `羽田空港 (RJTT)`（4本すべてモデル化済み）
- 伊丹: `伊丹空港 (RJOO) B滑走路 14R/32L` → `伊丹空港 (RJOO)`（build 122で14L/32Rも追加済み）
→ 全9空港で`approach_planner.html`側の`name`と文字列一致することを確認済み。
**本体の空港名を変えたら`tile_downloader.py`側も直すこと**（別管理なので片方だけ直して忘れやすい。
座標の二重管理と同じ罠）。

なお取得半径6NMは追加した滑走路も十分カバーしている（ARPから最遠THRまで
新千歳01L 0.82NM / 伊丹32L 0.97NM / 羽田23 2.14NM、いずれも余裕3.8NM以上）。

**⑦ 取得中のスリープ抑止（`keep_awake`）**
空港1つでタイル数百枚＝数分かかるため、放置中にPCがスリープして取得が
途中で止まるのを防ぐ。`process()`を`with keep_awake():`で囲んでいる。

| OS | 方法 |
|---|---|
| Windows | `SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)`、終了時に`ES_CONTINUOUS`で解除 |
| macOS | `caffeinate -i -w <自分のPID>` を子プロセスで起動 |
| Linux | `systemd-inhibit --what=idle ... sh -c 'while kill -0 <PID>; do sleep 5; done'` |

実装上の注意点（どれも踏むと事故になる）:
- **WindowsのSetThreadExecutionStateはスレッド単位**。GUIは別スレッドで取得するので、
  必ず取得を行うワーカースレッド内で`keep_awake`に入ること（`run_downloads`の中で囲んである）。
- **macOS/Linuxは子プロセスが即死することがある**（例: systemdセッションの無い環境で
  `Failed to connect to bus`）。`Popen`は成功するので、0.3秒待って`poll()`で生存確認してから
  「抑止できた」と表示する。できていないのに出来たと言わないため。
- **GUIのワーカーはdaemonスレッド**なので、ウィンドウを閉じるとfinallyが走らないことがある。
  Linuxで`sleep infinity`を使うと抑止が残り続けるため、自分のPIDを見張らせて
  親が死んだら子も終わるようにしてある。
- 抑止するのはシステムのアイドルスリープだけ。画面は消えてよいし、
  フタを閉じた場合や手動スリープはOSの仕様上抑止できない。
- 抑止に失敗しても取得自体は必ず続行する。

検証：Windows成功/API失敗、macOS成功/コマンド無し、Linux即死 の5分岐をモックで確認し、
いずれも中の処理が実行され、成功時のみ「スリープしないようにしています」と表示、
Windowsは設定0x80000001→解除0x80000000の順で呼ばれることを確認。
CLI相当・GUI相当(daemonスレッド)の両方で実際の取得フローも通した。

**⑤ build 126：個別ファイルをそもそも作らない方式に変更**
build 125でHTML側は統合版だけを読むようにしたが、`process()`が
`map_data_<key>.js`を書き→`merge()`が束ねる構造のままだったので、
**個別ファイルは相変わらず生成され続けていた**（ユーザー指摘）。
`save_as_js()`を廃止し、`save_to_merged()`が**`map_data_all.js`を直接更新する**方式にした。

- 統合版は「1空港＝1行の`var MAP_DATA_<KEY> = {...};`」という形式なので、
  行単位で読み書きすれば特定の空港だけ差し替えられる（`load_merged`/`write_merged`）。
- 取得時は既存の統合版を読み込み→該当空港の行を追加または差し替え→書き戻す。
  **既に入っている他の空港は消えない**。
- 書き込みは一時ファイル`.tmp`に書いてから`os.replace()`で差し替える。
  途中で失敗しても既存の統合版（全空港ぶん）を失わない。
- `merge()`は役割変更：**旧バージョンが残した個別ファイルを統合版に取り込む移行用**。
  取り込み後は個別ファイルを削除する（既定。`--keep`で残せる）。
  個別ファイルは材料ではなくなったので、削除しても以後の追加取得に影響しない。
- GUI/CLIからの自動merge呼び出しは不要になったので削除。

検証（ネット接続なしで`fetch_and_stitch`を差し替えて再現）：
羽田→新千歳と追加取得して`map_data_all.js`1ファイルのみ生成・2空港収録、
羽田を取り直すと差し替えになり新千歳は残る、旧個別ファイル2つを`merge`で取り込んで削除、
生成された統合版をNodeで実行して`MAP_DATA_*`が正しく定義されることを確認。

**⑥ build 126：クレジット表示(.attribution)の増殖を修正**（監査B1）
画像データが入って初めて表に出た不具合。空港を切り替えるたびに`.attribution`が
重なって増えていた（羽田→新千歳→伊丹で3個）。画像の無い空港へ移ると1個しか
消えず、他空港のクレジットが残った。地図を作り直す前に`querySelectorAll`で
全部消すよう修正。画像あり→なし→ありを往復しても常に1個/0個になることを確認。

**④ build 125：個別ファイルのフォールバック読み込みを廃止**
「今後は統合版しか使わない」とのユーザー判断により、HTMLの`<script src="map_data_*.js">`を
**`map_data_all.js`の1行だけ**にした。これで二重読み込みの懸念（統合版と個別ファイルを
両方置いた場合にブラウザが両方パースする件）は構造的に消えた。

これに伴い、**どの取得経路でも必ず統合版が作り直される**ようにした。
| 経路 | build 124まで | build 125 |
|---|---|---|
| GUI | mergeしない（← 不具合①） | mergeする |
| CLI `all` | merge する | merge する |
| CLI 単一空港 | mergeしない | **merge する**（統合版しか読まれないので必須） |

個別ファイル`map_data_<key>.js`は**次回mergeの材料**としてPC側に残す運用。
iPadに送るのは`map_data_all.js`（またはバンドル版HTML）だけでよい。
`merge --delete`は個別ファイルを消すので、以後空港を追加すると過去の空港が
統合版から抜け落ちる。実行時に警告を出すようにしたが、基本は使わないこと。

`bundle_html.py`は統合版1つを埋め込むだけになるが、古いHTML（個別も読む版）を
渡されたときのために重複スキップ処理は残してある。統合版が無く個別ファイルだけある
場合は「先にmergeを実行して」と案内する。

### 8.0c build 124：成立しないパターンはエラー表示にして描画しない（A4対応）

ユーザー指示「Downwind幅が旋回に対して足りないならエラーを出して描画しないように」。
旧版は幾何的に成立しない形でも何事もなく描いてしまい、飛べない経路を信じる危険があった。

**判定**（`computeFullPatternGeometry`が`geomError`を返し、`render()`が受けて描画を中止する）
1. **Visual**：Base turnとFinal turnの接線長の合計がDownwind offsetを超える
   → Base legが負になり経路が折り返す。実際に計算したarc1/arc2の端点から実測して判定。
2. **Circling**：180°連続旋回のBank逆算が探索上限(75°)に張り付く＝そのoffsetでは回りきれない。
   実際には60°台で解が出るためほぼ発火しない安全網（>30°の赤字警告は従来どおり別途出る）。
3. **Entry(S字)**：Turn1とTurn2の間の直進レグが負になる（Cut角が大きく風が強いとき）。

**エラー時の挙動**：地図上に赤枠のオーバーレイ（`#geomError`）、readoutをエラー文に差し替え、
VSDパネルを隠す。滑走路と周回進入区域はこの時点で既に描画済みなので位置関係だけ残る。
パターンの線・マーカー・TOD・Abeam・Entryは一切描かない。条件を戻せば通常表示に復帰する。

**閾値の実測**（羽田34R Visual 無風、Base 2.5NM）
| 条件 | Base leg | 判定 |
|---|---|---|
| offset 2.0NM 以上 | +1262m以上 | 正常 |
| offset 1.8NM | 負 | エラー（「約1.85NM以上に」と表示） |
| IAS 260kt / offset 2.5NM | +286m | 正常 |
| IAS 300kt / offset 2.5NM | −43m | エラー |

**運用上の注意**：45kt級の強風だと接線長が伸びるため、既定のVisual offset 2.5NMでも
一部の空港・滑走路でエラーになる（新千歳Visual + 280/45 等）。物理的に正しい判定だが、
閾値が厳しすぎると感じたらBank角の前提（25°固定）ごと見直す必要がある。
Circlingは既定offset 1.5NMでもBank逆算で収まるためエラーにならない。

> **検証手順の注意**: jsdomで`document.querySelector('svg')`を使うと風向計のSVG(`.windIndicator`内)を拾ってしまい、
> 地図SVGの検査が空振りする。地図は必ず **`#rotwrap svg`** を、VSDは `#vsdSvg` を指定すること。
> （build 122の検証で実際にこれを踏んで、NaN検査が無効になっていた）

### 8.0h build 129：Cut Angleの見た目を実態に合わせる・風入力の範囲検査・デッドコード削除（監査B2・B8・B9）

**B2（Cut Angle）**：VOR方式では押せるのに効かないボタンがあった。見た目を実態に揃えた。
- VOR A→16L：cut angleを一切使わない（常にNIL）→ `#cutAngle` グループごと `opacity .5` + `pointerEvents:none`
- VOR 34L→16R：45°/60°は有効だがNILは不可 → **NILボタンだけ** `opacity .35` + `pointerEvents:none`
- 通常滑走路：グループもNILも復元（VORで個別に落とした分を戻し忘れると、VORを一度通っただけで
  NILが押せないまま残る。非VOR分岐で必ず両方クリアすること）

**B8（風入力）**：`"999/99"` が剰余で279°M/50ktとして黙って通っていた。
- 方位 `<=360`・風速 `<=99` の範囲検査を追加。範囲外／書式不正は**無風扱い**にしたうえで
  `#wind` の枠線と文字を `var(--red)` にして知らせる（正常入力に戻すと自動で解除）。
- 風速0または不正のとき `#windIndicatorText` を毎回 `"---°M / --kt"` に戻す
  （従来は矢印だけ消えて直前の風の文字列が残っていた）。50kt上限クランプは従来どおり。

**B9（デッドコード）**：`convexHull` / `bufferedHullOutline` を削除（2449文字・65行、参照0）。
実際に使われているのは `unionStadiumOutline` のみ。削除範囲の終端は直後のコメント
`// 複数の滑走路(runwayPairs:` を目印にすること（`unionStadiumOutline`本体まで巻き込みやすい）。

**検証**（羽田のみ・3.4のスコープ規則どおり）
| 項目 | 結果 |
|---|---|
| 非VOR(34R) | cutSeg 有効 / NIL 有効 |
| VOR A→16L | cutSeg opacity .5 + none / NIL は個別指定なし |
| VOR 34L→16R | cutSeg 有効 / NIL opacity .35 + none |
| 34Rへ復帰 | cutSeg・NIL とも完全復元 |
| 風 `320/25` / `280 25` | 正常受理・赤なし・指示器 320°M/25kt |
| 風 `999/99` `361/10` `280/100` `abc` | 無風扱い＋赤表示＋指示器 `---°M / --kt` |
| 風 空欄 / `000/00` | 赤なし・指示器 `---°M / --kt` |
| `convexHull`/`bufferedHullOutline` | `undefined`（`unionStadiumOutline`は健在） |
| NaN走査（34R / VOR A→16L / VOR 34L→16R、`#rotwrap svg` + `#vsdSvg` + readout） | 0件 |

### 8.0i A2の定量化：追い風45ktでFinalは何NM取れるか（広島RWY28 Circling Left / Wind 280°M 45kt）

Downwindは45ktの**追い風**（G/S 197kt）、Finalは45ktの**向かい風**（G/S 107kt）。
Abeam通過からBase Turn開始までの待ち時間と、得られるFinal長さの関係（Bank 24.0°）：

| Final長 | Abeam→Turn開始 |
|---|---|
| 1.00NM | +3.6秒 |
| 1.25NM | +8.2秒 |
| 1.50NM | +12.8秒 |
| 2.00NM | +21.9秒 |
| 2.50NM | +31.1秒 |
| 3.00NM | +40.2秒 |

- 現行の目標秒数 `20 − 0.4×45 = 2.0秒` は**物理的に到達不能**（1.00NMでも3.6秒必要）。
- おおよそ **0.85NM未満はabeam通過前に旋回開始**という幾何的に破綻した領域。
  現行のフォールバックが返す Base距離 0.11NM はこの破綻領域の中にあり、
  build 124の`geomError`ガードはこれを検出できない（**未対応の穴**）。
- 無風時の基準：Final 0.84NM → Abeam+19.9秒（＝目標20秒の素の姿）。
- したがって「名目20秒」をこの追い風で成立させると **Final ≈ 1.9NM**。
- 係数を 0.2秒/kt にすると目標11秒 → **Final ≈ 1.4NM**。

→ 4.4の「0.4秒/kt が効きすぎ」問題の決着（係数を変えるのか、表示だけ実測値に合わせるのか）は
   **ユーザー判断待ち**。あわせて破綻領域を`geomError`で弾く対応も未実施。

### 8.0j build 130：地図の拡大(フルスクリーン)ボタンを廃止

ユーザー判断により `⤢` / `✕` の拡大ボタンを削除。地図の拡大・縮小は
**ピンチズーム / ホイールズーム / ドラッグパン**（`zoomPanWrap`、倍率1〜6倍）だけで行う。

**削除したもの**（4箇所、計867文字）
- CSS `.mapwrap.fullscreen`（position:fixed の全画面レイアウト）
- CSS `.zoomBtn`
- HTML `<button class="zoomBtn" id="zoomBtn">`
- JS のクリックハンドラ、および `pointerdown` 冒頭の
  `if(e.target.closest(".zoomBtn")) return;` ガード（ボタンが無くなったので不要）

`const mapwrap` は pointer capture / ズーム基準矩形で使い続けるので残してある。

**検証**（羽田）：`zoomBtn` はDOMに不在、`mapwrap` のclassは `mapwrap` のみ、
ドラッグパンは健在（40/30pxのドラッグで `translate(40px,30px) scale(1)`）、
34R / VOR A→16L / VOR 34L→16R でNaN 0件、JSエラーなし。

### 8.0k build 131：表示中の地図をPNGで保存する機能

`⤓ PNG` ボタンを地図右上（build 130で外した拡大ボタンの位置）に追加。
画面のDOM（衛星画像`<img>` + 経路`<svg>`）をCanvasに合成し直して書き出す。

**仕様（ユーザー選択）**
- **書き出し範囲**：ズーム/パン（`zoomPanWrap`）は**無視**し、常に既定の全体表示（等倍）。
  いつ押しても同じ構図になる。
- **含める情報**：**地図と経路のみ**。コンパス(`#compassLabel`)・風指示器(`#windIndicator`)・
  クレジット(`.attribution`)はHTMLオーバーレイなので焼き込まない。
- 出力は 2000×2000 px 固定（`DL_EXPORT_PX`）。画面サイズに依存しない。
- ファイル名：`<空港key>_<滑走路>_<approach>_<YYYYMMDD_HHMM>.png`
  例 `haneda_VOR3416R_circling_20260921_2113.png`

**3D表示中は使えない**：`rotateX` の3D変換はCanvasの2Dコンテキストで再現できないため、
`render()` 冒頭で `state.show3D` ならボタンごと `display:none` にしている。

**実装上の要点（踏むと静かに壊れる箇所）**
1. **CSS変数の解決**：SVG内の色は `stroke="var(--cyan)"` の形で書いてある。SVGを単体画像として
   ラスタライズすると `:root` の変数が引けず、色が全部既定値になる。`dlResolveCssVars()` で
   複製側の全属性を `getComputedStyle(document.documentElement)` の実値に置換してから直列化する。
2. **viewBox外のクリップ**：画面では `svg.style.overflow="visible"` で viewBox(0 0 1000 1000)の外へ
   伸ばした線も描いているが、単体SVG画像では viewBox 外が切られる。書き出し用の複製だけ
   viewBox を `-500 -500 2000 2000` に広げ、描画矩形も `(-R/2, -R/2, 2R, 2R)` と同じ比率で広げる。
3. **font-family**：`<text>` は画面ではbodyから継承しているが、単体SVGでは継承元が無く既定のserifに
   なる。複製のルートに `getComputedStyle(document.body).fontFamily` を明示する。
4. **transformの再現**：`rotwrap` は `left/top`＋`transform-origin`＋`rotate()` で配置されている。
   Canvas側は `translate(L,T) → translate(ox,oy) → transform(行列) → translate(-ox,-oy)` の順で
   同じ配置を作る。行列は `getComputedStyle(rotwrap).transform` の `matrix(...)` を読む。

**検証**（羽田・Playwright/Chromiumで実描画、ダミー衛星画像を入れて画像合成経路も確認）
| 項目 | 結果 |
|---|---|
| 2D時のボタン表示 / 3D時 / 2Dへ戻す | 表示 / 非表示 / 表示 |
| Visual 34R の書き出し | 画面スクショと構図・色・ラベルが一致（オーバーレイ3点を除く） |
| VOR 34L→16R + 風320/25 | 保護区域の赤破線・VORラジアル・TTE VORマーカーまで正しく出力 |
| 1.12倍にズームした状態で書き出し | ズームは無視され等倍の全体構図（仕様どおり） |
| ファイル名 | `haneda_VOR3416R_circling_20260921_2113.png` |
| pageerror / console error | 0件 |

**PNG採用の根拠（JPEGと実測比較済み・build 131で確定、再検討不要）**

2000pxで実際に書き出して比較した結果、**条件によって勝敗が逆転する**ため、
「最悪でもファイルが少し大きいだけ」で済むPNGを採用した。ユーザー承認済み。

| 条件 | PNG | JPEG q92 (4:4:4) | JPEG q85 (4:2:0) |
|---|---|---|---|
| 衛星画像あり | 4.43 MB | 1.02 MB | 0.59 MB |
| 衛星画像なし(線のみ) | **0.026 MB** | 0.166 MB | 0.094 MB |

- 衛星画像ありなら JPEG が4倍軽く、3倍拡大しても線・ラベルの劣化はほぼ判別不能
  （写真のノイズがブロックノイズを隠すため）。ここだけ見ればJPEG有利。
- 衛星画像なしだと **JPEGの方が6倍重いうえに汚い**。平坦な暗い背景＋彩度の高い細線＋小さい文字は
  JPEGの最悪ケースで、線と文字の周囲にモスキートノイズが出る。PNGは平坦部をほぼタダで圧縮できる。
- 送信サイズが問題になるなら「衛星画像ONのときだけJPEG q92、OFFならPNG」の自動切替が
  両取りになる（実装せず。必要になったらこの表を根拠に戻れる）。

> **検証時のメモ**：`map_data_all.js` が無いと衛星画像の合成経路を通らない。検証では
> ダミーのdataURI画像を持つ `map_data_all.js` を一時的に作って確認し、検証後に削除した。
> 配布物には含めないこと。

### 8.0l build 132：Flap Maneuver Speedからの速度自動計算（重量入力）

ANA B787 飛行機運用規程 PI.20.26「Flap Maneuver Speed」(Sea Level Pressure Altitude /
Effect. DEC 16, 2024 Rev.34 / All Engines) を書き起こし、重量から Downwind IAS と
Target APP SPD を自動入力できるようにした。**OPTの解析物ではなくAOMからの手書き起こし**
（OPTのAPDはライセンス物かつ暗号化されており、解析しない方針。8.1参照）。

**原本を読んだ結果の重要な発見**：10ページ（機種×エンジン型式）あるが、**同じ機種なら
エンジン型式が違っても速度は完全に同一**で、違うのは収録重量レンジだけ。よって表は3つで足りる。
787-8 だけフラップ段が異なり、**Flap 10 と 15/17/18 統合欄が無い**（UP/1/5/15/20/25/30）。
787-9 と 787-10 は UP/1/5/10/15,17,18/20/25/30。

| 機種 | 収録型式（重量レンジ 1000 LB） |
|---|---|
| 787-8 | TRENT1000-AE(480〜240) / -CE(500〜240) / -L(500〜240) / -H(400〜240) |
| 787-9 | TRENT1000-AE(500〜280) / -K(560〜280) / -D(560〜280) / GENX-1B64(500〜280) |
| 787-10 | TRENT1000-K(560〜280) / GENX-1B67(540〜280) |

> **原本どおりに保持している不審値**：787-10 の UP 欄が **440 と 420 の両方で 234**。
> 460=237, 400=227 なので 420 は 231 前後になりそうだが、TRENT1000-K と GENX-1B67 の
> **両ページで一致**しているため読み取り誤りではない。推測で直さないこと。

**仕様（すべてユーザー判断）**
- 機体型式は **3択**（787-8 / 787-9 / 787-10）。エンジン型式は持たない＝重量レンジは各機種の
  表の全域を使う。選んだ型式の上限超えを入れても引けてしまうが、そこはユーザー責任。
- 重量は **1000 LB の自由入力**（`400.0` のように小数第1位）。表は20,000 LB刻みなので
  **線形補間**。範囲外は端の値に**クランプ**（外挿はしない。根拠のない値になるため）し、
  ヒント欄に `⚠ 表の範囲外(…)` を出す。
- **Downwind IAS** = Visual なら **F5 をそのまま**（**+5しない** / build 133でユーザー指定）。
  **Circling は Downwind Flap を F20/F25/F30 から選択**（build 134）。
  F20 のときだけ **+5しない**、F25/F30 は **+5**。
- **Target APP SPD** = **Landing Flap (F25 / F30) を選択** して **+5kt**（`FMS_ADDITIVE_KT`）。
- **重量が空欄なら自動計算しない**＝従来どおり 182/147 の固定既定値。機能は既定でOFF。
- 入力欄はあくまで「既定値」で、手入力による上書きは可能。自動値を書き込むのは
  **自動値が変わる操作のとき**だけ（機種/重量/フラップ/Approach Type/滑走路切替）。
  毎フレーム書くと入力中の値と喧嘩するため `render()` からは書かない。

**build 127の固定147ktとの関係**：VOR 34L→16R / VOR A→16L の Downwind IAS は、
重量が入っていれば表由来の進入速度に置き換わる（`fmsVorDownwindSpd()`）。空欄なら147kt固定のまま。
`render()`から毎フレーム呼ばれる箇所なので、入力欄に書くのではなく**値を返すだけ**にしてある。

**追加した関数**：`fmsLookup(model, weightKLb, flapKey)` / `fmsAutoSpeeds(approachOverride)` /
`fmsVorDownwindSpd()` / `fmsApplySpeeds()` / `fmsUpdateHint()`。データは `FMS_TABLES`。
参照するのは F5・F25・F30 だけだが、原本検証と将来の拡張のため**全列を保持**してある。

**検証**
| 項目 | 結果 |
|---|---|
| 表の直値13点（3機種・端と中間） | 不一致 0 |
| **CSVとの全件突き合わせ（1014セル）** | **不一致 0**（書き起こしCSVとHTML内テーブルを機械照合） |
| 線形補間 787-9 410k F30 / 415k F30 | 149.5 / 150.25（期待どおり） |
| クランプ 999k / 100k | 174・clamped / 124・clamped |
| 重量空欄 | DW 182 / APP 147（従来どおり） |
| Visual 787-9 400.0k F30 | DW 188 (F5そのまま) / APP 153 (F30 148+5) |
| F25へ切替 | APP 158 / DW 188（DWはF5のまま） |
| Circling | DW 153 = APP 153 |
| 787-8 400.0k | DW 187 (F5そのまま) / APP 152 (F30 147+5) |
| VOR 34L→16R・VOR A→16L（重量あり） | DW 153（旧147を置換） |
| 同・重量空欄 | DW 147（従来どおり） |
| NaN走査（34R / VOR A→16L / VOR 34L→16R） | 0件 |

書き起こしの原本CSV（縦持ち1014行、型式・重量・フラップ・KIAS）は
プロジェクトの `flap_maneuver_speed_SL.csv` に保存してある。表を直すときは
**CSVとHTMLの両方**を直し、上の機械照合を再実行すること。

**Sea Level のみで確定**：10000 FT / 20000 FT Pressure Altitude の表は**取得しない**
（パターン高度は通常1500ft AFE程度で、進入計画にはSea Levelで足りるというユーザー判断。
build 134時点で「Sea Levelのみでいい」と明言されたので、再検討不要）。

> **build 133 の変更**：`+5kt` を足すのは **Target APP SPD だけ**（`FMS_ADDITIVE_KT`）。
> Downwind IAS(Visual) は F5 の Maneuver Speed をそのまま使う。再検証で
> 787-9 400.0k → DW 188 / APP 153、787-8 → 187 / 152、787-10 → 188 / 151、
> Circling と VOR 経路は DW = APP = 153、重量空欄は 182/147、NaN 0件を確認済み。

### 8.0m build 134：Circling の Downwind Flap 選択（F20 / F25 / F30）

Circling の Downwind IAS を「Target APP SPD と同じ」固定から、**Downwind Flap の選択**に変更。

| 選択 | Downwind IAS | 備考 |
|---|---|---|
| F20 | Maneuver Speed **そのまま（+5なし）** | APPより速いので **AbeamからTarget APP SPDまで0.7kt/secで減速**（Visualと同じ扱い） |
| F25 | Maneuver Speed **+5kt** | |
| F30 | Maneuver Speed **+5kt** | Landing Flapも30なら従来どおり DW = APP |

**減速について実装は不要だった**：`tasAtDistFromThr()` の減速プロファイルは元から進入方式に
依存せず `downwindTAS > targetAppTAS` なら必ず効く作りになっている。速度値を変えるだけで
F20の減速は自動的に成立する。

`#circDwFlapField` は **Circlingのときだけ表示**（`baseModeField`/`vorAltField` と同じ場所で制御）。
state に `circDwFlap`(既定30) を追加。VOR 34L→16R / VOR A→16L も常にCirclingなので同じ規則に従う。

**検証**（787-9 / 400.0k LB / F30 APP = 153kt）
| Circling DW Flap | Downwind IAS | Downwind TAS | Turn開始 | Roll out |
|---|---|---|---|---|
| F30 | 153kt (148+5) | 155kt | 3.57NM (Abeam+20.0s) | 360ft / 0.86NM |
| F25 | 158kt (153+5) | 160kt | 3.57NM (Abeam+20.0s) | 370ft / 0.87NM |
| F20 | **163kt (+5なし)** | 165kt | 3.59NM (Abeam+20.0s) | 370ft / 0.88NM |

差が小さいのは減速が効いているため（F20でも20秒のうち約14秒で153ktに到達する）。
他に Visual時の非表示、VOR 34L→16R でのF20反映(163kt)、重量空欄時の147kt固定、
NaN走査0件を確認済み。

### 8.0n build 135：コンパス表示の方位を3桁ゼロ埋め（監査B6）

地図左上が `RWY 01R ↑ 3°M`、滑走路プルダウンが `RWY 01R (003°M)` と表記が割れていた。
`String(rwyMagHeading).padStart(3,"0")` に統一（`getRunwayMagHeading()` は既に整数を返すので
`Math.round()` は不要だった）。新千歳01L/19R/01R/19Lと羽田34R/16L/04/22/23で、
地図の表記とプルダウンの表記が一致し3桁になることを確認。数値には影響しない表示のみの修正。

### 8.0o B7の調査結果：ずれているのは**ラベルだけ**で、TODの距離も位置も実経路ベース

「TODの計算自体がコーナー幾何ではないか」という疑問への回答。コードを追った結果、
**影響しているのは括弧内のセグメント名だけ**だった。

| 要素 | 根拠 | 判定 |
|---|---|---|
| `pat.descDistM`（必要降下距離） | 高度差・降下角・V/S立ち上がり(5秒)から算出。幾何に依存しない「必要なトラックマイル」 | 正しい |
| TODの地図上の位置 | `pointAtDistanceAlongPath(nearToFarFull, descDistM)` — **実際に描画している経路の弧長** | 正しい |
| TODのTRK表示 | `headingAtDistanceAlongPath(nearToFarFull, descDistM)` — 同上 | 正しい |
| 降下中/水平の色分け | `withArc[i].arc <= descDistM`（弧長） | 正しい |
| **セグメント名** `(Downwind)` 等 | `descDistM <= baseDistM_` / `<= baseDistM_+offsetM_` という**コーナー幾何のしきい値** | **B7＝ここだけ** |

修正するなら、しきい値を `closestPointArcLength(nearToFarFull, connectXY)` などの
**実経路の弧長**に置き換える（`actualTurnStartDistNM` が既に同じ方式で計算されているので流用できる）。

> **ついでに見つけたデッドデータ**：`buildPattern()` が返す `descentStartXY`
> （＝コーナー幾何の `pointAtDistFromThr(descDistM)`）は**どこからも読まれていない**。
> 実際の描画は上表のとおり実経路側を使っている。B9と同種の掃除対象。

### 8.0p build 136：TODのセグメント名を実経路の弧長で判定（監査B7）＋デッドデータ削除

8.0oの調査どおり、ずれていたのは**括弧内のセグメント名だけ**。しきい値を
コーナー幾何（`baseDistM_` / `baseDistM_+offsetM_`）から**実際に描画している経路
（`nearToFarFull`）の弧長**に置き換えた。

**境界の取り方**：区間の境目は旋回弧の端点そのもの。Visualは2つの旋回
（Final turn=`arc2` / Base turn=`arc1`）の端点4つ、Circlingは連続180°旋回（`cont`）の
端点2つを `closestPointArcLength()` で弧長にしてから昇順に並べ、境界として使う。
ラベルも細分化した。

| | 区間名 |
|---|---|
| Visual | Final / **Final Turn** / **Base** / **Base Turn** / Downwind |
| Circling | Final / Turn / Downwind |

Cut angle Entryを出しているときの遠方は従来どおり `Entry/Downwind` の併記のまま
（EntryとDownwindの境目は経路の組み立て方に依存するため、ここでは分けない）。

**ついでに削除**：`offsetM_`（この判定でしか使っていなかった）と、8.0oで見つけた
デッドデータ `descentStartXY`（`buildPattern()`が返すがどこからも読まれていない）。

**検証**（羽田34R・3.4のスコープ規則どおり代表1空港のみ / 120ケース）
Visual・Circling × Downwind offset 2.0/2.5/3.5 × Downwind Alt 1000〜3000 × Base Dist 1.0〜3.5。
**独立判定**として、TOD点から各旋回弧の点列までの最短距離を測り
「実際にどの弧の上にいるか」を幾何的に求めてラベルと突き合わせた。

| 項目 | 結果 |
|---|---|
| ケース数 | 120 |
| 旧ロジックと一致 | 81 |
| ラベルが変わった | 39 |
| **独立判定（TOD点と旋回弧の距離）との不一致** | **0** |

変わった39件には、語彙の細分化（旧`Turn`→新`Final Turn`/`Base Turn`）と、
**本物の誤り**（旧`Downwind`なのに実際はBase旋回中 / 旧`Final`なのに実際はFinal旋回中）の
両方が含まれる。例：
- off2.0 / alt1000 / bd1.0 → 旧 `Downwind` → 新 `Base Turn`（TOD点はarc1から1.9m）
- off2.0 / alt1500 / bd2.5 → 旧 `Downwind` → 新 `Base Turn`
- off2.5 / alt1000 / bd3.5 → 旧 `Final` → 新 `Final Turn`

個別確認：羽田34R Visual `4.60NM (Base Turn)` 境界1.60/3.08/3.76/5.28NM・arc1から1.9m、
VOR 34L→16R `2.11NM (Turn)` 境界0.83/3.50NM・contから2.2m、
VOR A→16L `5.48NM (Downwind)` 境界0.83.../5.33NM・contから268m（旋回の外）、
Cut45+高高度で `Entry/Downwind` 併記維持、NaN走査0件。

### 8.0q V/S立ち上がり秒数(VS_RAMP_SEC)の検討 → **5秒のまま据え置き（ユーザー判断）**

`buildPattern()` の `VS_RAMP_SEC`（TODでV/Sが0から目標値まで直線的に立ち上がる秒数）を
8秒・10秒にした場合のTODの変化を調べた。**結論：据え置き。**

**閉形式の整理**（コードを整理するとこうなる。これ自体が理解の鍵）
```
descDist = Δ高度 / tan(降下角)  +  G/S × T / 2
```
立ち上がり区間で進む距離 `G/S×T` から、その間に失えた高度ぶんの距離 `G/S×T/2` を引くので、
**正味の損は立ち上がり距離のちょうど半分**。言い換えると
**「立ち上がり T 秒 ＝ T/2 秒だけ水平飛行してから瞬時に降下開始」と等価**。
したがって増分は `G/S × ΔT/2` で、**Δ高度にも進入方式にも依存せず最終進入のG/Sだけで決まる**。
Visual(1500ft)とCircling(700ft)でTODの絶対値は倍以上違うのに、増分は同じになる。

**独立検証**：アプリの閉形式とは無関係に、0.001秒刻みの数値積分で確かめた（差 0.1m 以内）。

| T | 降下距離(1500→69ft, G/S149kt) | 5秒からの増分 |
|---|---|---|
| 0秒(瞬時) | 4.4938NM | — |
| **5秒(現行)** | **4.5974NM** | — |
| 8秒 | 4.6594NM | +115m |
| 10秒 | 4.7008NM | +192m |
| 15秒 | 4.8043NM | +383m |
| 20秒 | 4.9078NM | +575m |
| 30秒 | 5.1147NM | +958m |

効き方は**線形**で「一気に増える」ことはない（149ktで1秒あたり約39m）。
羽田34R / 787-9 370.0k / APP F30 での実測も Visual +117m / Circling +115m（5→8）、
+194m / +192m（5→10）と一致。向かい風25ktではG/Sが落ちるぶん +98m / +164m に縮む。

> **立ち上がり秒数よりも効く余地がある仮定（未対応・要注意）**：降下距離の計算は
> G/Sを**Target APP SPD基準の一定値**としている。実際はAbeamから減速中で、TOD付近では
> まだ速い可能性がある。既定設定では 182→147kt の減速に50秒、TODがAbeam+48秒と
> **ちょうど減速し終わる直前**という際どい一致で成立していた。Downwind速度を上げたり
> TODが手前に来る設定では、降下開始時点でTarget APP SPDより速く、実際のTODは計算より遠くなる。
> 直すなら降下距離を減速プロファイル込みの積分にする（旋回側の `tasAtDistFromThr` と同じ方式）。
> 立ち上がり秒数を5→10にする影響(0.1NM)より、こちらのほうが大きくなりうる。

### 8.0r build 137：UI表記を「Target APP Flap」→「Landing Flap」に

実運用でその言い方をしないというユーザー指摘による、ラベル文言のみの変更。
`#appFlap` / `state.appFlap` などの内部名は変更していない（挙動に影響しないため）。
Circling側の「Downwind Flap (Circling)」はDownwindの形態を指すもので Landing Flap ではないので
そのまま。

### 8.0s build 138：3D表示のドラッグ回転が指と逆だったのを修正

**症状**：3D表示で1本指ドラッグすると、掴んだところが指と逆方向へ逃げる。

**原因**：`state.headingOffsetDeg = (zpDragStart.hdg||0) + dx*0.4;` の符号。
CSSの `rotate()` は**時計回りが正**なので、右ドラッグ(dx>0)で上から見て時計回りに回る。
地図は `rotateX(55°)` で手前に倒してあるので**画面下＝手前側**。上から見て時計回りにすると
手前側の地物は左へ動く＝指と逆。3Dビューアの通例は「触ったところが指についてくる」なので
**マイナスが正しい**（上から見て反時計回り）。

**修正**：`+ dx*0.4` → `- dx*0.4`（1文字）。

**検証**（Playwright/Chromiumで実描画、地図上マーカーの画面座標を実測）
| | 手前(画面下)の地物 dx | 奥(画面上)の地物 dx |
|---|---|---|
| 修正前・右へ100px | **−156 / −121（指と逆）** | −27 |
| 修正後・右へ100px | **+95（指と同じ）** | −27 |
| 修正後・左へ100px | **−144（指と同じ）** | −27 |

奥側が逆向きに動くのはターンテーブルとして正しい挙動。2D時のドラッグパンは
`translate(80px, 0px)` で影響なし。JSエラー0件。

### 8.0t tile_downloader.py：標高データ(DEM)の取得を追加

3D表示での地形可視化とVSDの地形断面に使う標高データを、衛星画像と同じ仕組みで取得できるようにした。
**この時点ではダウンローダー側だけ。approach_planner.html はまだ標高を読んでいない。**

**データ源**：国土地理院 標高タイル（出典表示 `標高: 国土地理院 標高タイル` を `dem.attribution` に格納）
- `dem`（既定）: 10mメッシュ / zoom 14 / **日本全国カバー**
- `dem5a`: 5mメッシュ / zoom 15 / 整備済み区域のみ・**タイル数が4倍**
- 形式は `.txt`（256行×256列のカンマ区切り、単位m、欠測は `e`）

**設計上のポイント**
- **boundsは衛星画像と完全に同じ**（タイル格子の外周）。グリッドの並びもアプリの
  `latLonToFraction` と同じ「緯度・経度に対して線形」にしてあるので、画像レイヤーとそのまま重なる。
  画像が未取得の空港でも、同じ計算でboundsを求めてから標高だけ取れる。
- **出力セルには入力ピクセルの最大値を入れる**（平均ではない）。平均だと山頂が均されて消えるため。
  検証では真の山頂3776mに対しグリッド最大3775.7m（差0.3m）、位置ズレ30m（グリッド間隔70m）。
- **PNGで符号化**して data URI に入れる。`v = round((h+1000)*10)` を `R=v>>8, G=v&255`、
  `B=255`が有効・`B=0`が欠測。**ブラウザがPNGをネイティブに復号できる**ので、アプリ側は
  canvasに描いて `getImageData` するだけでよい（外部ライブラリ不要・完全オフラインを維持）。
  可逆圧縮なのでJPEGは不可。量子化は0.1m刻み＝**最大誤差5cm**（検証実測値と一致）。
- **エントリのマージ**：`update_merged_entry()` を新設し、`map_data_all.js` の1行を
  一度JSONに戻してからフィールドを足す方式にした。これにより
  **画像を取り直しても標高が消えず、標高を足しても画像が消えない**。

**使い方**
- GUI：「標高データも取得」チェックボックス（既定ON）と「5mメッシュ」チェックボックスを追加。
- CLI：`--no-dem`（画像だけ）/ `--dem-only`（標高だけ・画像は既存を保持）/ `--dem5a`（5mメッシュ）。

**データ量**（`DEM_GRID_SIZE` で調整可能。既定512）

| グリッド | 12NM四方での間隔 | PNG | base64 | 9空港合計(目安) |
|---|---|---|---|---|
| 512 (既定) | 約43m | 320〜370KB | 430〜490KB | 約4MB |
| 256 | 約87m | 80〜100KB | 110〜130KB | 約1MB |

GitHub/Cloudflare配信（8.2）でサイズが問題になるなら256に落とす余地がある。
山の可視化用途なら87m間隔でも実用上は足りるはず。

**検証**（GSIへは当環境から接続できない＝プロキシが遮断するため、**合成地形で検証**。
実データの取得はユーザーのPCで実行する。衛星画像と同じ運用）
| 項目 | 結果 |
|---|---|
| タイル取得・グリッド化 (110枚→256×256) | 有効セル 99.4% / 欠落タイル0 |
| 符号化→復号の往復誤差 | **最大 5cm**（0.1m量子化の理論値どおり） |
| 欠測(`e`)の扱い | B=0として保存され、復号時にnullで返る |
| 山頂の保存 | 真値3776m → グリッド最大3775.7m（差0.3m） |
| 最高点の位置 | 真の山頂から30m（グリッド間隔70m以内） |
| `update_merged_entry` | 標高追加後も `imageDataURI` が残存することを確認 |

> **次にやること**：approach_planner.html 側の実装。(a) DEMのPNGをcanvasで復号、
> (b) VSDに地形断面、(c) 3Dに**塗りの帯**（Downwind高度基準の色分け）。
> 傾斜のドラッグ操作は**ユーザー判断で保留**。

### 8.0u build 139：標高データの表示（VSD地形断面・3D等高帯・最高標高）

8.0tで取得できるようにしたDEMを、アプリ側で表示する。**傾斜のドラッグ操作はユーザー判断で保留**。

**(1) DEMの復号** — `loadDem()`。PNG data URIを `Image` → `canvas` → `getImageData` で読み、
`h_m = ((R*256+G)/scale) - offsetM`（B=0は欠測→NaN）で `Float32Array` にする。
外部ライブラリ不要・`file://` でも動く（data URIはcanvasを汚染しない）。
**非同期**なので、読み終わってから `render()` を呼び直す。読み込み中に空港を切り替えたときは
`demLoadToken` で古い結果を捨てる。DEMが無い空港では機能ごと消える（下記の検証参照）。

**(2) 経路まわりの最高標高**（readoutに1行）。真下1点では尾根を見落とすので、
**経路の左右 `DEM_CORRIDOR_NM`(=0.5NM) の帯の最大値**を拾う。Downwind高度との差で
色が変わる（上=danger / 1000ft以内=warn / それ以下=通常）。

**(3) VSDの地形断面**。同じ帯の最大標高を断面として塗り、稜線を引く。
**降下プロファイルより上に出ている区間は赤で重ねる**。山が高ければ縦軸を自動で広げる。
> 地形断面は `render()` 側で1回だけ作って `renderVSD()` に渡している。
> 最初は両方で別々に計算していて、**readoutとVSDのラベルで最高標高が2ft食い違った**。
> 同じ配列を共有させて解決。分けて計算しないこと。

**(4) 3Dの等高帯**（`buildDemBands()`、marching squares）。
- **帯ごとに高度が一定** → 3Dの持ち上げは**帯まるごとに `translate` ひとつ**で済む。
  点ごとの再計算が不要なので回転中も軽い。**これがWebGLなしで成立している理由**。
- 粗グリッド(`DEM_BAND_GRID`=160、セルは元グリッドの**最大値**＝山頂を潰さない)で輪郭を抽出し、
  端点をつないで閉じた輪にして `fill-rule="evenodd"` で塗る（穴も正しく抜ける）。
  外周を `DEM_BELOW` で囲ってあるので輪は必ず閉じる。曖昧ケース(5/10)はどちらの繋ぎ方でも閉じる。
- 帯の間隔は500ft。`DEM_BAND_MAX`(=14)を超えるなら間隔を倍にしていく。
- **色はDownwind高度(MSL)基準**：上=赤 / 1000ft以内=橙 / それ以下=緑。形は空港ごとに不変なので
  `demBands` にキャッシュし、色だけ毎回決める。
- 2D表示でも同じ帯が平面の等高線図として出る（`tiltDeg=0` なので持ち上げ量が0になるだけ）。
- 「地形 表示/非表示」トグルを追加（既定ON）。

**検証**（合成地形。GSIへは当環境から接続できないため、羽田のboundsに3,478ftの山を置いたDEMを生成）
| 項目 | 結果 |
|---|---|
| PNG復号の正しさ | `demElevM` が山頂で1060.1m（真値1066.8m、グリッド43m間隔ぶんの差） |
| 範囲外・欠測 | NaNを返す |
| 等高帯の抽出 | 6帯（500〜3000ft）/ **11ms** / パス長 498〜4,289文字 |
| 3Dドラッグ性能 | 30フレームで189ms＝**1フレーム6.3ms** |
| VSD | 地形断面・稜線・プロファイル超過区間の赤塗り・最高点ラベルが正しく描画 |
| readoutとVSDの最高標高 | 共通化して一致 |
| **DEMが無い場合** | hintが「未取得」/ 最高標高の行は出ない / NaN 0件 / トグル・3D操作でもエラーなし |

> **未対応（意図的）**：地形の帯は経路線より**背面**に描いており、山が機体より高くても
> 経路線は隠れない。計画用としては経路が常に見えるほうを優先した。正しく遮蔽したいなら
> 「Downwind高度より上の帯だけ経路線より後に描く」等の分岐が要る。

### 8.0v tile_downloader.py：タイル取得の並列化とレート制限

**動機**：9空港ぶんの取得が10〜17分かかっていた。調べると**帯域は全く律速していない**
（転送量780MB＝500Mbpsなら13秒ぶん）。効いていたのは
**「1枚ずつ直列」＋「1枚ごとに `time.sleep(0.05)`」** の2つで、所要時間は
実質「タイル枚数 ×(RTT + 0.05秒)」で決まっていた。

| | 画像 | 標高 | 合計 |
|---|---|---|---|
| 9空港のタイル枚数 | 5,029枚 (zoom15) | 1,498枚 (zoom14) | 6,527枚 |
| 転送量の目安 | 約123MB | 約658MB | 約781MB |

標高のほうが転送量は5倍なのに時間が短いのは、**律速が容量ではなく枚数**だから。

**変更**：固定スリープを廃止し、**同時 `MAX_WORKERS`(=8) 本の並列取得**＋
**全スレッド合計のリクエスト毎秒を `MAX_RPS`(既定25) で頭打ちにする** 方式にした。

- `_RateLimiter`：ロック下で「次に開始してよい時刻」を進める。並列でも**総リクエストレートは上限値そのもの**。
  RTTが大きい環境でも小さい環境でも、負荷も所要時間も同じ値に収束する（RTT依存が消える）。
- `_session()`：`requests.Session` を `threading.local` でスレッドごとに持つ（コネクション再利用）。
  プールサイズも `MAX_WORKERS` に合わせてある。
- `_run_parallel()`：**チャンクに区切って**投入する。一括で全部投げるとデコード済みタイルが
  メモリに山積みになる（画像なら1GB級）。「取る→使う→捨てる」を繰り返して使用量を抑える。
- **PILの `paste` はメインスレッドだけ**で行う（スレッド安全でないため）。取得とデコードだけ並列。
- 標高の積算ループを最適化：出力セル番号は**タイル内で行・列ごとに共通**なので、
  画素ごとに緯度経度を計算していたのを**列ごとの表引き**に置き換えた。

**速度の指定**：GUIに「取得速度」（控えめ10 / 標準25 / 速い50 rps）、
CLIに `--rps=N` / `--workers=N`。所要時間はおおむね **総タイル数 ÷ MAX_RPS**。

| 設定 | 9空港の見込み |
|---|---|
| 控えめ 10rps | 約11分 |
| **標準 25rps** | **約5分** |
| 速い 50rps | 約3分 |

> 標高の解析はPython側のCPU仕事（1タイル65,536値）で、GILがあるので並列化しても
> CPU時間は縮まない。標高フェーズは25rps設定だとレートより**CPU律速**になりやすい
> （実測で上限25に対し16.4rps）。それでも旧方式よりは速い。

**検証**（疑似サーバー・RTT50msで）
| 項目 | 結果 |
|---|---|
| **結果が旧ロジックと一致するか** | **不一致セル 0 / 16,384（完全一致）** — 積算ループ最適化の正しさを直列参照実装と比較して確認 |
| 速度（36タイル） | 直列4.3秒 → 並列2.3秒 |
| レート制限（上限25） | 実測16.4rps（CPU律速で上限未満） |
| レート制限（上限5） | **実測5.0rps** — 上限が正しく効いている |
| 画像の並列合成 | 30枚1.2秒 / 画素値・boundsとも正常 |

> **上げすぎないこと**：`MAX_RPS` はそのまま国土地理院・Esriへの負荷。個人が一度取るだけの
> 用途として常識的な範囲に留める前提の設計。既定25rpsを超える設定は明示操作でのみ有効になる。

### 8.0w build 140：地形を100ft刻みの「つながった面」に＋傾斜のドラッグ操作

**(1) 段板を側壁でつなぐ** — build 139は等高帯の天面だけを描いていたので、
段板が宙に浮いて「面が重なっているだけ」に見えていた。各帯の輪郭を**下の段の高さまで
垂直に降ろした側壁**を追加し、連続した立体として見えるようにした。間隔は500ft→**100ft**。

側壁が安く描ける理由：3Dの持ち上げは**平行移動**なので、側壁は
「輪郭を上の高さへずらしたもの」と「下の高さへずらしたもの」を繋いだリボンになる。
リボンは閉じているので普通に塗れる。`demBandPaths(band, liftTop, liftBot)` がこれを作る。
そのため `buildDemBands()` の戻り値を**パス文字列から輪郭の点列に変更**した
（側壁は角度ごとに作り直す必要があるため、文字列のままだと使えない）。

描画順は低い帯から「側壁 → 天面」。高いものが後に重なるので前後関係が自然に正しくなる。
側壁は天面より不透明度を落として（×0.62）段の立ち上がりが分かるようにしてある。

**(2) 縦ドラッグで傾斜** — `TILT_MIN_DEG`(20)〜`TILT_MAX_DEG`(75)。
`rotateX()` は角度が大きいほど横から見る形になるので、**上へドラッグ＝horizon寄り**、
**下へドラッグ＝真上から**。地図アプリの2本指操作と同じ向き。
コンパス表示に現在の傾斜を併記する。3DをOFF→ONすると既定の55°に戻る。

> **同時に直した罠**：経路の持ち上げが `Math.tan(TILT_3D_DEG*D2R)` と**固定値**を見ていた。
> CSS側は `state.tiltDeg` で傾くので、傾斜を可変にした瞬間に**経路の高さだけCSS変換とズレる**。
> `state.tiltDeg` を使うよう修正済み。傾斜を可変にするなら必ずここもセットで直すこと。

**検証**（合成地形3,478ft / Playwright実描画）
| 項目 | 結果 |
|---|---|
| 帯の生成 | 34本(100〜3400ft) / 輪59本 / 点4,951 / 抽出 **47ms** |
| ドラッグ性能（側壁を毎フレーム再構築） | **1フレーム 9〜11ms** |
| 傾斜クランプ | 上へ大きくドラッグ→75で停止 / 下へ→20で停止 |
| 斜めドラッグ | 方位と傾斜が同時に変化（-60° / 45°） |
| **持ち上げ量の正しさ** | 実点と地面の影の画面距離が **sin(傾斜)に完全比例**（55/75/40/25°で誤差 **0.0%**） |
| 見た目 | 低角度でも高角度でも連続した立体として描画（段板の浮きは解消） |

> 側壁のパスは角度が変わるたびに作り直す（点列4,951点ぶんの文字列生成）。
> 今は9〜11ms/フレームで収まっているが、重く感じるようなら `DEM_BAND_GRID`(160) を
> 下げるか `DEM_BAND_FT`(100) を粗くするのが効く。

### 8.0x build 141：3D表示OFF時は地形情報を出さないよう変更

ユーザー指摘：「3Dオフのときは標高情報出さなくていいよ。逆にみづらい」。

**経緯**：build140で等高帯の間隔を500ft→100ftに細かくした結果、2D表示（`tiltDeg`が
実質0で帯が持ち上がらない）では帯の枚数が34本にまで増え、平面の地図に何十本もの
同心円状の色帯が直接重なって描かれる状態になっていた。build139時点では「2Dでも
等高線図として意味がある」という想定で意図的に許容していたが、間隔を細かくした
結果、想定より線が密になり実用上見づらくなった（ユーザーが実際に確認して判断）。

**対応**：地形（等高帯・帯の色分けヒント文言）の表示自体を **`state.show3D`が
trueのときだけ** に限定した。2Dでは地形関連のUIごと隠す。

- `render()`内の帯描画ブロックを`if(state.showTerrain){...}`から`if(state.show3D){...}`
  に変更（既存の`if(!demData){...}else{...}`の外側を丸ごと`state.show3D`でくるむ形）。
  2Dのときはこのブロック自体を素通りし、`#terrainHint`のテキストも更新しない
  （前回描画時の文言が残っても非表示なので実害なし）。
- `#showTerrainToggle`（地形 表示/非表示トグル）と`#terrainHintField`（ヒント文言の欄）を
  それぞれ`state.show3D`に応じて`style.display`で出し分け（`circDwFlapField`等と同じ
  既存パターンに合わせた）。2Dでは設定パネルからもこの2項目が消える。
- VSDパネルの地形断面（`renderVSD()`内の稜線・赤塗り）は**対象外**：VSDは平面地図とは
  別のグラフ表示であり、ユーザーの指摘は地図が「重なって見づらい」という趣旨だったため、
  VSDの地形断面は3D/2Dの状態に関わらず従来どおり表示を継続する。

**検証**（Playwright実描画。羽田、合成DEM: 500/800/1100mの3値からなる8×8グリッド）
| 項目 | 結果 |
|---|---|
| 2D（既定）: `#showTerrainToggle`の親`.field`の`display` | `none` |
| 2D: `#terrainHintField`の`display` | `none` |
| 2D: 地図SVG内の帯パス数（`fill="var(--red)"` 等） | **0** |
| 2D: `#terrainHint`の文言 | 「標高データ未取得」のまま（前回状態が残るが非表示なので無害） |
| 3D ON: `#showTerrainToggle`の親`.field`の`display` | `""`（表示） |
| 3D ON: `#terrainHintField`の`display` | `""`（表示） |
| 3D ON: 地図SVG内の帯パス数 | **40**（正常に描画） |
| 3D ON: `#terrainHint`の文言 | 「標高 1,640〜3,609 ft MSL ／ TEST（色は Downwind 1,521 ft MSL 基準）」 |
| 3D→2Dへ戻す | 帯パス数が再び**0**に戻る／トグル・ヒント欄も再び非表示に戻る |
| pageerror / console error | 0件 |

3D↔2Dの往復を含め異常なし。build139/140で作り込んだ帯・側壁のロジック自体（`buildDemBands()`,
`demBandPaths()`）には手を入れていない（呼ぶかどうかの条件を変えただけ）。

### 8.0y build 142：Downwind側デフォルトの不具合2件を修正

**症状1（熊本）**：RWY07がDownwind Left既定なのは正しいが、RWY25を選んでもLeftのままだった。
**症状2（羽田）**：他の空港を色々見てから羽田に戻ると、Right DWのはずがLeftになっていた
（ユーザー報告、実機で確認済み）。

**原因の整理**：`AIRPORT_DEFAULTS`のDownwind側("side")は**空港を切り替えた瞬間にだけ**
適用され、(a) 同じ空港内で滑走路端(RWY07↔RWY25等)を切り替えても再適用されない、
(b) 羽田は元々`side`を指定しておらず「現在の選択を維持」という設計（build119時点の意図）
だった。(b)のせいで、他の空港（左右どちらかを強制する空港）を経由すると、羽田に戻っても
その空港が最後に強制した側がそのまま残ってしまう。羽田だけ「未指定」にしていたことが
実質的にバグの温床になっていた。

**対応**
- 羽田も他空港と同様に`AIRPORT_DEFAULTS.haneda`へ明示的に`side:"right"`を追加。
  これで空港を切り替えて戻るたびに必ずRightへ揃う（従来の「維持する」という設計は撤回）。
- 熊本に`sideByRwy:{"07":"left","25":"right"}`を追加し、**同じ空港内で滑走路端を
  切り替えたとき**にもDownwind側の既定を反映できるようにした。適用ヘルパー
  `applySideDefaultForRwy(airportKey, rwyLabel, opts)`を新設し、
  - 空港切替時（`setupAirport()`から呼ぶ、`opts`省略）は「その滑走路端の指定
    (`sideByRwy`) → 無ければ空港全体の指定(`side`)」の順で適用（従来どおりの動作）
  - 同一空港内での滑走路端切替時（`#rwyEnd`のchangeハンドラから`{rwyOnly:true}`で呼ぶ）は
    **`sideByRwy`に明示指定がある場合だけ**適用する。熊本以外の空港（`sideByRwy`を
    持たない）は、滑走路を変えてもDownwind側は従来どおり触らない
    （B3で確定済みの「側は自動リセット対象に含まれない」という既存仕様を壊さないため）。

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| 熊本を選択 | side=left（従来どおり） |
| 熊本でRWY25に切替 | **side=right** |
| 熊本でRWY07に戻す | side=left |
| 羽田を選択（起動直後） | **side=right** |
| 羽田でsideを手動でLeftに変更→新千歳→広島→伊丹→松山と巡回 | （各空港の既定どおりに変化） |
| 巡回後、羽田に戻る | **side=right**（修正前はLeftのまま残っていた不具合） |
| 新千歳でsideを手動でRightに変更→別の滑走路端(01R)へ切替 | **side=rightのまま**（`sideByRwy`を持たない空港は従来どおり滑走路を変えても側を維持することを確認、回帰なし） |
| 全9空港を切替走査 | 各空港のDownwind側が期待どおり／NaN 0件／JSエラー0件 |

### 8.0z build 143：函館もDownwind側の滑走路端別デフォルトに対応（RWY30→Left）

build142で入れた熊本と同じ仕組みを函館にも適用。ユーザー要望「函館も同じ様に12 Right DWが
デフォルトだけど30を選んだ場合はLeft DWになる様に」に対応。

**変更点**：`AIRPORT_DEFAULTS`に`hakodate: { rwy:"12", side:"right", sideByRwy: { "12":"right", "30":"left" } }`
を追加しただけ（build142で作った`applySideDefaultForRwy()`のしくみをそのまま再利用、
ロジック自体の変更はなし）。函館はこれまで`AIRPORT_DEFAULTS`に項目が無く
`landableEnds[0]`（＝12、結果的に既定はRightではなく「現在の選択を維持」）に頼っていたが、
今回明示的にRWY12→Right / RWY30→Leftを指定した。

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| 函館を選択(起動直後) | side=right |
| 函館でRWY30に切替 | **side=left** |
| 函館でRWY12に戻す | side=right |
| 羽田でsideを手動Leftに変更→函館へ切替 | side=right（空港切替でデフォルトが適用される） |
| 函館→羽田 | side=right（build142の修正に回帰なし） |
| 熊本でRWY25に切替 | side=right（build142の挙動に回帰なし） |
| 全9空港を切替走査 | 各空港のDownwind側が期待どおり／NaN 0件／JSエラー0件 |

### 8.0za build 144：熊本のみDownwind Altitude(Visual)の既定値を1800ft AFEに

ユーザー要望「熊本だけ特殊で1800ft AFE（100ft単位四捨五入）をdownwind altにして」。
適用範囲をユーザーに確認したところ、**Visualのみ**に適用（Circlingは他空港と同じ700ft AFEのまま）
との回答だった。

**実装**：`DOWNWIND_ALT_AFE_OVERRIDE = { kumamoto: { visual: 1800 } }` という上書きテーブルを新設し、
`defaultDownwindAltAFE()`が「現在の空港・進入方式に該当する上書きがあればそれを返す、
無ければ従来どおり Circling=700 / Visual=1500」という優先順で値を返すようにした。
`applyApproachDefaults()`内の`downwindAlt`設定をこの関数の戻り値に置き換えるだけで、
**Approach Type切替・滑走路端切替（build142/143で使ったのと同じ`applyApproachDefaults()`の
呼び出し経路）の両方に自動的に反映される**（ロジックの重複追加は不要だった）。

**空港切替時の扱い**：build142/143のDownwind側と同様、「他空港は入力値を保持する」という
B3の既存方針は変えていない。ただし`DOWNWIND_ALT_AFE_OVERRIDE`に載っている空港（現状は熊本のみ）
に切り替えたときだけは、`setupAirport()`から`defaultDownwindAltAFE()`を呼んで強制的に
上書きするようにした。理由：熊本のDownwind Altは他空港と異なる特別な既定値であり、
「熊本を選んだのに前の空港の値が残っている」状態は避けたいと判断（Downwind側デフォルトの
build142/143と同じ考え方）。熊本以外の空港は従来どおり、空港を切り替えても値を保持する。

> **「100ft単位四捨五入」について**：1800はユーザーが提示した確定値であり、アプリ側で
> 何かを計算して丸めているわけではない（既に100ft単位の値）。周辺地形を踏まえてユーザーが
> 決めた数値をそのまま定数として採用した。

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| 熊本を選択(起動直後、Visual) | **1800**（空港切替でも即座に反映） |
| 熊本でCirclingに切替 | 700（Circlingは上書き対象外） |
| 熊本でVisualに戻す | 1800 |
| 熊本でRWY25↔07を切替(Visual中) | 常に1800 |
| 熊本のDownwind Altを手動で1650に変更→羽田へ切替 | 1650のまま保持（羽田に上書き設定なし、B3の既存方針どおり） |
| 羽田(1650のまま)→熊本に戻る | **1800**に強制的に戻る（熊本は上書き対象のため） |
| 全9空港を切替走査 | 熊本以外はいずれも直前の値を保持／NaN 0件／JSエラー0件 |

### 8.0zb build 145：Downwind Altitude欄をMSL入力に変更（AFEは自動表示のヒントに）

ユーザー要望「downwind altはMSLで入力して、入力したら自動で（1500ft AFE）って入力欄に
表示されるようにしようかな」。従来は入力欄そのものがAFE(標高からの高さ)基準で、
パイロットが実際にセットする管制上のMSL高度に暗算で変換する必要があった。これをMSL入力に
変更し、換算後のAFEを読み取り専用のヒント文言として横に出すようにした。

**実装方針**：**内部の計算式は一切変更していない**。既存の`downwindAltAFE`という変数名・
その後の全ての降下計算・VSD・readout表示は従来どおりAFE基準のまま。変わったのは
「入力欄の値をどちらの単位として読み書きするか」という**入出力の変換層だけ**。

- ラベルを「Downwind Altitude (ft AFE)」→「Downwind Altitude (ft MSL)」に変更。
  入力欄の下に読み取り専用の`#downwindAltHint`（例: `(1,500 ft AFE)`）を追加、
  他のヒント欄(`.hint`)と同じスタイルを流用。
- `render()`内の読み取り箇所を変更：
  ```js
  const downwindAltMSL = parseFloat(...)||(airport.elevationFt+1500);
  const downwindAltAFE = downwindAltMSL - airport.elevationFt;   // ここから先は従来どおり
  document.getElementById("downwindAltHint").textContent = "("+Math.round(downwindAltAFE).toLocaleString()+" ft AFE)";
  ```
  `render()`は`#downwindAlt`のinput/changeで既に呼ばれる配線になっていたので、
  ヒント更新のための新しいイベントリスナーは不要だった。
- 既定値を書き込む`applyApproachDefaults()`・熊本の空港切替時上書き（build144の`DOWNWIND_ALT_AFE_OVERRIDE`）は、
  従来どおりAFEで管理した既定値（Visual 1500 / Circling 700 / 熊本Visual 1800）に
  **書き込む瞬間だけ**その空港の標高を足してMSLに変換する`defaultDownwindAltMSL()`を新設し、
  そちらを使うように差し替えた（既定値の管理そのものはAFEのまま、というのが素直だったため
  変更していない）。
- 羽田VOR方式2つの強制値も**むしろ簡潔になった**：従来「730ft MSL固定」を`730-airport.elevationFt`と
  わざわざAFEに変換して書き込んでいたのを、単に`"730"`（VOR A→16Lも`state.vorAltMSL`をそのまま）
  書き込むだけで済むようになった。

**空港を切り替えたときの挙動**：入力欄の生の値は従来どおり空港を切り替えても保持される
（B3で確定済みの仕様）。以前は「保持されるのはAFEの生数値」だったので、切替先の空港では
違う高さ(MSL)を意味していた。今回は「保持されるのはMSLの生数値」に変わったので、
**切替先の空港では違うAFE**を意味することになる（意味が入れ替わっただけで、
「値を保持する」という挙動自体は変えていない）。空港固有の既定に上書きされる場面
（Approach Type切替・滑走路端切替・熊本への空港切替）では、その都度その空港の標高で
再計算されるので実用上は問題にならない。

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| 起動直後(羽田Visual) | 入力欄 **1521**（=1500+21） / ヒント **(1,500 ft AFE)** |
| 入力欄に手入力で2000 | ヒント **(1,979 ft AFE)**（=2000-21、即時再計算） |
| Circlingに切替 | 入力欄 **721**（=700+21） / ヒント (700 ft AFE) |
| 熊本Visualに切替 | 入力欄 **2432**（=1800+632） / ヒント **(1,800 ft AFE)** |
| 熊本Circlingに切替 | 入力欄 **1332**（=700+632） / ヒント (700 ft AFE) |
| 羽田 VOR 34L→16R | 入力欄 **730**（そのままMSL） / ヒント (709 ft AFE) |
| 羽田 VOR A→16L (既定1800ft MSL) | 入力欄 **1800** / ヒント (1,779 ft AFE) |
| 同・1500ft MSLに切替 | 入力欄 **1500** / ヒント (1,479 ft AFE) |
| 全9空港×Visual/Circlingを機械的に走査 | いずれもAFE換算値・MSL値が空港標高と整合／**NaN 0件**／JSエラー0件 |

従来どおり「空港を切り替えても入力欄の生数値は保持される」ことも確認済み
（例: 広島にMSL 1539のまま切り替わると、広島の標高1086ftを引いたヒントが
(453 ft AFE)になる。挙動としては意図どおりで、Approach Type切替などで再計算されれば
すぐに正しい値に上書きされる）。

### 8.0zc build 146：Downwind Altitude(MSL)の丸め処理と、熊本離脱時の値残留を修正

ユーザーから2点の指摘。

**(1)「MSLは四捨五入したものを表示して。実際の計算も四捨五入されたものを使っていいよ」**

build145でMSL入力に変えた際、入力欄の生の値をそのままAFE計算に使っていたため、
手入力で端数(例: 1521.6)が入ると、そのままの端数がdownwindAltAFEの計算に流れ込む
構造になっていた。ユーザーからは「表示も計算も四捨五入した整数でよい」と明言されたため、
`render()`内でMSL値を読み取った直後に`Math.round()`で整数化し、
**入力欄の表示自体もその場で丸めた値に書き戻す**ようにした。以降のAFE計算
(`downwindAltAFE = downwindAltMSL - airport.elevationFt`)もこの丸めた整数を使う。
（`airport.elevationFt`自体は函館111.9ftのように端数を持つ空港があるため、
AFE側は従来どおり端数が残ることがあるが、これは標高データそのものの精度であり
今回の指摘の対象外と判断）。

**(2)「熊本から他の空港にいったら1800ft AFEが残っちゃう」**

build144で追加した熊本Visual=1800ft AFEの上書きは、空港切替時に**上書き対象の空港に
入るとき**にしか反映していなかった。そのため熊本(2432ft MSL=1800ft AFE)から
上書きを持たない空港へ移ると、「値を保持する」というB3の既定方針により2432という
MSLの生数値がそのまま残り、移った先の空港では標高が違うため意味の無いAFEになっていた
(例: 広島だと標高1086ftを引いて(1346 ft AFE)相当になってしまう)。

**修正**：直前にいた空港がDownwind Altitudeの上書き対象だったかを`prevAirportHadDwAltOverride`
というモジュール変数で覚えておき、`setupAirport()`で「**今回が上書き対象、または直前が
上書き対象だった**」場合に通常の既定値へ戻すようにした。これにより：
- 熊本 → 上書きなしの空港：通常既定値(Visual 1500 / Circling 700ft AFE)にリセットされる
- 上書きなしの空港 → 上書きなしの空港：従来どおり値を保持(回帰なし)
- 上書きなしの空港 → 熊本：従来どおり1800ft AFE(Visual)が強制される(build144の挙動を維持)

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| 1521.6を手入力 | 欄の表示が**1522**に丸められ、ヒントも(1,501 ft AFE)に追従 |
| 1521.4を手入力 | 欄の表示が**1521**に丸められる |
| 熊本Visual(2432=1800AFE)→千歳 | **1521**（=1500+21ではなく千歳の標高分。ヒントは正しく**(1,500 ft AFE)**）に自動リセット |
| 熊本Visual→函館 | 同様に通常既定値にリセット、ヒント**(1,500 ft AFE)** |
| 羽田で手動1700に変更→伊丹 | **1700のまま保持**（上書き対象を経由していないので回帰なし） |
| 全9空港を切替走査 | NaN 0件・JSエラー0件、熊本のみ2432(1800ft AFE)、他は前の空港からの持ち越し値（従来どおりの「値を保持する」挙動） |

### 8.0zd build 147：機体型式/重量の既定値、Downwind IAS・Target APP SPDを常時グレーアウト

ユーザー要望「型式と重量をデフォルトで787-9 370にして」「downwind iasとtarget app spdは
表示欄は残したまま、グレーアウトで編集できないようにして」。

**機体型式/重量の既定値**：`#acModel`は元々787-9が既定(`class="active"`)だったので変更不要。
`#acWeight`の初期値を空欄(`value=""`)から`"370.0"`に変更。これにより**起動直後から
Flap Maneuver Speedベースの自動計算が有効**になる（従来は重量が空欄で機能全体がOFFの状態が既定だった）。

**Downwind IAS・Target APP SPDの編集不可化**：`#downwindSpd`・`#targetAppSpd`の`<input>`に
`disabled`属性を追加（`#thrCross`が既に同じ扱いになっており、その前例に倣った）。
値は従来どおり`applyApproachDefaults()`・`fmsApplySpeeds()`・VOR方式の強制ロジックが
JS経由で書き込む（`disabled`でも`.value`のJS書き込みは可能なので、表示・自動計算の
ロジックは一切変更していない）。見た目のグレーアウトが分かりやすいよう
CSSに`.field input:disabled{ opacity:.45; color:var(--textdim); cursor:not-allowed; }`を追加。

**意味合い**：これまでは「重量を入れなければ手入力・重量を入れれば自動計算で上書きされる
が手入力で再度上書き可能」という設計だったが、既定で重量が入るようになったことで
Downwind IAS・Target APP SPDは実質的に**常に自動計算の出力専用欄**になった。
表示は残しつつ編集不可にすることで、うっかり手で上書きしてFMSの値と食い違う事故を防ぐ。

**検証**（Playwright実描画）
| 項目 | 結果 |
|---|---|
| 起動直後の`state.acModel` | 787-9 |
| 起動直後の`#acWeight`値 | 370.0 |
| `#downwindSpd`/`#targetAppSpd`の`disabled` | 両方true |
| 起動直後の値 | 182 / 147（`fmsAutoSpeeds()`の計算結果と一致、偶然ではなく実際に自動計算が効いている） |
| 重量を450.0に変更 | 197 / 162に追従（`fmsAutoSpeeds()`の計算結果と一致） |
| 全9空港×Visual/Circling切替 | 常に182/147(既定370.0k時)、NaN 0件・JSエラー0件 |

### 8.0ze build 148：Downwind Altitude(MSL)の丸め単位を1ft→10ftに変更

ユーザー指摘「まだdownwind altが1ft単位になってるな」。build146で導入した四捨五入は
`Math.round()`による1ft単位の丸めだったため、AFEの既定値(1500/700/1800ft)に空港標高
(21ft、632ft、111.9ftなど端数を持つものも)を足すと、1521・1332・1611.9→1612のような
1ft刻みの半端な数字になっていた。入力欄自体は元から`step="10"`（10ft単位の増減）だったので、
丸め単位もそれに揃えるのが自然と判断し、`roundToStepFt(v, step) = Math.round(v/step)*step`
という汎用の丸め関数を追加して、丸め処理をすべて10ft単位に変更した。

- `defaultDownwindAltMSL()`（既定値をMSLに変換して書き込む関数）
- `render()`内で入力欄の値を読み取って丸める箇所（手入力の端数もその場で10ft単位に丸めて書き戻す）

AFE換算(`downwindAltAFE = downwindAltMSL - airport.elevationFt`)は変更なし。標高が
端数を持つ空港(函館111.9ft等)では、MSLを10ft単位に丸めた結果としてAFE側にわずかな
端数が残ることがある(例: 函館Visual既定→MSL1610ft、AFEヒントは(1,498 ft AFE)であり
1500ぴったりにはならない)。これは標高データそのものの精度によるもので、
ユーザー指摘の対象(MSLの丸め)には含まれないと判断し、意図的に許容している。

HTML側の初期値(JS実行前の静的な`value`属性)も1521→**1520**に合わせて修正した。

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| 起動直後(羽田Visual) | **1520**（10の倍数） |
| 1523を手入力 | **1520**に丸まる |
| 1528を手入力 | **1530**に丸まる |
| 熊本Visual(AFE1800+632=2432) | **2430**に丸まる |
| 函館Visual(AFE1500+111.9=1611.9) | **1610**に丸まる |
| 全9空港を切替走査 | いずれも10の倍数／NaN 0件／JSエラー0件 |

### 8.0zf build 149：Downwind Altitude(MSL)の丸め単位を10ft→100ftに変更

ユーザー指摘「違う。100ft単位にしてほしいんだよ」。build148で10ft単位に変更したばかりだったが、
実際に管制へ言う／自分で設定する高度としては100ft単位が実用的とのことで、`roundToStepFt()`
の呼び出し2箇所(`defaultDownwindAltMSL()`・`render()`内の手入力丸め)の丸め単位を
`10`→`100`に変更。関数自体(`roundToStepFt(v, step)`)は前回追加したものをそのまま再利用できた。

入力欄の`step`属性も`"10"`→`"100"`に、HTML静的初期値も`1521`→（build148で`1520`）→
**`1500`**（=1500ft AFE+羽田標高21ftを100ft単位に丸めた値）に更新。

**丸めによるAFEの見え方について**：MSLを100ft単位に丸めるため、逆算したAFEヒントは
きれいな1500ぴったりにはならないことがある(例: 羽田Visual既定→MSL1500ft、標高21ftを
引いたAFEヒントは(1,479 ft AFE)。これは「MSLを100ft単位に丸めて、それを実際の計算にも
使ってよい」というユーザーの了承済み方針どおりの挙動。

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| 起動直後(羽田Visual) | **1500**（100の倍数）／ヒント(1,479 ft AFE) |
| 1540を手入力 | **1500**に丸まる |
| 1560を手入力 | **1600**に丸まる |
| 熊本Visual(AFE1800+632=2432) | **2400**に丸まる |
| 函館Visual(AFE1500+111.9=1611.9) | **1600**に丸まる |
| 函館Circling(AFE700+111.9=811.9) | **800**に丸まる |
| 全9空港を切替走査 | いずれも100の倍数／NaN 0件／JSエラー0件 |

### 8.0zg build 150：VOR方式のDownwind Altitudeが100ft丸めで改変されてしまうバグを修正

ユーザーからの2点の確認・指摘。

**(1)「表記だけじゃなくて実際の計算も100ft単位の高度からにしてるよね？」**
→ **はい、意図どおり**。`render()`内で入力欄のMSL値を100ft単位に丸めてから
`downwindAltAFE`（以降のすべての降下計算・VSD・readoutが参照する値）を算出しており、
表示だけでなく計算そのものが丸めた後の値を使っている。ここは確認のみで変更なし。

**(2)「別途指定しているVOR34Lとかは10ft単位のままになってるよね？」**
→ **指摘のとおりで、実際にはこちらは意図せず100ft丸めの影響を受けてバグっていた**。
VOR 34L→16Rは手順書どおり**730ft MSL固定**を`isVor34L`のブロックで直接セットしていたが、
そのすぐ後で走る「入力欄の値を100ft単位に丸めて書き戻す」処理（build149で追加）が
**このVOR固有の値にも無条件にかかってしまい、730→700ft MSLに書き換えられていた**
（実測で確認：修正前は`VOR3416R`選択時に欄が730ではなく700になっていた）。
単なる表示の丸め忘れではなく、**AFE計算に使う実際の高度が変わってしまう**実害のあるバグだった。

**修正**：`render()`内のMSL丸め処理を`isVorProcedure`（VOR 34L→16R または VOR A→16L）の
間は丸めずスキップするように変更。VOR方式は手順書に明記された特定の値
（730 / 1800 / 1500ft MSL）をそのまま使う。通常滑走路（VOR方式でない）のときだけ
従来どおり100ft単位に丸める。

**検証**（Playwright実描画）
| 操作 | 結果 |
|---|---|
| VOR 34L→16Rを選択 | **730**（修正前は700に化けていた） |
| 同・renderを複数回呼び直す | 730のまま変化しない（丸め処理が繰り返しかかって縮んでいかないことを確認） |
| VOR A→16L(既定1800) | 1800のまま |
| 同・1500に切替 | 1500のまま、再renderしても1500のまま |
| VORから通常滑走路(34R)に戻す | 100の倍数(1500)に戻る（従来どおりの丸めが機能） |
| 全9空港のVisual既定値 | いずれも100の倍数、NaN 0件 |

### 8.0zh build 151：Landing Flap既定をF25に、起動時にFMS自動計算が反映されていなかった不具合を修正

ユーザー要望「Landing Flapは25をデフォルトに」。`#appFlap`のHTML既定と`state.appFlap`の
初期値をF30→**F25**に変更。

**副次的に発覚した不具合**：修正後に確認すると、起動直後のTarget APP SPD欄が
F25の自動計算値(152kt)ではなく、HTML静的値の**147kt(F30相当)のまま**表示されていた。
調べたところ、`setupAirport()`は末尾で`render()`を呼ぶだけで`fmsApplySpeeds()`
（重量・機種・フラップから実際の値を計算して`#downwindSpd`/`#targetAppSpd`に書き込む関数）
を呼んでおらず、**起動時に一度もFMS自動計算が実行されていなかった**。
build147で重量欄の既定値を370.0に変えた際は、たまたま787-9/370.0k/F30の計算結果が
 legacy固定値(182/147)と一致していたため表面化しなかっただけで、実際にはbuild147の
時点から「起動直後の表示はFMS計算結果ではなく静的なプレースホルダー」という状態だった。
F25に変えたことで計算結果(152kt)と静的値(147kt)が食い違い、初めて可視化された。

**修正**：スクリプト末尾の初期化呼び出しを`setupAirport();`から
`fmsApplySpeeds(); setupAirport();`に変更。`setupAirport()`内の`render()`が走る**前**に
速度欄を計算済みの値で埋めておくことで、初回描画から正しい値になるようにした。
この関数はDOMの静的入力値と`state`（初期化済みの定数オブジェクト）だけを参照するため、
`setupAirport()`より先に呼んでも問題ない。

**検証**（Playwright実描画）
| 項目 | 結果 |
|---|---|
| 起動直後の`#appFlap`アクティブボタン | F25 |
| 起動直後の`state.appFlap` | 25 |
| 起動直後のTarget APP SPD | **152**（F25 147kt+5、修正前は147のまま） |
| 起動直後のDownwind IAS | 182（Visual・F5そのまま、従来から正しかった） |
| 全9空港を切替走査 | NaN 0件・JSエラー0件 |

### 8.0zi build 152：FMSヒントの「DW ... (+5なし)」表記を削除

ユーザー要望「DW +5なし って表記しなくていいよ」。`fmsUpdateHint()`が組み立てる
`dwTxt`で、Downwind IASに+5kt加算が無い場合(Visualは常にこれに該当。Circling F20も該当)に
付けていた"(+5なし)"という注記を削除。+5加算が**ある**場合の"+5"表記はそのまま残す
(Circling F25/F30など)。

```js
// 変更前: (a.dwAdd ? " +5" : " (+5なし)")
// 変更後: (a.dwAdd ? " +5" : "")
```

**検証**（Playwright実描画）
| 状況 | ヒント表示 |
|---|---|
| Visual(常にdwAdd=0) | `DW F5 182kt = 182kt`（"(+5なし)"が消えている） |
| Circling F20(dwAdd=0) | `DW F20 157kt = 157kt → Abeamから減速`（同様に消えている） |
| Circling F25(dwAdd=+5) | `DW F25 147kt +5 = 152kt`（"+5"表記は従来どおり残る） |

全9空港切替でNaN 0件・JSエラー0件を確認。

### 8.0zj build 153：横長(ランドスケープ)時に地図と数値を2カラム表示（縦横レイアウト最適化）

ユーザー要望「縦横レイアウト最適化いいね」（以前提案した改善案リストのうち採用された1件）。
iPadを横向きにしたとき、従来は地図→readout→VSDが1カラムのままでページ全体スクロールが必要
だったが、横長時だけ**地図を左カラム・readoutとVSDを右カラムに横並び**にして、地図を見ながら
数値もスクロールなしで確認できるようにした。

**変更範囲は追加のみ**（縦持ち/幅900px未満の既存パスは一切変更していない）：

- HTML：既存の`<div class="layout">`（`.controls`引き出し＋`.mapwrap`を内包）を
  `<div class="mainArea"><div class="mapCol"><div class="layout">...`で包み、
  それまで`.layout`の兄弟要素だった`.readout`と`.vsdPanel`を`<div class="sideCol">`で
  くくって`.mapCol`の隣に配置。DOM構造は
  `.mainArea` > `.mapCol` > `.layout`（引き出し＋地図）、
  `.mainArea` > `.sideCol` > (`.readout` + `.vsdPanel`) となる。
- CSS：新規`@media(min-width:900px)`ブロックで、`body`をフルスクリーン固定
  （`height:100dvh; overflow:hidden`）にし、`.mainArea`を`flex-direction:row`、
  `.mapCol`と`.sideCol`をそれぞれ**独立して縦スクロール可能**にした
  （`.sideCol`は`flex:0 0 380px; max-width:38vw`）。
  `.mapwrap`は元々`width:min(100%,78vh)`で高さ基準の正方形になっているため、
  左カラムの横幅を絞っても地図自体は自然に縮小して収まり、追加調整は不要だった。
  幅900px未満（縦持ち）ではこのブロックが効かず、従来どおり`.mainArea`は
  `flex-direction:column`のままページ全体がスクロールする。

**罠**：引き出し(`body.drawer-open .layout{ margin-left:... }`)は`.layout`セレクタを
直接指定しているだけの子孫セレクタなので、`.layout`が`.mapCol`の内側に1階層深くなっても
挙動は変わらず、修正不要だった（念のためスクリーンショットで両orientationとも引き出しの
開閉を確認済み）。

**検証**（Playwright, `/opt/pw-browsers/chromium`）

| 項目 | 縦持ち 768×1024 | 横持ち 1024×768 |
|---|---|---|
| `.mainArea`のflex-direction | `column`（変更なし） | `row`（新規） |
| ページ全体スクロール要否 | 必要（従来どおり） | **不要**（`pageScrollable:false`。今回の目的そのもの） |
| `.mapCol`/`.sideCol`のrect | - | `mapCol:{x:14,y:52,w:600,h:702}` / `sideCol:{x:630,y:52,w:380,h:702}` |
| 引き出し開閉 | スクリーンショットで確認、正常 | スクリーンショットで確認、正常 |
| スクリーンショット比較 | 変更前と視覚的に一致 | 地図＋readout/VSDが同時に見える2カラム表示を確認 |
| 全9空港NaNスイープ | 0件 | 同左（レイアウトはCSS/HTMLのみの変更のため空港切替ロジックへの影響なし） |
| `dlExportPng()` | 実行してPNG(blob URL)出力を確認、エラーなし | 同左 |
| JSエラー(`pageerror`) | 0件 | 0件 |

なお本ビルドでは**永続化(localStorage)機能は未実装**（次ビルドで対応予定）。ユーザーからの
依頼は同一メッセージ内で「レイアウト最適化」と「入力状態の永続化」の2件だったが、
実装の独立性を優先して本ビルドはレイアウトのみとし、永続化は別ビルドとして次に進める。

### 8.0zk build 154：入力状態の永続化(localStorage)

ユーザー要望「最初の件（＝以前の改善提案リストの1件目=状態の永続化）も運航中に1度設定した
のが他のアプリいってる間に消えたりしたら困るから終了しても残るようにしたほうがいいかな」。
このアプリはPWAとしてiPadのホーム画面から単体起動する運用のため、パイロットが運航中に
他アプリへ切り替えている間にOS側でこのPWAが終了・再読み込みされると、それまで設定した
空港・滑走路・機体重量・Base距離などが全部消えてしまう問題があった。`localStorage`に
入力状態を保存し、次回起動時に自動復元するようにした。

**保存対象の選び方**：`state`オブジェクトのうち`acModel/appFlap/circDwFlap/approach/side/
airportKey/cutAngle/baseMode/vorAltMSL/showImage/showVSD/show3D/showTerrain/tiltDeg/
headingOffsetDeg/showAltLabels`（`PERSIST_STATE_KEYS`）と、DOM入力欄のうち
`downwindOffset/downwindAlt/baseDist/baseTime/aimingPoint/glideAngle/oat/wind/acWeight`
＋`rwyEnd`の選択値（`PERSIST_FIELD_IDS`）。**`downwindSpd`/`targetAppSpd`/`thrCross`は
保存しない**——これらはFMS自動計算や描画処理が他の値から毎回計算し直す「導出値」なので、
保存対象の値さえ正しく復元すれば`fmsApplySpeeds()`/`render()`が呼ばれた時点で自動的に
正しい値に再計算される。導出値まで保存・復元してしまうと、複数のロジック(FMS計算・
VOR方式の強制設定など)のうちどちらが最終的な値を決めるべきかが復元時に曖昧になり、
かえって不整合の原因になる。

**保存タイミング**：`render()`はほぼ全ての操作(空港/滑走路/進入方式切替、各種入力欄の
input/change、ボタン群のクリック)の最後に呼ばれる共通の処理なので、`render()`の末尾に
`scheduleSaveState()`を1箇所フックするだけで大半のケースをカバーできる。唯一
`#showImageToggle`のクリックハンドラだけは`render()`を呼ばずに`currentImg`の表示を
直接切り替えているため、そこだけ個別に`scheduleSaveState()`を追加した。保存自体は
300msデバウンス(`setTimeout`)してから`JSON.stringify`して`localStorage.setItem`する
(頻繁な`input`イベントのたびに同期書き込みするのは無駄が多いため)。

**復元の順序が最大の罠**：このアプリには「空港/滑走路を切り替えるたびに走る既定値
強制処理」が複数ある(Downwind側のsideByRwy既定、熊本のDownwind Alt=1800ft AFE上書きなど、
build 142〜150で作った機能そのもの)。保存データを先に`state`へ書き込んでから
`setupAirport()`を呼ぶと、これらの既定値強制処理が保存値を問答無用で上書きしてしまい
「復元したはずの設定が起動直後に消える」事故になる。そのため
**「保存された空港でまず`setupAirport()`を通常どおり実行し尽くしてから、
その結果の上に保存値を最後に上書きする」**という順序(`applySavedStateIfAny()` →
`setupAirport()` → `applySavedStateOverride()`)にした。`applySavedStateOverride()`の
最後で`syncButtonsFromState()`(`render()`内で同期されない`#acModel`/`#appFlap`/
`#circDwFlap`/`#cutAngle`/表示切替系のボタンをstateに合わせて`active`クラスへ揃える、
新規関数)→`fmsApplySpeeds()`→`render()`の順に呼び、画面のボタン表示と数値の両方を
保存時点の見た目に一致させる。

**罠2：`rwyEnd`の復元にchangeイベントを発火させない**。`<select id="rwyEnd">`へ
直接`.value`を代入するだけなら`change`イベントは発生しないため、そのハンドラが呼ぶ
`applyApproachDefaults()`(Downwind Offset/Alt/Speedを進入方式の既定値へ戻す処理)が
誤発火して復元直後に値が上書きされる心配がない。保存された`rwyEnd`の値が現在の
空港の選択肢に存在する場合のみ反映する(空港データ更新等で滑走路名が変わっていた
場合の安全策)。

**検証**（Playwright, `/opt/pw-browsers/chromium`、localStorageを使うため`file://`でも
動作することを確認）

| シナリオ | 結果 |
|---|---|
| 熊本・RWY25・Circling・787-9/400.0t・F30・BaseDist 3.1NM・Wind 270/15・3D ON に設定 → `page.reload()` | 全項目(`state`の15項目＋DOM9項目＋rwyEnd)が保存時点の値と完全一致で復元。`#appFlap`(F30)/`#approachType`(Circling)/`#show3DToggle`(ON)ボタンの`active`クラスも正しく復元、`#airportSel`の表示も熊本に復元 |
| 保存データが無い新規セッション(別ブラウザコンテキスト) | 従来どおり羽田・787-9・F25・370.0t等の通常デフォルトで起動(downwindSpd=182/targetAppSpd=152とFMS計算値も正しく反映) |
| 全9空港のNaNスイープ(永続化コード追加後) | 0件 |
| ページエラー(`pageerror`) | 保存→リロード双方のシナリオで0件 |

**罠3(未然に回避)**：`localStorage`はプライベートブラウジング等で例外を投げる場合が
あるため、`saveState()`/`loadSavedState()`は共に`try/catch`で包み、失敗時は永続化を
黙って諦めるだけでアプリ本体の動作(計算・描画)には一切影響しないようにしてある。

### 8.0zl build 155：永続化機能に2件の問題を発見・修正(ユーザー指摘「またエラーやバグを詳しく精査して」)

build 154の永続化機能をユーザーが実運用の観点から「保存値がなくてもエラーにはならないか」
と確認した際、正常系は問題なしと回答した。ただしその後「色々と機能追加したから、
またエラーやバグを詳しく精査して」という依頼を受け、Playwrightで意図的に複合シナリオ
(VOR方式中の保存・復元、複数タブ相当の再読み込みタイミングなど)を再現テストしたところ、
**実際に運用に影響しうるバグを1件と、頑健性の抜けを1件**発見したため、両方修正した。

**バグ1(実害あり・修正済み)：VOR方式で保存→復元すると、その後通常滑走路に戻しても
Circling/VOR方式の数値が残り続ける**

羽田専用の特殊進入方式(VOR 34L→16R / VOR A→16L)は、選択時に`saveBeforeVorForcing()`が
呼ばれ、モジュール変数`vorSavedState`に「VOR方式に入る前のApproach/Side」を退避しておき、
通常滑走路や別空港に戻ったときに`clearVorForcing()`がそれを使って元に戻す仕組みになっている
(build 142以前からの既存機構)。

ところがbuild 154の復元処理(`applySavedStateOverride()`)は、保存されたstate/DOM値を
直接書き込むだけで、この退避処理(`saveBeforeVorForcing()`)を一切経由しない。そのため、
**VOR方式を選択した状態でアプリが終了・再読み込みされて復元されると、`vorSavedState`は
`null`のまま**になる。この状態で(復元後に)通常滑走路を選ぶと、`clearVorForcing()`は
「戻すものが無い」と判断して何もせず、`state.approach`(Circling)や`state.side`が
そのまま残り、Downwind Offset/Speed/Altitudeも通常のVisual用ではなくVOR方式の値
(1.5NM/147kt/700ft等)が残ってしまう——**通常のVisual進入のつもりで見た数値が、
実際には直前のVOR方式の数値のままになる**という、実運用上見過ごせない不具合だった。
Playwrightで実際に再現・確認：

| 手順 | 結果 |
|---|---|
| 羽田でVOR 34L→16Rを選択→保存→リロード(復元) | rwyEnd=VOR3416R, Downwind Offset=1.5NM, Alt=730ft (復元自体は正常) |
| 復元後に通常のRWY34Rを選択(修正前) | approach=circling(**Visualに戻らない**), offset=1.5NM(**2.5NMに戻らない**), downwindSpd=147kt(**182ktに戻らない**) |
| 同上(修正後) | approach=visual, offset=2.5NM, downwindSpd=182kt(**正しく通常のVisual既定値に復帰**) |

**修正**：`saveState()`で`vorSavedState`(nullまたは`{approach,side}`)も一緒に保存し、
`applySavedStateOverride()`の最後(`setupAirport()`内部の`clearVorForcing()`が
一度`vorSavedState`をnullにリセットした後)で、保存されていた`vorSavedState`を
モジュール変数へ書き戻すようにした。これにより、復元後に本当にVOR方式から離れる
操作をしたときも、退避されていた本来のApproach/Sideへ正しく戻るようになる。
VOR A→16Lで保存→復元→別空港(千歳)へ切替、というケースでも正常復帰を確認済み。

**バグ2ではなく頑健性の抜け(未然に補強)：バックグラウンド化の瞬間、300msデバウンス中の
変更が保存されない可能性**

`scheduleSaveState()`は入力のたびに300msデバウンスしてから`localStorage`に書き込む設計
だった。これは通常の操作では十分だが、**このアプリを作った本来の目的**——「他アプリに
切り替えている間にPWAが終了・再読み込みされても設定が消えない」——という状況では、
iOS側がバックグラウンド化と同時にJSタイマーを即座に一時停止/プロセスごと終了させる
可能性があり、その場合デバウンス中の直近の変更(300ms以内に入力した値)が保存されずに
消えてしまう恐れがあった。

**対策**：`document`の`visibilitychange`(`document.visibilityState==="hidden"`になった
瞬間)と`window`の`pagehide`の両方に、保留中のデバウンスタイマーを打ち切って同期的に
即座に`saveState()`を呼ぶハンドラ(`flushSaveState()`)を追加した。Playwrightで
`visibilitychange`を発火させ、300ms待たずに直前の変更(機体重量350.0t)が即座に
`localStorage`へ反映されることを確認済み。

**検証上の注意点(実装バグではなくテスト手法の罠)**：この対策を追加した直後、
「壊れた/部分的なlocalStorageデータを直接書き込んでから`page.reload()`する」という
既存のテスト手法が、一見「壊れた」ように見える結果(復元されるはずの空港が既定値の
羽田になる)を示した。調査の結果、これは**アプリの実装バグではなくテスト手法側の限界**
だと判明した：`page.reload()`は遷移前のページに対して`pagehide`を発火させるため、
(UIを一切操作せず)直接`localStorage.setItem()`で書き込んだテスト用のダミーデータが、
遷移直前に`flushSaveState()`が保存する「そのページの実際のライブstate(=まだ何も
操作していない既定値)」で上書きされてしまう。実運用では保存データは常にアプリ自身の
ライブstateから生成されるため、この上書きは望ましい挙動そのもの(リロード直前の
最新状態を確実に保存する)であり、複数タブ等の特殊な状況を除けば問題にならない。
念のため、`page.addInitScript()`でアプリのスクリプトが走る**前**にlocalStorageへ
データを仕込む(＝過去のセッションから正しく引き継いだ状態を模擬する)方式に
テストを書き直して再検証し、以前と同じ6パターン(キー無し・JSON破損・空オブジェクト・
fields欠落・存在しない空港名・一部項目のみ)すべてでエラー0件・正しいフォールバックを
再確認した。

**検証**（Playwright, `/opt/pw-browsers/chromium`、追加分）

| 項目 | 結果 |
|---|---|
| VOR34L→16R 保存→復元→通常滑走路選択(修正後) | Visual/2.5NM/182ktへ正しく復帰 |
| VOR A→16L 保存→復元→別空港(千歳)切替 | 千歳の通常デフォルト(Visual/Left/2.5NM)へ正しく復帰 |
| 函館RWY30(side=left)の保存→復元 | side=left、`#patternSide`のleftボタンも`active`で復元 |
| `visibilitychange`(hidden)発火直後(300ms待たず)の即時保存 | 直前の変更が即座に`localStorage`へ反映 |
| 6パターンの壊れた/欠落データ(`addInitScript`方式で再検証) | 全て正常フォールバック、エラー0件 |
| 全9空港NaNスイープ・`dlExportPng()`(VOR復元状態から) | NaN 0件、PNG出力正常、JSエラー0件 |

### 8.0zm build 156：Traffic Patternモード（離陸→Downwind進入）を新規追加

ユーザー要望「Traffic Patternモードも作ろうかな。visualは離陸後、滑走路から1.5NMまで
飛行してから1500ftでdownwind方向に旋回開始。速度は離陸後からそのままDownwind IAS
（F5 maneuver speed）を使用。旋回は180°旋回でBank角調整で上手くDownwindに乗れるように。
circlingは700ftでF20のManeuver speed。downwind flapはF20をデフォルトに」。

**仕様確定までのやり取り(AskUserQuestionで3ラウンド確認)**：
1. UIの位置づけ → 既存のApproach表示と切り替える新モード（`state.mode`="approach"/"pattern"）。
   Approach Type(Visual/Circling)の選択はそのまま流用し、Traffic Patternの中でも
   Visual/Circlingの区別に使う。
2. 「滑走路から1.5NMまで飛行してから1500ftで旋回開始」の解釈 → 高度が主条件、
   1.5NMは参考値（のはずだった）。
3. Downwindへロールアウトするオフセット距離 → 既存のDownwind Offset入力欄をそのまま流用。
4. しかし高度(1500ft/700ft AFE)を主条件にすると、そこに達するまでの水平距離は上昇率
   (Rate of Climb)次第で変わり、このアプリは上昇性能データを一切持っていない
   （降下側はGlide Angleがあるが、上昇側に対応する値が無い）。上昇率モデルを新設するか
   ユーザーに確認したところ、**「1.5NMをそのまま距離として使う」**との回答。
   → 結果として、1500ft/700ft AFEという高度は**表示用の参考ラベルに留め**、
   旋回開始点の位置計算には使わない（`PATTERN_TURN_ALT_AFE`は表示専用の定数）。

**実装のポイント**：
- 新しい上位モード切り替え`#opMode`(Approach/Traffic Pattern)を追加。`state.mode`を
  `PERSIST_STATE_KEYS`・`syncButtonsFromState()`にも追加し、永続化・復元にも対応。
- 離陸(THR)から滑走路方位(Finalコースと同じux,uy方向。着陸のロールアウトと同じ方向へ
  そのまま滑走を続けて離陸するタッチ&ゴーを想定)へ`PATTERN_STRAIGHT_OUT_NM`(1.5NM、
  固定値)直進した点を旋回開始点(pStart)とし、そこから真横にDownwind Offset分ずらした点
  (pEnd)へ180°旋回で正確に乗せる。
- **この「180°旋回でBank角を調整してちょうどオフセットに乗せる」計算は、Circling進入で
  既に使っている`computeRealisticContinuousTurn()`(Roll-in/out・風のドリフト込みで
  Bank角を二分法で逆算する関数、build以前から実装済み)をそのまま流用できた**。
  新規に追加したのは`computeDepartureToDownwind()`という薄いラッパーのみ
  (pStart/pEndを計算して`computeRealisticContinuousTurn`に渡すだけ)。Circling側の
  既存呼び出しと異なり、開始点・終了点をあらかじめ固定値から直接計算できるため、
  Circling側にある「shiftTを外側でさらに逆算する」処理は不要だった。
- 速度は離陸直後からDownwind IAS一定(既存の`downwindTAS`をそのまま使用。Abeam以降の
  減速プロファイルは着陸側の話なので離陸側には適用しない)。Visual=F5 Maneuver Speed、
  Circling=`circDwFlap`で選択したManeuver Speedという speed の決め方も、既存の
  `fmsAutoSpeeds()`がapproach種別に応じてそのまま計算する値を流用しており、
  Traffic Pattern専用の速度ロジックは追加していない。
- 「downwind flapはF20をデフォルトに」に対応: `applyPatternCircDwFlapDefaultIfNeeded()`
  を新設し、Traffic Patternモードへの切替時、および既にTraffic Pattern中に
  Approach TypeをCirclingへ切り替えたときの両方で`circDwFlap`を20に設定する
  （通常Approach時のCircling既定F30とは別扱いで、Traffic Pattern固有のデフォルト）。
- 描画：離陸から旋回開始点までの直進(amber)・180°旋回弧(amber)を新規に描き、
  Visualはdownwindの動的延長線の終点をこれまでの画面端/Entry接続点から
  `departure.pEnd`に差し替え、Circling(元々downwindの直線区間を描いていなかった)は
  `departure.pEnd`から連続180°旋回の開始点までの直線を新規に追加。結果として
  離陸→旋回→Downwind→Base/Final→着陸までの一周が閉じた形で描画される。
- 幾何的に成立しない場合(必要なBank角が74°を超える)は、既存のCircling用チェックと
  同じ閾値・同じ形式でエラー表示にする(飛べない形をそれらしく描かない、という
  既存方針を踏襲)。
- readoutに「離陸→Downwind旋回 必要バンク角(180°, Roll+風込み)」を追加(30°超で警告色)。

**既知の簡略化(重要・HANDOVER記載必須)**：
- **1.5NMは実際の上昇性能から逆算した距離ではなく、ユーザー指定の固定値をそのまま
  使用している。** 1500ft/700ft AFEという目安高度は地図上のマーカーラベルとして
  表示するだけで、その高度に実際に到達しているかどうかの計算的な裏付けは無い。
  将来、上昇率(Rate of Climb)や上昇勾配のデータが得られれば、正式に高度基準の
  計算に切り替えることを検討する。
- 離陸滑走中・ロトーション・初期上昇中の速度変化(V1/VR/V2からManeuver Speedへの
  加速過程)は一切モデル化していない。Downwind IASは離陸直後から即座に一定という
  簡略化。

**検証**（Playwright, `/opt/pw-browsers/chromium`）

| 項目 | 結果 |
|---|---|
| Visual Traffic Pattern(羽田34R既定) | Bank角 22.3°、NaN無し、geomError無し |
| Circling Traffic Pattern(同上、circDwFlap自動でF20化) | Bank角 26.4°、downwindSpd 157kt、NaN無し |
| 風090/20ktを入れた場合 | Bank角 22.2°／風ドリフト0.14NM、破綻なし |
| 全9空港×Visual/Circling×Traffic Patternのスイープ | NaN 0件・geomError 0件 |
| Downwind Offsetを極端に狭くした場合 | 既存の(Circling/Visual共通の)幾何破綻エラーが正しく表示される |
| `dlExportPng()`(Traffic Patternモード中) | 正常にPNG(blob URL)出力 |
| 永続化(state.mode="pattern"で保存→リロード) | モード・ボタンのactive状態とも正しく復元 |
| 通常Approachモードの全9空港NaNスイープ(既存回帰確認) | 0件、影響なし |
| スクリーンショット(Visual/Circling) | 離陸→旋回→Downwind→Base/Final→着陸が閉じた1周の経路として正しく表示されることを目視確認 |

### 8.0zn Claude Artifactとしての公開＋PNG保存機能のdownloads capability対応

ユーザーから「アプリの共有にサファリが出ないときってどうしたらいいんだっけ」との質問。
最初は誤った回答（iOS Filesアプリで直接タップすればSafariで開けるはず）をしてしまい、
ユーザーから「クイックルックで開かれちゃう」と訂正を受けた。再調査した結果、
**iOSではQuick Look経由で開いたローカルHTML(`file://`)からSafariへ抜ける手段が存在しない、
現行のプラットフォーム制約**であることが判明（Apple Developer Forums `thread/701845`、
Apple Community `thread/256102223` で確認。Quick LookプレビューはJSが動かず、
「Safariで開く」という選択肢自体が用意されていない）。この制約は、8.2に既に記録していた
「GitHub Pages/Cloudflare Pagesでのホスティング」というTODOと同根の問題である
（`file://`である限りSafariの外に出られない）。

**ユーザーへの提案と選択**：(1) 今すぐの対処として、現在のアプリをClaude Artifactとして
公開し、本物の`https://`URLを発行する（Safariで確実に開ける・JS完全動作）。
(2) 本格対応として8.2のGitHub Pages/Cloudflare Pages計画に着手する。
ユーザーは「よろしく！」と回答し、(1)の即時対応で進めることに同意した。

**実施内容**：

1. **`artifact_body.html`を新設**（`approach_planner.html`のbuild 156時点の内容を元に、
   `<!DOCTYPE html>`/`<html>`/`<head>`/2つの`<meta>`/`</head>`/`<body>`/`</body></html>`の
   ラッパータグのみを除去したもの。Artifactツールは公開時にこれらを自動で付与するため、
   本体側に重複して持たせない）。`<title>`を「Approach Planner - prototype」→
   「Approach Planner」に変更（Artifactの命名規則＝「名前のみ、ハイフン以降の説明語は
   付けない」に合わせ、説明文はpublish時の`description`パラメータ側に移した）。
2. `Artifact`ツールで公開：`https://claude.ai/artifact/X5M2wPkYSQxFb12vqiM6MS`
   （title: Approach Planner、icon: compass）。
3. **公開直後に判明した問題**：既存のPNG保存機能(`dlExportPng()`)が使っていた
   `<a download>`によるブラウザダウンロードは、Artifactビューアのサンドボックス内では
   **常に無効**（`file://`版やGitHub Pages版では動くが、Artifact特有の制約）。
   放置せず、Artifactの`downloads` capability（`capabilities:{downloads:true}`を宣言し、
   `await claude.use(\"downloads\")` → `await downloads.save({filename, data})`という
   プラットフォーム側のダウンロードAPI）を使うよう修正した。
   `dlExportPng()`内で、PNGのBlobを生成した直後に`claude.use`の有無を判定し、
   利用可能なら`downloads.save({filename: dlFileName(), data: blob})`を呼び、
   利用不可（`claude`未定義、または`use(\"downloads\")`が`null`を返す＝`file://`版や
   通常ブラウザで開いた場合）なら**従来どおりの`<a download>`方式にフォールバック**する
   実装にした。これにより、Artifact版・`file://`版の両方でPNG保存が機能する。
4. `capabilities:{downloads:true}`を指定して同じURLへ再公開（Version 2）。

**設計判断**：`artifact_body.html`は`approach_planner.html`（canonical本体）とは別ファイルとして
維持している。今後の運用は、本体側でbuildを上げるたびに同じストリップ処理を`artifact_body.html`
にも適用して再公開する必要がある（自動化はまだしていない。次にbuildを上げる際、
 Artifact版への反映を忘れないよう要注意）。

**検証方針について**：Artifactの`artifact-design`スキルの指示（「一度見て公開、テストループを
自前で組まない」）に従い、本体アプリのような大規模なPlaywright回帰スイープは実施していない。
downloads capabilityのコード自体は型定義(`downloads.d.ts`)を直接読み、契約どおりの
呼び出し方（`filename`/`data`/エラーコード`declined`等の扱い）になっていることを確認した
うえで実装した。

**ユーザーへの案内**：ArtifactのURLをiPad上でブックマークすれば、Safariで直接開ける
（Quick Look経由の制約を回避できる）。ただしオフライン動作は保証されない
（Claude ArtifactはWebページであり、現状ではService Worker等によるオフラインキャッシュは
組み込んでいない）。完全オフライン運用が必須の場面では、引き続き`file://`版
（`approach_planner.html`をAirDrop等で送る従来の運用）を使うこと。

### 8.0zo Claude Artifact版に衛星画像・標高データをbundleする方法（検討中・未着手）

ユーザーから「衛星画像と標高データをArtifactにbundleしたい場合はどうしたらいい？」との質問。
方式は整理できたが、**実施はユーザーが「ちょっと考える」として保留**。再開の指示待ち。

**現状**：`artifact_body.html`（Claude Artifact公開用、8.0zn参照）は`approach_planner.html`の
build156相当をそのまま流用しており、`<script src="map_data_all.js"></script>`という
**外部ファイル参照のまま**。衛星画像・標高データ(`map_data_all.js`)自体はArtifactに
含めていないため、現在公開中のArtifact版には地図・地形は表示されない。

**方式（案）**：Artifactの`Artifact`ツールpublishは、本体HTML(`file_path`)とは別に
`files`パラメータで静的な補助ファイルを同URL配下に公開できる（HTMLの
`<script src="...">`が指す相対パスと一致させればそのまま読み込まれる）。
これを使って`map_data_all.js`を補助ファイルとして一緒に公開する。

**制約（Artifactツールの上限、要注意）**：
- 補助ファイル1つあたり最大16MB（テキストファイルの場合）
- 1回のpublishで補助ファイル合計最大64MB、ファイル数最大255個
- 本体HTML自体の16MB上限とは別枠（`files`はここにカウントされない）

9空港分の`map_data_all.js`は空港ごとに衛星画像(JPEG q85で数MB〜10数MB/空港)+
DEM(0.1〜0.5MB程度/空港)を含むため、**全空港をまとめた1ファイルだと16MB上限に
抵触する可能性が高く、64MB合計にも迫りうる**。

**対策の方向性**：`map_data_all.js`は「1空港＝1行の`var MAP_DATA_<KEY> = {...};`」という
単純な構造なので、行単位で`map_data_<key>.js`のように**空港ごとに分割**すれば
16MB上限は回避しやすい。`artifact_body.html`側も`<script src="map_data_all.js">`を
**空港ごとの複数`<script src="map_data_<key>.js">`タグ**に書き換える必要がある
（これはbuild124以前のアーキテクチャに近い形で、コード変更としては大きくない）。
それでも合計64MBを超えそうな場合は、載せる空港を絞る／DEMグリッドを512→256に落とす／
画像取得半径を絞る、といった追加調整が必要になる可能性がある。

**次にやること（再開時）**：
1. ユーザーの`tile_downloader.py`実行結果(`map_data_all.js`)を受け取り、実際のサイズを確認。
2. サイズに応じて空港ごとに分割するか判断し、`artifact_body.html`のscriptタグを調整。
3. `Artifact`ツールのpublishで`file_path`(本体)+`files`(分割後のmap_data_*.js群)を指定し、
   既存のArtifact URL(`https://claude.ai/artifact/X5M2wPkYSQxFb12vqiM6MS`)へ上書き公開。
4. 合計64MB超なら、空港を絞る／DEMグリッド解像度を下げる等の削減案をユーザーと相談。

### 8.0zp build 157：衛星画像bundle版の起動が重い問題を軽量化（map_data埋め込み方式の変更）

ユーザー報告「動作が重いんだけど軽量化の為に出来ることはある？htmlを開いてもなかなか
表示されない」。確認したところ「衛星画像入りbundle版（`bundle_html.py`で作った
`approach_planner_bundled.html`）を開いた瞬間」が重いとのことで、原因を特定して修正した。

**根本原因**：`bundle_html.py`は従来、`map_data_all.js`の中身（9空港ぶんの衛星画像・標高データ、
各空港が`var MAP_DATA_<KEY> = {...(数MB〜10数MBのbase64文字列)...};`という**実行可能な
JS代入文**）を、そのまま`<script>...</script>`としてHTMLに埋め込んでいた。この形式だと、
HTMLを開いた瞬間に、**今表示していない8空港ぶんも含めて全部**をJSエンジンが
オブジェクト化（文字列をヒープに確保）し終えるまでページが固まる。実際に使うのは
選択中の1空港の画像・標高だけなのに、起動時に9空港ぶん全部の重い処理を強制されていた
（画像・DEMのデコード自体は元々選択中の空港だけを遅延実行しており問題なかったが、
それ以前の「JSオブジェクトとして構築する」段階が全空港ぶん一括で走っていた）。

**修正方針**：`bundle_html.py`側で、各空港のデータを実行可能なJSではなく
`type="application/json"`の**非実行テキストブロック**として埋め込むよう変更した
（`<script type="application/json" id="mapdata-<key>">{...json...}</script>`、
1空港＝1ブロック）。HTMLパーサはこの中身をただの文字列として保持するだけで、
JSエンジンによるオブジェクト化は一切発生しない。approach_planner.html側（build 157）
に新設した`getAirportMapData(key)`が、**ユーザーがその空港を選んだ時だけ**
該当ブロックを`JSON.parse()`する。これで起動時に重い処理が走るのは選択中の
1空港ぶんだけになり、体感の起動速度が空港数にほとんど依存しなくなった。

**後方互換**：`tile_downloader.py`が生成する`map_data_all.js`自体のフォーマットは
変更していない（従来どおり`var MAP_DATA_<KEY> = {...};`形式のまま）。変換は
`bundle_html.py`が1つのHTMLにまとめる**その瞬間だけ**行う。`getAirportMapData()`は
新形式（inertなJSONブロック）と旧形式（`window["MAP_DATA_"+KEY]`、PCで
`map_data_all.js`を直接`<script src>`で読み込んで動作確認する場合）の**両方**を
見に行くので、ユーザーの既存の`map_data_all.js`はそのまま使える。
**再取得は不要。`bundle_html.py`を最新版に差し替えて再実行するだけでよい**
（ただし、埋め込まれる本体`approach_planner.html`はbuild 157以降のものを使うこと。
build 156以前の本体には`getAirportMapData()`が無く新形式を読めない）。

**作業中に見つけた自分のミス（教訓として記録）**：実装の説明コメントとして
ソースコード中に「`<script type=\"application/json\" ...>...</script>`という形式に変更した」
という文言をそのまま`<script>`タグの中のJSコメントとして書いてしまい、**そのコメート
自体に含まれる`</script>`という文字列がHTMLパーサに本物の閉じタグとして解釈され、
メインの`<script>`ブロックがそこで強制終了してしまう**という自爆バグを起こした
（build 157実装直後のPlaywright検証で発覚：`AIRPORTS`や新設した関数が`undefined`に
なり、しかも通常のJSエラーとしては一切表面化しない——HTMLパーサレベルでタグを
閉じているだけなので`pageerror`も`console.error`も出ない——という、気づきにくい
種類の破損だった）。コメント内の説明を、実際の山括弧タグ表記を使わない言い回しに
書き換えて解決。**HTMLに埋め込む文字列（JSONデータに限らず、JSコメントや文字列
リテラルも含む）に`</script`という並びを入れてはいけない**という、まさに
`bundle_html.py`の`_to_inert_json_blocks()`で対策していたのと同じ種類の罠に、
実装した本人が別の場所で引っかかった形。今後同種のコメントを書く際は注意すること。

**検証**（Playwright, `/opt/pw-browsers/chromium`。実データは環境からGSI/Esriへ
接続できないため、9空港ぶん・各空港約6MB（base64ランダムバイト、実際の衛星画像
程度のサイズ感）の合成`map_data_all.js`（合計56.3MB）を生成して比較）

| 項目 | 結果 |
|---|---|
| 旧方式（9空港ぶん`var MAP_DATA_X=...`を全部実行可能JSとして埋め込み） | DOMContentLoaded **2846ms** / load **2865ms** |
| 新方式（build 157、9空港ぶんinert JSONブロック＋選択中1空港だけJSON.parse） | DOMContentLoaded **1298ms** / load **1325ms** |
| 改善率 | **約2.2倍高速化**（56MB/9空港のケース） |
| 全9空港への切替（bundle版） | 各空港で画像(md5ハッシュ)・DEM・attribution文言がすべて別々に正しく読み込まれることを確認（キャッシュ取り違え等の混線なし） |
| 全9空港×Visual/Circling×Approach/Traffic Patternの36通り（通常版、画像なし） | NaN 0件・`pageerror` 0件（本改修が計算ロジックに影響しないことを確認） |
| `dlExportPng()`（bundle版、実デコード可能なJPEGに差し替えて検証） | 正常にPNG出力（合成ランダムバイトのままだと「画像がbroken state」でエラーになるが、これはテストデータが正規のJPEGでないためであり、本改修とは無関係と確認済み） |
| 旧形式`map_data_all.js`を直接PCで`<script src>`読み込みする経路（後方互換） | `getAirportMapData()`の`window[...]`フォールバックが機能し、従来どおり動作 |

**残っている軽量化の余地（コード変更以外・ユーザー側でできること）**：
- そもそもの絶対データ量を減らす：普段使う空港だけをbundleする／JPEG品質(既定85)や
  取得半径(既定6NM)・ズームレベル(既定15)を下げる／DEMグリッドを512→256にする
  （8.0tに記載の目安で9空港合計 約4MB→約1MB）。
- これらは全て`tile_downloader.py`側の設定で、今回の変更とは独立に効く。

### 8.0zq build 158：衛星画像以外の軽量化 — Circling protection areaの再計算を空港ごとにキャッシュ

ユーザーから「衛星画像の部分以外でできる軽量化は？」との追加質問。build 157（衛星画像
埋め込み方式の変更）とは別に、**アプリ自体の計算コスト**を調べ、無駄な再計算を1件発見・修正した。

**見つけたこと**：Circling進入時に地図へ赤破線で表示している「周回進入区域」の外形線
（`unionStadiumOutline()`、全滑走路それぞれの2.5NMスタジアム形状の和集合を120方向の
角度スキャン＋半径方向の二分探索で求める処理）は、**空港の滑走路座標だけで決まる
静的な形状**で、風・Base距離・Downwind Offset・高度など、他のどの入力値にも一切
依存しない。にもかかわらず、この処理は`render()`の中で**無条件に**呼ばれており、
`render()`は数値入力欄に**1文字打つたびに**(`input`イベント)実行される作りになっていた。
つまり、変わるはずのない形を、キー入力のたびに律儀にゼロから計算し直していた。

実測（Playwright、羽田）：`render()` 1回あたり約16ms（コールド）のうち、この
再計算だけで約9ms（全体の半分以上）を占めていた。DEM(標高)の等高帯(`demBands`)は
build 139の時点で既に「空港ごとに1回だけ作って使い回す」キャッシュが入っていたが、
こちらは同じ考え方が抜けたまま残っていた。

**修正**：`demBands`と同じパターンで、`protectionAreaCache`（`{key: airport.key, ...}`）を
モジュールスコープに新設。`setupAirport()`（空港が変わった時）でのみ破棄し、`render()`は
キャッシュのキーが現在の空港と一致すればそのまま再利用、一致しなければ（＝空港が
変わった直後の1回だけ）計算し直す。

**検証**（Playwright, `/opt/pw-browsers/chromium`、羽田）

| 項目 | 結果 |
|---|---|
| `render()` 1回(コールド、キャッシュ計算含む) | 修正前 約16.2ms → 修正後 約7.2ms |
| `render()` 20連続呼び出しの平均(定常状態) | 修正前 約6.9ms/回 → 修正後 約4.5ms/回 |
| キャッシュのキー一致判定 | 全9空港でそれぞれ異なる外形線が正しく描画されることを確認（同一形状の使い回し事故なし） |
| 空港を切り替えて戻す(羽田→千歳→羽田) | 戻った時の外形線が切替前と完全一致（キャッシュの取り違え・破損なし） |
| 全9空港×Visual/Circling×Approach/Traffic Patternの36通り | NaN 0件・`pageerror` 0件（build 157検証時と同じ標準スイープを再実行、回帰なし） |

DEM側の帯・側壁も同様に「入力に依存しない静的形状は空港ごとに1回だけ」という
方針が既にあったので、今回はその方針を取りこぼしていた1箇所を揃えた形。

**他に見つかった・見つからなかったこと**：`computeFullPatternGeometry`・
`computeRealisticContinuousTurn`・`buildPattern`など、パターン形状そのものを計算する
関数群は風・Base距離・Downwind Offset等の入力値に正当に依存しているため、
毎回のrender()で再計算されるのは仕様どおりであり、キャッシュ対象ではない
（今回はこれらには手を入れていない）。

**さらに踏み込んだ軽量化の余地（未着手・より大掛かりな変更が必要）**：`render()`は
呼ばれるたびに地図のSVG要素を(部分的にではなく)まるごと作り直す設計になっている。
差分だけを更新する方式に書き換えれば理論上さらに速くなるが、影響範囲が広く
リスクも大きいため、今回は手を付けていない。体感の重さが今回の2件（build 157/158）
の修正後もまだ気になるようであれば、次の候補として検討する。

### 8.0zr build 159：衛星画像・標高データの圧縮検討（tile_downloader.pyにWebPオプション追加）

ユーザーから「衛星画像と標高データは品質を落とさずに圧縮する方法ないかな？現状9空港で
100MB以上あるんだよね。さっきの合計64MBにも収まらない」との質問（「さっきの合計64MB」は
8.0zoのArtifact `files` パラメータの上限のこと）。衛星画像・標高データそれぞれについて
圧縮の余地を調べ、**画像側は実装、標高側は検討の末に見送り**という結果になった。

**標高データ(DEM)側 — 検討したが不採用**：現行のPNG符号化はRGB(3チャンネル)を使い、
実際の標高値はR,Gの2バイトしか使っていない。B(1バイト)は「欠測かどうか」という
1ビットの情報のためだけに丸ごと1チャンネル消費しており、一見無駄に見えた。

"LA"(グレースケール+アルファ、2チャンネル)モードのPNGに変えてBチャンネルを削れば
理論上33%減るはずと考えて試作したが、**Playwrightで実際にブラウザに読ませて検証した
ところ、致命的な罠が見つかった**：アルファ値が255未満のピクセルは、ブラウザが
canvas内部で色を「アルファ事前乗算」した状態で保持するため、`getImageData()`で
読み戻すとRGB(グレー)値が変化してしまう（実測: L=200,A=30のピクセルを埋め込んで
読み戻すと L=204 に化けた）。標高データの値がブラウザの内部実装依存で静かに
改ざんされるという、地形データとして絶対に避けるべき種類のバグだったため、**この案は
不採用**にした（試作コードは実装前にこの検証で発見・破棄済み。ユーザーに配布される
ビルドには一切含まれていない）。

代替として「R,Gの2チャンネルを、アルファを使わない2枚の別々のグレースケールPNG
（不透明・アルファなしなので上記の罠を回避できる）に分離する」案も検討し、実測で
Bチャンネルありの現行方式より約22%小さくなることを確認した（512×512のテストで
350.2KB→272.4KB）。ただし、DEMは9空港合計でも**数MB程度**（8.0tの実測目安で
512グリッド時「9空港合計 約4MB」）であり、100MB超の全体に対する寄与はごくわずか。
実装の複雑さ（2枚の画像を読み込んで合成する処理が必要）とバグ余地の増加に見合わないと
判断し、**現時点では見送り**とした。DEM側で本格的に削るなら、複雑な符号化を変えるより
単純にグリッドを512→256に落とす方（8.0t記載の目安で約4MB→約1MB）が費用対効果が良い。

**衛星画像側 — 実装した対策**：こちらが100MB超の大部分を占めている本体。
`tile_downloader.py`に2つの変更を加えた。

1. **JPEG保存時に`optimize=True`を追加**（既定のJPEG形式を使い続ける場合、無条件に有効）。
   画質設定(quality=85)自体は変えず、ハフマン符号化のテーブルを最適化するだけなので、
   **画質の劣化は一切なく**、実測で数%（テストで約3.5%）ファイルサイズが減る。
2. **WebP形式を選択できるオプションを追加**（`IMAGE_FORMAT`定数、CLIは`--webp`フラグ、
   GUIは「衛星画像をWebP形式で保存」チェックボックス。既定は従来どおりJPEGのまま、
   完全にオプトイン）。WebPは同じ視覚品質を狙った設定でJPEGより一般に20〜35%
   小さくなることが業界共通のベンチマークで知られている（Googleの比較や各種独立検証）。
   **ただし実際の圧縮率は写真の内容に依存し、この環境からはGSI/Esriの実タイルに
   接続して検証できない**ため、この session内では正確な削減率を実測できていない。
   このため「まず1空港だけ試して、既存のJPEG版とファイルサイズ・見た目を比較してから
   全空港に適用する」ことを推奨する（HANDOVER内のコメントにも明記）。
   iOS 14以降のSafari（現行の全iPadで該当）ならWebPもJPEGと同様にネイティブ表示できる。

**アプリ本体(approach_planner.html)側の変更は不要**：`imageDataURI`は
`data:<mime>;base64,...`という自己記述的な形式で、`<img>.src`に代入するだけの
既存コードがMIMEタイプに関わらずそのままブラウザに解釈させている。そのため
JPEG→WebPへの切替はデータを作る側(`tile_downloader.py`)だけの変更で完結し、
approach_planner.html側のbuildは今回上げていない。

**検証**（Playwright, `/opt/pw-browsers/chromium`）

| 項目 | 結果 |
|---|---|
| 既定(JPEG, optimize=True)での`save_to_merged()` | 従来どおり`data:image/jpeg;...`のエントリが生成されることを確認 |
| `--webp`相当(`IMAGE_FORMAT=\"webp\"`)での`save_to_merged()` | `data:image/webp;...`のエントリが生成されることを確認 |
| WebP画像をアプリで表示 | `<img>`が正常にデコード完了(`complete:true`, `naturalWidth:1024`)、JSエラー0件 |
| WebP画像に対する`dlExportPng()` | 正常にPNG出力（`ctx.drawImage()`はデコード済み画像を扱うだけなので、元がJPEGでもWebPでも扱いは同じ） |
| "LA"2チャンネルDEM案の罠 | 上記のとおりPlaywrightで再現・確認済み(採用しなかった理由の裏付け) |

**現実的な見積もりと、64MB制約への向き合い方**：WebPで20〜35%減っても、
100MB超は単純計算で65〜75MB程度にしかならず、**Artifactの`files`パラメータの
合計64MB上限にはそれだけでは収まらない可能性が高い**。8.0zoで検討した
「Artifactの`assets`capability（画像を実バイナリのままアップロードし、URLで参照する
仕組み。base64化による33%の水増しが無くなり、`files`の64MB上限とは別の枠になる）」
と組み合わせるのが本命。WebP化(画像そのものを20〜35%削減)＋assets化(base64の
33%水増しを解消)を両方行えば、100MB超でも十分收まる可能性が高い。

**再取得 vs 変換（トレードオフの説明、ユーザーに提示済み）**：WebPへの切替を
適用するには2つの道がある。
- **再取得**（推奨・画質最優先）：更新した`tile_downloader.py`で改めて
  `python3 tile_downloader.py all --webp`を実行する。GSI/Esriの元タイルから
  直接WebPにエンコードするので、画質劣化は圧縮設定(quality=85)の範囲内のみ。
  9空港で標準25rps・約4〜5分。
- **変換**（速いが非推奨）：既存の`map_data_all.js`に入っている**JPEGを一度
  デコードしてWebPに再エンコード**する方法。再取得不要で速いが、既にJPEGで
  圧縮されたものをもう一度圧縮し直すため、**二重圧縮による追加の画質劣化が
  わずかに生じる**（quality85→85でも、JPEGのブロックノイズがWebPの予測に
  影響するため理論上ゼロではない）。「品質を落とさずに」という要望に対しては
  再取得の方が適切なので、今回は変換スクリプトは作成していない。必要であれば
  別途対応する。

### 8.0zs build 160：衛星画像にもズームレベルの選択肢を追加（tile_downloader.py）

ユーザーから「標高データと同じように衛星画像も倍率選べたほうがいいかな？どう思う？」との
質問。8.0zrで衛星画像のJPEG/WebP圧縮を検討したが、ズームレベルを下げる方が桁違いに
効果が大きいと判断し、DEMの`dem`/`dem5a`選択と同じ発想で実装した。

**判断の根拠**：タイルベースの地図はズームレベルを1段階下げると、同じ範囲をカバーする
タイル数（＝データ量）がおおよそ1/4になる（緯度・経度それぞれの方向で解像度が半分に
なるため）。実測（羽田・半径6NM）でzoom15の529枚(23×23)に対しzoom14は144枚(12×12)、
約3.7倍の削減。WebP化による20〜35%減（8.0zr）とは効果の桁が違う。

**DEMのdem5a選択との違い**：DEMの`dem`/`dem5a`は「5mメッシュが無い区域は欠測になる」
というカバレッジの制約だったが、衛星画像（Esri/GSIの航空写真タイル）は日本の空港周辺
（市街地・平地）ならどのズームでも普通にタイルが存在するため、これは純粋に
「鮮明さ」対「ファイルサイズ」のトレードオフになる。zoom15は滑走路縁や誘導路標示まで
くっきり見えるが、zoom14まで落とすと目視でもややぼやける。進入経路確認用途でどこまで
粗くても許容できるかは、実際に見比べて判断する必要がある（このため一律のデフォルト
変更はせず、選択式にした）。

**実装**：`tile_downloader.py`に以下を追加。
- `AIRPORTS`辞書の各空港エントリから個別の`"zoom": 15`を削除し、モジュール共通の
  `IMAGE_ZOOM`定数（既定15）に一本化（`fetch_and_stitch()`は既に`zoom`を引数として
  受け取れる構造だったため、配管は`process()`内の参照を`airport["zoom"]`から
  `IMAGE_ZOOM`に差し替えるだけで済んだ）。
- CLI: `--zoom=N`フラグを追加（`--rps=`/`--workers=`と同じパターン）。
- GUI: 「衛星画像の解像度」ラジオボタン（標準・高精細=zoom15 / 軽量=zoom14）を追加し、
  `on_start()`で`IMAGE_ZOOM`に反映。

**検証**：`python3 -c`でモジュールをimportし、`ast.parse`による構文チェック、
および羽田のbboxに対しzoom15/14それぞれの実際のタイル数を計算する簡易テストを実施
（上記の529枚→144枚を確認）。ネットワーク接続不要な範囲での検証であり、実際の
GSI/Esriへの接続を伴う取得テストはこの環境からはできないため、ユーザー側で
1空港試し取り（`python3 tile_downloader.py haneda --zoom=14`等）して見た目と
ファイルサイズを確認することを推奨する。

**副次対応**：本セクション追記にあたり、8.0zn〜8.0zr（このHANDOVER.md内）が
段落の改行を実際の改行ではなくリテラルな`\n`文字列のまま書き込んでしまっていた
（過去のセッションでの書き込み時の不具合）ことに気づき、該当箇所を実際の改行に
修正した（内容自体の変更ではなく表示上の修正）。

### 8.0zt build 161：「取得中なのにスリープした」報告への対応（スリープ検出ウォッチドッグ）

ユーザーから「tile downloader起動中なのにスリープした」との報告。ヒアリングの結果、
Windows・GUI使用・フタは開いたまま・かつログ欄には「抑止できた」「抑止に失敗した」
のどちらのメッセージも見当たらなかった、とのこと。

**原因の切り分け**：`keep_awake()`のWindows分岐は、`SetThreadExecutionState`の
成否いずれの場合も必ずログを1行出す作りになっているため、コード上は「何もログが
出ない」状態は本来起こらないはずである。ログ欄（`ScrolledText`）は新しい行が
追加されるたびに`see("end")`で末尾へ自動スクロールするため、抑止メッセージは
一番最初の1行としてすぐ流れて見えなくなっていた可能性が高い（＝実際には出ていたが
見落とした、という可能性を否定できない）。一方で、`SetThreadExecutionState`は
あくまでOSへの「お願い」であり、OEM製の独自電源管理ソフトやグループポリシー等に
よって実際には上書きされて効かないケースも実例として知られている。どちらが
真因かをこの環境（ネットワーク接続なし・Windows実機なし）から確定することは
できないため、**両方に効く対策**として以下を実装した。

**実装**：
1. **常時表示のスリープ抑止状態ラベルをGUIに追加**（ログ欄とは別枠、スクロールで
   流れない）。抑止に成功/失敗した時点で即座に色分け表示される（成功=緑、失敗=赤）。
   これでログを見落としても、今回のような「そもそも抑止できていたのか」という
   疑問には即答できるようになる。
2. **スリープ検出ウォッチドッグを追加**（`_sleep_watchdog()`、20秒間隔）。
   抑止の成否によらず、取得中は常にバックグラウンドスレッドで実時間を計測し、
   想定間隔(20秒)の2.5倍以上ずれていたら「その間スリープしていた可能性が高い」
   と判定してログとステータスラベルに警告を出す（スリープ・休止状態は
   `time.sleep()`/`threading.Event.wait()`も一緒に止まるため、実時間の
   ギャップとして検出できる）。あわせてWindowsでは、このウォッチドッグの中で
   `SetThreadExecutionState`を20秒おきに再要求するようにした（1回きりの要求より
   確実という報告があるため、実装コストが低い割に保険として有効）。
   これにより、「抑止が効かず、かつユーザーも気づかないまま一部タイルが
   静かに欠測扱いになる」という最悪のケースを防げる（欠測扱いになったタイルは
   404相当として扱われ、見た目上は海上のデータ無しと区別がつかないため、
   検出できないと非常に気づきにくい）。

**検証**：`_sleep_watchdog()`を単体で呼び出し、`time.time()`をモック化して
(1)通常経過(20秒間隔)では警告が出ないこと、(2)180秒相当のギャップを注入すると
即座に警告ログが出ること、の両方をPlaywrightではなく素のPython単体テストで確認した
（この関数はブラウザ非依存の純粋なPythonロジックのため）。あわせて
`keep_awake()`全体をLinux環境（この検証環境）で最後まで実行し、ウォッチドッグ
スレッドの起動・終了を含めて例外なく完走することを確認した。GUI部分
（tkinterウィジェットの追加・queueハンドリング）は、この環境ではtkinterの
バージョン不整合によりGUIを実際に起動しての確認はできなかったため、
既存の動作実績があるウィジェット・queueパターンと完全に同じ書き方に
揃えることでリスクを抑えた（コード全体の構文チェックは実施済み）。

**ユーザーへの依頼**：次回Windowsで実行した際、スリープ抑止ラベルが実際に
「有効」と出るか、それでもスリープ警告が出るかを確認してほしい。「有効」と
出たのに警告も出た場合は、Windows側のOEM電源管理ソフトやグループポリシーが
`SetThreadExecutionState`を上書きしている可能性が高く、その場合は電源設定側
（設定 → システム → 電源とバッテリー → 画面とスリープ、または
OEM製電源管理アプリ側の「スリープしない」設定）で個別に対応してもらう
必要がある。

### 8.0zu build 160/161実運用結果：zoom14+WebPで9空港合計32MBまで圧縮（Artifact bundleは引き続き保留）

ユーザーが実際に`tile_downloader.py`を`--zoom=14 --webp`相当の設定（GUIの
「軽量(zoom14)」＋「衛星画像をWebP形式で保存」を両方有効化）で9空港ぶん再取得した
結果、`map_data_all.js`の合計サイズが**32MB**まで縮んだとの報告。100MB超だった
当初（8.0zr時点）から1/3以下になり、8.0zoで検討していたClaude Artifactの`files`
パラメータ合計64MB上限を単独で下回る水準になった。

**Artifact bundleへの影響（重要・未解決のまま）**：合計サイズは64MB以内に収まったが、
`files`パラメータには**1ファイルあたり16MBの上限**も別途ある（8.0zo参照）。
現状の`map_data_all.js`は9空港を1ファイルにまとめる方式なので、32MBのままでは
1ファイル16MB上限に抵触する。Artifactへ載せる場合は、8.0zoで検討済みの
「空港ごとに`map_data_<key>.js`へ分割し、`artifact_body.html`側のscriptタグも
空港ごとの複数`<script src="...">`に書き換える」方式への変更が引き続き必要。

**現状の結論**：ユーザーに実装を進めてよいか確認したところ、**「まだ保留」**との
回答。8.0zoからの保留状態は変わっていない。サイズ問題は実質的に解決した
（32MBなら空港ごとに分割すれば各ファイル数MB程度に収まり、16MB上限にも
合計64MB上限にも十分な余裕がある）ため、再開する際の技術的な障害は無くなっている。
再開の指示があれば、8.0zoの「次にやること」の手順（分割→scriptタグ調整→
Artifact publishでfile_path+files指定→既存URLへ上書き公開）にそのまま進める。

### 8.0zv build 162：Traffic Patternの旋回開始点の基準を修正、旋回→Downwind接続の切れを修正、3D表示に離陸区間の上昇を追加

ユーザーから3件の指摘：
1. 「traffic pattern modeの時は、離陸と反対側の滑走路端（滑走路の終わり）から1.5NMで
   ターン開始ってこと」
2. 「3D表示の時はそこまで直線的に上昇するってことでいいや」
3. 「旋回からDownwindの接続が切れたりするからちゃんと連続的に繋がるように」

**(1) 旋回開始点の基準を修正**：build 156時点の実装は、1.5NMの起点を「離陸した
THR（滑走路の入口側）」にしていた。ユーザー指摘のとおり、正しくは「離陸と反対側の
滑走路端（oppXY。滑走路の終わり）」から1.5NM。旧実装は滑走路長ぶん（羽田で
実測約1.51NM）旋回開始点が手前に寄りすぎていた。`computeDepartureToDownwind()`の
`pStart`計算を`pat.thrXY + ux*1.5NM`から`pat.oppXY + ux*1.5NM`に修正した
（`pat.oppXY`は`computePattern()`が既に計算済みの値をそのまま使えたため、
配管の変更は最小限で済んだ）。

**(2) 3D表示での離陸区間の上昇を追加**：従来、Traffic Patternの離陸→旋回区間は
3D表示でも常に地面(高度0)に張り付いたまま描画されていた（3D表示のリフト処理が
着陸側の経路(`nearToFarFull`)にしか適用されておらず、離陸側の経路には一切
適用されていなかったため）。ユーザーへの確認により「実際の上昇性能データが無い
ので、直線的な(線形の)上昇で構わない」との回答を得たため、「離陸(THR、AFE0)から
旋回開始点までAFE0→downwindAltAFEへ直線的に上昇し、旋回中はdownwindAltAFEで
水平」という単純化モデルで3D表示にも上昇を追加した。Downwindに乗った後の高度
(downwindAltAFE)と終端が一致するため、(3)の接続修正とあわせて3D表示上も
途切れずに繋がるようになった。

**(3) 旋回とDownwindの接続が切れる不具合を修正**：`computeRealisticContinuousTurn()`
はBank角を「目標終了点pEndに対する横方向(cross-track)のオフセット」だけが
一致するように解いており、風がある場合の沿方向(along-track)のドリフト
(`driftM`)まではゼロにしていない。Circling側の連続旋回(`cont`)はBase/Finalの
交点という「動かせない1点」に厳密に着地させる必要があるため、旋回開始位置
(`shiftT`)を逆算してドリフト込みで正確に一致させているが、Traffic Pattern側の
Downwindには動かせない特定の1点は無いため、この`shiftT`逆算は「不要」と当初
判断していた（build 156時点のコメントに明記）。ところがこの判断は、
Bank角の解自体は妥当でも、**描画側が理想化した目標点`pEnd`を使って
Downwindへの接続線を引いていた**ことの副作用を見落としていた：実際の旋回は
`driftM`ぶん`pEnd`とは違う点で終わるため、風がある時は「旋回の弧」と
「Downwindへの接続線」が別の点から生えることになり、線が繋がって見えない
（切れる）不具合になっていた。

**修正**：`computeDepartureToDownwind()`の返り値に、実際の旋回終了点
`endXY`(`turn.pts`の最後の点、`driftM`込み)を追加。描画側(render())で
接続線を引く2箇所（Visual時のDownwind直線の動的延長、Circling時の
`departure→cont`のつなぎ線）を、いずれも`departure.pEnd`(理想値)から
`departure.endXY`(実測値)に差し替えた。`pEnd`自体はBank角を解くための
内部の目標値としてそのまま残している(Circling側のshiftT逆算を追加する
という大掛かりな変更は不要だった)。

**検証**（Playwright, `/opt/pw-browsers/chromium`）

| 項目 | 結果 |
|---|---|
| 旋回開始点の基準（羽田、滑走路長を模した合成データ、実測約1.51NM） | 修正前: THRから1.5NM地点(oppXYより0.01NM手前) → 修正後: oppXYから正確に1.5NM(THRからは約3.01NM) |
| 風ドリフトによる接続の切れ（25kt/横風寄りの合成ケース） | 修正前: 旋回の実終端と接続線の始点(`pEnd`)の間に**約1057m(約0.57NM)の隙間** → 修正後: 隙間0（`endXY`と旋回終端が完全一致、数式上の恒等式なので当然だが実測でも確認） |
| 全9空港×Visual/Circling×Traffic Pattern×風(無風/250°25kt/070°25kt)の54通り | エラー0件・NaN0件 |
| 全9空港×Approach/Traffic Pattern×Visual/Circling×3D表示ON/OFFの72通り | エラー0件・NaN0件（3D表示の離陸区間追加を含む回帰確認） |
| 3D表示ONでの離陸区間ポリライン | 期待どおりamber・stroke-width3で描画されることを確認 |

**影響範囲についての補足**：この修正はTraffic Patternモード(`state.mode==="pattern"`)
専用の`computeDepartureToDownwind()`とその描画部分のみに閉じており、通常の
Approachモードの計算・描画（Circling側の`cont`/`shiftT`ロジックを含む）には
一切手を入れていない。

### 8.0zw build 163：3D表示で地形の標高基準が空港標高とズレていた不具合を修正（広島で滑走路が山に埋まって見える）

ユーザーから「3D表示の時って空港の標高はどう考えてる？広島（山あいの空港）で表示すると
滑走路が山の中に表示される」との報告。

**原因**：3D表示の「持ち上げ(lift)」計算には2種類あり、基準がズレていた。
- 経路(進入経路・Traffic Patternの離陸区間)側の`liftOf()`は、`pat.altAtDist()`が
  返す**AFE(空港標高からの高さ。滑走路面=0)**を受け取って持ち上げる。
- 地形の等高帯側の`liftOfFt()`は、`buildDemBands()`が返す`band.levelFt`
  （DEMデータそのものの**絶対標高＝ft MSL**）を、AFEへの変換をせずそのまま
  受け取って持ち上げていた。

つまり「滑走路面=高さ0」という基準に対し、地形だけが「海抜0m=高さ0」という
別の基準で持ち上げられていた。空港標高が低い空港（羽田21ft等）ではこのズレは
何十ft程度で目立たなかったが、広島（標高1086ft、周囲を山に囲まれた台地上の空港）
では地形全体が実際より1086ftぶん(表示上は`ALT_EXAG_3D`=4倍の誇張込みでさらに
大きく)余計に持ち上げられ、結果として「滑走路（AFE基準で高さ0近辺）が、
本来なら同じ高さのはずの地形（絶対標高基準で1086ft相当持ち上げ済み）よりずっと
低い位置に描かれる」＝見た目上「滑走路が山の中に埋まっている」ようになっていた。

**修正**：`render()`内、地形の等高帯を描く箇所で、`liftOfFt(band.levelFt)`/
`liftOfFt(band.prevFt)`としていた呼び出しを、`liftOfFt(band.levelFt-airport.elevationFt)`/
`liftOfFt(band.prevFt-airport.elevationFt)`に変更。地形も経路と同じ「空港標高＝高さ0
(AFE)」の基準で持ち上げるように統一した。色分け用の`rel = band.levelFt - dwMSL_t`
（Downwind高度との比較、両方ともMSL基準）はそのままで問題ないため変更していない
（絶対標高同士の比較なので、持ち上げ計算のバグとは無関係）。

**検証**（Playwright, `/opt/pw-browsers/chromium`。実際の広島の地形はネットワーク接続が
無いため取得できず、代わりに「空港標高と同じ標高の平坦地＋その外周に+400ftの山」という
単純な合成DEMデータを注入して検証）

| 項目 | 結果 |
|---|---|
| 空港標高(1086ft)に最も近いband(1100ft、AFE+14ft相当)の持ち上げ量 | 修正後: 約1.10 SVG単位（ほぼ地上面どおりの妥当な値） |
| 最も高いband(1400ft、AFE+314ft相当)の持ち上げ量 | 修正後: 約24.6 SVG単位（低いbandの約22倍。AFEの比314/14≒22.4倍とほぼ一致し、線形の持ち上げ計算が正しく効いていることを確認） |
| 参考：修正前の計算式で1100ft(絶対標高のまま)を持ち上げた場合の相対値 | 同じ係数で計算すると約86 SVG単位相当（AFE+14ft相当が正しく見せるべき約1.10の約78倍。地形が大幅に浮き上がって描かれていたことを裏付ける） |
| 全9空港×Approach/Traffic Pattern×Visual/Circling×3D表示ON/OFF(`showTerrain`含む)の72通り | エラー0件・NaN0件（回帰確認） |

**注記**：この環境からは実際のGSI標高タイルを取得できないため、広島の実データでの
見た目の確認はユーザー側での確認をお願いしたい。次回広島で3D表示＋地形表示を
オンにした際、滑走路と周囲の山の高さ関係が自然に見えるかどうかを確認してほしい。

### 8.0zx build 164：空港標高より低い地形は塗らないように変更

build 163の地形色分けルールの説明に対し、ユーザーから「空港より低い（広島とか顕著）
山は塗らなくていいかも」との指摘。

**背景**：build 163時点の色分けは、Downwind高度(MSL)を基準にした3段階
（赤=Downwind高度以上／オレンジ=1000ft未満に接近／緑=1000ft以上の余裕）で、
高度の高低に関わらず**全ての等高帯を塗っていた**。広島のような台地上の空港
（標高1086ft）では、周囲の谷など「空港よりずっと低い土地」まで全部緑で
塗りつぶされることになり、進入経路上の障害物として本当に意味のある高さ関係
（空港より高い山）がかえって埋もれて見づらくなる、という指摘は妥当だった。

**修正**：地形の等高帯を描く`for`ループの先頭に
`if(band.levelFt <= airport.elevationFt) continue;`を追加し、空港標高以下の
帯は描画自体をスキップするようにした。滑走路より低い地形は着陸の障害物には
ならないため、表示する必要が無いという判断。色分けの3段階ルール自体（赤/オレンジ/緑、
Downwind高度基準）は変更していない。最も低く残った帯の側壁は、その帯の下端
(`prevFt`。空港標高を下回ることもある)まで描かれるので、「空港標高のところで
唐突に浮いて見える」ことはなく、そこから下は単に何も描かれない(=非表示)という
自然な見た目になる。

**検証**（Playwright, `/opt/pw-browsers/chromium`。合成DEM：広島の中心付近を
空港標高より600ft低い谷、外周を空港標高より400ft高い山とした10段の等高帯データ）

| 項目 | 結果 |
|---|---|
| 空港標高以下の帯（6段） | 描画呼び出し(`demBandPaths`)が一切発生しないことを確認（0件） |
| 空港標高より高い帯（4段） | 全て従来どおり描画されることを確認（4件） |
| 全9空港×Approach/Traffic Pattern×Visual/Circling×3D表示ON/OFF(`showTerrain`含む)の72通り | エラー0件・NaN0件（回帰確認） |

### 8.0zy build 165：新モード「Departure」を新規追加（伊丹RWY32L 離陸時騒音軽減経路の計算）

ユーザー要望：「伊丹離陸時の騒音軽減のルートを計算したい」。既存のApproach/Traffic Pattern
に続く**3つ目のモード「Departure」**として新規実装（Traffic Pattern追加のときと同様、
ユーザーに「新しいモードを追加/既存モードの派生どちらがよいか」を確認し、新モード追加を
選択）。現時点では**伊丹空港 RWY32L専用**（AIPのNoise Preferential Routeがこの滑走路・
方向固有の手順のため）。

**ユーザー指定パラメータ**（全て既定値、太字以外はUIから変更可能）：

| 項目 | 既定値 | 備考 |
|---|---|---|
| 離陸滑走路 | **RWY32L固定** | 伊丹の14R/32L滑走路 |
| 離陸開始点 | 滑走路終端(反対側=14R)の2000ft手前 | 「終端」＝進行方向側の遠い方の端 |
| 速度(IAS) | 155kt固定 | Acceleration Heightまでは加速しない |
| 旋回開始 | ISK(ILS-DME RWY32L)から1.8NM | 角度ではなく位置(DME距離の円との交差)で決まる |
| Bank角 | 25° / 30°選択式 | 左旋回 |
| Acceleration Height | 3000ft AFE | 変更可 |
| 上昇率 | Accel Heightまで2000fpm → それ以降1000fpm固定 | ユーザー指定の2段階モデル |
| 加速率 | Accel Height以降 1.5kt/sec | 上限なし(ユーザー指定なし。長時間シミュレーションすると速度が際限なく増える点は既知の割り切り) |
| 補助線 | ISKから2.8NM/3.6NM、ITEから2.2NMの円 | いずれもAIP RJOO AD2.19の図に記載の目安距離 |

**旋回開始・終了の基準は「AIPのNoise Preferential Route記載文そのまま」**：
「離陸後、ITE VOR/DMEの近くを通過するよう継続的な左旋回上昇を行い、Chugoku Expressway
(北)・和賀池/己新池(南)・武庫川(西)で囲まれた範囲内に飛行経路を収めながら、ITE VOR/DME
2.2DMEを横切るまでその状態を維持し、その後SIDへ移行する」（AIP RJOO AD2.19、project内
`RJOO__AD2_20260806.pdf`より）。ITE/ISKの緯度経度もこのAIPの実測値（build 165着手前に
`navaids`として`itami`空港データへ追加済み。ISKはILS-DME RWY32L=RWY32L THR内側でGPアンテナ
と同架、ITEはVOR/DME）。

**なぜ既存の旋回計算(computeRealisticContinuousTurn等)が使えないか**：
Traffic Pattern/CirclingのTurnはいずれも「旋回角度(90°/180°)」または「固定の目標地点」を
Roll-in/out込みで二分法で解く方式。今回の旋回は角度でも固定地点でもなく、**旋回中に
DME距離の円(ISK1.8NM／ITE2.2NM)と交差した位置**で開始・終了が決まり、かつ上昇と加速に
よって速度・旋回半径が旋回の最中も連続的に変わる。そのため`simulateItamiDeparture()`を
新規に実装し、**0.2秒刻みの数値積分**で直接シミュレーションする方式にした
（Roll-inの立ち上がりだけは、既存のBase/Final旋回等と同じROLL_RATE_DEG_PER_SEC(3°/sec)の
モデルを流用し、旋回開始の瞬間にいきなり目標Bank角になる不自然さを避けている。
Roll-outはモデル化していない＝AIP記載の「2.2DME到達後はSIDへ」という手順上、
その先はこの機能のスコープ外という判断）。風は考慮していない(ユーザーからの指定なし)。

**実装箇所**：
- `simulateItamiDeparture(opts)` — 上記の数値積分本体。滑走路方位・開始点・ISK/ITEの
  位置はいずれもXY(平面直交)座標へ変換した上で計算する（既存の`toXY`/`toLatLon`をそのまま
  流用）。旋回開始は「ISKからの距離が閾値を上回った瞬間」、旋回終了は「ITEからの距離と
  閾値の大小関係が反転した瞬間」で検出する（実測値では旋回開始時点で既にITEから2.2NM圏内に
  いるため、圏内→圏外への交差として検出される。念のため両方向の交差に対応させてある）。
- `circlePointsXY(centerXY, radiusM, n)` — 補助円(DME距離の目安)の描画用。96角形近似。
- `renderDeparture(airport, rwyLabel)` — 専用の描画関数。`render()`冒頭で
  `state.mode==="departure"`のときはここへ分岐して即returnし、既存のApproach/
  Traffic Pattern描画本体（VOR強制・Base/Final旋回・地形等、複雑に絡み合っている）には
  一切触れない設計にした。伊丹RWY32L以外が選択されている場合は、既存の`geomError`パネルの
  仕組みをそのまま流用してエラー表示のみ行う。
- UI：`#opMode`に「Departure」ボタンを追加。既存のApproach/Traffic Pattern専用の入力欄
  （Approach Type〜Cut Angleまでの一群）は`#approachPatternFields`という1つのdivで
  まとめて包み、Departureモードでは丸ごと非表示にする。代わりに`#departureFields`
  （離陸開始点/速度/Turn Start距離/Bank角/Acceleration Height/上昇率2種/加速率の7項目）を
  表示する。OAT入力欄は元々Approach専用の並びの中にあったが、Departureモードでの
  IAS→TAS変換にも必要なため、モード共通の欄として外に出した。

**制約・既知の割り切り(今回のスコープ外)**：
- **3D表示・地形(DEM)表示・VSD(縦断面)には対応していない**。Departureモード中も
  3D/地形/VSDのトグル自体は操作できてしまうが、`renderDeparture()`は常に2D平面図のみを
  描画する（3D表示中に押しても見た目が変わらないだけで、エラーにはならないことを
  回帰テストで確認済み）。
- 風の影響は考慮しない。
- 加速に上限を設けていないため、旋回に極端に時間がかかる設定（Bank角を小さくする等）に
  すると終端速度が非現実的な値まで伸びる可能性がある。実測値（既定値一式）では
  ITE 2.2NM到達まで約2分45秒〜3分3秒(Bank30°/25°)、その時点のTASは約291〜320ktで、
  この範囲では実用上問題ない。
- 地図の表示倍率(拡大率)は既存のApproach/Traffic Pattern用の衛星画像データでの
  想定倍率をそのまま流用しているため、Departureの経路（直進+180°超の旋回で
  合計7〜8NM四方程度に広がる）が画面の表示エリアからはみ出す可能性がある
  （表示上の問題のみで、計算・数値自体には影響しない）。

**検証**（Playwright, `/opt/pw-browsers/chromium`。伊丹の衛星画像データは本サンドボックスに
無いため、`AIRPORTS`に埋め込み済みのAIP実測の滑走路端・ISK/ITE座標のみを使って幾何・
数値を検証）：

| 項目 | 結果 |
|---|---|
| 離陸開始点と反対端(14R)の距離 | 2000.0 ft（既定値どおり） |
| 旋回開始点とISKの距離 | 1.805 NM（目標1.8NMに対し許容誤差内。0.2秒刻みの数値積分による） |
| 旋回終了点とITEの距離 | 2.203 NM（目標2.2NMに対し許容誤差内） |
| Bank 25°での結果 | 旋回開始: 16.2秒後・533ft AFE・157kt TAS ／ ITE到達: 182.8秒後・4550ft AFE・320kt TAS |
| Bank 30°での結果 | 旋回開始: 16.2秒後・533ft AFE・157kt TAS ／ ITE到達: 166.2秒後・4273ft AFE・291kt TAS（Bank角が大きいほど旋回半径が小さくなり早く2.2NM圏外へ抜ける） |
| 伊丹以外の空港でDepartureモードを選択 | `geomError`パネルが表示され、計算を行わずに安全に終了することを確認 |
| 伊丹でRWY32L以外(例:14R)を選択しDepartureモード | 同上、`geomError`パネルが表示されることを確認 |
| 全9空港×Approach/Traffic Pattern/Departure×Visual/Circling×3D表示ON/OFFの372通り(既存モードへの影響確認の回帰テスト) | エラー0件・NaN0件 |
| スクリーンショット確認 | 直進区間(緑)→旋回区間(オレンジ)が視覚的に途切れなく繋がっており、ISK/ITE/補助円/TKOF/Turn Start/ITE到達点のマーカーが期待通りの位置に描画されることを目視確認 |

### 8.0zz build 166：Departureモードの旋回打ち切り基準をTRK180°に変更、補助円を簡略化、Traffic Patternの上昇線の起点を変更

build 165公開直後、ユーザーから3点の修正指示：「旋回はTRK180°で止めて！描画はITE2.2NM
まででいいや。TrafficPatternの上昇は最初の旋回のタイミング1.5NMの地点でPattern altに
到達するように滑走路末端2000ft手前から直線的に線引いていいよ。」

**1. Departureモードの旋回打ち切り基準をTRK180°に変更**

build 165では旋回の終了を「ITEから2.2NMの円との交差」で判定していたが、ユーザー指定で
「TRK(真方位)180°に達した時点」に変更。`simulateItamiDeparture()`のループで、
`headingDeg<=180`を満たした瞬間の状態を`trkStopInfo`として記録し、そこでシミュレーションを
打ち切るようにした。ITEとの距離判定(`iteCrossInfo`)は削除はせず、FYI用の付随情報として
残してある(万一パラメータ次第でTRK180に達する前にITE2.2NMを通過するケースがあっても
表示できるように)。

なお、この変更に伴う副作用として、**heading(headingDeg)の更新から`%360`の剰余を外した**
（build165時点では旋回中の毎ステップで`(headingDeg + rate*dt + 360)%360`としていたが、
左旋回は常に単調減少で0°を跨ぐことがない前提のもとでは、剰余を取ると値の連続性は
保たれるためTRK180判定自体は本来壊れないはずだが、念のため剰余を外して単調減少のまま
比較できるようにし、意図を明確にした)。

**検証結果**（既定値、Bank25°/30°とも）：TRK180到達時点でのITEからの距離は
それぞれ1.16NM／0.95NMで、**いずれも2.2NM圏内に留まったまま旋回が終わる**
（=build165時点のITE2.2NM到達基準では、この短い旋回だと到達しない）。
所要時間はBank25°で約63秒、Bank30°で約55秒（旋回開始から）。

**2. 補助線(円)を簡略化：ISKの2.8NM/3.6NM円を廃止、ITEの2.2NM円のみ残す**

上記の旋回打ち切り基準の変更で、旋回がISKの2.8NM/3.6NM圏まで届かなくなり
（そもそも旋回はISK起点ではなくITE付近で完結する短いものになった）、この2円を
表示する意味が薄れたため、ユーザー指摘どおり廃止。ITEの2.2NM円は「AIP記載の目安距離」
としての参考値の意味がまだあるため残す。ISKからのTurn Start距離の円（入力欄
`depTurnStartNM`と連動する可変円）はそのまま残している。

**3. Traffic Pattern(3D表示)の離陸区間の上昇線：起点を「滑走路末端2000ft手前」に変更**

build 162時点では、Traffic Patternの3D表示における離陸区間の上昇線は「THR(離陸滑走路の
手前側閾値、AFE0)から最初の旋回開始点(departure.pStart、反対側閾値から1.5NM)まで
直線的にAFE0→Pattern altitude」というモデルだった。ユーザー指摘で、上昇の起点を
「滑走路末端(反対側)の2000ft手前」に変更（build165のDepartureモードの離陸開始点と
同じ考え方・同じ2000ft固定値）。THRからこの起点までの区間は従来どおりAFE0のまま
水平に描き、起点からdeparture.pStart(1.5NM地点)までを直線的にAFE0→Pattern altitudeへ
上昇するモデルに変更した。

実装は`render()`内のTraffic Pattern 3D描画ブロックで、離陸経路の頂点列
(`depPath`)に新しい起点(`depClimbStartXY = oppXY - ux*2000ft`)を追加し、
高度を求める`depAltAt(arc)`を「起点(`climbStartArc`)までは0、起点から
`departure.pStart`(`climbEndArc`)までは線形補間、それ以降はPattern altitude一定」の
3区間に変更しただけで、他の計算（旋回そのものやDownwind以降）には手を入れていない。
起点には新たに「2000ft手前」ラベルの高度マーカーも追加した。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：

| 項目 | 結果 |
|---|---|
| Bank25°/30°ともTRK180で打ち切られること | `trkStopInfo.headingDeg`が180±0.5°以内であることを確認 |
| ITE2.2NM到達フラグ(`iteCrossInfo`) | 既定値ではどちらのBank角でも`false`(=到達せず、想定どおり) |
| Departureモードのフル描画(`renderDeparture`) | エラー0件、`geomError`非表示、readoutにTRK180の情報が表示されることを確認 |
| SVG内に新しい補助円/ラベル | ISK2.8/3.6NM円が無くなり、ITE2.2NM円のみになったことを目視確認。「2000ft手前」ラベルがTraffic Pattern(3D)描画に出現することを確認 |
| 全9空港×Approach/Traffic Pattern/Departure×Visual/Circling×3D ON/OFFの372通り回帰 | エラー0件・NaN0件 |
| スクリーンショット | 旋回が約135°(Bank25°)で止まり、TRK180マーカーで終端していることを目視確認 |

### 8.0A0 build 167：ISK2.8/3.6NM円を復活、ISK/ITEをApproach/Traffic Patternでも常時プロット

build 166公開直後、ユーザーから2点の指摘：「2.8と3.6は残していいんだよ　あとITEとISKの
場所をプロット」。

**1. ISK 2.8NM/3.6NM円の復活**

build 166で「描画はITE2.2NMまででいい」という指示を「ISKの2.8/3.6NM円は不要」という
意味に解釈して削除したが、これは誤読だった。ユーザーの意図は円の取捨選択ではなく、
（後日判明した文脈から）実際の描画線の話だったとみられる。ユーザーからの明示的な
「2.8と3.6は残していい」との指摘を受け、`renderDeparture()`内の補助円描画を
build165時点の状態（ISK 2.8NM・3.6NM・ITE 2.2NMの3円、いずれもAIP RJOO AD2.19の図に
記載の目安距離）に戻した。Turn Start距離の可変円（入力欄と連動）も変更なくそのまま。

**2. ISK/ITEの位置をApproach/Traffic Patternモードでも常時プロット**

build165時点ではISK/ITEのマーカーはDepartureモードの中でしか描画されていなかった。
ユーザー指摘を受け、Approach/Traffic Pattern両モードの地図にも常時プロットするように
変更（伊丹空港が選択されている場合のみ。既存のHaneda TTE/AOMIのように特定の進入方式
選択時だけ出す仕組みとは異なり、ISK/ITEはどのRWY・どの進入方式でも常に位置関係を
把握できると有用なため、無条件で表示する設計にした）。

実装は`render()`内の、Circling/Visualの主要マーカー（Turn/DW Entry等）を描き終えた
直後、地図上の距離/高度ラベル描画より前の位置に追加。`airport.key==="itami"`かつ
`airport.navaids.ISK/ITE`が存在する場合のみ、そのままの位置(XY座標に変換)で
`markerXY()`により「ISK」（シアン）・「ITE」（アンバー）のラベル付きマーカーを描く。
既存のVOR進入方式(`isVorProcedure`)のガード条件もつけているが、これはHaneda専用の
特殊進入方式であり伊丹では常にfalseになるため実質的な影響はない(将来の拡張に備えた
安全側の条件)。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：

| 項目 | 結果 |
|---|---|
| DepartureモードでISK2.8/3.6NM円が復活していること | スクリーンショットで3円(ISK2.8/3.6NM・ITE2.2NM)が表示されていることを目視確認 |
| Approach(Visual, RWY32L)モードでISK/ITEがプロットされること | スクリーンショットで「ISK」「ITE」ラベル付きマーカーが表示されることを目視確認 |
| 全9空港×Approach/Traffic Pattern/Departure×Visual/Circling×3D ON/OFFの372通り回帰 | エラー0件・NaN0件 |

### 8.0A1 build 168：ISK/ITEのプロットをDepartureモード限定に戻す

build 167でApproach/Traffic Patternモードにも常時プロットするようにしたISK/ITEの
マーカーについて、ユーザーから「isk iteはDepartureモードのときだけでいいよ！」との
指摘。`render()`内に追加した無条件表示ブロックを削除し、build165時点と同じ
「Departureモード内(`renderDeparture()`)でのみ表示」に戻した。Departureモード自体の
ISK/ITEマーカー・ISK2.8/3.6NM・ITE2.2NM円の表示に変更はない。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：Approach(Visual, 伊丹RWY32L)描画後の
SVGに"ISK"ラベルが含まれないこと、Departureモードでは引き続き含まれることを確認。
全9空港×Approach/Traffic Pattern/Departure×Visual/Circling×3D ON/OFFの372通り回帰、
エラー0件・NaN0件。

### 8.0A2 build 169：旋回終了TRKを選択式に、ITE2.2NM到達まで描画を延長、ISK帯からの逸脱を赤表示

ユーザーから3点の機能拡張指示：「旋回終了TRKを180°デフォルトで選択式に。ITE2.2NMまで
飛ぶまでは描画して。一旦2.8から3.6の間に入った後で、その範囲外に出たら経路を赤くする
ように変更。」

**経路のフェーズ構成をbuild166時点の2段階(直進1→旋回)から3段階に変更**
（`simulateItamiDeparture()`を全面的に書き直し）：

1. **直進1(straight1)**：離陸開始点からISK 1.8NM(可変)まで、従来どおり。
2. **旋回(turn)**：左旋回。終了条件を固定値180°から、新設のUI入力欄
  「旋回終了TRK」（既定180°、変更可能）に変更。
3. **直進2(straight2、新規)**：旋回終了TRKの方位のまま直進を継続し、ITEから2.2NMの円
  (`ITAMI_DEP_ITE_TARGET_NM`)と交差するまで計算・描画する。build166〜168時点では
  旋回終了(TRK到達)がそのままシミュレーション全体の終了でもあったため、既定値では
  ITE2.2NMまで届かず経路が短く終わっていたが、ユーザー指摘「ITE2.2NMまで飛ぶまでは
  描画して」により、**旋回終了後もこの直進フェーズを追加して必ずITE2.2NM到達まで
  計算・描画する**ように変更した。

これにより`sim.ok`の判定も「旋回開始・旋回終了・ITE2.2NM到達の3点すべてが得られたか」
に変更(`trkStopInfo && iteCrossInfo`の両方が必須)。既定値(TRK180、Bank25°)では
旋回終了から約24秒・約1.3NM直進してITE2.2NMに到達する(全体で離陸から約87秒)。

**経路の色分けにISK 2.8-3.6NM帯からの逸脱表示を追加**：ISKからの距離を経路の全ポイントで
記録し、一度でも2.8NM〜3.6NMの帯に入ったら`hasEnteredIskBand`フラグを立てる。その後
帯の外(2.8NM未満または3.6NM超)に出ている区間は「逸脱」とみなし、経路の色を通常の
フェーズ色(直進1=緑、旋回・旋回後直進=アンバー)から**赤**に上書きする(ユーザー指定
「一旦2.8から3.6の間に入った後で、その範囲外に出たら経路を赤くする」)。帯に入る前や
帯の中にいる間は逸脱として扱わない。描画は色の変わり目ごとにpolylineを分割し、
区間の境目の点を次の区間の先頭にも複製することで、色が変わっても線が途切れて
見えないようにしている(Traffic Patternの旋回接続と同じ手法)。

**UI**：Departureモードの入力欄に「旋回終了TRK (真方位, °)」を追加(既定180、
`depTrkStopDeg`、他の入力欄と同じ永続化・自動再描画の対象)。

**読み出しパネル**：「旋回終了(TRK{値})」「ITE2.2NM到達(計算終了点)」の2項目に加え、
「ISK 2.8-3.6NM帯からの逸脱(赤区間)」の状態(帯に入っていない／帯に入ったが逸脱なし／
あり)を表示するようにした。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：

| 項目 | 結果 |
|---|---|
| TRK180/150/90の3パターンで旋回終了TRKが指定どおりになること | いずれも`trkStopInfo.headingDeg`が指定値と一致 |
| いずれのTRK設定でも最終的にITEから2.2NM(±0.01NM程度)で経路が終わること | 3パターンとも`distFromITE`が2.20〜2.21NMで一致、`sim.path`の最終点がITE到達点と一致することを確認 |
| ISK2.8-3.6NM帯に入ってから逸脱していること | 既定値で`everInBand`/`anyOutOfBand`ともtrueであることを確認 |
| 全9空港×Approach/Traffic Pattern/Departure×Visual/Circling×3D ON/OFFの372通り回帰 | エラー0件・NaN0件 |
| スクリーンショット | 緑(直進)→アンバー(旋回)→赤(帯逸脱後、ITE2.2NM到達点まで)の色分けが期待通り表示されることを目視確認 |

### 8.0A3 build 170：readoutの表記2項目を削除、Departure選択時にRWY32Lへ強制変更、速度既定値を170ktに

ユーザーから3点の指摘：「ISK/ITE 位置データ出典...ISK 2.8-3.6NM帯からの逸脱(赤区間)...の
表記不要／Departureを選んだときは32Lに強制変更／速度をデフォルト170ktに」。

1. `renderDeparture()`の読み出しパネルから「ISK/ITE 位置データ出典」と「ISK 2.8-3.6NM帯
   からの逸脱(赤区間)」の2項目を削除。データの出典自体はコード内コメントに残っている
   ため実害はなく、赤区間かどうかは地図上の色を見ればわかるため、表記としては不要という
   判断。ロジック(色分けの計算自体)には変更なし。
2. `#opMode`の「Departure」ボタンのクリックハンドラに、`#rwyEnd`セレクトが
   "32L"という値を持つ場合はそれへ強制的に切り替える処理を追加。空港自体は変更しない
   （伊丹以外を選んでいる場合、"32L"という選択肢自体が無いため何も起きず、従来どおり
   「対応滑走路を選択してください」の案内が出る）。
3. Departureモードの速度(IAS)の既定値を155kt→170ktに変更（HTML入力欄の初期値、
   JSのフォールバック値、コード内コメントの3箇所）。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：速度入力欄の初期値が170であること、
RWY14R/14L選択中に「Departure」ボタンをクリックするとRWY32Lへ切り替わること(2パターンで
確認)、読み出しパネルのHTMLに「位置データ出典」「帯からの逸脱」の文字列が含まれなくなった
ことを確認。全9空港×Approach/Traffic Pattern/Departure×Visual/Circling×3D ON/OFFの
372通り回帰、エラー0件・NaN0件。

### 8.0A4 build 171：阪神高速11号池田線(E2A)の目安ラインをDepartureモードに追加

ユーザー質問「阪神高速E2Aのラインをプロットすることって可能？」を受けて対応。

**路線の特定**：WebSearchで確認したところ、路線記号「E2A」は阪神高速**11号池田線**
（起点：中之島JCT、終点：池田出入口・池田木部出入口。伊丹空港付近では
豊中南出入口→豊中北出入口→大阪空港出入口→蛍池JCT→神田出入口→池田木部出入口、
の順で通過する）だった([Wikipedia](https://ja.wikipedia.org/wiki/%E9%98%AA%E7%A5%9E%E9%AB%98%E9%80%9F11%E5%8F%B7%E6%B1%A0%E7%94%B0%E7%B7%9A))。

**座標の精度についてユーザーに確認**：この路線の正確な測量(サーベイ)データはWeb検索
だけでは見つけられず、AIP実測値のISK/ITEと同じ精度は用意できない旨を説明し、
「大まかな目安線で良いか／正確な線形データが必要か」を確認したところ、
**「大まかな目安線でOK(視覚的な参考程度)」**との回答。

**実装**：ホームメイト調べの各IC/JCTの住所([参照](https://www.homemate-research-ic.com/27/h143/))を、
国土地理院(GSI)の住所検索API(`https://msearch.gsi.go.jp/address-search/`)でジオコーディングし、
伊丹空港周辺の5点(豊中南出入口・豊中北出入口・大阪空港出入口/蛍池JCT・神田出入口・
池田木部出入口)を南→北の順に直線で結んだだけの、**近似的な目安ライン**を
`AIRPORTS.itami.hanshinE2A`データとして追加した。コード内コメントで「AIP実測値とは
異なり、住所からのジオコーディングによる近似値である」ことを明記している。
`renderDeparture()`内で、ISK/ITEマーカーのすぐ後に薄いグレーの点線として描画し、
「E2A 池田線(目安)」のラベルを付けた。UIのヒント文にも「大まかな目安線で、AIP実測値
ではない」旨を追記した。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：Departureモードの描画でエラーが
発生しないこと、SVGに"E2A"のラベルが含まれることを確認。スクリーンショットで
地図上に目安線が表示されることを目視確認。全9空港×Approach/Traffic Pattern/
Departure×Visual/Circling×3D ON/OFFの372通り回帰、エラー0件・NaN0件。

### 8.0A5 build 172：E2Aの路線を中国自動車道に訂正、区間を中国池田IC～宝塚ICのみに限定、座標精度を向上

ユーザー指摘「E2Aは11号じゃないよ　中国池田ICと宝塚ICの間のみの描画でいいから
もう少し高精度にできない？」を受けて、build171の内容を訂正。

**誤りの内容**：build171では路線記号「E2A」を阪神高速**11号池田線**と誤認していた。
実際には**中国自動車道**（NEXCO西日本）が正しく、これはAIP RJOO AD2.19の騒音軽減経路
の説明文にある"Chugoku Expressway"と同一路線である（build171時点で見落としていた
重要な符合）。誤認の原因は、検索結果に「阪神高速」「E2A」「池田」が偶然同時に出現した
ことと、11号池田線の終点が「池田出入口」であることが、中国自動車道の「中国池田IC」と
紛らわしかったこと。

**再確認**：WebSearchで、NEXCO西日本の公式発表タイトル（「E2A 中国自動車道（吹田JCT～
中国池田IC）...」等）や、路線記号を専門に扱う複数の道路情報サイトの表記を確認し、
「E2A＝中国自動車道」であることを確認。またWikipediaで、対象区間付近のIC/JCT順序
（吹田JCT→中国吹田IC→中国豊中IC→**中国池田IC**→**宝塚IC**→(以東：西宮名塩SA・
西宮山口JCT)）を確認し、中国池田IC～宝塚IC間に中間IC/JCTが存在しないことを確認した。

**座標精度の向上**：ユーザー指摘の「もう少し高精度に」に対応するため、住所からの
GSIジオコーディング（build171で使用）よりも精度の高い方法として、mapion.co.jp
電話帳ページに埋め込まれた地図URLのクエリ文字列から座標を直接抽出した：
中国池田IC (34.8046532, 135.43284718)、宝塚IC (34.80461211, 135.36796649)。
（参考：宝塚東/西トンネルや青葉台シェルター等の中間地物も精度向上の候補として調査したが、
青葉台シェルターは宝塚IC～西宮山口JCT間（対象区間の東側・区間外）に位置することが判明した
ため対象外とし、また従来ユーザーが確認済みの「大まかな目安線でOK」という精度期待に照らし、
2点の高精度な直線区間で十分と判断し、追加の中間点は採用しなかった。）

**実装**：`AIRPORTS.itami.hanshinE2A`（5点、阪神高速11号池田線の全区間）を削除し、
`AIRPORTS.itami.chugokuExpwyE2A`（2点、中国池田IC・宝塚ICのみ）に置き換えた。
`renderDeparture()`内の描画ブロックも新プロパティ名に追従させ、ラベルを
「E2A 池田線(目安)」から「E2A 中国道(目安)」に変更。UIのヒント文も
「地図上の「E2A 中国道」は中国自動車道(中国池田IC～宝塚IC間)のIC位置からの
大まかな目安線で、AIP実測値ではありません。」に更新した。コード内コメントに
build171→172の訂正経緯を詳細に記録している。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：`hanshinE2A`プロパティが完全に
削除され`chugokuExpwyE2A`（2点）のみが存在すること、Departureモードの描画がエラー
なく完了しSVGに新ラベル「E2A 中国道」を含み旧ラベル「池田線」を含まないこと、
readoutにNaNや幾何エラーが出ないことを確認。全9空港×Approach/Traffic Pattern/
Departure×Visual/Circling×3D ON/OFFの372通り回帰、エラー0件・NaN0件。
スクリーンショットで、目安線が伊丹空港の北側を通り、離陸経路やISK/ITEとは
地理的に離れた位置に表示されることを目視確認（中国自動車道は伊丹空港の北方を
東西に走る道路のため、位置関係として妥当）。

### 8.0A6 build 173：E2Aの目安ラインを2点直線から経路形状(カーブ)近似に変更

ユーザー要望「2点間の直線じゃなくて、道の形自体をある程度正確にプロットしてほしい」を
受けて、build172の中国池田IC-宝塚IC間2点直線を、実際の道路の曲がり具合を反映した
多点ポリラインに置き換えた。

**データ取得の試行錯誤**：まず測量データ相当の取得を目指し、OpenStreetMap公式API
(`api.openstreetmap.org`)やOverpass API(`overpass-api.de`とそのミラー)へのアクセスを
試みたが、いずれもrobots.txtによりWebFetchでの取得を拒否された。国土地理院の道路中心線
ベクトルタイル(`experimental_rdcl`)も試したが、レスポンスがWebFetchの要約処理で
「バイナリデータ」として扱われ内容を読み取れなかった(GSIタイル画像も同様にWebFetchは
画像コンテンツ非対応)。次にOSRM(オープンソース経路探索エンジン)の公開デモサーバーで
中国池田IC・宝塚ICの座標を直接指定してルート検索したところ、距離27kmという明らかな
迂回ルートが返り(実際の直線距離は約6km)、一旦「工事による通行止めの影響では」と
誤って判断してユーザーに確認したが、**ユーザーから「その区間は工事してないはず。
宝塚から千里中央のルートで検索すればグーグルマップでも普通に経路が出る」と指摘を受け、
判断が誤りだったことが判明**。実際には中国池田IC・宝塚ICの座標(mapion電話帳由来、
料金所付近)がランプの正しい方向に乗らず迂回させられていたことが原因と判明。

**訂正した取得方法**：千里中央～宝塚間の長距離ルートでOSRMにクエリしたところ、
中国池田IC～宝塚IC間を含む自然な経路(合計18km、迂回なし)が得られ、その経路データの
うち対象区間に該当する部分（本線上の座標、料金所ではなく実際に高速道路上にある2点)を
起終点として改めてOSRMでルート検索した結果、距離6,415m・迂回なしの現実的な経路が
得られた。この経路の座標列(約100点)をWebFetchで複数回に分けて取得し(1回のリクエストで
大量の座標を要約させると欠落・誤カウントのリスクがあるため)、取得のたびに前回取得分との
重複区間が完全一致することを確認して整合性を検証。得られた座標列から約20点に間引いて
採用し(始点・終点は引き続きmapion電話帳のIC座標を採用、中間18点はOSRM経路データから
選定)、`AIRPORTS.itami.chugokuExpwyE2A`を2点から20点に拡張した。

**留意点**：OSRMの経路はOpenStreetMapの地図データに基づく計算結果であり、AIP実測値や
測量(サーベイ)データではない。あくまで「地上物の視覚参考」レベルの近似カーブであることに
変わりはなく、コード内コメント・UIヒント文の両方にその旨を明記している。

**実装**：`renderDeparture()`の描画ロジック自体は変更なし(既存の`polylineFromXY`が
配列の全点を結んでポリラインを描くため、データ点数を増やすだけでカーブ表現に対応済み)。
コード内コメントとUIヒント文を、直線から経路形状近似に変更した経緯が分かるよう更新した。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：`chugokuExpwyE2A`が20点であること、
経度が単調減少(西へ向かう自然な並び)であること、緯度が34.8046～34.8111の範囲に収まる
(北へ膨らんでから戻る、山間部の地形に矛盾しない形状)ことを確認。Departureモードの描画が
エラーなく完了しSVGラベルが正しく表示されること、readoutにNaN・幾何エラーが出ないこと、
UIヒント文が更新されていることを確認。全9空港×Approach/Traffic Pattern/Departure×
Visual/Circling×3D ON/OFFの372通り回帰、エラー0件・NaN0件。スクリーンショットで、
目安線が直線ではなく北側に膨らむ滑らかなカーブとして描画されることを目視確認。

### 8.0A7 build 174：羽田 LDA W RWY22/23 モードを新規追加

ユーザー要望「羽田でLDA W 22と23のModeを作りたい」に対応。要件は以下の通り（要約）：
Missed Approach Point(MAP)手前から描画開始、速度は型式/重量を反映、MAP以降にそれぞれの
RWY Finalへ旋回開始、旋回開始DMEの既定値=MAPのDMEでそれより大きいDMEは入力不可、
途中でレベルフライトしないよう1312ft Aiming Pointへ向けて全区間Continuous Descent、
MAP通過高度と旋回に必要なBank角を計算。

**ユーザーとの事前すり合わせで訂正された3点**（実装前のAskUserQuestionでの初期提案から変更）：
1. 降下角：BONDO/DAMBO(いずれも描画範囲外)にアンカーせず、両滑走路とも単純に**3°一定**
   (チャート記載のRWY22=3.25°/RWY23=3.00°は不使用)。
2. 描画開始点：BONDO/DAMBOではなく**地図の表示範囲端**(`boundsMaxRadiusM`)から。
3. 旋回のターゲット方位：ILS-LOC(IAD 222°M/ITD 232°M)の磁方位を暗算で真方位化するのではなく、
   **実測THR座標(既存`AIRPORTS.haneda`のRWY22/23閾値lat/lon)から算出した真方位**を使用。
   検討過程でユーザーから「その理解で合ってるけどRWY HDG1°違うんじゃない？」との指摘を受け、
   `headingTrueDegAtoB`ベースの近似(magVarDeg=8を単純加算)とAIP実測のTRUE BRG
   (RWY22=215.01°T/RWY23=222.56°T)の間に約1°のズレがあることを確認。これは
   `AIRPORTS.haneda`の各滑走路が`thresholds`(閾値の実測lat/lon)を持つ場合、
   `headingTrueDegAtoB`/`magVarDeg`は幾何計算に使われず(閾値位置導出にのみ関与)、
   実際の描画・計算はthresholdsの実測座標から都度算出される方位を使う既存の設計方針
   (コード内コメント「羽田がその実装例」)と整合させ、ILS-LOCの磁方位変換を経由しない
   実測THR座標ベースの方位を採用することで解決した(ILS-LOCアンテナは滑走路中心線から
   意図的にわずかにオフセットして設置されるため、ILS-LOCの磁方位＝滑走路実測方位とは
   本来一致しない)。

**データ**（AIP RJTT AD2 (EFF:3 SEP 2026) より実測値、`RJTT__AD2_20260903.pdf`）：
- `AIRPORTS.haneda.navaids`に`IKL`(LDA-DME RWY22、999MHz CH-38X、353612.96N/1394908.33E、
  elev122ft)と`ITL`(LDA-DME RWY23、983MHz CH-22X、353411.11N/1394656.12E、elev34ft)を追加。
- `AIRPORTS.haneda.ldaProcedures`を新設：`"22":{navaid:"IKL", mapDmeNm:1.1, finalApchCrsMagDeg:277}`、
  `"23":{navaid:"ITL", mapDmeNm:4.9, finalApchCrsMagDeg:277}`。MAP DME・Final Apch Crs(277°M、
  両滑走路共通)はJeppesenチャート(11-8 LDA W RWY22 / 11-15 LDA W RWY23、`IMG_1727.png`/
  `IMG_1728.png`)の記載値。

**幾何モデル**（新規`renderLda(airport, rwyLabel)`、`render()`から`renderDeparture`と同様に
早期分岐する独立関数。既存Approach/Traffic Pattern/Departureのコードには一切手を入れていない）：
- LDA Final Apch Crs(277°M)は、IKL/ITLの実測位置を通り277°M方位を持つ直線として定義。
  描画開始点はMAPから地図表示範囲端(`boundsMaxRadiusM`)まで、この直線上を遡った位置。
- MAP = 各navaidからのDME距離(`mapDmeNm`)分、進行方向と逆向きに離れた位置。
- Turn Start入力欄(`#ldaTurnStartDme`、既定値=MAPのDME)：`max`属性をMAPのDMEに設定し、
  それより大きい値が入力された場合はMAPのDMEにクランプする(=MAP通過前には旋回開始できない)。
- 旋回：進入方位(277°Mの実測真方位)から、実測THR座標ベースのRWY Final方位への旋回角・
  旋回方向(左右)を算出し、`computeVariableBankTurnLocal`(既存のRoll-in/Steady/Roll-out数値積分、
  Roll-rateは`ROLL_RATE_DEG_PER_SEC`=3°/secで既存Departure/Traffic Patternと共通)で旋回軌跡を
  生成。Bank角は、旋回終了点がRWY Finalの延長線にちょうど乗る(法線方向の符号付き距離=0)値を
  1°〜75°の範囲で二分法により逆算する(`computeRealisticContinuousTurn`と同じ考え方の二分法。
  74°超で警告を出す既存Departure/Traffic Patternの慣例に合わせ、75°で探索範囲外エラーとした)。
- 高度：全区間、1312ft Aiming Point(RWY閾値から着陸方向へ1312ft、既存`AIMING_POINT_FT`概念を
  流用)に向けて3°一定でContinuous Descent。各地点のAFEは「その地点からAiming Pointまでの
  経路長(直線区間+旋回弧長+直線区間の合算)×tan(3°)」で算出するため、旋回を挟んでも
  レベルフライトなしで連続的に降下する。
- 速度：`#targetAppSpd`(FMSの型式/重量/Flapから自動計算されるTarget App Spd、IAS)をそのまま
  使用。TAS換算はMAP付近の代表気圧高度(標高+1200ft AFE)で一度だけ行う簡略モデル(旋回中の
  減速等は考慮しない)。風は考慮しない(Departureモードと同じ方針)。
- UI：`#opMode`に「LDA」ボタンを追加、`#ldaFields`(Turn Start DME入力)を新設、選択時は
  羽田RWY22へ強制変更(RWY22/23選択中はそのまま)。羽田RWY22/23以外を選択した場合は
  Departureモードと同様の幾何エラー表示。VSD/PNG保存はこのモード未対応(Departureと同じ)。

**重要な既知の制約・所見**（ユーザーへの報告事項）：
- RWY22はデフォルト設定(Turn Start=MAP)でBank角6.4°(左旋回54.1°)に収束するが、
  RWY23はデフォルト設定でもBank角が75°の探索上限に達し収束しない(赤の幾何エラー表示)。
  検証の結果、これはコードのバグではなく、ITL(LDA-DME RWY23)の実測位置がRWY23実測
  センターラインの延長線から大きく(MAP地点で計算上約1,740m=0.94NM)オフセットしている
  ことに起因する幾何的な事実であり、AIP記載の「ITLはRWY23センターラインの4834m北」
  という記述とも整合する(オフセット局が生む角度差が進行するにつれ線形に開いていくため)。
  Turn Start DMEをMAPより大きくすることはできない(ユーザー要件)ため、この単純な
  一定バンク旋回1回のモデルでは、RWY23はデフォルトのままでは「Finalへちょうど乗る」
  解が見つからない。実際のAIP手順はAD2.23記載の「SIMULTANEOUS INDEPENDENT LDA
  APPROACHES(SILA)」に基づく、チャート"11-15A1"(未取得)記載のPrescribed Track
  (単純な一定バンク円弧ではない可能性がある)に依っている可能性が高い。
- 3°一定のContinuous Descentモデルにより、計算されるMAP通過高度(RWY22で約430ft MSL/
  AFE405ft)は、AIPに実際に記載されているMDA(H) 1000ft(979ft)とは一致しない。これは
  BONDO/DAMBO(いずれも描画範囲外のため今回未使用)の強制通過高度を経由しない、
  ユーザー指定の単純化されたモデルによる予期された結果であり、バグではない。

**検証**（Playwright, `/opt/pw-browsers/chromium`）：構文チェックOK。羽田RWY22/23での
LDAモードレンダリングがエラーなく完了しreadoutにNaN/undefinedが出ないこと、Turn Start DME
入力欄がMAPのDMEにクランプされること(5.0nm入力→1.1nmにクランプ)、MAPより小さいDME
(=MAP通過後の旋回開始、0.3nm)も許容されること、羽田以外/RWY22・23以外選択時は幾何エラーが
正しく表示されることを確認。既存モードへの影響がないことを、全9空港×Approach/Traffic
Pattern×Visual/Circling×3D ON/OFF(324通り)+全9空港×Departure/LDA(クラッシュ有無のみ)の
回帰で確認、失敗0件。スクリーンショットで、羽田RWY22のLDA進入経路(LDA Final Apch Crs→
旋回→RWY Final→Aiming Point)が正しい形状で描画され、RWY23では想定通り幾何エラーが
表示されることを確認。

### 8.0A8 build 175：衛星画像データを統合版(map_data_all.js)から空港ごとの個別ファイルに戻した

ユーザー要望「ごめん、やっぱりmap dataは統合じゃなくて空港毎に出力される形式にして」に対応。
build 125/126で「今後は統合版map_data_all.jsだけを使う」というユーザー判断のもと個別ファイルを
廃止していたが、8.2節(GitHub公開の検討)で洗い出した通り、この判断はオンライン配信の
ファイルサイズ上限（Cloudflare Pages 25MiB/ファイル、GitHub Pages 100MB/ファイル等）と
正面から衝突することが判明していた（9空港分の統合版は数十MB〜100MB級になり、いずれの
hostingでも1ファイルとしては通らない）。今回、空港ごとに分割する方針へ戻すことになった。

**変更内容**：
- **`tile_downloader.py`**：`process()`が画像/標高を取得するたびに、その空港専用の
  `map_data_<空港キー>.js`（例: `map_data_haneda.js`）へ直接保存するように戻した
  (`save_to_merged`/`save_dem_to_merged`/`update_entry`が内部で使う`load_entry`/`write_entry`/
  `_entry_path`を新設。中身のフォーマット自体は従来通りの1行`var MAP_DATA_<KEY> = {...};`
  のままなので、approach_planner.html/bundle_html.py側の読み込みロジックは変更不要)。
  `merge`コマンド(個別→統合、他用途で欲しい場合向けに残置。既定では個別ファイルを削除しない
  よう安全側に変更)はそのまま使えるほか、逆方向の**`split`コマンドを新設**（統合版
  `map_data_all.js`を空港ごとの個別ファイルに分割。build 126〜173の間に統合版で貯めた
  データを個別ファイルに戻す移行用）。GUI完了メッセージも「統合版を更新しました」から
  「取得したN空港ぶんのmap_data_<空港キー>.jsを更新しました」に修正。
- **`approach_planner.html`**：`<script src="map_data_all.js">`の1行を、9空港ぶんの
  `<script src="map_data_<空港キー>.js">`(chitose/hakodate/haneda/itami/hiroshima/
  takamatsu/matsuyama/fukuoka/kumamoto)に戻した。未取得の空港のファイルが無くても
  そのタグは静かに読み込み失敗するだけで(page errorにならない)、他空港の表示には
  影響しない(build 125以前と同じ挙動)。`getAirportMapData()`本体・`bundle_html.py`は
  元々どちらの形式(統合1行 or 個別ファイル)でも読める作りだったため、コード変更は
  コメント文言の修正のみで済んだ。
- **`bundle_html.py`**：コード変更なし。元々`<script src="map_data_XXX.js">`のタグを
  正規表現でスキャンして個々のファイルを埋め込む作りだったため、HTML側のタグが
  個別ファイル9本に戻ったことで自動的に個別ファイルを埋め込む経路に戻った
  (統合版があればそちらを優先し重複埋め込みを避ける処理も残っているので、
  統合版と個別ファイルが両方存在する過渡期でも問題ない)。

**検証**：
- `tile_downloader.py`の`update_entry`/`load_entry`/`merge()`/`split()`を実際にPythonで
  実行し、画像→標高の順で1空港ぶんのフィールドがマージされて`map_data_<key>.js`に
  正しく保存されること、`merge()`で統合版を作った後も個別ファイルが残ること、
  `split()`でその統合版から個別ファイルを再構築でき中身が完全一致することを確認。
- `approach_planner.html`の構文チェックOK。Playwrightで、`map_data_*.js`が1つも
  存在しない状態でも9本の`<script src>`タグがpage errorを出さずに静かに読み込み失敗
  することを確認（=未取得空港があっても他の動作に影響しない、従来どおりの挙動）。
- `bundle_html.py`を実際に2空港ぶん(`map_data_haneda.js`/`map_data_itami.js`、
  統合版なし)の個別ファイルがある状態で実行し、両方とも正しく埋め込まれ
  `approach_planner_bundled.html`が生成されること、生成後のHTMLをPlaywrightで開いて
  `getAirportMapData('haneda')`が埋め込んだデータを正しく返すことを確認。
- 既存モードへの回帰：全9空港×Approach/Traffic Pattern×Visual/Circling×3D ON/OFF
  (324通り)+全9空港×Departure/LDA(クラッシュ有無)の回帰で失敗0件（今回の変更は
  データ読み込み経路のみで計算ロジックには触れていないため、想定通り影響なし）。

### 8.0A9 Claude Artifact版をbuild156→175に更新し、map_data_<空港キー>.js 9本を添付ファイルとして追加

ユーザーが「artifactに統合してもらうためにアップロードしようとしたんだけど、何かいい手はある？」と
9本のmap_data_<空港キー>.jsを添付してきたことをきっかけに対応。8.0zoで検討していた
「Claude Artifact版に衛星画像・標高データをbundleする」課題への実践的な解決策となった。

**背景**：base64化された画像データをテキストとして会話コンテキストに読み込ませると、
(1) base64は高エントロピーな文字列でBPEトークナイザが圧縮できずトークン効率が非常に悪い、
(2) Claude Projectsの知識容量は文字数/トークン数で測られるため、数MBのbase64が容量を
大きく圧迫する、という二重の問題がある。この問題を回避する手段として、Artifactツールの
`files`パラメータ（ローカルファイルをbase64化・コンテキスト読み込みなしにそのまま
添付ファイルとしてコピーする機能）を採用した。制約は1ファイル16MB・合計64MB・255ファイルまで
（今回は9ファイル合計約31.5MB、最大単体ファイル(伊丹)約5.5MBで余裕あり）。

**発見した問題**：公開済みArtifact本体(`artifact_body.html`)を確認したところ、build156の
まま19ビルド分（LDAモード・map_data形式の再分割など）取り残されていたことが判明。
過去のセッションで「Artifact公開用の別ファイルを保守する」運用を始めた(8.0zn)ものの、
本体`approach_planner.html`を更新するたびにこちらへも反映する習慣が根付いていなかった。

**対応**：
- `approach_planner.html`(build175)から`<!DOCTYPE html>`/`<html>`/`<head>`/2つの`<meta>`/
  `</head>`/`<body>`/`</body></html>`のラッパー部分だけを取り除き、`<title>`をArtifact用の
  短い名前に差し替えて`artifact_body.html`を再生成（Artifactツールは公開時に独自の
  スケルトンでラップするため、本体側の同等タグは含めてはいけない）。
- ユーザー添付の9本のmap_data_<空港キー>.jsを添付ファイルとして`files`パラメータに指定し、
  Artifactツールで再公開（`https://claude.ai/artifact/X5M2wPkYSQxFb12vqiM6MS`、Version 3）。
- 公開済み(旧build156)の内容を上書きする前に、Artifactツールの安全機構に従い保存済み
  live版(3784行)を全文読み直し、ユーザーが手を加えた形跡(diff)が無くbuild156スナップショット
  そのままであることを確認してから上書きした（意図しない編集消失を防ぐための手順）。

**検証**：`list`(scope:files)で公開後のファイル一覧を確認し、`index.html`(273,292 bytes)＋
9本のmap_data_<空港キー>.js（合計約31.5MB、各ファイルのバイト数がローカルの
`ls -la`結果と一致）が正しくアップロードされていることを確認。

**注意点（申し送り）**：`artifact_body.html`は`approach_planner.html`と自動同期しない
別ファイルなので、今後`approach_planner.html`に機能追加・修正を行った際は、Artifact版へ
公開する必要が生じたタイミングで都度この手順（ラッパー除去→再公開）を繰り返す必要がある。
放置するとまた今回のように取り残される。

### 8.0B0 build 176：Departureモードを伊丹専用表示に、LDAを独立モードからRWY欄の選択肢(LDA 22/LDA 23)に変更、LDAは常にNorth up＋MAP-RWY中間点センタリングに

ユーザー要望3点に対応。

**1. Departureモードは伊丹のみ表示**：`setupAirport()`内で、空港が伊丹(`airport.key==="itami"`)
でない間はModeセグメントの「Departure」ボタン(`#opModeDepartureBtn`というidを新設)自体を
`display:none`で隠すようにした。伊丹以外を選んでいる間にDeparture中だった場合(空港を
切り替えて伊丹から離れた場合)は、選べなくなる前にstate.modeを自動的に"approach"へ戻す
(ボタンのactive表示も同期)。保存状態の復元時(`applySavedStateOverride`)にも同様の
サニタイズを追加し、伊丹以外+departureという組み合わせがlocalStorageの異常値として
残っていた場合にapproachへ読み替える保険を入れた。

**2. LDAは独立モードボタンではなくRWY欄の選択肢に変更**：従来の`state.mode==="lda"`という
独立モード(Modeセグメントに専用ボタン)を廃止し、既存のVOR 34L→16R / VOR A→16L方式と
同じ考え方で、RWYドロップダウンに「LDA 22」「LDA 23」という選択肢を追加する方式にした
(`setupAirport()`で`airport.ldaProcedures`のキーを列挙し`value="LDA"+key`のoptionを追加。
羽田専用データなのでldaProceduresが定義された空港でのみ選択肢が出る)。`render()`は
`state.mode`ではなくRWYドロップダウンの選択値そのもの(`rwyLabel_raw==="LDA22"/"LDA23"`)で
`renderLda()`への分岐を判定するよう変更(VOR方式の判定と同じ考え方)。Approach/Traffic
Pattern/DepartureのいずれかのModeボタンをクリックしたときにRWYがLDA22/23のままだと
表示と選択の食い違いが起きるため、対応する通常の滑走路(LDA22→22、LDA23→23)へ自動的に
戻すようにした。設定パネルの3系統の入力欄(通常Approach/Pattern用・Departure専用・LDA専用)
の表示切替をすべて`updateModeFieldsVisibility()`という1つの関数に集約し、
`isLdaRwySelected()`(RWY欄の値を見るだけの小さなヘルパー)でLDA判定する形に整理した
(以前はstate.mode==="lda"のチェックが複数箇所に散らばっていた)。保存状態の復元時、
build175以前の保存データに残っている可能性がある古い`mode:"lda"`値は"approach"へ
読み替えるサニタイズを追加。

**3. LDAは常にNorth up、中心はMAPとRWYの中間点**：`renderLda()`内の回転設定を
`rotationDeg = -finalCourseTrueDeg`(着陸滑走方向を上にする、他モードと同じ考え方)から
`rotationDeg = 0`固定に変更(counterRotも同様に0固定。地図を回転させないのでラベルの
向きを打ち消す必要がなくなったため)。パン中心も、従来は経路全体(描画開始点・MAP・
旋回・Aiming Point・両THR)を囲むバウンディングボックスの中心だったが、ユーザー指定により
`(mapXY + thrXY) / 2`(Missed Approach Pointと着陸滑走路THRの単純な中間点)に変更した。
コンパスラベルの表記も、他モードのような「RWY xx ↑ ddd°M」(矢印が磁方位を指す表記)は
North up固定では意味が変わってしまうため、「N ↑ ／ RWY xx (LDA W) ddd°M」(北が上である
ことを明示し、滑走路磁方位は参考値として併記)に変更した。

**検証**：Playwrightで(1)羽田・デフォルト表示でDepartureボタンがhiddenであること、
RWY欄にLDA22/LDA23が含まれること、(2)LDA22選択時にldaFields表示・approachPatternFields
非表示・rotwrapのtransformが`rotate(0deg)`になること、(3)Traffic Patternモードへ切り替えると
RWYが自動的に"22"(プレーンな滑走路)へ戻りldaFieldsが隠れること、(4)空港を伊丹へ切り替えると
Departureボタンが表示され、Departureモード選択でRWYが自動的に32Lへ切り替わること、
(5)伊丹のRWY欄にはLDA選択肢が含まれないこと(ldaProceduresが定義されていないため)、
(6)伊丹からLDA選択肢のある羽田へ戻すとDepartureモードが自動的にApproachへ戻り
Departureボタンが再びhiddenになることを確認。さらに、全9空港×全RWY選択肢×表示可能な
各Mode(合計70通り)の組み合わせでreadoutパネルが空にならずpage errorも出ないことを
回帰確認。LDA22/LDA23それぞれについて、実際のパン中心座標(`rotwrap.style.left/top`)が
MAP座標とTHR座標から計算した理論値と完全一致すること(丸め誤差の範囲内)も個別に検証。

### 8.0B1 build 177：LDAの2つの不具合を修正（Aiming Pointの方向誤りと、RWY切替時のTurn Start DME残留）

ユーザーからLDA機能(build176)使用後の2件の不具合報告に対応。

**不具合1「LDA22でFinalにのってからのコースがRWY HDGとちょっとズレてる」**：
原因は`renderLda()`内の`aimingPointXY`(滑走路面上のAiming Pointの座標)算出で、
本来は実際の滑走路方向(`finalCourseTrueDeg`、両THRの実測座標から計算)に沿って
THRから測るべきところを、誤ってLDA Final Apch Crs(277°M、`ldaCourseTrueDeg`由来の
`fwd`ベクトル)の方向を流用していたこと。この2方向は滑走路によって約47〜54°も
異なるため、Aiming Point自体が真の滑走路延長線から数百m横にずれた位置に置かれて
しまい、旋回終了点(実際の滑走路延長線上、ここは正しく計算されていた)からAiming
Pointまでの最終区間で経路が折れ、「Finalに乗ってからコースが少しズレる」ように
見えていた。診断用Playwrightスクリプトで`renderLda()`内部の関数を直接評価し、
旋回自体のheading/位置誤差はごく僅か(1e-7°/1e-9m)である一方、Aiming Pointの
横方向誤差(cross-track)が数百mある実測値を確認して原因を特定。**修正**：
実際の滑走路方向の単位ベクトル`rwyFwd`(`finalCourseTrueDeg`から算出)を新設し、
`aimingPointXY`の計算をこの`rwyFwd`基準に変更。修正後はLDA22/LDA23とも
Aiming Pointのcross-track誤差が0mになることを確認。

**不具合2「LDA23はMAPから左旋回で23のFinalに乗ればいい、Bank23°くらいのはず」**：
原因はRWY欄をLDA22→LDA23に切り替えても`#ldaTurnStartDme`(旋回開始点のDME距離
入力欄)の値がLDA22用の値("1.1"、RWY22のMAP DME)のまま残ってしまい、RWY23の
実際のMAP(D4.9)とは全く異なる、MAPよりずっと滑走路寄りの地点から旋回を開始する
計算になっていたこと。これによりMAP通過後に既に必要以上に滑走路へ接近した状態
から急旋回でFinalに合わせる必要が生じ、Bank角を1°〜75°の範囲で二分探索する
`perpAt(bankDeg)`関数が解に収束せず(全域で同符号=交差点なし)、範囲の境界値
(≒75°)に張り付く形で異常に大きいBank角が算出されていた。診断スクリプトで
bank1〜75を2°刻みでスキャンし、全区間で符号が変化していないことを確認して
原因を特定。**修正**：RWY欄変更ハンドラのLDA分岐で、選択されたRWYに対応する
`ldaProcedures[label].mapDmeNm`の値を`#ldaTurnStartDme`へ都度書き戻すようにし、
「旋回はMAPちょうどから開始する」という既定動作をRWY切替のたびに再適用するよう
修正。修正後、LDA23のBank角は約6.9°(左旋回・旋回角46.6°)に収束し、
heading/位置誤差はほぼ0となることを確認。同様にLDA22は約6.4°(左旋回・
旋回角54.1°)。

**要確認事項（ユーザーへ）**：算出されたBank角(LDA22で約6.4°、LDA23で約6.9°)は、
ユーザーが見積もっていた「Bank23°くらい」よりかなり浅い値になっている。旋回開始点
(MAP)と着陸滑走路延長線との位置関係・旋回角度から計算上はこの値で幾何学的に
矛盾なく閉じている(cross-track/heading誤差ともほぼ0)ことは確認済みだが、実際の
IKL/ITL航法援助施設の位置関係やAIP上のチャート表記と比べて違和感がないか、
ユーザー自身でのご確認をお願いしたい。

**検証**：`node --check`による構文チェック、9空港×全RWY選択肢×表示可能Mode
(70通り)の回帰確認(readoutが空にならない・page errorが出ない)、LDA22/LDA23
それぞれについてAiming Pointのcross-track誤差が0mであること、Turn Start DME欄が
RWY切替のたびに正しい値(LDA22→1.1、LDA23→4.9)にリセットされること、Bank角の
二分探索が[1°,75°]の範囲内でスキャン全域にわたる符号変化を伴って正しく収束する
ことを、いずれもPlaywrightで`renderLda()`内部関数を直接評価する診断スクリプトに
より数値的に確認。

### 8.0B2 build 178：LDAのNorth up表示に3Dドラッグの残留回転が混入する不具合を修正、3D表示(地形量が多いとき)のドラッグ操作のもたつきを軽減

ユーザー報告2件に対応。

**1. 「LDA north upになってないよ。south upになってるよ」**：
原因は`renderLda()`の回転設定`rotwrap.style.transform`が、`rotationDeg`自体は
North up用に`0`固定にしていたものの、実際に適用する角度は他モードと同じ式
`rotate(rotationDeg + state.headingOffsetDeg)`のままだったこと。
`state.headingOffsetDeg`は本来、3D表示(Approach/Pattern/Departureモード)で
1本指ドラッグにより視点方位を回した際の残留オフセット値で、`localStorage`に
永続化されるため、3D表示でドラッグ操作をした後にLDAへ切り替えると、その
ドラッグ残留角度がNorth up表示にそのまま混入し、コンパス上「North up」の
はずが実際には別の向き(今回はSouth up)で表示されてしまっていた。LDAは
そもそも3D非対応(tiltは常に0)にもかかわらず、3D専用のドラッグ回転値を
律儀に引き継いでしまっていたのが原因。**修正**：LDAの回転適用を
`rotwrap.style.transform = "rotate(0deg)"`に変更し、`state.headingOffsetDeg`を
一切参照しないようにした。これによりLDAは他モードでの3D操作履歴に関わらず、
常に真のNorth upで表示される。診断のため`state.headingOffsetDeg`を意図的に
173.4°など任意の値にセットしてからLDAへ切り替えるテストを行い、修正前は
その値がそのまま透過して反映される(=North upが崩れる)ことを確認、修正後は
常に`rotate(0deg)`になることをPlaywrightで確認。

**2. 「地形表示の量が多いと3D表示が結構重くてカクカクする」**：
原因は、3D表示中の1本指ドラッグ(視点方位・傾斜の変更)が`pointermove`イベントの
たびに`render()`を同期的に直接呼び出していたこと。`render()`は毎回SVGの中身を
全部作り直しており、地形の等高帯表示(`showTerrain`、最大80段階×輪郭多数、
山がちな空港ほど輪郭の本数・頂点数が増える)を含む重い描画を、指を動かすたびに
(場合によっては1フレームの間に何十回も発火する`pointermove`の回数ぶん)
繰り返し実行してしまっており、地形データの多い空港ほど1回あたりのrender()
コストが上がってドラッグ操作全体がカクつく原因になっていた。**修正**：
ドラッグ中の`render()`呼び出しを`requestAnimationFrame`で間引く
`requestDragRender()`という薄いラッパーを新設し、1本指ドラッグの`pointermove`
ハンドラ内の直接`render()`呼び出しをこれに置き換えた。取りこぼした
中間のイベントの入力値(方位・傾斜)は`state`に反映済みなので、次の描画フレームで
最新の状態に追いつく(見た目上の劣化はない)。**検証**：熊本空港(地形が
比較的大きい空港)で3D+地形表示をONにした状態で、40回分の`pointermove`を
1フレーム分の待ち時間を空けずに連続発火させるテストを実施。修正前相当の
直接呼び出し方式では40回のイベントすべてがそのまま`render()`を実行してしまう
(1回あたり実測約14ms×40回=最大500ms超のメインスレッド占有になり得る)のに対し、
修正後は同じ40回のイベント発火に対して実際に実行される`render()`は1回のみに
収束することを確認(`window.render`をラップしたカウンタで実測)。加えて
9空港×全RWY×全Modeの70通り回帰確認、LDA関連の既存検証(build176/177)も
すべて再実行し、いずれも異常なし。

### 8.0B3 build 179：LDA Turn Start DMEを小さくすると必要Bank角が実際は存在するのに「見つかりません」エラーになる不具合を修正

ユーザー報告「LDA22の旋回開始DMEを0.5以下にするとエラーになる。22°くらいで曲がれるはずなのに」に対応。

**原因**：`renderLda()`のBank角逆算(旋回終了点が実測Final線にちょうど乗るBankを
探す部分)は、`perp(bank)`(そのBankで旋回した場合の、旋回終了点からFinal延長線
までの横ずれ量)という関数の符号が、探索範囲の両端(Bank=1°と75°)で異なっていれば
その間に解があるはず、という前提で二分法を回していた。ところが`perp(bank)`は
Roll-in/Roll-outを含む数値積分の結果であり、単調に変化するとは限らない。
実際に診断したところ、Turn Start DMEを1.1(既定)から0.5・0.3・0.1と小さくして
いくと、Bankを1°から上げていく過程で一度「符号が変わって解にたどり着く」
(例: D0.5では約10.6°、D0.1では約20.3°で解に到達)ものの、その後Bankをさらに
上げていくと再び符号が反転して75°時点では最初の1°時点と同じ符号に戻って
しまう(=関数が1°→解→75°の間で一往復している)ケースがあることが判明。
従来のロジックは「1°と75°の符号が同じ」というだけで機械的に「解なし」と
判定していたため、区間の途中に実在する解(ユーザーの言う22°付近の妥当な
Bank角)を見逃してエラー表示してしまっていた。

**修正**：Bank角の探索を、境界2点だけを見る方式から、[1°,75°]を1°刻みで
走査して符号が変わる区間(ブラケット)を実際に探す方式に変更。見つかった
最初の(=最も浅い、旋回として物理的に自然な)区間だけを二分法にかけて精密化する。
どこにもブラケットが見つからない場合のみ、本当に解なしとしてエラー表示する
(この分岐自体は残しているので、本当に幾何学的に不可能な設定では引き続き
エラーが出る)。

**検証**：LDA22でTurn Start DMEを1.1/0.9/0.5/0.3/0.1と変えながら、修正前は
D0.5以下でエラー表示になっていたことを確認した上で、修正後は同じ設定で
エラーが一切出ず、Bank角がD1.1→6.4°、D0.9→7.4°、D0.5→10.6°、D0.3→13.7°、
D0.1→20.3°と、DMEを小さくする(=旋回に使える距離が短くなる)ほど単調に
大きくなる、物理的に妥当な値へ収束することをPlaywrightで確認(D0.1でのユーザーの
「22°くらい」という見立てにも近い値になっている)。加えてLDA22/LDA23の
既存検証(build176〜178のcross-track誤差・North up・3Dドラッグ間引き)と
9空港×全RWY×全Modeの70通り回帰確認もすべて再実行し、いずれも異常なし。

### 8.0B4 build 180：3D地形表示の帯を斜面としてつながって見えるように連結線を追加、伊丹E2A(中国道)ラインをラベルなし・太め緑実線に変更

ユーザー要望2点に対応。

**1. 「地形表示なんだけど、今は面が重なってるだけだから斜面を線で繋げて連続的にできたりする？」**：
3D表示の地形(等高帯)は、各標高レベルごとに水平な「天面」ポリゴンと、
1段下のレベルまでの「側壁」ポリゴンを積み重ねて描く方式(build 141〜)。
側壁自体は塗りで繋いでいたが、隣接する帯が同じ色(Downwind高度との相対関係で
3色に丸めているため、近い標高の帯どうしは同色になりやすい)になると段差の
境目が塗りに埋もれ、見た目には「水平な板が単に積み重なっている」ように
見えてしまっていた。**修正**：`demBandPaths()`が返す帯の輪郭点を間引いた
サンプル点ごとに、その帯の天面(現在の標高)から1段下の標高まで結ぶ短い
連結線(タイライン)を追加で生成するようにし(`ticks`という新しいpathデータ)、
render側でこれを明るいニュートラル色(既存の3D用「地面の骨格線」色
`--groundline`。経路の接地線などで使っているのと同じ)で細く描画するようにした。
輪郭の頂点密度に依らず概ね一定本数(1輪郭あたり約24本)になるよう間引き幅を
自動調整している。結果として、地形の起伏に沿って短い斜めの連結線が
無数に散らばって見えるようになり(等高線図の"ハッチング"のような効果)、
単なる水平面の重なりではなく連続した斜面として繋がっていることが
視覚的に分かるようになった。

**2. 「中国道のラベルはなくていいけど緑の太めの実線で見えやすくして」**：
伊丹RWY32L Departureモードの地図に表示している中国自動車道(E2A、build171〜173)の
目安ラインについて、ラベル("E2A 中国道(目安)"というマーカー付きテキスト)を
削除し、線のスタイルを細め・破線・グレー(`var(--textdim)`, 幅2, 破線)から
太め・実線・緑(`var(--green)`, 幅4)に変更した。

**検証**：`node --check`構文チェック、9空港×全RWY×全Modeの70通り回帰確認、
build176〜179で行った既存のLDA関連検証(cross-track誤差・North up・3Dドラッグ
間引き・Bank角のブラケット探索)をすべて再実行しいずれも異常なし。地形の
連結線追加によるrender()コストの増加は熊本空港3D表示で約14.8ms→約17.4msに
とどまり、build178で導入したrequestAnimationFrameによるドラッグ中の間引きの
効果内に収まることを確認。熊本空港での3D地形表示のスクリーンショットを
拡大して連結線が実際に描画されていることを目視確認、伊丹RWY32L Departureの
E2Aラインがラベルなしの太い緑実線になっていることもスクリーンショットで確認。

### 8.0B5 build 181：LDAの高度計算が単位変換漏れで実際の約1/3.28の値になっていた不具合を修正

ユーザー報告「LDAの高度計算おかしくない？？」に対応。

**原因**：`renderLda()`内でMAP通過高度・Turn Start高度・on Final(旋回終了点)高度・
描画開始点高度を求める式(`mapCrossAFE`/`rollOutAFE`/`drawStartAFE`/Turn Startの
readout内計算)が、いずれも`distMapToTurnStart`・`turnArcLenM`・`distTurnEndToAiming`・
`distDrawStartToMap`(＝いずれもメートル単位、`toXY`や`NM`定数由来)にそのまま
降下角のtan値(`tanDesc`)を掛けていた。しかしAFE(ft)を距離×tan(降下角)で求める
場合、距離はフィート単位でなければならず、実際には他の高度計算(`computePattern`の
`altAtDist`など)ではすべて`distM/0.3048`でフィートに変換してからtanを掛けている。
LDAのこの部分だけメートルからフィートへの変換(`/0.3048`)が漏れており、算出される
AFEがすべて実際の値の約1/3.28(≒1メートルが何フィートかの比の逆数)になって
しまっていた。例えばLDA22既定値(Turn Start=MAP)でのMAP通過高度は、修正前は
412ft AFE(430ft MSL)と表示されていたが、これは本来1353ft AFE(1370ft MSL)程度で
あるべき値だった。着陸間際の"on Final"高度など、値が小さく違和感が出にくい
項目もあったため見過ごされていた可能性がある。

**修正**：`mapCrossAFE`・`rollOutAFE`・`drawStartAFE`・Turn Start高度の各計算式に
`/0.3048`(メートル→フィート変換)を追加。

**検証**：修正後、LDA22既定値でMAP通過高度が430ft MSL→1,370ft MSL、on Final(旋回
終了点)が140ft MSL→410ft MSL、描画開始点が1,360ft MSL→4,420ft MSLに変化し、
LDA23も同様に修正されたことをPlaywrightで確認。値そのものが実際の進入手順として
物理的に妥当な範囲(MAP通過が1000ft台、旋回終了が数百ft)になったことを確認。
加えて、この修正はAFEの値だけに影響し、旋回のBank角・位置・cross-track誤差の
計算には影響しないことを確認するため、build176〜180で行った既存のLDA関連検証
(cross-track誤差0m・North up・Bank角のブラケット探索・3Dドラッグ間引き)と
9空港×全RWY×全Modeの70通り回帰確認もすべて再実行し、いずれも異常なし。

### 8.0B6 build 182：3D地形表示を「上下の面が同じ色でつながる」連続した斜面に修正（build180の連結線方式を撤回）

ユーザー報告「地形表示はそういうことじゃなくて上下の面の端を同じ色で繋げて連続的に
（まさに山のようになるように）表示できないかなってこと」に対応。これはbuild180
（8.0B4）で実装した「間引き連結線(タイライン)」方式が意図と違う、という明示的な
訂正指摘。

**build180方式のどこが違っていたか**：ユーザーの要望は「上下の面の端」＝側壁(wall)を
天面(top)と**同じ色でつなげる**ことで山のように見せたい、というものだった。しかし
build180では、側壁は天面より暗く（`op*0.62`、`stroke:"none"`）塗ったままにし、
その代わりに天面から側壁下端へ向けて短い線（ティック/ハッチング状の連結線）を
別途重ねて描く方式を取っていた。これは「上下の面を同じ色でつなげる」という要望とは
別の（見た目もかなり違う）アプローチであり、ユーザーに再指摘された。

**修正**：`render()`内の地形帯描画ループで、
1. build180で追加した`paths.ticks`（連結線）の描画を完全に削除。
2. 側壁(wall)の`fill-opacity`を天面(top)と同じ`op`に変更（従来`op*0.62`で暗かった）。
3. 側壁に天面と同じ色・同じ濃さのストローク(`stroke: col, stroke-opacity: op,
   stroke-width: 0.8`)を追加。
4. 天面(top)のストロークも、従来の固定値(`stroke-opacity:0.45, stroke-width:0.6`、
   これが100ft刻みごとに必ず入る目立つ輪郭線になっていた)から、側壁と完全に同じ
   `stroke-opacity: op, stroke-width: 0.8`に変更。

この結果、同じ色カテゴリ（Downwind高度との相対関係で決まる赤/橙/緑の3色）に属する
帯どうしは、側壁と天面が完全に同じ色・同じ濃さで塗られるようになり、100ft刻みの
段差ごとの輪郭線・濃淡差が視覚的に消える。色カテゴリが変わらない範囲では、
継ぎ目のない一枚の連続した斜面（山）として見えるようになる。色カテゴリの境界
（赤/橙/緑の切り替わり）自体はDownwind高度との高低関係という意味のある情報なので、
そこだけは引き続き色の変化として残る。

なお、`demBandPaths()`内に残っていた（build180のティック線生成用の）`ticks`
生成コード自体も、使われなくなったため合わせて削除した。

**検証**：`node --check`で構文確認。熊本空港（山がちな地形）を3D表示にして
Playwrightでスクリーンショットを撮り、目視で段差の輪郭線・タイル状の継ぎ目が
解消され、同一色カテゴリ内は滑らかにつながった斜面として見えることを確認
（等高線に沿った濃淡の起伏自体は実際の地形の谷・尾根の形を反映したもので、
人工的な段差ではない）。9空港×全RWY×全Modeの70通り回帰確認、LDA関連の
既存検証（North up、Bank角ブラケット探索、小DME時の解、3Dドラッグ間引き）を
すべて再実行し、いずれも異常なし。

### 8.0B7 build 183：3D地形の側壁を垂直から斜面に変更、同じ色の帯の重なりで出る縞を解消

ユーザー要望「側壁は垂直じゃなくて、ちゃんと斜めにして上下を繋げる？」に対応。

**変更前**：側壁(wall)は、その帯の等高線を**同じ平面位置のまま**1段下の高さへ平行移動した
リボンだった(＝垂直な崖)。100ftごとに段々畑のような段差になっていた。

**変更1(斜面化)**：`buildDemBands()`で輪郭を作った後、輪郭上の各点から粗グリッド(双線形
補間)上を最急降下方向に1/4セルずつたどり、標高が1段下(prevFt)になる地点を求めて
`ring.bot`として保存(最大12セルで打ち切り、欠測・範囲外に当たったらそこで止める)。
`demBandPaths()`は側壁の下端を`ring.bot`(1段下の高さ)にする。側壁の下端が1段下の
等高線上に乗るので、側壁は上下の等高線を斜めに結ぶ面になる。計算は空港読み込み時に
1回だけ(実測 約50〜190ms)で、render()側のコストは変わらない。自己交差しても抜けない
よう、側壁のfill-ruleはevenoddからnonzeroに変更。

**変更2(縞の解消)**：半透明の帯を何十枚も重ねると、重なった枚数ぶん色が濃くなって
帯の外周ごとに縞(段)が見えていた(build182のスクリーンショットでも橙の縁に残っていた)。
同じ色カテゴリ(赤/橙/緑。高さに対して単調なので連続して並ぶ)の帯を1つの`<g>`に
まとめ、中身は不透明で塗って`<g>`自体に透明度(緑0.5/橙0.6/赤0.66)を掛けるように変更。
これでカテゴリ内は重なり枚数によらず濃さが一定になる。

**変更3(起伏の表現)**：ベタ一色だと山の形が読めないため、帯の色を高さで連続的に変える
(麓=基本色×0.7 → 山頂=白を45%混ぜた色、空港標高〜DEM最高点で線形)。帯1枚ごとの差は
数%以下なので段には見えず、麓から山頂へ明るくなるグラデーションとして見える。

**検証**：不透明化したテスト版で熊本を描画し、側壁と天面の間に隙間が無いこと(斜面が
連続していること)を確認。熊本・函館・広島の3D表示をスクリーンショットで目視確認。
70通り回帰、LDA関連検証、3Dドラッグ間引きすべて異常なし。
既知の見え方：DEMデータ範囲の端(地図の外周)で地形が切れる所は、たどる先が無いため
側壁が垂直のまま残る(データの端なので実害なし)。

### 8.0B8 build 184：LDAでTurn Start DMEが近すぎるとき、実際には成立しない深いBankを解として表示していた不具合を修正

ユーザー質問「LDA23で旋回開始を4.3から4.2にすると必要bankが18から29に急増するけどあってる？」
を調査。

**急増そのものは正しい挙動**：Roll rateは3°/s固定なので、Bankを深くするほどRoll-in/Roll-outに
かかる時間(と、その間に進む距離・旋回する角度)が増え、旋回半径が小さくなる効果を打ち消す。
LDA23(左46.6°旋回、TAS約155kt)で旋回終了点のFinal延長線からの横ずれをBankごとに計算すると、
Bank10°→20°では約500m動くのに、20°→30°では約100mしか動かない(頭打ち)。このため
Turn Startが限界に近づくほど、DME 0.05〜0.1NM(90〜185m)の差で必要Bankが大きく跳ねる
(実測: 4.3→14.4°, 4.2→18.2°, 4.15→21.5°, 4.1→29.4°。重量/OATで数値は多少ずれる)。

**見つかった不具合**：Bankが約32.5°を超えると、Roll-in＋Roll-outだけで旋回角46.6°を超えて
しまう(`computeVariableBankTurnLocal`の`clamped`。実際の旋回はBank37°で60°、41.5°で78°)。
この領域の軌跡は終点がたまたまFinal延長線上に乗っても機首方位がFinalと一致しない＝解ではないのに、
以前はそれを解として採用し、DME 4.0で36.6°、3.8で41.5°などを表示していた。

**修正**：Bank探索(1°刻み走査)でclampedになるBankが出た時点で打ち切り(境界は二分法で詰める)、
それより浅い範囲に解が無ければ「解なし」エラーを出す。エラー文には限界Bank(約33°)と、
Turn Start DMEを大きく(MAP寄りに)するよう案内を表示。LDA23では DME 4.1(29.4°)までが成立、
4.05以下はエラーになる。

**検証**：LDA23でDME 4.9〜3.8を走査しBank値・エラー表示を確認。LDA22の小DMEケース
(build179の検証)、LDA cross-track 0m、North up、70通り回帰すべて異常なし。

### 8.0B9 build 185：3D地形に陰影(ヒルシェード)を追加し、複数の山・尾根を見分けやすくした

ユーザー要望「地形表示をより実際に山のように表示するには？山が複数あると周りと色が重なって
分かりづらい」に対応。

**原因**：build183時点では、色はDownwind高度との関係で決まる3カテゴリ(赤/橙/緑)＋高さによる
わずかな明るさ変化だけで、同じカテゴリ内の隣り合う山は同じ色の塊としてつながって見えていた。

**修正**：実際の地図の陰影起伏図と同じ方式を追加(新関数`demBandShadedPaths()`、描画ループは
`demBandPaths()`の代わりにこれを使う。`demBandPaths()`自体は未使用だが残置)。
- 側壁(斜面)を「上の等高線の点ring[k]と、斜面を下った点ring.bot[k]」を結ぶ四角形の連続として
  扱い、四角形ごとに下り方向(アスペクト)と傾き(縦誇張3倍)から面の法線を求め、
  光(画面の左上から、高度角45°)との内積で明るさを計算。地図をどう回転しても光は常に画面左上
  (rotwrapの回転thetaTの逆回転でviewBox座標に変換)。
- 明るさは平面を基準に-1(陰)〜+1(日向)に正規化し、輪郭に沿って前後2点の移動平均で平滑化してから
  9段階(`SHADE_LEVELS`)に量子化。同じ段階が続く区間は1つの多角形にまとめて要素数を抑える
  (帯ごとに最大9パス＋天面1パス)。
- 色：日向は白寄り(最大55%)、陰は黒寄り(最大60%暗く)。高さによる明るさ変化は陰影と競合しない
  よう控えめ(基本色×0.85〜白30%)に変更。

**検証**：熊本/函館/広島を3D表示してスクリーンショットで確認。同じ緑の中でも個々の丘・尾根・谷が
明暗で分かれて見えることを確認(衛星画像非表示でも確認)。render()は約15ms→約20ms、3Dドラッグは
rAF間引きで1フレーム1回のまま。70通り回帰・LDA検証異常なし。

### 8.0B10 build 186：3D地形の描画をSVGからcanvasに変更して軽量化

ユーザー報告「地形が多いとやはり重い」に対応。

**原因**：render()のたびに地形だけで数十万文字のSVGパス文字列を組み立て(熊本で約60万文字)、
ブラウザがそれを解析・描画していた。さらに地形と無関係な入力(速度・重量など)を変えただけでも
毎回まるごと作り直していた。

**変更**(`drawTerrainCanvas()`、関連: `getTerrainCanvas()`/`hideTerrainCanvas()`/`terrCacheKey`)：
1. **canvas化**：地形はrotwrap内・SVGの直前(背面)に置いた<canvas>に、SVGと同じviewBox(0..1000)
   座標で描く。3Dで持ち上がった地形がはみ出す分は、持ち上げ方向の側だけ余白を取る。
   色カテゴリごとの透明度はオフスクリーンcanvas(terrLayer)に不透明で描いてからglobalAlphaで合成
   (build183の<g opacity>と同じ効果)。
2. **再描画の省略**：空港・表示サイズ・回転・傾き・Downwind高度・解像度から作るキーが前回と同じなら
   描き直さない。地形に関係ない入力変更時のrender()は約6ms。
3. **輪郭線(stroke)の廃止**：面の継ぎ目隠しのための同色ストロークが描画負荷の約7割を占めていた。
   代わりにカテゴリのレイヤーを自分自身に2回重ねて、アンチエイリアスで半透明になった境目だけを埋める。
4. **天面をドーナツ状に**：各帯の天面は「1段上の等高線の外側」だけ塗る(evenodd)。内側は上の帯で
   必ず覆われるため。山頂付近で数十枚重なっていた塗りを削減。
5. **ドラッグ中は半分の解像度**(`terrDragging`)。指を離したら通常解像度で描き直す。
6. **解像度**：devicePixelRatio(最大2)×ズーム倍率(最大2)。一辺最大3072px(iPadのメモリ配慮)。
   ピンチ/ホイールズームが止まって180ms後にその倍率で描き直す。表示サイズの変化(設定パネル開閉・
   画面回転)はResizeObserverで検知して描き直す(しないと引き伸ばされてぼやける)。
7. 陰影計算の「下り方向・勾配」を輪郭ごとにキャッシュ(ring.slope)。描画時は光との内積のみ。
不要になった`demBandPaths()`/`demBandShadedPaths()`は削除。

**計測**(ヘッドレスChromium・ソフトウェア描画・dpr2、実機はGPU描画なのでさらに軽いはず)：
地形の描き直し 約150ms→約90ms(ストローク廃止)、ドラッグ中はさらに約半分。地形に関係ない入力の
render()は約6ms。見た目はbuild185と同等であることをスクリーンショットで確認(継ぎ目なし)。
2D/地形OFF/Departure/LDAではcanvasが非表示になること、空港切替後の再生成、ドラッグ終了後の
通常解像度への復帰、70通り回帰・LDA検証すべて確認。

### 8.0B11 build 187：地形描画をWeb Workerへ移動、LDA中のMode選択を無効化、入力値リセットボタン、高度基準をTHR標高に変更

ユーザー要望(4件)：「重いのは変わらない」「LDAの時はMode選べない様にしていい」「数値をデフォルトに
するリセットボタン欲しい」「LDAとかも含めて着陸地点は空港標高じゃなくて滑走路標高で計算してる？」

**1. 地形描画のWorker化**：iPadのSafariではcanvasの塗りがCPUで行われ、build186でも地形の描き直し
1回に数十ms以上かかり、その間ドラッグ(指の追従)が止まっていた。塗り処理を外部変数に依存しない
純粋関数`terrainPaint()`に切り出し、そのソースを`Blob`URLのWeb Workerへ渡して別スレッドで実行
(`terrWorkerMain()`)。Workerは`OffscreenCanvas`に描いて`ImageBitmap`で返し、メインは`bitmaprenderer`
コンテキストで差し替えるだけ。要求は「最新の1件」だけ処理(溜まった古い要求は捨てる)。帯データは
空港ごとに1回だけFloat32Arrayに平たくして送る(`terrCompactBands()`)。古い結果(空港切替前・canvas
作り直し前)は世代番号(gen/cgen)で破棄。Worker/OffscreenCanvasが無い、またはセキュリティ設定で
Workerが1.5秒応答しない場合は自動でメインスレッド描画に切り替え(`terrFailWorker()`)。
計測(ヘッドレス)：ドラッグ中のメインスレッドのrender()は約4ms、フレームはほぼ60fps。地形はわずかに
遅れて追従する。

**2. LDA中のMode無効化**：`updateModeFieldsVisibility()`でRWYがLDA22/23の間はModeボタンを
disabled＋半透明、「LDA選択中はModeを変更できません」の注記を表示。回帰テスト
(verify_176_regress.js)は無効なボタンをクリックしないよう修正。

**3. 入力値リセットボタン**(設定パネルのMode直下、`resetInputsToDefaults()`)：空港・滑走路・Mode・
表示設定(3D/衛星画像/VSD/地形/傾き等)は維持し、機体・進入設定(state: acModel, appFlap, circDwFlap,
approach, side, cutAngle, baseMode, vorAltMSL, depBank)を起動時の既定値(`DEFAULT_STATE`)へ、
入力欄(PERSIST_FIELD_IDS)をHTMLの初期値へ戻したうえでsetupAirport()と滑走路のchange処理を
再実行(空港・滑走路ごとの既定＝Downwind高度・Downwind側・VOR強制値・LDAのTurn Start=MAPを適用)。
誤操作防止に2回押し方式(1回目で赤く「もう一度押すとリセット」、3秒で解除)。confirm()は埋め込み
表示で使えない場合があるため使わない。

**4. 高度基準をTHR標高に**：以前は全ての高度(3°パス・TCH・各点のMSL・VSD・3D持ち上げ)を空港標高
基準で計算しており、THR標高(AIP AD2.12、既にデータにあったが未使用)は使っていなかった。
例: 熊本RWY07はTHR 601ft(空港632ft)で、着陸点を31ft高く計算していた。render()で着陸RWYの
`refElevFt = thr.elevFt`を求め、Downwind MSL→内部高さの変換、各MSL表示、VSD、地形の持ち上げ、
気圧高度をこの基準に変更。Downwind Altitudeの注記は「(xxx ft AFE ／ THRyy上 zzz ft)」の併記。
LDAも`ldaRefElevFt`(THR22=35ft、THR23=54.7ft、空港21ft)でMSL表示。表示ラベル「AFE」は「THR上」へ。
Departure(伊丹32L)は従来どおり空港標高基準のまま(未変更)。Downwind高度の既定値(空港標高+既定AFE)も
従来どおり空港標高基準。

**検証**：Worker描画の見た目がbuild186と同じことをスクリーンショットで確認、Worker無効/無応答時の
フォールバック、2D/地形OFF/Departure/LDAでの非表示、LDA中のMode無効と解除、リセット(値・Flap・
Approach Type・RWY維持・速度再計算)、70通り回帰・LDA検証すべて異常なし。

### 8.0B12 build 188：3Dドラッグ中は描き直さずCSS変換だけにして滑らかにする

ユーザー報告「ドラッグしてもまだカクカク。前に描写回数を1回にしたのが影響してない？」

**描写回数(build178のrequestAnimationFrame間引き)は原因ではない**：あれは「1フレームに表示できるのは
1回だけなので、それ以上のrender()を捨てる」処理で、無ければもっと重くなる。

**本当の原因(推定)**：ドラッグ中もフレームごとにrender()で経路SVGを全部作り直していた。JSの計算は
数msでも、SVGが変わると、それを含む地図のレイヤー(142%サイズの衛星画像ごと)をブラウザが毎フレーム
ラスタ化し直す。iPad(Safari)ではこれが重い(ヘッドレス計測では見えないGPU/合成側のコスト)。

**修正**：
1. 3Dドラッグ中(pointermove)はrender()を呼ばず、`requestDragViewTransform()`でrotwrapのCSS transform
   (rotateX/rotate)だけを1フレーム1回更新。描いてある絵をGPUで回すだけになる。
2. 指が止まったら(120ms、`scheduleDragSettleRender()`)と、指を離したときにrender()で正しく描き直す。
   ドラッグ中は3Dで持ち上げた経路・地形の「持ち上げ方向」が一時的に古いまま(少し傾いて見える)。
3. `.rotwrap > img/svg/canvas` に `will-change: transform` を付け、衛星画像・地形・経路を別レイヤーに
   (経路SVGの描き直しで衛星画像まで描き直さない。回転・傾斜は合成だけ)。

**検証**：連続ドラッグ中のrender()呼び出し0回・transformは毎フレーム更新、停止120ms後と指を離した後に
描き直し、terrDragging解除を確認。70通り回帰・LDA・リセット・地形表示切替すべて異常なし。
(perf_3d_drag.jsの「render回数」はドラッグ中0回が正しい挙動になった)

### 8.0B13 build 189：地形の遅延描画(Worker)をやめる、空港切替時にDownwind Altitudeを既定値へ戻す

ユーザー報告「(build188で)ヌルヌルになった！これで地形描写遅らせるの無しにしてみて」
「downwind altが他の空港とかにいっても前の空港の値が残っちゃう」

**1. 地形のWorker描画を無効化**：build188でドラッグ中は描き直さない方式にしたため、Workerの利点
(描画中も指の追従を止めない)がなくなり、「経路より地形が少し遅れて出る」欠点だけが残っていた。
`TERR_USE_WORKER = false`でメインスレッド同期描画に戻した(Workerの仕組みは残置、trueで復活)。
あわせてドラッグ中の半分解像度もやめた(指を止めた時に半分→離した時に通常、と2回描いていたため)。

**2. Downwind Altitudeの空港切替リセット**：setupAirport()で、以前は熊本など既定値の上書き
(DOWNWIND_ALT_AFE_OVERRIDE)がある空港が絡む時だけDownwind Altitudeを入れ直していたため、
それ以外の空港間では前の空港のMSL値が残っていた。空港切替時は常に`defaultDownwindAltMSL()`
(空港標高+既定AFE、100ft丸め)を入れる。起動時の保存状態復元は、この後に保存値で上書きされるので影響なし
(再読み込みで保存値が戻ることを確認)。

**検証**：羽田→熊本→広島→福岡→広島→羽田と切り替え、毎回その空港の既定値(1500/2400/2600/1500…)に
なることを確認(各空港で3000に変えてから切替)。地形がメインスレッドで経路と同時に描かれること、
ドラッグ挙動(ドラッグ中render 0回、停止・離した時に描き直し)、70通り回帰・LDA・リセットすべて異常なし。

### 8.0B14 build 190：3Dドラッグ中もリアルタイムで描き直す方式に戻す

ユーザー要望「前と同じようにリアルタイムで表示される(ドラッグ中も描画)ように戻して」。

build188の「ドラッグ中はCSS変換だけ回し、止めた/離した時に描き直す」方式を、定数`DRAG_LIVE_RENDER`
(true)で切り替え可能にし、既定をtrue＝ドラッグ中も毎フレーム(rAFで1フレーム1回)render()する方式に
戻した。3Dで持ち上げた経路・地形の持ち上げ方向がドラッグ中も常に正しい。
- build188のレイヤー分離(`.rotwrap > img/svg/canvas { will-change: transform }`)は残す
  (経路SVGの描き直しで衛星画像まで描き直さない)ので、build187以前よりは軽いはず。
- 地形はドラッグ中のみ半分解像度(build186と同じ)、指を離したら通常解像度で描き直し。
- falseにすればbuild188方式(ヌルヌル優先・持ち上げ方向は止めるまで古いまま)に戻せる。
- 地形はbuild189のまま同期描画(Workerなし)。

**検証**：連続ドラッグ中にrender()が毎フレーム呼ばれること、ドラッグ中の地形canvas幅が半分・
離すと通常に戻ること、70通り回帰・LDA・Downwind Altitudeの空港切替すべて異常なし。

### 8.0B15 build 191：3Dドラッグ中は経路だけ毎フレーム描き直し、地形は止めてから描き直す

ユーザー報告「(build190は)やっぱりカクカク。戻そう。ただ経路はリアルタイムで描ける？経路も遅れて
表示すると変な線になっちゃう」

**方式**：ドラッグ中(pointermove)は毎フレームrender()するが、`terrFreeze=true`の間は
`drawTerrainCanvas()`が地形を描き直さず、既存の地形canvasをそのまま表示する(rotwrapのCSS変換で
地図と一緒に回転・傾斜)。経路SVGは毎フレーム正しい持ち上げ方向で描き直される。指が止まって120ms
(`scheduleDragSettleRender()`でterrFreeze=false→render)と指を離した時に地形を描き直す。
ドラッグ中の地形半分解像度は廃止(ドラッグ中は描かないため不要)。
地形は描き直しを待つ間だけ持ち上げ方向が少し古いまま(止めると直る)。

**計測**：地形凍結中のrender()は約5ms(ヘッドレス)。連続ドラッグ30フレームでrender 30回・地形描画0回、
停止後に地形1回描画、離した後は同じ条件なのでキャッシュで描き直しなしを確認。70通り回帰等異常なし。

### 8.0B16 build 192：空港・滑走路を変えたら3D表示をOFFに戻す

ユーザー要望「滑走路や空港変えた場合は3D非表示に戻して」。

`resetTo2DView()`を追加(show3D=false、tiltDeg=0、headingOffsetDeg=0、3Dトグルボタンの表示同期)。
空港選択(airportSel change)と滑走路選択(rwyEnd change、LDA/VOR含む)の最初で呼ぶ。
入力値リセットボタンは内部で滑走路のchange処理を再実行するため、表示設定は維持する約束どおり
3Dの状態(show3D/tilt/heading)を退避して最後に戻すよう修正。起動時の保存状態復元は影響なし
(setupAirport()自体では3Dを変えない)。

**検証**：熊本で3D ON→RWY25に変更でOFF、再度ON→広島に変更でOFF、ON→リセットでONのまま、
70通り回帰・LDA・Downwind Altitude切替すべて異常なし。

### 8.0B17 build 193：描画範囲を衛星画像の範囲内に限定、拡大時に文字・航跡がぼやける問題を修正

ユーザー要望「描画範囲は衛星画像があるところまででいい」「拡大すると衛星画像がぼやけるのは分かるけど
文字や航跡までぼやけるのはなぜ？」

**1. 描画範囲**：
- `rayExitDistM(originXY, dir)`を追加(ある点からある方向に進んで衛星画像の範囲の端に達する距離)。
- LDAの描画開始点：以前は`boundsMaxRadiusM`(画像の四隅までの最大距離)をMAPからの距離に使っていたため
  画像の外まで線が伸びていた → MAPからLDAコースを逆にたどって画像の端に達する点に変更
  (readoutの「描画開始点」高度もこの点の値に。LDA22で2,640ft MSL)。
- Visual Downwindの画面端までの延長も同様に画像の端まで。
- 経路SVGを`overflow:hidden`にし、画像範囲(viewBox 0..1000)の外は描かない(3Dで持ち上げた線も
  画像の外にはみ出す部分は切る)。地形canvasは従来どおり(はみ出し分も表示)。

**2. 拡大時のぼやけの原因と修正**：地図の拡大はCSSのscale(zoomPanWrap)で行っている。
(a) `.zoomPanWrap`の`transform-style:preserve-3d`と、2D表示でも付いていた`rotateX(0deg)`(3D変換扱い)
により、2D表示でも地図全体が3D合成レイヤーとして「拡大前の解像度の絵」を引き伸ばしていた。
(b) build188で付けた`will-change:transform`(レイヤー分離)も、ブラウザがそのレイヤーを最初の倍率で
描いた絵のまま持ち続ける原因になっていた。
修正：2D表示ではrotwrapに`rotate()`だけを使い、preserve-3dは3D表示中だけ(`.mapwrap.is3d`)。
will-changeは指で操作している間だけ(`.mapwrap.interacting`、指を離して/ホイール停止0.2秒後に外す)。
これで2Dでは拡大後に文字・航跡が今の倍率で描き直されてくっきりする。3D表示中の拡大はブラウザが
3D変換レイヤーを描き直さない場合があり、多少ぼやける可能性は残る。

**検証**：LDA22の線が画像の端で止まること、3D/2Dでのクラス・transform、2Dでホイール3.9倍拡大後に
文字がくっきり描かれることをスクリーンショットで確認。ドラッグ挙動(経路毎フレーム・地形凍結)、
70通り回帰・LDA・3D OFF戻しすべて異常なし。

### 8.0B18 build 194：iPad用の数字キーパッドを追加

ユーザー要望「iPadのキーボードが小数点が打ちにくい。数字と小数点を入れる専用のキーボードにできる？」

iPadのソフトウェアキーボードにはテンキー配列が無い(inputmode="decimal"でも記号ページが出るだけ)ため、
アプリ内に専用キーパッド(`#numpad`、`setupNumpad()`)を実装した。
- タッチ端末(`matchMedia("(pointer: coarse)")`)のみ有効。PCは従来どおり。
- 設定パネル内の数値欄(type=number)とWind欄を`type="text"`+`inputmode="none"`に切り替え、システムの
  キーボードを出さずにキーパッドを表示(type=numberのままだと「12.」のような入力途中の値を受け付けない)。
- 配列: 7 8 9 ⌫ / 4 5 6 C / 1 2 3 ± / . 0 / 完了。「±」はOATのみ、「/」はWindのみ有効。
- 欄をタップした直後の最初の数字は元の値を置き換える(電卓と同じ)。1キーごとにinputイベントを出すので
  計算はリアルタイム更新。完了・キーパッド外タップ・設定パネルを閉じる・別の欄へ移るでchange(確定)。
- キーパッドは設定パネルの下端に重なるので、表示中はパネル下に余白(`.controls.np-open`)を取り、
  タップした欄を中央へスクロール。
- 関連修正：Downwind Altitudeは入力中(フォーカス中/キーパッド編集中)に100ft丸めを書き戻さない
  (「2」と打った瞬間に「0」に書き換わって続きが打てなかった)。欄を離れた/確定した時に丸める。

**検証**：iPad Pro 11エミュレーション(タッチ)で、Offset「1.8」、OAT「±5」→-5、Downwind Altitude
「2540」→完了で2500に丸め、Wind「010/15」を入力できること、キーパッドの表示/非表示を確認。
PC表示・70通り回帰・リセット・Downwind Altitude切替異常なし。

### 8.0B19 build 195：VSDを廃止、数字キーパッドを計算結果ボードの位置に表示

ユーザー要望「VSD使わないから廃止していい」「下の計算結果が表示されるボードの部分がキーボードに
変わるといいかも。今の仕様だと入力部分がキーボードで潰されて見にくい」

**1. VSD廃止**：VSDパネル(#vsdPanel)・表示切替(#showVSDToggle)・`renderVSD()`・関連CSS・
state.showVSD(保存キー含む)を削除。Departure/LDAで行っていたVSDのクリア処理も削除。
(経路まわりの最高標高の計算`demProfileAlongPath`はreadoutで使うので残置)

**2. キーパッドの位置**：build194では設定パネル下端に重ねて表示していたため、入力欄が隠れていた。
キーパッド(#numpad)をsideCol内のreadoutの直後に移動し、表示中は`.sideCol.np-active`でreadoutを
隠して同じ場所にキーパッドを出す。縦持ちでは地図の下、横持ちでは右カラムに出る。
ボタンを大きく(高さ54px)、最大幅520px中央寄せ。操作仕様(置き換え入力・±/・完了など)は変更なし。

**検証**：iPad Pro 11(縦・横)エミュレーションでキーパッドがreadoutの位置に出て入力欄が隠れないこと、
入力動作(1.8 / -5 / 2540→2500 / 010/15)、70通り回帰・LDA・リセット・地形表示・ドラッグ挙動異常なし。

### 8.0B20 build 196：伊丹Departureに風を追加、「E2A 中国道」ラベルを重ならない位置に復活

ユーザー要望「伊丹のDeparture modeも風を入れられるようにして。離陸直後から風の影響を受けるように。
風は高度変わっても一定でいい」「やっぱり中国道のラベルはほしい。ほかの補助線とかに重ならない位置で」

**1. Wind欄の共通化**：#wind欄をapproachPatternFieldsの外(OATの直下、`#windField`)へ移動。
Approach/Pattern/Departureで表示、LDA選択中のみ非表示(LDAは風非対応のまま)。Departure中は
「離陸開始点から高度によらず一定の風」のヒント(#windDepHint)を出す。入力解釈は`readWindInput()`、
左下の風インジケータは`updateWindIndicator()`に共通化(render()側の挙動は従来と同じ)。

**2. simulateItamiDepartureの風モデル**(opts に windSpeedKt / windFromTrueDeg 追加、MAG VARは滑走路の値)：
- 離陸開始点(t=0)から地上位置 = TASベクトル＋風ベクトル(一定)で積分。
- straight1：滑走路方位のHDGを維持(クラブ補正なし＝横風で流される)。
- turn：旋回率は従来どおり対気(bank/TAS)。**旋回終了TRKは地上軌跡(TRK)で判定**(HDG基準で連続化)。
- straight2：`wcaHeadingForTrack`で指定TRKを維持(加速でTASが変わるので毎ステップ再計算)。
- 各点に gsKt / trackDeg を追加。readoutに「風(高度によらず一定)」行(向かい/追い風・左右横風成分)と
  各通過点のGSを追加。無風時は従来と同一結果。

**3. 「E2A 中国道」ラベル**(`placeE2ALabel` / `drawE2ALabelAt`)：build180で消したラベルを復活。
SVG座標で、E2A線上の点ごとに上下左右＋斜め4方向×隙間3段(4/9/16)の候補矩形(counterRot回転)を作り、
障害物(ISK 2.8/3.6・ITE 2.2・Turn Start円、経路、滑走路、各マーカー＋その文字、E2A線自体)からの
離れが最大(12で頭打ち)・線に近い・中央寄りの候補を採用。斜め候補がないと斜めに走るE2A線自体が
横長ラベルに被って置き場所が無くなる点に注意。E2A外接矩形から遠い障害物は事前に除外し、
結果は入力(障害物点列のハッシュ)でキャッシュ(初回約11ms、以降0.3ms)。緑・太字・黒縁取り。

**検証**：無風・320/20・140/20・050/20・230/20でGS・TRK180到達・ITE2.2NM到達が物理的に妥当、
Turn Start 1.0/1.8/2.5NMでもラベルが補助線に重ならないことをスクリーンショットで確認。
LDAでWind欄非表示、70通り回帰・キーパッド(Wind 010/15)・LDA・リセット・地形表示の各チェック異常なし。

### 8.0B21 build 197：精査で見つかった計算の不整合3件を修正＋Departure旋回終了TRKを磁方位に

精査結果は `claude/audit_build196.md`(プロジェクト)。ユーザー指示「3つ直して」「TRK180はMagnetic」。

**1. Entry(Cut angle S字/VOR方式)の速度**：速度プロファイル(tasProfileFn)は「THRからの経路長」で
TASを返すのに、Entry側はTHRからの直線距離(Math.hypot)を渡していたため、Abeamより遥か上流でも
減速後のTASで旋回を計算していた。`makeDownwindPathDistFn(pat, connectXY, connArcM)`
(旋回開始点の弧長＋Downwind上流方向の沿軸距離＋Downwind線からの横距離)を追加し、
computeCutAngleEntryFrom / computeEntryProcedure / computeVOR3416RProcedure / computeVORA16LProcedure
に distFn 引数で渡す。34L Visual Cut45°：Turn1→Turn2 81s→67s。Visual距離基準(Cut NIL)の結果は不変。
(同種の軽微な近似: Visual/CirclingのBase/Final旋回で`baseTurnDistNM+shiftT`(L字距離)を使っている箇所は未修正)

**2. 時間基準Base距離の探索**：measureSecForが減速なしの経路(Pass1相当)で測っていたため、
描画経路と旋回開始点が0.39NMずれていた(表示Abeam+35sに対し実際44s)。本描画と同じ手順
(Pass1→`makeDecelProfile(abeam弧長)`→Pass2)で作った経路上で測るように変更。減速プロファイルは
`makeDecelProfile`として探索と本描画で共通化。時間は符号付き(Abeamより手前=負)にして単調性を回復
(旧版は絶対値で、旋回開始がAbeam手前になると解なし誤判定→黙って初期推定)。解が無いときは
`timeBaseUnreachable`でreadoutに「(Abeam+Xsは設定不能・推定値)」と表示。二分探索30→18回。
結果は入力キーで`timeBaseSolveCache`(Map、60件で全消去)にメモ。VOR A→16LのAOMI三分探索も同じMapでメモ。
→ Circling/VOR方式のrenderは初回約90ms、同じ入力の再描画(3Dドラッグ等)は6〜8ms(旧 約100ms毎回)。
検証：無風35.0s/35s、160/40 51.0s/51s、250/30 34.6s/35s、Circling 340/30 8.0s/8s で表示と実測が一致。

**3. TOD/旋回開始の「Abeamからの秒数」**：`signedTimeAlongPath(path, tasFn, wind, dFrom, dTo)`
(累積弧長配列でO(N+steps))に置き換え、符号付きに。Abeamより手前なら「Abeam手前 N sec」と表示
(`fmtFromAbeam`)。例：Circling 1.5NM/1500ft 無風で「Abeam手前 2 sec」(旧「4 sec」は符号と積分の誤差)。
地図ラベルのGS表示用に`gsAtDistFromThr`は残置。

**4. Departure 旋回終了TRK = 磁方位**(ユーザー確認)：入力ラベルを「磁方位, °M」に、シミュレーションへは
`trkStopDeg - depMagVar`(伊丹8°W)で真方位に換算して渡す。表示は「TRK180°M」。既定180のまま。
無風で旋回終了 1分5秒→1分8秒(8°多く回る)。

**検証**：node --check、70通り回帰、キーパッド、LDA、リセット、地形表示、Departure風の各チェック異常なし。

### 8.0B22 build 198：精査結果(claude/audit_build196.md)の残り全項目を修正

ユーザー指示「他も全て直して。Abeamより前にTODがくる場合はマイナス符号をつけて」。

**秒数表示**：Abeamより手前は「-6 sec」のようにマイナス符号(fmtFromAbeam)。

**計算**
- `groundSpeedOnTrack(tas, wind, windFrom, track)`＝√(TAS²−横風²)＋追い風成分。Final G/S(computePattern)、
  signedTimeAlongPath、gsAtDistFromThr、Entry直進レグで使用(旧: TAS+追い風のみ)。
- Target APP SPDのTASは「Downwind AFEの半分」の気圧高度・標準減率で補正したOATで換算(旧: Downwind高度)。
- 旋回半径の表示ラベルを「定常旋回半径(…Roll-in・out除く)」に。
- 風速50kt超は50ktで計算しつつWind欄を黄色枠、風インジケータに「(上限50ktで計算)」。
- Visual/Circlingの旋回TASを実経路距離で引く補正(Visual: `visualTurnsWith(corr)`を2回、Circling: `corrC`)。
- 性能: Circlingのバンク二分探索30→17回、時間基準の探索を二分法→Illinois法、AOMI探索を黄金分割に。
  Circling初回描画 約90ms→約25ms、同入力の再描画6ms。

**動作・状態**
- VOR方式の退避(vorSavedState)にcutAngle/offsetを追加し、解除時に戻す(保存・復元も対応)。VOR中はOffset欄も操作不可表示。
- 3Dで方位を回すと風矢印と方位表示が追従(`screenUpMagText`、updateWindIndicatorにheadingOffsetDegを加算)。
- Departure/LDAに入ると2Dに戻し、3Dトグルをdisabled(updateModeFieldsVisibility)。
- render()をラッパー化(`renderMain`)し、途中returnでも必ずscheduleSaveState。
- 重量空欄時はDownwind IAS/Target APP SPDを既定値(Visual 182/147、Circling 147/147)に戻す。
- 入力検証 `readNum(id, 既定, min, max, 名前, 単位)` / `readOat()` / `stopIfInputErrors()`：
  空欄/不正→既定値で計算＋黄色枠＋readout先頭に「入力の注意」、範囲外→赤枠＋エラー表示で計算停止。
  範囲: Offset 0.5〜6NM、Alt 標高+300〜+6000ft、Base 0.5〜8NM、降下角2.0〜4.5°、Aiming 0〜3000ft、
  Base時間5〜120s、OAT −50〜55℃、Departure各欄も。OAT空欄は「ISAで計算」と明示(旧: 補正なし)。
- キーパッド: Wind欄では「.」を無効、外付けキーボードは数字と . - / 以外を拒否、Enter/Escで閉じる、Escでキーパッドも閉じる。
- 保存stateの型・範囲検証(STATE_VALID)。不正値は既定値、3D ONで傾斜が範囲外なら55°。

**iPad UI**
- 設定パネル(#controls)を.layoutの外(header直後)へ移動 → 縦持ちで縮小・切れる問題を解消。
- パネルの並び: 空港・滑走路 → 気象(OAT/Wind) → 機体・速度(#aircraftFields, LDAでも表示) → パターン →
  Departure/LDA → 表示(2列トグル＋夜間モード) → 詳細設定(<details>: Aiming/TCH/降下角) → リセット。
  各グループは.grp(grid gap)。エラー文は「着陸重量・Flapを見直して」に。
- タップ対象44pt(seg/入力/☰/PNG)、入力欄16px。
- キーパッド: readoutを隠さない。横持ちは右カラム先頭、縦持ちは画面下端固定(パネルが開いていればその右)。
  主要結果の要約(#npSummary, npSummaryText)をキーパッド上に表示。
- readout: 列数はauto-fill(minmax 150px)、主要4項目(Turn開始/TOD/Turn終了高度/Bank・半径)を.keyで大きく先頭に。
  黄色は真高度補正100ft以上など条件付きのみ。LDA/Departureも主要項目を先頭に。
- 地図ラベル: 黒縁取り(LABEL_HALO)、文字サイズを地図表示幅から逆算(labelFS、画面上約13px)、
  距離/高度ラベルは明るい色＋近すぎるものを間引き、3Dでは地面側マーカーの名前を省略。
- 3D+地形でDownwind Alt入力中は地形を描き直さない(確定時に1回)。
- 2本指の中点移動でパン、地図右上に⟲(表示リセット)ボタン、3D ON時(最初の2回)に操作ヒント。
- 夜間モード(state.nightMode, body.night): 画像・地形の減光、文字色を落とす、選択ボタンは枠線のみ。
- viewport-fit=cover＋header/sideCol/controlsにsafe-area-inset。

**データ・コード**
- 衛星データは起動時の同期読込をやめ、選んだ空港だけ `loadAirportMapScript` で読込(非同梱時)。
  解析済みデータは今の1空港だけ保持。HTMLには `<!-- MAP_DATA_EMBED -->` の目印。
- bundle_html.py: 目印の位置にフォルダ内の個別版を全部埋め込み、個別版が無い空港だけ統合版から補う。
  旧HTMLでも、HTMLが統合版を読まない限り個別ファイルを飛ばさない。埋め込み0件ならエラー終了。
  (検証: 古い統合版を置いても9空港・重複なし、同梱版は起動後ヒープ約15MB)
- 未使用のslopePercent(値と説明)、bankForRadiusDeg、pointAlongPolylineByFraction、未使用変数、.note、
  VSD名残コメント、古い「他空港はプレースホルダー」コメントを削除。LDA表示の277°Mをデータ参照に。
  伊丹の騒音軽減経路は32R/32L共通だが32Lのみ計算する旨をヒントに明記。
- **残したもの**: 地形Worker描画(TERR_USE_WORKER=false)は切替で戻せるよう残置。QNH欄は追加していない
  (MSL=気圧高度として扱う従来どおり)。

**検証**：node --check、70通り回帰、キーパッド、LDA、リセット、地形表示、B1〜B11・夜間・遅延読込・同梱版の
個別チェック、iPad縦横スクリーンショットで異常なし。

### 8.0B23 build 199：バグ精査(claude/bugs_build198.md)の全22件を修正

ユーザー指示「全て直して」。Bankの運用上限はユーザー確認のうえ 35°超エラー／30°超警告。

**計算**
- K1 時間基準×Cut45/60で「設定不能」誤判定：Abeamの弧長を `abeamArcLengthOf(geo, pat, abeamXY)`
  (旋回開始点の弧長＋Downwind沿いの符号付き距離)で求める。探索・Pass1・本描画の4か所。両端で挟めないときは
  12分割の粗い走査で目標をまたぐ区間を探す。千歳01R Cut45 000/25 → 5.64NM/25s(旧4.60NM/2s)。
- K2 EntryのS字がBase旋回開始点より下流で合流 → geomError(上流0.1NM未満)。
- K3 VisualのRoll outがTHR手前0.3NM未満/THR越え → geomError。
- K4 目標秒数が負のとき「Abeam-11.0s＝Abeam手前で旋回」。
- K5 Departure Turn Startが離陸点のISK距離+0.2NM未満 → 入力エラー。
- K6 Departure 旋回終了TRK: 滑走路方位からの左旋回量が10〜270°以外 → 入力エラー。加速はIAS 250ktで頭打ち。
- K7 Traffic Patternで強制したCircling DW Flap F20を、Approachへ戻るとき元に戻す(circDwFlapBeforePattern)。
- K8 Target APP SPD > DW速度のときは0.7kt/secで加速(旧: Abeamで瞬時に跳ね上げ)。
- K9 BANK_ERROR_DEG=35 / BANK_WARN_DEG=30(Circlingの連続旋回とTraffic Patternの離陸旋回)。

**入力・状態**
- R1 Base Leg Distanceは「Visual・距離固定・非VOR」のときだけ検証。エラー欄が閉じた詳細設定内なら開く。
- R2 Windの不正入力は計算停止＋エラー表示。
- R3 編集中(npEditing/フォーカス中)は範囲外・空欄でも止めず、直前の有効値(lastGoodNum)で計算。確定時に判定。
- R4 非同梱時の空港切替で前の空港の MAP_DATA_* に undefined を代入(varはdeleteできない)。
- R7 縦持ちのキーパッド表示中はbodyにキーパッド高さぶんのpadding-bottom(fitBodyForPad)。
- R8 VOR解除時のOffset復元はapplyApproachDefaultsで上書きされる仕様なので削除(Cutの復元のみ)。

**iPad操作**
- S1 ResizeObserverで幅が変わったら常に(150msデバウンスで)再描画 → 地図文字サイズ・ラベル間引きを再計算。
- S2 zpApplyで平行移動を制限(枠の25%までのはみ出し)、リサイズ時は移動量を幅の比で補正。
- S3 地図上のボタン(PNG・⟲)では地図操作を開始しない(マウス/トラックパッドでPNGが効かなかった)。
- S4/S5 縦持ちで設定パネルを開いたときの scale(.88) をやめ、横持ちと同じく margin-left で地図を右に寄せる。
- S7 衛星データ読込失敗時は状態を残さず、次に選んだとき再試行。案内文を「読み込めませんでした」に。
- S8 gesturestart/gesturechange を preventDefault(iPad Safariでのページ全体のピンチ拡大を防止)。

**検証**：70通り回帰、キーパッド、LDA、リセット、地形、build198の個別チェック、全22件の再現ケース、
縦持ちパネル＋キーパッドのスクリーンショットで確認。

### 8.0B24 build 200：表示切替ボタンを「左OFF／右ON」に統一

ユーザー要望「左側がOFF 右側ONで 表示 非表示もオンオフ表記でいいよ」。設定パネル「表示」の5つ
(衛星画像・3D表示・地形・地図上の距離/高度・夜間モード)のボタンを OFF(左)／ON(右) の順・表記に統一。
ボタンは data-v で判定しているので処理側の変更なし。

### 8.0B25 build 201：3Dの高さ強調を2倍に、地形を空港標高基準の7色に、地図ラベルを一回り小さく

ユーザー指摘「地形表示が衛星画像と少しズレている」の調査結果：地形データの位置合わせは正しい
(持ち上げずに重ねると画像の尾根・谷と一致)。ズレの主因は3Dで地形を高さ×4倍で持ち上げていたこと
(傾斜55°で1000ft AFEの尾根が約1.7km奥へずれる)。副因は輪郭抽出の粗グリッド(160分割≒170m)を
最大値で作っているため輪郭が最大100m程度外側へ広がること(未対応)。

- ALT_EXAG_3D 4→2(ユーザー決定)。航跡の持ち上げも同じ定数なので地形との相対関係は保たれる。
- 地形の色(TERR_CATS)を「Downwind高度基準の赤/橙/緑」から「空港標高からの高さ(ft AFE)」の7段階に:
  0–300 深緑 / 300–600 緑 / 600–900 黄緑 / 900–1200 黄 / 1200–1500 橙 / 1500–2500 赤 / 2500超 濃赤。
  (ユーザー指定: 1500ft AFE超は赤)。Downwind Altitudeを変えても地形の色は変わらない(キャッシュkeyからも除外)。
  terrainPaintは P.CATS を参照(Worker経由でも同じ)。
- 3D+地形表示中は地図右下に色の凡例(#terrLegend, showTerrLegend)。設定パネルの地形ヒント文も変更。
- 地図ラベルの文字サイズ(labelFS)を画面上約13px→約11.5pxに。

### 8.0B26 build 202：Circling DW FlapとLanding Flapの連動、VOR方式ではTraffic Patternを選べなくする

ユーザー要望。
- CirclingのDownwind FlapでF25/F30を選ぶと、Landing Flapも同じFlapに変える(F20のときは変えない)。
  #circDwFlap のクリック処理で state.appFlap を合わせ、fmsApplySpeeds。
- VOR 34L→16R / VOR A→16L 選択中はModeのTraffic Patternボタンをdisabled。Traffic Pattern中にVOR方式を
  選んだらApproachへ戻す(updateModeFieldsVisibility内。Pattern用のF20強制も戻す)。
- disabledのsegボタンを薄く表示(opacity .35)。

### 8.0B27 build 203：Traffic PatternではCut AngleをNIL固定

ユーザー要望「TrafficPattern選んだときはApproachは強制的にNilに」(Entry=Cut Angleのこと)。
- Traffic Pattern中はCut AngleをNILにし、ボタン群を操作不可(render内のcutSeg処理)。
- 入る前のCut Angleは cutBeforePattern に退避し、Approachへ戻ると復元(updateModeFieldsVisibility)。
  Pattern中にApproach Typeを変えた場合は、その既定値(Visual=NIL/Circling=45°)を戻り先にする。

### 8.0B28 build 204：Circling＋Cut NILで旋回開始前のDownwind線が消える不具合

ユーザー報告。Downwind直線の描画はVisual(visualDownwindNearXYから)と、Entry/Traffic Patternの接続線しか
無かったため、Circling＋Cut NIL(かつPatternでない)では連続旋回の開始点より手前の線が1本も描かれていなかった。
connectXYから画像の端まで(rayExitDistM)のDownwind線を追加(2D、3Dの地面側)。3Dの持ち上げ航跡は元から表示されていた。
あわせて3Dの操作ヒントを地形凡例と重ならないよう少し上へ(bottom 70px)。

### 8.0B29 build 205：3Dの高さ強調を4倍に戻す／地形描画の高速化

ユーザー「3Dは4倍でいいや。3Dがかなり重い」。
- ALT_EXAG_3D 2→4(ユーザー判断で戻す)。
- 地形の描き直しが重かった主因は、build201の7色化で「色カテゴリごとに別レイヤーへ描いて全面drawImageで
  合成(1カテゴリ3回)」が21回になっていたこと(dpr2で1回の描き直し約250ms)。全カテゴリを1枚のレイヤーに
  不透明で描き、最後に1回だけ共通の透明度(TERR_OPACITY=0.6)で合成するよう変更(terrainPaintのflushは最後だけ)。
- 地形canvasの解像度を dpr上限2→1.25、一辺上限3072→2400px に(半透明の陰影なので見た目の差は小さい)。
- labelFS()を1回のrender中はキャッシュ(ラベルごとのclientWidth読みで強制レイアウトが起きていた)。
→ dpr2(iPad相当)で地形の描き直し 約250ms→約70ms。ドラッグ中のフレームは約10ms(PC計測)。

### 8.0B30 build 206：結果ボードの項目整理、真高度補正を飛行場標高基準に

ユーザー指定。
- Approach/Pattern: 上段(大きく)＝Abeam→Turn開始秒数(時間基準なら目標秒数を併記)・TOD Abeamからの秒数/TRK・
  必要Bank角(Visualは「25°固定／定常旋回半径」、PatternはDownwindへの旋回Bankも上段)。
  下段＝Turn終了高度・必要降下率・Final G/S・真高度補正・Turn1→Turn2。
  削除: THRからのTurn開始距離、TOD距離、経路まわりの最高標高(計算もしない)、VOR進入コース、TAS補正値2項目。
  キーパッドの要約も秒数ベースに。
- ITM Departure: Turn Start・Bank角/旋回終了TRK・風のみ。
- 真高度補正: 「同じDownwind Altitudeでも滑走路で値が違う」との質問。build187で高度の基準をTHR標高にした際、
  補正式の高さ(基準点からの高さ)もTHR標高基準になっていたのが原因。気温補正は高度計規正値の基準点
  (飛行場標高)からの高さに比例する(ICAO Doc 8168)ので、飛行場標高基準に戻した(熊本07/25とも+140ft @OAT30)。

### 8.0B31 build 207：伊丹DepartureのClimb Rate・Acceleration Rate欄を廃止

ユーザー要望。入力欄(depClimbRateLowFpm/HighFpm/depAccelRateKtSec)と保存対象から削除し、
従来の既定値を定数で固定(ITAMI_DEP_CLIMB_LOW_FPM=2000, _HIGH_FPM=1000, ITAMI_DEP_ACCEL_KT_SEC=1.5、250kt上限)。
パネルに固定値のヒントを表示。
※ユーザーから「真高度補正はMSLで決まるのでAFE無関係では？」との質問。回答：QNHは飛行場標高で高度計を
正しく合わせる値なので、気温による誤差は飛行場より上の空気の層の分だけ生じる(飛行場上で誤差0)。
したがって補正量は飛行場標高からの高さに比例する(ICAO Doc 8168の表・4%/10℃の目安も同じ)。build206の実装で正しい。

### 8.0B32 build 208：Cut Angle時のBreak Point距離を選べるように、Break Pointより手前も描画

ユーザー要望「Cut angleを選んでいる時はBreak point(cut angleに向けた旋回開始ポイント)の距離を反対側の
滑走路(34なら16)からの距離で選べるように。既定: Circling=周回進入区域に入る距離、Visual=3NM。
周回進入区域に入るより前も描画」。
- 入力欄 #breakDist(パターン欄、Cut 45/60かつVOR方式・Traffic Pattern以外で表示)。空欄=既定値(placeholder/ヒントに表示)。
  範囲0.5〜12NM(readNum)。保存対象・既定値リセット(applyApproachDefaultsで空欄)に追加。
- 既定値: Circling＝反対側THRからの延長線が周回進入区域(全滑走路2.5NMの和集合)に入る距離
  (findPolygonEntryT。羽田34Lで2.89NM、伊丹で2.50NM)、Visual＝3.0NM。
  周回進入区域の計算を ensureProtectionArea() に分離してパターン計算より前に使えるようにした。
- computeEntryProcedure は pat.breakDistM(buildPatternで付与)を使う。時間基準のキャッシュキーにも追加。
- 描画: Break Pointより手前の延長線上を画像の端からアンバーで描き、Break点に「Break X.XNM」マーカー。
  3Dの持ち上げ経路(nearToFarFull)にもBreak Pointの先6NMを追加。
- 注意: 回帰スクリプト /tmp/chk_199.js のK6はbuild207で削除した欄を使うため失敗する(アプリ側の問題ではない)。

### 8.0B33 build 209：LDAの結果ボードを3項目に

ユーザー指定で、Turn Start高度・滑走路・LDA Final Apch Crs・速度・降下角・描画開始点を削除。
残すのは MAP通過高度・Finalにのるための必要Bank角・on Final(旋回終了点)高度 の3つ(すべて上段表示)。

### 8.0B34 build 210：VisualのBreak Point既定を5NMに、VOR 34L→16Rの周回進入区域より前のコースを描画

ユーザー要望。
- VisualのBreak Point既定値 3.0→5.0NM(Circlingは周回進入区域に入る距離のまま)。
- VOR 34L→16R: 周回進入区域に入る前の333°Mインバウンドコースを、境界通過点(Entry旋回開始点)から上流へ
  15NMのアンバー実線で描く(SVGの表示範囲で切れる)。TTE→境界通過点の点線の参考線は残す。
  境界通過点はTTEから約5.3NM(153°側)なので、手前のコースの大半は衛星画像の端付近〜外になる。
- Break Pointより手前の直進線(通常のCut Angle Entry)も、Break Pointが画像外でも描けるよう15NMで引く方式に。
- 3Dの持ち上げ経路(nearToFarFull)にもVOR方式のインバウンド手前6NMを追加。

### 8.0B35 build 211：Break PointのILS DME距離表示とDME入力、VOR 34L→16RはTTEのDME

ユーザー要望「cut angleありの場合、反対側の滑走路にILSがあればBreakのポイントにILSのDME距離も表示。
入力欄は数字だけなら滑走路からの距離、DをつけたらDME。VOR34LはILSじゃなくてVORからのDME」。
- ILS_DME 定数を追加(AIP AD2.19より9空港の全ILS/LOCのDMEアンテナ位置・標高。福岡16R/34LはLOC-DME)。
  AD2.19抽出結果: RJCC 19R ICS/01L ICN/19L ICM/01R ICH、RJCH 12 IHL、RJTT 16L IOC/34R ITC/16R ITA/34L IHA/
  22 IAD/23 ITD、RJOO 32L ISK、RJOA 10 IHG、RJOT 26 IKT、RJOM 14 IMP、RJFF 16L IFO/34R IFF/16R IFN/34L IFW、RJFT 07 IKU。
- Break Point欄(#breakDist)はtype=textに。「6」=反対側THRからの距離、「D6.0」=反対側滑走路端のILSのDME距離。
  DMEは斜距離(Break PointでDownwind Altitude(MSL)にいるとして、アンテナ標高との差を含む)。D指定は
  延長線上で逆算(horiz²=D²−高さ差²−横ずれ²)。反対側にILSが無い／成り立たない／0.5〜12NM外は入力エラー。
- キーパッド: Break Point欄では「/」キーが「D」になり、先頭のDを付け外し。外付けキーボードはd/Dを許可。
- 地図のBreakマーカー「Break 5.0NM / ITA D5.2」。ヒントに既定値のDMEも表示。
- VOR 34L→16R: 境界通過点(Break)に「Break / TTE D5.3」(TTEアンテナ標高101ftとの斜距離)。

### 8.0B36 build 212：Cut Angle EntryのA/B/C表記と、A→C・B→Cの秒数

ユーザー要望。A=Turn1開始点(Break Point)、B=Turn1終了点、C=Turn2開始点を地図に表記
(AはBreakマーカーに「A Break …」と併記、B/Cは新規マーカー。VOR 34L→16Rも同様、VOR A→16Lは対象外)。
結果ボード: A→C(従来のTurn1開始→Turn2開始)を上段の先頭に、B→C(直進レグ)は下段に。
computeCutAngleEntryFromが legTimeSec も返し、geometryで entryLegBCSec として受け渡す。

### 8.0B37 build 213：Turn1/Turn2表記を削除、地図ラベルを航跡と重ならない位置に自動配置

- 結果ボードの表記を `A→C 秒数` / `B→C 秒数` に変更。エラーメッセージからも Turn1/Turn2 表記を削除。
- 新関数 `placeMapLabels()`（render() 内で renderMain() の直後に呼ぶ、例外は握りつぶして console.error）。
  - 対象：markerXY / liftMarkAt が作るテキスト（`dataset.lbl/ax/ay/rot/fs` を付与）。
  - 障害物：SVG内の全 polyline/line を3単位ごとにサンプリング＋circle。回転フレーム（-rot）で評価。
  - 候補8方向（右上既定, 右, 右下, 左上, 左, 左下, 上, 下）を text-anchor start/end/middle で試し、スコア＝障害物ヒット数＋既配置ラベル重なり×60＋候補順×0.2。
  - 2Dのみ：mapwrap 外（上24px=コンパス帯, 下20px=帰属表示帯, 左右2px）にはみ出す候補に +400。
  - 長いラベルから順に配置。コストは PC で約10ms/render。

### 8.0B38 build 214：設定を開くと☰が消えて閉じられなくなる不具合への対策

- 報告：iPadで設定(引き出し)を開くとヘッダーの☰が見えなくなり、閉じられない。Chromiumでは再現せず（☰は elementFromPoint で最前面）。iPad Safari特有のレイヤー合成（3D地図の preserve-3d が overflow:hidden を無視してはみ出す／スクロール要素が z-index を無視して前面に出る）と推定。
- 対策（多重）：
  - header：z-index 60、`transform:translateZ(0)` で独立レイヤー化。
  - .mapwrap：`isolation:isolate; clip-path:inset(0)` で3D地図を確実に枠内に切り抜く。
  - 引き出し内に「✕ 閉じる」ボタン（`.drawerClose`、position:sticky で引き出し上端に常時表示）と、最下部に「設定を閉じる」ボタンを追加。どちらもキーパッドも閉じる（`drawerclosed` イベント）。

### 8.0B39 build 215：引き出しをヘッダーの下から開くように変更（☰が消える不具合の再対策）

- build214 後も「設定を開くと☰が消える」との報告（開く前は見えている）。Chromium では依然再現しない。
  212/213 の変更（A/B/Cマーカー、placeMapLabels）はヘッダーやレイアウトに触れておらず、コード上の因果は特定できず。
  前回の再現調査では、ページや body のスクロール、scrollIntoView によるずれ、はみ出しもなかった。
- 対策：`.controls` を `top:var(--hdrH)`（ヘッダーの実寸の bottom、JS の syncHdrH で resize/ResizeObserver/開くたびに更新）から始め、
  ヘッダーと引き出しが画面上で一切重ならない構造にした（以前は top:0 でヘッダーの下に潜り込ませ padding-top:72px で逃がしていた）。
  これで重なり順（z-index）の問題は起こり得ない。
- 引き出し内の「✕ 閉じる」は top:-12px の sticky で引き出し上端に密着。

### 8.0B40 build 216：☰が消える不具合の真因＝公開用本文の生成で <header> タグを消していた

- 真因：build213 から、公開用 artifact_body.html を作る正規表現を `<head[^>]*>` と書いていたため、
  `<head>` だけでなく **`<header>` の開始タグまで削除**していた（ローカルの approach_planner.html は正常なので Playwright では再現しなかった）。
  - 213/214：ヘッダー要素が無くなり☰がただの body 直下のボタンに → z-index:50 の sticky ヘッダーの保護が消え、top:0・z-index:40 の引き出しに覆われて「開くと☰が消える」。
  - 215：追加した syncHdrH が `document.querySelector("header")`=null で例外 → 以降のスクリプトが止まり、地図が真っ黒・☰も無反応。
- 修正：
  - 生成スクリプトを `make_artifact_body.py` として固定（`<head(\s[^>]*)?>` 等、タグ名の後ろに空白か > が来る場合だけ消す）。`<header>` が1つ残っていることを assert する。
  - syncHdrH は header が無くても落ちないようにした。
  - 214 の推測対策のうち header の translateZ と mapwrap の clip-path は不要なので削除。引き出し内の「✕ 閉じる」「設定を閉じる」と、引き出しをヘッダーの下から開く構造(215)は残す。
- 検証手順に追加：公開用本文を Artifact と同じ骨組み(doctype/head/body)で包んだファイルでも Playwright で☰の開閉・衛星画像の読み込み・pageerror 無しを確認する。

### 8.0B41 build 217：引き出し内の閉じるボタンを削除

- ☰が正常に戻ったため、build214 で追加した「✕ 閉じる」（.drawerClose）と最下部の「設定を閉じる」（#drawerCloseBtn2）、関連JS（drawerclosed イベント）を削除。
- 引き出しをヘッダーの下から開く構造（top:var(--hdrH)、padding-top:12px）は残す。
- 注意：削除時の正規表現で `.controls.open{…}` まで巻き込んで消し、引き出しが開かなくなった（回帰テストの selectOption がタイムアウトして発覚）→ 復元済み。

### 8.0B42 build 218：GitHub（GitHub Pages）対応

- リポジトリ構成（ルート＝公開物＋スクリプト）：approach_planner.html、map_data_<空港>.js ×9（各2.3〜5.6MB、GitHubの100MB/ファイル上限内）、
  sw.js、manifest.webmanifest、icons/（apple-touch-icon 180、192、512、maskable 512）、tile_downloader.py、bundle_html.py、
  tools/make_artifact_body.py、tests/（regress.js＝旧 verify_176_regress.js、drawer.js＝☰開閉＋Artifact本文の健全性）、
  docs/HANDOVER.md、data/flap_maneuver_speed_SL.csv、README.md、.gitignore、package.json（npm test）。
- 公開先：GitHub ユーザー名 `zwsgjssl`、リポジトリ `approach-planner` → https://zwsgjssl.github.io/approach-planner/
- .github/workflows/pages.yml：main へ push で _site（index.html＝approach_planner.html のコピー、approach_planner.html、sw.js、manifest、icons、map_data）だけを
  GitHub Pages に配備。Settings → Pages → Source を「GitHub Actions」にする必要あり。
- approach_planner.html：
  - head に manifest / apple-touch-icon / apple-mobile-web-app-capable / status-bar black-translucent / theme-color を追加。ホーム画面から全画面起動。
  - 末尾で https かつ iframe でないときだけ Service Worker（sw.js）を登録。
- sw.js：
  - HTML はネットワーク優先で、4秒でタイムアウトしたら保存版を返す。キャッシュ名は ap-app-v1。
  - map_data は保存版優先で、キャッシュ名は ap-map-v1。起動5秒後にページから precache-maps メッセージを送り、未保存の空港データを順に保存する。
  - 地図データを作り直したら MAP_CACHE の番号を上げる。
  - AIRPORT_KEYS は AIRPORTS のキーと一致させること（空港追加時は sw.js も更新）。
- Artifact 公開は tools/make_artifact_body.py（manifest / apple-touch-icon の link も除去、`<header>` 残存を assert）。
- 検証：_site 相当を http.server で配信し、SW 登録・9空港の地図データ保存・オフラインでの熊本表示を確認。npm test（70/0 と drawer 4/4）合格。
- 注意：衛星画像は Esri World Imagery。公開リポジトリ／Pages では再配布の扱いになるため、利用規約を確認するか GSI（TILE_SOURCE="gsi"）への切替を検討（README に記載）。

### 8.0B43 build 219：遅い回線（機内Wi-Fi）でも起動が止まらず、壊れた版に置き換わらないように

- ユーザーの懸念：機内 Wi-Fi が極端に遅いときに更新がかかり、読み込めず元にも戻せなくなること。
- sw.js を stale-while-revalidate に変更：
  - HTML は保存済みの版を即返す。更新確認は裏で（e.waitUntil(revalidateHtml)）行い、新しい版は次に開いたときに反映。
  - 保存済みが無い初回だけネットワークを待つ（上限30秒）。
- 保存前の検証：
  - HTML：status 200、リダイレクトなし、`APPROACH PLANNER … build N</span> … </html>` の形で最後まで受信できたときだけ保存。
    途中切断・Wi-Fi ログイン画面（302/200）は捨てる。"./" と approach_planner.html は同じ中身で揃える。
  - install でも同じ検証を行い、通らなければインストール失敗扱いにして旧 SW と旧キャッシュを使い続ける。
  - map_data：`var MAP_DATA_<KEY> = {` を含み `}` で終わるときだけ保存（取得時・precache 時とも）。
- ページ側：SW から app-updated（build）を受けたら画面下にトースト「新しい版(build N)を保存しました。アプリを開き直すと反映されます。」を12秒表示（タップで消える）。
- Wi-Fi 名(SSID)による切替は Web アプリから SSID を取得できないため不可。手動のオフライン固定スイッチはユーザー判断で見送り。
- テスト：tests/slow_server.py（normal/slow/truncated/portal302/portal200/offline を再現）と tests/offline.js を追加。
  全モードで保存済みの build 219 が約0.2秒で起動し、キャッシュも壊れないことを確認。正常回線に戻ると build 220 を保存してトーストを出し、再起動で220になることも確認。npm test に追加。

### 8.1 その他

| 項目 | 状況 |
|---|---|
| **衛星画像の取得** | 各空港ぶん未取得（tile_downloader.pyのcenter座標は全空港AIP準拠に更新済み） |
| **PCでの地図スクロール追従** | レイアウトが崩れるため撤回。下記4案から選定待ち |
| **0.4秒/kt の風補正係数** | 効きすぎの疑い。0.2秒/kt 程度が妥当か要検討 |
| **Roll rate 比較ツール** | `roll_rate_compare.html` として別途作成済み。本体とは未統合 |
| **線の色の継ぎ目** | 区間ごとに別SVG要素のため。round cap で緩和済み。1本化するかは保留 |
| **GitHub公開＋自動アップデート** | **ユーザー要望として正式にTODO入り（下記8.2参照）。友達への共有が目的。着手前に決めることが2つある** |
| **PAPI整合限界点の機能** | ユーザー要望として提起されたが「一旦保留」中（下記参照）。再開の指示待ち |

## 8.2 GitHub公開と自動アップデート（TODO・未着手）

**目的**：友達に共有する。オンラインでアプリを開いたら自動で更新を確認し、適用する。

### 先に決める必要があること（2つ）

**(1) AOM由来データの公開可否 — ユーザー見解と、残る論点**

build 132以降、`FMS_TABLES` に **ANA飛行機運用規程(AOM) PI.20.26 の Flap Maneuver Speed** が
埋め込まれている。ユーザーの見解は「元はボーイングのデータ／配布先は同じ会社の同僚／原本ではなく
それを元に作成した表なので問題ない」。

事実関係として補足しておくべき点：
- **ボーイングはこの表を一般公開していない。** AOM/FCOMは運航者にライセンス供与される文書で、
  手元のPDFもANAのブランド・ページ番号(PI.20.26 / Rev.34)が入った**ANAのAOM**。
- **書き起こした表は要約ではなく完全な複製。** CSVとHTMLの機械照合で1014セル全一致を確認済み
  （8.0l）。値そのものが同一なので「作成した表だから別物」とは言いにくい。
- **「同僚に配る」という点は筋が通る。** 同じAOMに正当にアクセスできる相手なら、誰も新たに
  何かを得るわけではない。**問題は配布先ではなく配布の仕組みのほう**で、GitHub Pagesは
  誰でも読める公開URL（アクセス制御はEnterprise限定）。意図は同僚限定でも、実態は全世界公開になる。

> **結論の方向**：ユーザーの理屈は「同僚限定で配る」なら成立する。ならば**配信側を実際に
> 同僚限定にすれば筋が通る**。→ (2)で Cloudflare Access（無料・50ユーザーまで・メール許可リスト）を
> 採用すれば、この問題と配信の問題が同時に解決する。会社の「社外クラウドに社内文書を置く」規程に
> 触れるかはユーザーの判断領域。
>
> なお切り離し案（`fms_data.js` を公開ビルドに含めない）も引き続き有効な保険。build 132の設計上、
> 重量欄が空ならこの機能は丸ごとOFFで従来の固定値182/147で動くので、データ無しでもアプリは成立する。

**(2) 衛星画像をどう配るか — 現在の設計と正面から衝突する**

bundle_html.py が作る単一HTMLは **1空港あたり数MB〜10数MB**（data URI埋め込み）。9空港だと
数十MB〜100MB級になる。**どのホスティングでも1ファイル数十MBは通らない**（GitHubは1ファイル100MB上限、
Cloudflare Pagesは**1アセット25MiB上限**）。加えて初回アクセスでその容量をLTEで落とすのは現実的でない。

つまり **build 125/126で決めた「統合版map_data_all.jsだけを読む／個別ファイルは廃止」という方針を、
オンライン配信では見直す必要がある**（ローカル単一ファイル版とオンライン版で読み込みが分岐する）。
空港ごとに分割して遅延ロードするのが本命。

### 社内Google基盤という選択肢（**ユーザー保留中・要確認**）

ユーザーから「社内用のGoogle Cloudがあり、規程類も全てそこに置いてある」との情報。
**これは(1)の論点を完全に消す**（マニュアルが既にある場所に、そこから作った表を置くだけになる）。
ただし「Google Cloud」がWorkspaceかGCPかで結論が真逆になる。**ユーザーが確認を保留中。**

| | 配布 | 自動更新(PWA) | 備考 |
|---|---|---|---|
| **Workspace(Drive/Docs)のみ** | ○ 共有フォルダで社内限定 | **×** | **Driveのウェブホスティングは2015年告知・2016年停止で現存しない**。Google Sitesは任意JSをiframeサンドボックスするのでService Worker不可。Apps Scriptウェブアプリも出力がgoogleusercontent.comのサンドボックスに入るため同様に不可。`file://`運用のままなので自動更新は諦めることになる |
| **GCPコンソールが使える** | ○ | **○** | **理想形**。Cloud Run（またはCloud Storage）で静的配信し、前段に**IAP**を置いて社内Googleアカウント限定。Cloudflare Accessと同じことを社内テナンシー内でやる形。HTTPSなのでService Workerも動く。難点はプロジェクト用意に情シスが絡む可能性と、Cloud Storage+HTTPS LBだとLBの月額が発生する点（Cloud Run経由なら回避可） |

> **GCPが使えなかった場合の本命＝ハイブリッド案**（全部両立する）
> **AOM由来の `fms_data.js` だけを社内Driveに置き、アプリ本体（データなし）をCloudflareで自動更新配信する。**
> 各自が一度Driveから `fms_data.js` を取得して置けば機能が有効になる。
> **社外ホストに会社のデータが一切乗らず、かつアプリは自動更新される。**
> build 132の設計上、重量欄が空ならこの機能は丸ごとOFFで従来の固定値182/147で動くので、
> データ無しのビルドでもアプリは完全に成立する（＝公開ビルドが壊れない）。

### ホスティングの比較（2026年9月時点で確認）

| | 無料枠 | ファイル上限 | アクセス制御 | 判定 |
|---|---|---|---|---|
| **Cloudflare Pages + Access** | 静的配信の帯域**無制限**／20,000ファイル／ビルド500回/月 | **25MiB/アセット** | **Zero Trust無料枠で50ユーザーまで。メール許可リストやドメイン指定が可能** | **本命** |
| GitHub Pages | サイト1GB／帯域100GB/月(ソフト) | 100MB/ファイル | 実質なし（Enterprise限定） | (1)と両立しない |
| Netlify | 帯域100GB/月 | 緩い | パスワード保護は有料(Pro) | 次点 |
| Firebase Hosting | ストレージ10GB／**転送360MB/日** | 緩い | Firebase Authで可 | 転送量が厳しい |
| GitHub Releases | 2GB/ファイル | 大きい | なし | **画像の置き場としてだけ**なら有力 |

**Cloudflare Access採用時の注意（実装時に踏む）**：認証がサイトの手前に入るので、
Service Workerの更新確認が**セッション切れ時にJSONではなくログインページのHTMLを受け取る**。
`version.json` のパース失敗を「更新なし」として無視し、**キャッシュ済みアプリを絶対に壊さない**
ガードが必須。オフライン動作自体はSWキャッシュなので認証の影響を受けない（初回だけログインが要る）。

### 自動アップデートの構成（決まってから実装）

- **Service Worker + Cache API** が本命。**HTTPSが必須**なので GitHub Pages なら条件を満たす。
  逆に **`file://` では Service Worker が動かない**ので、現在の「HTMLファイルをiPadに送って開く」
  運用のままでは自動更新はできない。**「一度URLで開けば以後オフラインでも動く」**形に変わる。
- **cache-first**（オフラインでも必ず起動する）＋**起動時に裏で更新確認**。
  パイロットが圏外で開く可能性があるので、**更新確認は起動をブロックしてはいけない**
  （タイムアウト付き・失敗しても無視）。
- 更新検知は小さな `version.json`（`{"build": 137}`）を取りに行き、キャッシュ中のbuild番号と比較。
  新しければ裏でダウンロードし、**「新しいバージョンがあります／再読み込み」バナー**を出して
  ユーザーが押したときに切り替える（飛行中に勝手に画面が変わらないように、自動適用はしない）。
- `manifest.json` を足してホーム画面追加（PWA）に対応。今もiPadで使っている運用に合う。
- バージョンは**H1の `build NNN` がそのまま版数**。GitHub Actionsで push 時に
  `bundle_html.py` を実行 → build番号を抽出して `version.json` を生成 → Pages へ公開、が自然。

### 作業順序（案）
0. **【保留中】社内Google基盤がWorkspaceかGCPかの確認**。ここで方針が分岐する（上記参照）。
1. 配信先の限定（GCP+IAP／Cloudflare Access／ハイブリッド案のいずれか）。これで(1)の前提を満たす。
2. 画像配布方式の決定（上記(2)）。
3. Cloudflare Pages にリポジトリを接続・Access設定（メール許可リスト）・`manifest.json`。
4. Service Worker（cache-first＋非ブロッキング更新確認＋バナー）。
5. ビルドと `version.json` 生成の自動化（Cloudflare Pagesのビルド、またはGitHub Actions）。

### PAPI「整合限界点」機能（保留中・角度値のみユーザー確認済み）
On path上にいるのに実際のPAPIが3red（1W3R）を示してしまう高度/地点を計算する機能。
アプリの計算上のグライドパス基準点（Aiming Point、既定THRから1312ft/400m）と、
実際のPAPI灯火ユニットの設置位置（AD2.14 "DIST FM THR"）のズレに起因する現象。
**ユーザーから「一旦保留で」と明言されており、明示的な再開指示があるまで実装しない。**
ただし以下はユーザー確認済みの数値として記録しておく：

- **非ILS滑走路のPAPI角度**：最も近い灯火 3.50°／次 3.17°／次 2.83°／最も遠い灯火 2.50°
  （On path帯=2W2R は 2.83°〜3.17°、公称3.0°±10'）
- **ILSあり滑走路のPAPI角度**：最も近い灯火 3.58°／次 3.25°／次 2.75°／最も遠い灯火 2.42°
  （On path帯=2W2R は 2.75°〜3.25°、公称3.0°±15'、外側の全赤/全白境界は±35'）
- 各滑走路のPAPI設置距離（DIST FM THR、AD2.14）は高松(08:403m/26:363m)・
  松山(14:415m/32:461.3m)のみ取得済み。他7空港は未取得。

### EGPWS Mode 2相当の地形接近Cautionアラート機能（保留中・build 155時点の調査結果を記録）

ユーザー要望「EGPWSの作動条件データを入れて標高情報(DEM)と合わせて条件に合致したときに
Cautionを出せるか」。既存のDEM(標高)データと進入経路の降下計算を組み合わせれば技術的には
可能だが、**実際のEGPWS(Honeywell Mk V〜VIII)のMode 2(過大な地形接近率)エンベロープの
正確な数値が、信頼できる一次資料からは入手できなかった**ため、**現時点では未実装・保留**。
ユーザーから「一旦保留にしてhandover」と明言されており、明示的な再開指示があるまで
実装しない。

**調査の経緯**：

1. Honeywell純正のPilot Guideを2種(MK V&VII版・MK VI&VIII版、いずれもskybrary.aero /
   lso.fe.uni-lj.siで公開されているPDF)をWebFetchで確認。Mode 2A/2Bの有効条件
   （2A=フラップ非着陸形態かつG/S中心線外、2B=フラップ着陸形態またはG/S・LOC偏差2ドット
   未満、離陸後60秒間も2B）はテキストで明記されていたが、**肝心の境界線(高度×接近率)の
   数値は本文中の表ではなく12〜14ページのグラフ画像としてのみ存在**し、WebFetchのテキスト
   抽出では目盛りの数値まで読み取れなかった。ManualsLib版（ページ単位で閲覧可能）も同様。
2. 唯一テキストで確認できた具体的数値：「TAD(Terrain Alerting and Display)機能が有効な
   場合、Mode 2Aの上限高度が全速度域で1250ft(ソフトウェアバージョン-022以降は950ft)に
   引き下げられる」（両Pilot Guideで一致）。
3. ユーザーが最初に提示した数値(他のAIから得た回答)：
   - **Mode 2A**(非着陸形態)：30〜1650ft RA(低速側)、220〜310ktで上限が2450ftまで拡大、
     TAD有効時は1250ft/950ftに低減(→この最後の一文はHoneywell純正資料と一致)。
   - **Mode 2B**(着陸形態・アプローチ)：30〜789ft RA、下限は降下率/形態により30〜600ftで可変。
   出どころ不明のAI回答だったため、そのまま信用せず裏取りを実施。
4. 追加調査で発見した情報源：
   - **Sundstrand社の1980年代の基本GPWS Mode 2特許(US4639730A, Google Patents)**：
     高度100〜1800ftの範囲で有効、対気速度200kt超が条件、接近率の閾値は高度に対して
     線形（100ftで約2,800fpm、1800ftで約15,000fpmに達すると警報）。ただし**これはEGPWSの
     前身にあたる古い世代のシステムの特許**であり、現行の787搭載Honeywell EGPWS(Mk V〜VIII)
     の数値とは直接一致しない可能性がある(高度上限が1800ft vs 1650/2450ft等、世代が違う)。
   - **フランスの学位論文(修士相当、EGPWSのモードを研究・シミュレーションしたもの、
     memoireonline.com)**：ユーザーが提示した数値とほぼ一致する独立記述を発見。
     「Mode 2A: 30〜1650ft、対気速度220〜310ktで2450ftまで拡大」
     「Mode 2B: 30〜789ft、下限は30〜600ftで降下率とフラップ位置により可変」。
     おそらくHoneywellマニュアルのグラフを目視で読み取って記載したものと推測されるが、
     他のAIの回答とは完全に独立した情報源でありながら一致している。
5. ユーザーに「独立した2つの情報源(他のAI回答＋この論文)が一致していることを根拠に
   採用してよいか」を確認したところ、**「一旦保留にしてhandover」との回答**。会社の
   FCOM/AFMSで実際の数値を確認してから、改めて着手する方針。

**現時点でのステータス**：**未実装**。上記4.の数値(Mode 2A: 30〜1650ft／220〜310ktで
2450ft、Mode 2B: 30〜789ft・可変下限30〜600ft)は「Honeywell公式の数値表ではなく、
複数の二次資料から読み取った推定値」という位置づけのまま記録に留める。実装を再開する
際は、まずユーザーの会社のFCOM/AFMSで実際の数値を確認するか、Honeywell Pilot Guideの
12〜14ページのグラフ自体を画像として確認する作業から始めること。

**実装時の設計方針(会話内で合意済みの前提)**：
- 実機のEGPWSの再現ではなく、**「このアプリ独自の地形接近注意情報」として明示的に
  ラベリングする**（万一エンベロープの数値が不正確でも、実機のEGPWSの代替だと
  誤認されないようにするため）。
- 対象はまず**Mode 2相当(地形への異常接近率)のみ**。このアプリが既に持っている
  DEM(標高)データ・進入経路に沿った降下率計算との相性が良いため。Mode 1(過大降下率)・
  Mode 3(離陸後高度損失)・Mode 4(不適切な地形クリアランス)・Mode 5(G/S逸脱)は対象外。
- 数値が不確実な間は、実機より早め/厳しめ(保守的)の閾値にして安全側に倒す設計にする。

### スクロール追従の案（要選択）
- **A. アプリ風固定レイアウト**：ページ全体を画面高に収め、readoutだけ内部スクロール（おすすめ）
- **B. readoutを左カラムへ**：右カラムが地図だけになり sticky が自然に効く
- **C. ピクチャーインピクチャー**：スクロールで地図が隅に小さく固定
- **D. 右カラム方式の再挑戦**：幅指定を直せば実現可能

---

## 9. 会話の進め方についての申し送り

- ユーザーは**現役パイロット**。航空の専門用語はそのまま使ってよい
- **数値の裏取りを重視する**。「たぶん」で実装せず、計算して見せると議論が早い
- 仕様の解釈がズレたまま実装すると大きく手戻りする。過去に
  「cut angle は 333°基準か Downwind 基準か」「Final 1.2NM 固定か AOMI 通過か」で
  往復が発生した。**曖昧なら実装前に短く確認する**
- 指摘は鋭く、**こちらの説明の誤りもよく見抜かれる**。取り繕わず、検証して訂正すること
  （実際に「旋回中も風に流されるはず」「同じ20秒で距離が変わらないのはおかしい」といった
  指摘から複数の実バグが見つかっている）
