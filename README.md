# 通学路 危険区間スコアリング（市川市）

CODIHA 2026（ちばオープンデータハッカソン）参加作品。

市川市の通学路データと警察庁の交通事故統計を突き合わせ、通学路から 25m 以内で起きた事故を学校ごとに集計して危険度スコアを算出する Web アプリです。

- スコア = (死亡事故 ×10 + 歩行者・自転車関与事故 ×3 + その他の事故 ×1) を通学路延長で正規化
- 地図上で通学路をスコア上位 20% / 50% / それ以外で色分けし、事故地点を種別ごとにプロット
- 学校別の危険度ランキング表を表示

## 起動

```bash
docker compose up
```

ブラウザで http://localhost:5002/ を開きます。終了は `docker compose down`。
起動のたびに `src/data_src/` の生データから `data/app.db`（SQLite）を再生成します。

## 構成

- `src/db.py`: Shapefile・CSV の読み込み、座標変換（平面直角座標系第9系 → WGS84）、近接判定、スコアリング
- `src/app.py`: Flask アプリ
- `src/data_src/`: 使用データ（通学路 Shapefile、市川市分に抽出した事故 CSV）

## 使用データ

- 市川市オープンデータ「小学校通学ルート」: https://www.city.ichikawa.lg.jp/pla01/opendata.html
- 警察庁オープンデータ「交通事故統計情報」（2024 年、市川市分を抽出）: https://www.npa.go.jp/publications/statistics/koutsuu/opendata/index_opendata.html

データの利用条件は各提供元に従います。コードのライセンスは [MIT](LICENSE) です。

## 注意

事故地点と通学路の近接に基づく簡易スコアであり、実際の危険度を保証するものではありません。
