# Web UI（確認コンソール）— S6

Cognible ログイン → 文書アップロード → 成果物のディレクトリビュー → 墨消しレビュー → 承認、までを
ブラウザから行う最小UI。React + Babel を CDN から読み込む**単一HTML**で、ビルド工程・npm依存なし。

## 構成
- `index.html` のみ。React/ReactDOM/Babel を CDN（unpkg）から読み込む。
- 認証は Cognito `InitiateAuth`（USER_PASSWORD_AUTH）を `fetch` で直接呼ぶ（AWS SDK不要）。
- ファイル実体はブラウザ↔S3を署名付きURLで直結（アップロードPUT・閲覧GET）。API は署名付きURL発行と
  一覧・承認のみ。

## 事前準備（重要）：S3 CORS の有効化
ブラウザから S3 へ直接アップロード/ダウンロードするため、入出力バケットに CORS が必要。
基盤スタックに追加済みなので、**Foundation を再デプロイ**して反映する。
```bash
cd infra
# UI のオリジンに絞る場合は -c ui_origin=https://<your-cloudfront-domain>
cdk deploy JpPiiMaskingFoundation -c guardrail_id=<GUARDRAIL_ID>
```
開発中は既定 `ui_origin="*"` で全オリジンを許可（本番は配信オリジンに絞ること）。

## 使い方（ローカル確認）
`index.html` をブラウザで開く（ローカルファイルでも動作）。初回は「接続設定」に以下を入力：
- API エンドポイント：`JpPiiMaskingApi.ApiEndpoint`（例 `https://xxxx.execute-api.ap-northeast-1.amazonaws.com`）
- リージョン：`ap-northeast-1`
- ユーザープール クライアントID：`JpPiiMaskingApi.UserPoolClientId`

設定はブラウザに保存される。Cognito の利用者（admin-create-user で作成）でサインインする。

> ローカルファイル（file://）で CORS や混在コンテンツの制限に当たる場合は、簡易サーバで配信：
> `python3 -m http.server 8000` → `http://localhost:8000/index.html`。
> その場合、`ui_origin` に `http://localhost:8000` を含める（開発中は `*` で可）。

## 操作
1. **アップロード**：保存先プレフィックス（例 `2026/06/`）を指定し、PDF/Word/MD/TXT を選択。
   署名付きURL経由でS3へ直接PUT。処理後、数十秒で成果物（`*.masked.txt` / `*.meta.json`）が一覧に出る。
2. **一覧**：プレフィックス階層をディレクトリビューで辿る。各成果物の状態（確認待ち/承認済み）を表示。
3. **確認**：マスク済みテキストを墨消しチップで表示。右側に検出内訳（区分・位置・捕捉層）。
   マスク漏れがないか確認する。
4. **承認**：「確認済みとして承認」でメタJSONを `approved` に更新。承認の印影を表示。

## 本番配信（任意）
S3（静的ホスティング用の別バケット）＋ CloudFront に `index.html` を配置し、その配信ドメインを
`-c ui_origin=https://<domain>` で CORS 許可先に指定する。Cognito はホストUIを使わず本UIで完結。

## 制約（現バージョン）
- マスク漏れ発見時の手動マスク編集は未実装（出力破棄＋原文側対処を案内）。今後の対応。
- 単一テナント・少数利用者前提（権限分掌なし）。
