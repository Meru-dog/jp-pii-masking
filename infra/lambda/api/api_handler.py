"""
api_handler.py — API ハンドラ群（S5）。

HTTP API（API Gateway, JWT認証）からのリクエストを処理する。
ファイル実体はブラウザ↔S3を署名付きURLで直結し、本Lambdaを経由させない（露出経路最小化）。

エンドポイント（routeKey）:
  POST /uploads            : アップロード用 署名付きURL（PutObject）を発行
  GET  /documents          : 出力バケットのディレクトリビュー（プレフィックス階層）を返す
  GET  /documents/download : 成果物取得用 署名付きURL（GetObject）を発行
  POST /documents/approve  : メタJSONのステータスを review_pending → approved に更新

環境変数:
  INPUT_BUCKET, OUTPUT_BUCKET : バケット名
  URL_TTL_SECONDS             : 署名付きURLの有効期限（既定 900=15分）
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.parse

import boto3

s3 = boto3.client("s3")

INPUT_BUCKET = os.environ["INPUT_BUCKET"]
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
URL_TTL = int(os.environ.get("URL_TTL_SECONDS", "900"))

# 受理する入力拡張子（処理対象）
ALLOWED_EXT = {".pdf", ".docx", ".md", ".markdown", ".txt", ".text"}
# キーに使える安全な文字（パストラバーサル・不正文字を排除）
SAFE_KEY = re.compile(r"^[\w\-./ぁ-んァ-ヶ一-龠]+$")


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _route(event) -> str:
    # HTTP API (payload v2.0)
    return event.get("routeKey") or f'{event.get("requestContext",{}).get("http",{}).get("method","")} {event.get("rawPath","")}'


def _body(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _safe_key(key: str) -> bool:
    if not key or ".." in key or key.startswith("/"):
        return False
    return bool(SAFE_KEY.match(key))


# --- ハンドラ群 ---------------------------------------------------------------

def create_upload_url(event):
    body = _body(event)
    key = body.get("key", "")
    if not _safe_key(key):
        return _resp(400, {"error": "invalid key"})
    ext = os.path.splitext(key)[1].lower()
    if ext not in ALLOWED_EXT:
        return _resp(400, {"error": f"unsupported extension: {ext}"})

    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": INPUT_BUCKET, "Key": key},
        ExpiresIn=URL_TTL,
    )
    return _resp(200, {"url": url, "bucket": INPUT_BUCKET, "key": key, "ttl": URL_TTL})


def list_documents(event):
    qs = event.get("queryStringParameters") or {}
    prefix = qs.get("prefix", "")
    if prefix and not _safe_key(prefix.rstrip("/") + "/x"):
        return _resp(400, {"error": "invalid prefix"})

    paginator = s3.get_paginator("list_objects_v2")
    folders, files = [], []
    for page in paginator.paginate(Bucket=OUTPUT_BUCKET, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []) or []:
            folders.append(cp["Prefix"])
        for obj in page.get("Contents", []) or []:
            if obj["Key"] == prefix:
                continue
            files.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })
    return _resp(200, {"prefix": prefix, "folders": folders, "files": files})


def create_download_url(event):
    qs = event.get("queryStringParameters") or {}
    key = qs.get("key", "")
    if not _safe_key(key):
        return _resp(400, {"error": "invalid key"})
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": OUTPUT_BUCKET, "Key": key},
        ExpiresIn=URL_TTL,
    )
    return _resp(200, {"url": url, "key": key, "ttl": URL_TTL})


def approve_document(event):
    body = _body(event)
    meta_key = body.get("meta_key", "")
    if not _safe_key(meta_key) or not meta_key.endswith(".meta.json"):
        return _resp(400, {"error": "invalid meta_key"})

    reviewer = (event.get("requestContext", {})
                .get("authorizer", {}).get("jwt", {})
                .get("claims", {}).get("sub", "unknown"))

    try:
        obj = s3.get_object(Bucket=OUTPUT_BUCKET, Key=meta_key)
        meta = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return _resp(404, {"error": "meta not found"})

    meta["status"] = "approved"
    meta["approved_by"] = reviewer
    meta["approved_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    s3.put_object(
        Bucket=OUTPUT_BUCKET, Key=meta_key,
        Body=json.dumps(meta, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )
    return _resp(200, {"status": "approved", "meta_key": meta_key})


_ROUTES = {
    "POST /uploads": create_upload_url,
    "GET /documents": list_documents,
    "GET /documents/download": create_download_url,
    "POST /documents/approve": approve_document,
}


def handler(event, context):
    route = _route(event)
    fn = _ROUTES.get(route)
    if fn is None:
        return _resp(404, {"error": f"no route: {route}"})
    try:
        return fn(event)
    except Exception as exc:  # noqa: BLE001
        # 詳細は CloudWatch に残し、レスポンスには出さない（情報漏洩防止）
        print(f"ERROR route={route}: {exc.__class__.__name__}: {exc}")
        return _resp(500, {"error": "internal error"})
