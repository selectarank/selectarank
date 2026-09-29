# SelectaRank バッジ仕様（下書き・未デプロイ）

## 目的
x402 / MCP の売り手（サービス提供者）が、自分の README・LP・Bazaar 掲載説明文に
「エージェントからの選ばれやすさスコア」を shields.io 形式のバッジとして貼れるようにする。
バッジ自体が営業ツールになる（見せる場所が増えるほど無料の露出になる）。

## 仕組み: shields.io "endpoint" バッジ
shields.io は自前で画像を描かず、次の URL 形式で **JSON を返す静的エンドポイント**を
指せば任意のバッジを描画できる。

```
https://img.shields.io/endpoint?url=<SelectaRank側が返すJSONのURL>
```

JSON の形（shields.io の endpoint schema そのもの。第三者検証済みの一次仕様）:

```json
{
  "schemaVersion": 1,
  "label": "SelectaRank",
  "message": "82/100",
  "color": "brightgreen"
}
```

- `message` にスコア（0-100）を入れる。
- `color` はスコア帯で自動選定する（下記）。
- 実装上、SelectaRank 側は **サービスごとに1つの静的 JSON ファイル**を
  `https://selectarank.<domain>/badge/<host-slug>.json` のようなパスで配信すればよい。
  動的サーバー不要・CDN 静的配信で足りる（デプロイ先未定なのでここでは仕様のみ）。

## スコア色マッピング（案）
| スコア | color | 意味 |
|---|---|---|
| 80-100 | `brightgreen` | 上位帯（説明文・速さ・価格・実績が揃っている） |
| 65-79 | `green` | 良好 |
| 50-64 | `yellow` | 平均的。改善余地あり |
| 35-49 | `orange` | 選ばれにくい要因が複数 |
| 0-34 | `red` | 要改善（有料診断への導線） |

## 埋め込み例（売り手が README に貼る想定）

```markdown
![SelectaRank Score](https://img.shields.io/endpoint?url=https://selectarank.example/badge/api-example-com.json)
[![SelectaRank](https://img.shields.io/endpoint?url=https://selectarank.example/badge/api-example-com.json)](https://selectarank.example/s/api-example-com)
```

クリック先 (`/s/<slug>`) はそのサービスの内訳ページ（speed/desc/price/traction/freshness/meta の
6軸レーダーと、上位に入るための改善提案の要約 — 詳細は有料診断で）。

## slug 規則
`host` (例: `api.onesource.io`) を `.`→`-` に変換した文字列を slug とする
（例: `api-onesource-io`）。同一ホストに複数リソースがある場合はホスト単位で
最高スコアを採用する（1サービス=1バッジの原則。個別エンドポイント別バッジは将来検討）。

## 生成器
`badge/generate_badges.py` — `out/scored_all.jsonl` を読み、ホスト単位で最高スコアを
集計し、`badge/out/<slug>.json` に shields.io endpoint 形式で書き出す（ローカル生成のみ。
公開ホスティングは行わない＝デプロイ未実施）。

## 未確定・承認待ち
- 実際の配信ドメイン（`selectarank.example` はプレースホルダ。ops/APPROVALS.md では
  ドメインは「まだ無いもの」として保留中）
- バッジ画像自体は shields.io 側がホストするため無料。SelectaRank 側はJSON配信のみでよく、
  費用はほぼゼロ（静的ホスティング1ファイル/サービス）。
