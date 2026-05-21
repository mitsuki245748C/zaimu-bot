# zaimu-bot

Raspberry Pi 上で動かす個人用の家計集計ボットです。

PayPayの取引CSVや、サブスク・通販・電話料金などの領収書メールを取り込み、月ごとの支出集計をLINE botへ通知することを目的にしています。

## 方針

- PayPay: iPhoneでCSVをダウンロードし、Raspberry PiのHTTP APIへ送信する
- Netflix / Amazon / 電話料金など: 専用メールアドレスに通知や領収書を集める
- 集計データ: ローカルのSQLiteに保存する予定
- 通知: LINE botで月次集計を送る予定

## 技術構成

- Runtime: Docker
- Language: Python
- Upload API: Python標準ライブラリのHTTPサーバー
- Data directory: `data/`
- PayPay CSV inbox: `data/imports/paypay/inbox/`

## セキュリティ方針

- `.env` はGit管理しない
- アップロードAPIは `UPLOAD_TOKEN` で認証する
- ルーター側でRaspberry Piのポート開放はしない
- 外部から使う場合は、直接公開ではなくVPNやTailscaleなどを使う

## 現在の実装

- iPhoneショートカットからPayPay CSVを受け取るHTTP API
- `Transactions*.csv` 形式のファイル名だけを受け付ける保存処理
- Docker Composeによる起動構成
