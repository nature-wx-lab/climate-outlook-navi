# 気候ものさしナビ

Nature Wx Lab第4弾の公開ツールです。全国陸域387,717個の1kmメッシュで算出した平均気温と、気象庁の1か月予報・3か月予報が示す地域別3階級確率を、同じ地点で読み比べます。

- 日本語名: 気候ものさしナビ
- 英語名: Climate Outlook Navi
- 公開URL: `https://nature-wx-lab.github.io/climate-outlook-navi/`

## 現在の実装範囲

- 要素: 201 平均気温
- 気候期間: `1991_2020` / `1996_2025`
- 区分: 月別1〜12と年平均13
- 表示: 絶対値 / `30年平均値の更新差（1996–2025 − 1991–2020）`
- 地点参照: 全387,717メッシュを176個の1次メッシュ別バイナリへ分割
- 季節予報: P1M / P3Mの気温3階級確率を、気象庁の現行地域変換規則で公式class15s境界へ展開。最大確率が同率の場合は灰色の「同率首位」と明示
- 操作: 地図クリック、現在地、表示状態URL、PNG保存、ベース地図、透明度

初回公開範囲は201平均気温の全量版です。残る7要素は、この公開repo上で同じデータ契約へ順次展開します。

## データ生成

共有気象基盤DBは読み取り専用です。入力先をコードへ固定せず、環境変数か引数で渡します。

```bash
export NATUREWXLAB_DB_ROOT="<shared-climate-db-root>"
python3 scripts/build_climate_data.py \
  --prefectures <prefecture-boundary.geojson>
python3 scripts/copy_leaflet_vendor.py \
  --source <leaflet-1.9.4-directory>
```

生成物は `data/climate/` の派生ファイルだけです。共有DBのgzip CSVや絶対パスは公開物へ含めません。

季節予報は次で更新します。

```bash
python3 scripts/update_season_forecast.py
python3 scripts/verify_contract.py
python3 scripts/privacy_gate.py
```

更新処理はP1M/P3M、class15s、area hierarchyを取得し、schema・確率合計・地域展開・境界件数を検査します。正常なcandidateだけをversioned datasetとして保存し、最後に `data/season/manifest.json` をatomicに差し替えます。取得や検査に失敗した場合は、前回正常datasetへの参照を保持します。

`datasets/` は現行manifestが参照するdatasetと直前正常版の計2世代を既定で保持します。promotion成功後だけ古い世代を削除するため、取得・検査失敗時のlast-known-goodは維持され、未参照datasetも無制限には蓄積しません。保持数は `--keep-datasets 1..5` で変更できます。

画面側でも対象期間終了と保守的な発表経過日数（P1M 9日、P3M 45日）を再判定し、古いデータを`利用可能`のまま固定表示しません。P3Mの本番更新スケジュールは公開前に気象庁の年次発表カレンダー連動へ更新する余地があります。

## ローカル起動

リポジトリ直下をHTTP配信します。

```bash
python3 -m http.server 8765
```

ブラウザで `http://127.0.0.1:8765/` を開きます。`file://` ではバイナリとJSONを読み込めません。

## 自動更新と公開

- `privacy-gate.yml`: 現行ファイル、到達可能なGit履歴、commit message、author/committer identityを検査します。
- `update-and-deploy.yml`: 毎日15:05 JSTごろに公式P1M/P3Mを確認し、内容が変わった場合だけGitHub Actions botで更新します。同じrunで契約検査済みのallowlist成果物だけをPagesへ配信し、公開後に全配信ファイルのchecksumを再確認します。

公開用Git identityは `nature-wx-lab` のGitHub noreply identityとGitHub Actions botだけを許可します。Web UI編集は使用しません。

## 表示契約

- `1991–2020年平均（独自算出・気象庁平年期間）`を「気象庁の平年値」と呼びません。
- 全国ラスターは初期表示を軽くするための描画縮約です。地点値は全387,717メッシュを保持する分割バイナリから読みます。
- 季節予報は地域単位の確率です。1kmへ補間せず、確率から絶対気温や偏差量を作りません。
- `P1M.json` / `P3M.json` は気象庁Web地図の運用JSONであり、schema変更に備えて更新時に毎回検証します。

## 出典

- 気候平均: 気象庁観測等をもとにNature Wx Labが独自算出・独自内挿した1km面
- 季節予報: `https://www.jma.go.jp/bosai/season/data/P1M.json` / `P3M.json`
- 予報地域境界: `https://www.jma.go.jp/bosai/common/const/geojson/class15s.json`
- 地域階層: `https://www.jma.go.jp/bosai/common/const/area.json`
- ベース地図: 国土地理院 地理院タイル
