# JP PII Masking — 日本語文書 機密情報マスキングシステム

日本語文書（PDF / Word / Markdown / プレーンテキスト）に含まれる個人情報・機密情報を検出し、
**不可逆なタグ置換**でマスキングする、AWS ネイティブのサーバーレスシステム。法務実務で利用できる
「法務 × 技術」の実装プロジェクトとして構築している。

検出は **3層の和集合**で行う。

- **規則層（正規表現）** — マイナンバー・法人番号・銀行口座・郵便番号・生年月日・電話・メール・
  IP・URL・カード番号など、日本固有の構造化識別子を決定論的に捕捉する。これらはマネージド検出層の
  組み込み型に存在しないため、規則層が唯一の捕捉手段となる。
- **Amazon Bedrock Guardrails（機微情報フィルタ）** — 氏名・住所など文脈依存の PII を、日本語最適化済みの
  マネージド検出で捕捉する（`ApplyGuardrail` を FM 非呼び出しで使用）。
- **Amazon Comprehend（汎用エンティティ認識・日本語）** — 組織名・役職・施設名など、Guardrails が
  型を持たない領域を補完する。

> 設計の経緯・判断根拠は [`docs/RDD.md`](docs/RDD.md)（要件定義書）、[`docs/implementation-plan.md`](docs/implementation-plan.md)、
> [`docs/phase0-coverage-matrix.md`](docs/phase0-coverage-matrix.md)（カバレッジ実測の設計）を参照。

---

## アーキテクチャ

```
[Web UI (React, Cognito認証)]
   │  ① 署名付きURLでS3へ直接アップロード
   ▼
[S3 入力バケット (CMK暗号化)]
   │  ② S3イベント → EventBridge → 起動
   ▼
[処理 Lambda (コンテナ・VPC内・egレスなし)]
   抽出 → チャンク分割 → 3層検出(規則 ∪ Guardrails ∪ Comprehend)
        → 和集合統合 → 不可逆タグ置換
   │  （Bedrock/Comprehend へは VPCインターフェースエンドポイント経由）
   ▼
[S3 出力バケット (CMK暗号化)]  … マスク済みテキスト + 検出メタJSON
   │  ③ Web UI で確認（墨消し表示）→ 承認
   ▼
[確認済み成果物]
```

- **秘匿性**：全文書を最高階層相当で扱う。VPC 内処理 + PrivateLink（S3/Bedrock/Comprehend）で
  処理経路にインターネット egress を持たない。入出力・中間生成物は CMK 暗号化。中間生成物は非永続。
- **捕捉性（recall）**：単一の確率的検出器では漏洩ゼロを保証できないため、3層和集合 + 全件人手レビューの
  二段構えで実効漏洩率をゼロ近傍に抑える設計。
- **マスク方式**：タグ置換（例 `[氏名]`）。不可逆。原文・マッピングは保持しない。

---

## リポジトリ構成

```
.
├── docs/                  設計ドキュメント（RDD・実装計画・カバレッジ実測設計）
├── detection-harness/     検出コア＋評価ハーネス（AWS非依存で recall を実測可能）
│   ├── detection/         検出コア（規則層・チャンク・統合・マスク・抽出）＋ アダプタ（Guardrails/Comprehend）
│   ├── evaluation/        合成日本語データ（実PII不使用）＋ 評価指標・ゲート判定
│   └── tests/             単体テスト
├── infra/                 AWS CDK(Python)
│   ├── stacks/            Foundation(S3/KMS/IAM) / Processing(VPC/Lambda) / Api(Cognito/HTTP API)
│   └── lambda/            処理 Lambda（コンテナ）/ API Lambda
└── ui/                    React 単一HTML（ビルド不要）
```

各ディレクトリに個別の README がある。

---

## クイックスタート

### 1. 検出ハーネスを手元で試す（AWS不要）
```bash
cd detection-harness
python run_harness.py --by-variant
```
規則層のみで合成データの recall を測定できる。3層実測（Guardrails/Comprehend を有効化）は
`detection-harness/README.md` を参照。

### 2. AWS にデプロイ
前提：AWS CLI 認証、Node.js、Docker、Python 3.12+、AWS CDK CLI（`npm i -g aws-cdk`）。
事前に Bedrock Guardrail を作成し、機微情報フィルタ（NAME/ADDRESS/EMAIL/PHONE/CARD/IP/URL）を有効化する。
```bash
cd infra
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export CDK_DEFAULT_ACCOUNT=<ACCOUNT_ID>
export CDK_DEFAULT_REGION=ap-northeast-1
cdk bootstrap
cdk deploy --all -c guardrail_id=<GUARDRAIL_ID>
```
詳細・コスト・段階構築は `infra/README.md` を参照。

### 3. UI
`ui/README.md` を参照。デプロイ後の出力値（API エンドポイント・Cognito の Pool/Client ID）を
UI の「接続設定」に入力する。値はソースに埋め込まない。

---

## セキュリティと従量課金に関する注意（公開・運用前に必読）

このリポジトリは設計情報のみを含み、アカウント固有値（アカウントID・Guardrail ID・エンドポイント・
バケット名・Cognito ID 等）は**一切含まない**。デプロイ環境では以下を守ること。

- **実値をコミットしない**：UI は設定値を画面入力、Lambda は環境変数（CDK が注入）から読む。
  実値をソースやドキュメントに埋め込まない。`.env` 等は `.gitignore` 済み。
- **認証は招待制**：Cognito はセルフサインアップ無効（`self_sign_up_enabled=False`）。
  利用者は管理者が作成する。API は JWT 必須で、未認証リクエストは 401。
- **CORS は本番で絞る**：開発の既定は全オリジン許可（`ui_origin="*"`）。本番は配信オリジンに
  限定する（`cdk deploy -c ui_origin=https://<your-domain>`）。
- **従量課金の歯止め**：想定外アクセスに備え、(1) AWS Budgets でコストアラート、(2) API Gateway の
  スロットリング（レート/バースト上限）、(3) 不要時は処理スタックを `cdk destroy` してVPCインターフェース
  エンドポイント（時間課金）を停止、を推奨。
- **Bedrock 呼び出しログは無効を維持**：原文がログに残らないようにする（不可逆マスクの趣旨）。

---

## 既知の制約

- **過剰マスク（FP）が起こりうる**：精度方針は recall 最優先・FP 許容のため、相対日付表現（「翌月末」等）や
  法律用語（「日本法」「合意管轄」等）を日付・組織・役職と誤検出してマスクすることがある。漏洩は
  生じないが、文書の可読性は損なわれうる。最終的な品質は全件人手レビューで担保する前提。
- **手動マスク編集は未実装**：レビューでマスク漏れを見つけた場合の手動修正は今後の対応。現状は
  出力破棄＋原文側対処で運用する。
- **OCR 非対応**：テキスト抽出可能な電子文書のみ（スキャンPDFは対象外）。
- **体裁保持マスク（元のPDF/Word体裁での再生成）は未実装**：第1段階はテキスト/JSON 出力。
- **単一テナント前提**：少数の認証済み利用者・権限分掌なし。

---

## ライセンス
MIT License（[LICENSE](LICENSE)）。

> 本ソフトウェアは現状有姿で提供される。実データ（実PII）での利用にあたっては、各組織の
> コンプライアンス要件・委託先監督・安全管理措置に照らして適合性を検証すること。
# jp-pii-masking
