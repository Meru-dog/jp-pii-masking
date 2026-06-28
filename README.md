# JP PII Masking — 日本語文書 機密情報マスキングシステム
# JP PII Masking — Confidential Information Masking for Japanese Documents

日本語文書（PDF / Word / Markdown / プレーンテキスト）に含まれる個人情報・機密情報を検出し、
**不可逆なタグ置換**でマスキングする、AWS ネイティブのサーバーレスシステム。法務実務で利用できる
「法務 × 技術」の実装プロジェクトとして構築している。

*An AWS-native, serverless system that detects personal and confidential information in Japanese
documents (PDF / Word / Markdown / plain text) and masks it via **irreversible tag substitution**.
Built as a "law × technology" project intended for real legal-practice use.*

検出は **3層の和集合**で行う。
*Detection is performed as the **union of three layers**:*

- **規則層（正規表現）** — マイナンバー・法人番号・銀行口座・郵便番号・生年月日・電話・メール・
  IP・URL・カード番号など、日本固有の構造化識別子を決定論的に捕捉する。これらはマネージド検出層の
  組み込み型に存在しないため、規則層が唯一の捕捉手段となる。
  *Rule layer (regex) — deterministically captures Japan-specific structured identifiers (My Number,
  corporate number, bank account, postal code, date of birth, phone, email, IP, URL, card number).
  These have no built-in type in the managed detectors, so the rule layer is their only means of capture.*
- **Amazon Bedrock Guardrails（機微情報フィルタ）** — 氏名・住所など文脈依存の PII を、日本語最適化済みの
  マネージド検出で捕捉する（`ApplyGuardrail` を FM 非呼び出しで使用）。
  *Amazon Bedrock Guardrails (sensitive information filter) — captures context-dependent PII such as
  names and addresses using Japanese-optimized managed detection (`ApplyGuardrail` without invoking an FM).*
- **Amazon Comprehend（汎用エンティティ認識・日本語）** — 組織名・役職・施設名など、Guardrails が
  型を持たない領域を補完する。
  *Amazon Comprehend (general entity recognition, Japanese) — complements the areas Guardrails has no
  type for, such as organization names, job titles, and facility names.*

> 設計の経緯・判断根拠は [`docs/RDD.md`](docs/RDD.md)（要件定義書）、[`docs/implementation-plan.md`](docs/implementation-plan.md)、
> [`docs/phase0-coverage-matrix.md`](docs/phase0-coverage-matrix.md)（カバレッジ実測の設計）を参照。
>
> *For design rationale and decisions, see [`docs/RDD.md`](docs/RDD.md) (requirements definition),
> [`docs/implementation-plan.md`](docs/implementation-plan.md), and
> [`docs/phase0-coverage-matrix.md`](docs/phase0-coverage-matrix.md) (coverage-measurement design).*

---

## アーキテクチャ / Architecture
[Web UI (React, Cognito認証)]

│  ① 署名付きURLでS3へ直接アップロード / Direct upload to S3 via presigned URL

▼

[S3 入力バケット (CMK暗号化) / Input bucket (CMK-encrypted)]

│  ② S3イベント → EventBridge → 起動 / S3 event → EventBridge → trigger

▼

[処理 Lambda (コンテナ・VPC内・egレスなし) / Processing Lambda (container, in-VPC, no egress)]

抽出 → チャンク分割 → 3層検出(規則 ∪ Guardrails ∪ Comprehend)

→ 和集合統合 → 不可逆タグ置換

extract → chunk → 3-layer detection (rule ∪ Guardrails ∪ Comprehend)

→ union merge → irreversible tag substitution

│  （Bedrock/Comprehend へは VPCインターフェースエンドポイント経由 / via VPC interface endpoints）

▼

[S3 出力バケット (CMK暗号化)]  … マスク済みテキスト + 検出メタJSON

[Output bucket (CMK-encrypted)] … masked text + detection metadata JSON

│  ③ Web UI で確認（墨消し表示）→ 承認 / Review (redaction view) → approve

▼

[確認済み成果物 / Approved artifact]

- **秘匿性 / Confidentiality**：全文書を最高階層相当で扱う。VPC 内処理 + PrivateLink（S3/Bedrock/Comprehend）で
  処理経路にインターネット egress を持たない。入出力・中間生成物は CMK 暗号化。中間生成物は非永続。
  *Every document is handled at the highest sensitivity tier. Processing runs in-VPC with PrivateLink
  (S3/Bedrock/Comprehend) so the processing path has no internet egress. Inputs, outputs, and
  intermediate artifacts are CMK-encrypted; intermediates are not persisted.*
- **捕捉性（recall）/ Recall**：単一の確率的検出器では漏洩ゼロを保証できないため、3層和集合 + 全件人手レビューの
  二段構えで実効漏洩率をゼロ近傍に抑える設計。
  *A single probabilistic detector cannot guarantee zero leakage, so a two-stage design — the 3-layer
  union plus full human review — drives the effective leakage rate toward zero.*
- **マスク方式 / Masking**：タグ置換（例 `[氏名]`）。不可逆。原文・マッピングは保持しない。
  *Tag substitution (e.g. `[氏名]` = "[name]"). Irreversible; the original text and mapping are not retained.*

---

## リポジトリ構成 / Repository layout
.

├── docs/                  設計ドキュメント / Design docs (RDD, implementation plan, coverage design)

├── detection-harness/     検出コア＋評価ハーネス / Detection core + evaluation harness (runs without AWS)

│   ├── detection/         検出コア / Detection core (rule layer, chunking, union, masking, extraction)

│   │                      ＋ アダプタ / + adapters (Guardrails / Comprehend)

│   ├── evaluation/        合成日本語データ / Synthetic Japanese data (no real PII) + metrics & gate

│   └── tests/             単体テスト / Unit tests

├── infra/                 AWS CDK (Python)

│   ├── stacks/            Foundation(S3/KMS/IAM) / Processing(VPC/Lambda) / Api(Cognito/HTTP API)

│   └── lambda/            処理 Lambda（コンテナ）/ API Lambda

└── ui/                    React 単一HTML / React single HTML (no build step)

各ディレクトリに個別の README がある。
*Each directory has its own README.*

---

## クイックスタート / Quick start

### 1. 検出ハーネスを手元で試す（AWS不要） / Try the detection harness locally (no AWS)
```bash
cd detection-harness
python run_harness.py --by-variant
```
規則層のみで合成データの recall を測定できる。3層実測（Guardrails/Comprehend を有効化）は
`detection-harness/README.md` を参照。
*Measures recall on synthetic data using the rule layer alone. For the full 3-layer measurement
(enabling Guardrails/Comprehend), see `detection-harness/README.md`.*

### 2. AWS にデプロイ / Deploy to AWS
前提：AWS CLI 認証、Node.js、Docker、Python 3.12+、AWS CDK CLI（`npm i -g aws-cdk`）。
事前に Bedrock Guardrail を作成し、機微情報フィルタ（NAME/ADDRESS/EMAIL/PHONE/CARD/IP/URL）を有効化する。
*Prerequisites: authenticated AWS CLI, Node.js, Docker, Python 3.12+, AWS CDK CLI (`npm i -g aws-cdk`).
Create a Bedrock Guardrail beforehand and enable the sensitive-information filter
(NAME/ADDRESS/EMAIL/PHONE/CARD/IP/URL).*
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
*For details, cost, and the staged build, see `infra/README.md`.*

### 3. UI
`ui/README.md` を参照。デプロイ後の出力値（API エンドポイント・Cognito の Pool/Client ID）を
UI の「接続設定」に入力する。値はソースに埋め込まない。
*See `ui/README.md`. Enter the post-deploy outputs (API endpoint, Cognito Pool/Client ID) into the
UI's "connection settings". These values are never hard-coded in the source.*

---

## セキュリティと従量課金に関する注意（公開・運用前に必読）
## Security & usage-cost notes (read before publishing or operating)

このリポジトリは設計情報のみを含み、アカウント固有値（アカウントID・Guardrail ID・エンドポイント・
バケット名・Cognito ID 等）は**一切含まない**。デプロイ環境では以下を守ること。
*This repository contains design information only and includes **no** account-specific values
(account ID, Guardrail ID, endpoints, bucket names, Cognito IDs, etc.). In a deployed environment,
observe the following.*

- **実値をコミットしない / Never commit real values**：UI は設定値を画面入力、Lambda は環境変数（CDK が注入）から読む。
  実値をソースやドキュメントに埋め込まない。`.env` 等は `.gitignore` 済み。
  *The UI takes settings via the screen and the Lambda reads them from environment variables
  (injected by CDK); never embed real values in source or docs. `.env` files are git-ignored.*
- **認証は招待制 / Invitation-only auth**：Cognito はセルフサインアップ無効（`self_sign_up_enabled=False`）。
  利用者は管理者が作成する。API は JWT 必須で、未認証リクエストは 401。
  *Cognito self-sign-up is disabled; users are created by an admin. The API requires a JWT and
  rejects unauthenticated requests with 401.*
- **CORS は本番で絞る / Restrict CORS in production**：開発の既定は全オリジン許可（`ui_origin="*"`）。本番は配信オリジンに
  限定する（`cdk deploy -c ui_origin=https://<your-domain>`）。
  *The development default allows all origins (`ui_origin="*"`). In production, limit it to your
  delivery origin (`cdk deploy -c ui_origin=https://<your-domain>`).*
- **従量課金の歯止め / Guardrails against runaway cost**：想定外アクセスに備え、(1) AWS Budgets でコストアラート、
  (2) API Gateway のスロットリング（レート/バースト上限）、(3) 不要時は処理スタックを `cdk destroy` してVPCインターフェース
  エンドポイント（時間課金）を停止、を推奨。
  *To guard against unexpected access: (1) cost alerts via AWS Budgets, (2) API Gateway throttling
  (rate/burst limits), (3) when idle, `cdk destroy` the processing stack to stop the VPC interface
  endpoints (billed per hour).*
- **Bedrock 呼び出しログは無効を維持 / Keep Bedrock invocation logging disabled**：原文がログに残らないようにする（不可逆マスクの趣旨）。
  *Ensure original text is not retained in logs (consistent with irreversible masking).*

---

## 既知の制約 / Known limitations

- **過剰マスク（FP）が起こりうる / Over-masking (false positives) can occur**：精度方針は recall 最優先・FP 許容のため、
  相対日付表現（「翌月末」等）や法律用語（「日本法」「合意管轄」等）を日付・組織・役職と誤検出してマスクすることがある。
  漏洩は生じないが、文書の可読性は損なわれうる。最終的な品質は全件人手レビューで担保する前提。
  *Because the policy is recall-first and tolerates false positives, relative date expressions
  (e.g. "end of next month") and legal terms (e.g. "Japanese law", "agreed jurisdiction") may be
  mis-detected and masked as dates/organizations/titles. No leakage results, but readability can
  suffer; final quality is assured by full human review.*
- **手動マスク編集は未実装 / Manual mask editing not implemented**：レビューでマスク漏れを見つけた場合の手動修正は今後の対応。
  現状は出力破棄＋原文側対処で運用する。
  *Manual correction of missed masks during review is future work; for now, discard the output and
  handle it on the source side.*
- **OCR 非対応 / No OCR**：テキスト抽出可能な電子文書のみ（スキャンPDFは対象外）。
  *Only text-extractable electronic documents (scanned PDFs are out of scope).*
- **体裁保持マスク（元のPDF/Word体裁での再生成）は未実装 / Layout-preserving masking not implemented**：第1段階はテキスト/JSON 出力。
  *The first stage outputs text/JSON; regenerating the original PDF/Word layout is not yet implemented.*
- **単一テナント前提 / Single-tenant**：少数の認証済み利用者・権限分掌なし。
  *A small number of authenticated users; no role separation.*

---

## ライセンス / License
MIT License（[LICENSE](LICENSE)）。

> 本ソフトウェアは現状有姿で提供される。実データ（実PII）での利用にあたっては、各組織の
> コンプライアンス要件・委託先監督・安全管理措置に照らして適合性を検証すること。
>
> *This software is provided as-is. Before using it with real data (real PII), verify its suitability
> against your organization's compliance requirements, oversight of subcontractors, and security
> management measures.*