"""
retry.py — マネージドAPI呼び出しのリトライ（C2の堅牢化）。

Guardrails / Comprehend はスロットリング（ThrottlingException 等）や一時的エラーを
返しうる。指数バックオフ＋ジッタで限定回数リトライする。boto3 非依存（例外名で判定）。
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

# リトライ対象とする例外クラス名（boto3 ClientError の error code 由来名を含む）
_RETRYABLE_NAMES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ProvisionedThroughputExceededException",
    "RequestTimeout",
    "ServiceQuotaExceededException",
}


def _is_retryable(exc: Exception) -> bool:
    name = exc.__class__.__name__
    if name in _RETRYABLE_NAMES:
        return True
    # boto3 ClientError は response['Error']['Code'] にコードを持つ
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code", "")
        if code in _RETRYABLE_NAMES:
            return True
    return False


def with_retry(fn: Callable[[], T],
               max_attempts: int = 4,
               base_delay: float = 0.5,
               max_delay: float = 8.0) -> T:
    """fn() を最大 max_attempts 回試行。リトライ可能例外のみ指数バックオフで再試行。"""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 判定は _is_retryable に委ねる
            attempt += 1
            if attempt >= max_attempts or not _is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # ジッタ
            time.sleep(delay)
