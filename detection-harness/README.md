# Phase 0 第二段 実測ハーネス（S1）

RDD 完成版 v1.1 の検出コア（C1）・検出アダプタ（C2）・評価（C4）の最小実装。
**3層和集合検出（規則層 ∪ Guardrails ∪ Comprehend汎用NER）の日本語 recall を実測する**ためのもの。

実装計画 S1 に対応。本ハーネスは S2（検出コアの確立）の土台も兼ねる。

---

## 1. 構成

```
harness/
├─ detection/                # C1 検出コア + C2 アダプタ
│  ├─ spans.py               # Spanモデル・和集合統合（広い方優先）
│  ├─ regex_layer.py         # 規則層（日本固有識別子・独立工程）
│  ├─ chunking.py            # チャンク分割・オフセット再マッピング
│  ├─ extraction.py          # テキスト抽出（PDF/Word/MD/プレーン・ローカル完結）[S2]
│  ├─ guardrails_adapter.py  # Bedrock Guardrails（ApplyGuardrail, FM非呼出, リトライ）
│  ├─ comprehend_adapter.py  # Comprehend DetectEntities（ja, リトライ）
│  ├─ retry.py               # 指数バックオフ・リトライ [S2]
│  ├─ masking.py             # 不可逆タグ置換
│  ├─ config.py              # 設定外部化（有効化型・閾値・ゲート基準）[S2]
│  ├─ pipeline.py            # 3層オーケストレーション（text入力）
│  └─ process.py             # ファイル/バイト処理（抽出→検出, 中間生成物非永続）[S2]
├─ evaluation/               # C4 評価
│  ├─ synthetic_data.py      # 合成日本語データ（8サンプル・variant付き）
│  └─ metrics.py             # recall・漏洩率・過剰マスク率・ゲート判定（§7.4）
├─ tests/                    # 単体テスト（AWS非接続）[S2]
│  └─ test_detection.py
├─ run_harness.py            # CLIエントリ（評価ハーネス）
└─ requirements.txt
```

### 単体テスト [S2]
```bash
python -m unittest discover -s tests -v
```
規則層・和集合統合・チャンク分割・マスキング・テキスト抽出・リトライの純ロジックを検証（AWS不要）。

### ファイル処理 [S2]
```python
from detection import process
res = process.process_file("契約書.pdf")          # 規則層のみ
# res = process.process_file("契約書.pdf", g_adapter, c_adapter)  # 3層
print(res.detection.masked_text)
```
対応形式：PDF / Word(docx) / Markdown / プレーンテキスト（OCR非対象）。抽出はローカル完結。
全角数字の多い文書は `normalize_width=True` で半角正規化して規則層 recall を上げられる（既定オフ）。

---

## 2. 実行方法

### 2.1 規則層のみ（AWS不要・手元検証）
```bash
python run_harness.py --show-masked
```
日本固有識別子（マイナンバー・法人番号・口座・郵便番号・生年月日・電話・メール・IP・URL・カード）の
規則層 recall を検証できる。文脈依存（氏名・住所・組織名・役職・施設名）は検出されない（設計どおり）。

表記形(variant)別の分解表示:
```bash
python run_harness.py --by-variant
```
文脈依存エンティティを表記形（分かち書き有無・ひらがな/カタカナ・法人格の位置 等）別に
分解して recall を表示する。AWS 2層を有効化すると「どの表記形で取りこぼすか」が判明する。

### 2.2 3層（AWS必要・recall実測）
```bash
pip install -r requirements.txt
python run_harness.py \
  --use-guardrails --guardrail-id <GUARDRAIL_ID> --guardrail-version DRAFT \
  --use-comprehend --region ap-northeast-1
```

JSON出力:
```bash
python run_harness.py --use-guardrails --guardrail-id <ID> --use-comprehend --region ap-northeast-1 --json
```

---

## 3. AWS 前提（3層実測時）

1. **Bedrock Guardrail を作成**（東京リージョン ap-northeast-1 推奨）。
   - 機微情報フィルタを有効化し、PII型を有効化：NAME, ADDRESS, EMAIL, PHONE,
     CREDIT_DEBIT_CARD_NUMBER, IP_ADDRESS, URL（本ハーネスの想定に合わせる）。
   - 作成後の **Guardrail ID** と **バージョン**（DRAFT 可）を控える。
2. **IAM 権限**（実行主体に付与）：
   - `bedrock:ApplyGuardrail`
   - `comprehend:DetectEntities`
3. **秘匿性（RDD §8 / 本番要件）**：本ハーネスは検証用だが、本番では
   **Bedrock モデル呼び出しログを無効化**し、Comprehend 入出力を永続化しないこと。
   検証時も実データ（実PII）は投入しない（本ハーネスは合成データのみ）。
4. **リージョン整合**：Guardrails・Comprehend(ja)・（将来の）Bedrock モデルが
   当該リージョンで提供されることを確認（Phase 0 のリージョン最終確認に対応）。
5. ネットワーク：本番は VPC 内から PrivateLink 経由（S3 / Bedrock / Comprehend）。
   本ハーネス単体検証では必須ではない。

---

## 4. 結果の読み方とゲート（§7.4）

- **規則層エンティティ**（日本固有識別子）：`recall = 1.0`（漏洩ゼロ）を要求。
- **文脈依存エンティティ**（氏名・住所・組織名・役職・施設名）：`recall >= 0.98` を目標。
- 主指標は **char_leak_rate（文字レベル漏洩率）**。マスキングは「真PII文字をどれだけ覆えたか」が本質。
- **overmask_rate（過剰マスク率）** は FP 許容方針のため従指標。
- 総合 GO/NO-GO を表示。NO-GO の場合は §6.4 の順（規則網羅追加 → Guardrail regex 追加 →
  Comprehend 閾値調整）で補強し再測定する。

---

## 5. 既知の挙動・限界（Phase 0 メモ）

- **13桁の法人番号がカード番号タグになり得る**：13桁はカード番号（13〜16桁）と構造的に曖昧。
  マスク自体は確実に行われ漏洩はゼロ（用途(a)・一括マスク方針では実害なし）。タグ精度のみの問題。
  必要ならタグ確定の優先規則を §3.1 で定義して調整可能。
- **規則層パターンは出発点**：実測 recall に応じて §6.4 の手順で網羅を追加して調整する想定。
  特に銀行口座は文脈語依存のため、対象文書の表記に合わせた調整が要る。
- **Guardrails のスパン位置**：レスポンスの `match` 文字列をチャンク内検索してオフセットを得る
  防御的実装。trace に開始位置が含まれる場合はそちらを優先する改修が可能。
- **合成データは法務ドメイン向けに拡張済み**（8サンプル・文脈依存gold 53件）：契約・通知・メモに
  加え、訴訟当事者目録・登記履歴事項・社内規程・氏名集中・住所/施設集中を収録。氏名（分かち書き
  有無・ひらがな/カタカナ・役職直結）、組織（法人格前置/後置/略記・合同会社・官公庁・英字混じり）、
  住所（省略形・ビル付き・改行跨ぎ）等の表記形を variant ラベルで分解測定できる。
  さらに信頼性を上げるには対象文書の表記に合わせて増やす（実PIIは使わない）。
- **設立日の過剰マスク**：登記サンプルの「令和3年4月1日」（設立日＝生年月日ではない）は gold 非対象
  だが日付regexが検出するため過剰マスク（約3%）として計上される。用途(a)・FP許容方針では実害なし。

---

## 6. 次の手順
1. 上記 2.2 を利用者の AWS アカウントで実行し、3層の recall を実測。
2. 結果を §7.4 ゲートと対照。文脈依存が 0.98 未満なら §6.4 補強を適用して再測定。
3. recall が基準に達したら、実装計画 S2（検出コアの確立）→ S3（秘匿性基盤）へ進む。
