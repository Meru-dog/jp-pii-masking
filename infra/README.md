# 秘匿性基盤 IaC（S3 Foundation + S4 Processing）

RDD v1.1 §8 のセキュリティ要件を CDK(Python) で実体化する。本段階は **S3バケット・CMK・
最小権限IAM** のみを構築する。VPC/エンドポイントは次段（S4）で追加する（下記「段階構築」参照）。

## 構築されるもの
- **入力/出力 S3 バケット**：パブリックアクセス全面ブロック・CMK暗号化・TLS必須（非TLS拒否）・
  バージョニング・ライフサイクル（中間生成物 `work/` は1日、入力7日、出力30日で失効＝D-2）。
- **KMS CMK**：自動ローテーション有効、エイリアス `alias/jp-pii-masking`。
- **IAM ロール2種（分離）**：
  - 処理ロール：入力読取／出力書込／KMS／`bedrock:ApplyGuardrail`／`comprehend:DetectEntities`／Logs。
  - UI/APIロール：署名付きURL用の入力put・出力read・KMSのみ（**検出系権限なし**）。

## 段階構築（現状：S4まで実装済み）
- **Foundation 段（S3）**：S3・CMK・IAM（固定費ほぼゼロ）。
- **Processing 段（S4）**：VPC（NATなし）・VPCエンドポイント・コンテナLambda・EventBridge起動。
  ここで「処理経路にインターネットegressなし」（RDD §8）を達成する。
- **Bedrock モデル呼び出しログ**：本IaCでは作らない。**「有効化しない」ことで無効を担保**する
  （RDD §8：原文残存の防止）。アカウントで既に有効化している場合は、本システムのリージョン/用途で
  無効になっていることを確認すること。

## 前提
- AWS CLI 認証済み、Node.js（CDK CLI が使用）、Python 3.x。
- CDK CLI: `npm install -g aws-cdk`
- 東京リージョン（ap-northeast-1）を既定とする。

## デプロイ手順
```bash
cd infra
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export CDK_DEFAULT_ACCOUNT=<ACCOUNT_ID>
export CDK_DEFAULT_REGION=ap-northeast-1

# 初回のみ（アカウント/リージョンのブートストラップ）
cdk bootstrap

# 差分確認 → デプロイ
cdk diff
cdk deploy -c guardrail_id=<GUARDRAIL_ID>
```
- `-c guardrail_id=<ID>` を渡すと、処理ロールの `bedrock:ApplyGuardrail` を当該 Guardrail の
  ARN に絞り込む（未指定なら in-region 全 Guardrail）。

## S4（処理関数ホスト）の追加デプロイ本リポジトリには2スタックがある。
- `JpPiiMaskingFoundation`（S3段）：S3・CMK・IAM。
- `JpPiiMaskingProcessing`（S4段）：VPC・VPCエンドポイント・コンテナLambda・EventBridge起動。

**前提**: ローカルで Docker が起動していること（コンテナLambdaのビルドに使用）。

```bash
# 両スタックをまとめてデプロイ（Processing は Foundation をクロススタック参照）
cdk deploy --all -c guardrail_id=<GUARDRAIL_ID>
```
- Processing のデプロイ時、Foundation も更新される（クロススタックExport追加、入力バケットの
  EventBridge通知有効化、処理ロールへのENI権限付与）。これは想定どおりの安全な更新。
- デプロイ後、入力バケットへ PDF/Word/MD/TXT をアップロードすると EventBridge→Lambda が起動し、
  出力バケットに `<同名>.masked.txt` と `<同名>.meta.json` が生成される（ステータス review_pending）。

### 動作確認
```bash
aws s3 cp ./sample.txt s3://<INPUT_BUCKET>/2026/06/sample.txt
# 数秒後
aws s3 ls s3://<OUTPUT_BUCKET>/2026/06/
aws s3 cp s3://<OUTPUT_BUCKET>/2026/06/sample.masked.txt -
```

## S5（API ＋ 認証）の追加デプロイ
スタック `JpPiiMaskingApi`（Cognito User Pool・HTTP API・JWT認証・APIハンドラLambda）。
ファイル実体はブラウザ↔S3を署名付きURLで直結し、API Lambdaを経由させない。

```bash
cdk deploy JpPiiMaskingApi -c guardrail_id=<GUARDRAIL_ID>
```
- 出力：`ApiEndpoint`（HTTP APIのURL）、`UserPoolId`、`UserPoolClientId`。S6（UI）で参照する。

### 利用者の作成（招待制・self_sign_up無効）
```bash
aws cognito-idp admin-create-user \
  --user-pool-id <UserPoolId> --username <email> \
  --user-attributes Name=email,Value=<email> Name=email_verified,Value=true
# 初回ログイン用の恒久パスワード設定（必要に応じ）
aws cognito-idp admin-set-user-password \
  --user-pool-id <UserPoolId> --username <email> --password '<StrongPassw0rd!>' --permanent
```

### エンドポイント（すべて JWT 認証必須）
- `POST /uploads`            : アップロード用 署名付きURL（PutObject）を発行（body: {"key": "2026/06/x.pdf"}）
- `GET  /documents?prefix=`  : 出力バケットのディレクトリビュー（folders/files）
- `GET  /documents/download?key=` : 成果物取得用 署名付きURL（GetObject）
- `POST /documents/approve`  : メタJSONを review_pending → approved（body: {"meta_key": "2026/06/x.meta.json"}）

### 動作確認（JWT取得 → API呼び出し）
```bash
# IDトークン取得（USER_PASSWORD_AUTH）
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH --client-id <UserPoolClientId> \
  --auth-parameters USERNAME=<email>,PASSWORD='<StrongPassw0rd!>' \
  --query "AuthenticationResult.IdToken" --output text)

# 成果物のディレクトリビュー
curl -s -H "Authorization: $TOKEN" "<ApiEndpoint>/documents?prefix=2026/06/" | jq
```

## コスト目安
- S3・KMS は本規模（月50文書）ではほぼ無視できる額。
- Foundation 段（S3・CMK・IAM）はデプロイしても固定費はほぼ発生しない。
- **Processing 段の Interface VPCエンドポイント（Bedrock-runtime/Comprehend/KMS/Logs の4つ）は時間課金**で、
  本構成の固定費の主因。NATゲートウェイは使わない（egレスなし・コスト抑制）。
  コストを止めたい場合は `cdk destroy JpPiiMaskingProcessing` でVPC/エンドポイントを撤去できる
  （Foundation は残す）。

## teardown（PoC片付け）
既定は `retain=true`（バケット・鍵を保持）。破棄を容易にするには:
```bash
cdk deploy -c retain=false   # バケット自動空化・鍵破棄を許可
cdk destroy
```
※ `retain=false` はオブジェクトとCMKを失う。実データ投入後は使用しないこと。

## 出力（デプロイ後に控える）
- InputBucketName / OutputBucketName / KmsKeyArn / ProcessingRoleArn / UiApiRoleArn
  → S4（処理関数）以降で参照する。
