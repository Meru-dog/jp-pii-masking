"""
handler.py — 処理関数（S4）。

EventBridge 経由の S3 オブジェクト作成イベントで起動し、入力バケットの文書を
メモリ上で抽出 → 3層検出（規則 ∪ Guardrails ∪ Comprehend）→ 不可逆マスク → 出力バケットへ
マスク済み成果物＋検出メタJSONを書き出す。中間生成物は永続化しない（D-2）。

環境変数:
  OUTPUT_BUCKET        : 出力バケット名
  GUARDRAIL_ID         : Bedrock Guardrail ID（任意。無ければ Guardrails 層は無効）
  GUARDRAIL_VERSION    : 既定 "1"
  COMPREHEND_THRESHOLD : 既定 "0.3"
  NORMALIZE_WIDTH      : "true"/"false"（全角→半角正規化。既定 false）
  AWS_REGION           : Lambda ランタイムが自動設定
"""

from __future__ import annotations

import json
import os
import urllib.parse

import boto3

from detection import process
from detection.guardrails_adapter import GuardrailsAdapter
from detection.comprehend_adapter import ComprehendAdapter
from detection import extraction


s3 = boto3.client("s3")

OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID") or None
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "1")
COMPREHEND_THRESHOLD = float(os.environ.get("COMPREHEND_THRESHOLD", "0.3"))
NORMALIZE_WIDTH = os.environ.get("NORMALIZE_WIDTH", "false").lower() == "true"
REGION = os.environ.get("AWS_REGION")


def _adapters():
    g = (GuardrailsAdapter(guardrail_id=GUARDRAIL_ID,
                           guardrail_version=GUARDRAIL_VERSION,
                           region=REGION)
         if GUARDRAIL_ID else None)
    c = ComprehendAdapter(region=REGION, score_threshold=COMPREHEND_THRESHOLD)
    return g, c


def _iter_records(event):
    """EventBridge(S3) と S3 通知の両形式から bucket/key を取り出す。"""
    # EventBridge "Object Created"
    if event.get("detail-type") == "Object Created" and "detail" in event:
        d = event["detail"]
        yield d["bucket"]["name"], d["object"]["key"]
        return
    # S3 → Lambda 直接通知
    for rec in event.get("Records", []):
        s3rec = rec.get("s3")
        if s3rec:
            yield (s3rec["bucket"]["name"],
                   urllib.parse.unquote_plus(s3rec["object"]["key"]))


def _output_key(input_key: str) -> str:
    """入力キーから出力キーを作る（成果物とメタを同一プレフィックス配下に）。"""
    base = input_key.rsplit(".", 1)[0]
    return base


def handler(event, context):
    g_adapter, c_adapter = _adapters()
    results = []

    for bucket, key in _iter_records(event):
        # 作業用プレフィックスや非対応拡張子はスキップ
        try:
            kind = extraction.detect_kind(key)
        except extraction.UnsupportedFormat:
            results.append({"key": key, "status": "skipped_unsupported"})
            continue

        obj = s3.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()

        det = process.process_bytes(
            data, kind,
            guardrails_adapter=g_adapter,
            comprehend_adapter=c_adapter,
            normalize_width=NORMALIZE_WIDTH,
        )

        out_base = _output_key(key)
        masked_key = f"{out_base}.masked.txt"
        meta_key = f"{out_base}.meta.json"

        # マスク済みテキスト
        s3.put_object(
            Bucket=OUTPUT_BUCKET, Key=masked_key,
            Body=det.masked_text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        # 検出メタ（原文は含めない＝不可逆・原文非保持）
        meta = {
            "source_key": key,
            "kind": kind,
            "status": "review_pending",
            "regions": det.meta,
            "layers": sorted(det.spans_by_layer.keys()),
        }
        s3.put_object(
            Bucket=OUTPUT_BUCKET, Key=meta_key,
            Body=json.dumps(meta, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        results.append({
            "key": key, "status": "masked",
            "masked_key": masked_key, "meta_key": meta_key,
            "region_count": len(det.meta),
        })

    return {"processed": results}
