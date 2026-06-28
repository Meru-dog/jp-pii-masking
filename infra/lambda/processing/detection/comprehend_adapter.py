"""
comprehend_adapter.py — Amazon Comprehend 汎用エンティティ認識のアダプタ（C2・第3層）。

RDD v1.1 §3.3 / §6.3 / D-8: Guardrails が型を持たない
組織名・会社名（ORGANIZATION）・役職（TITLE）・施設名（LOCATION/ORGANIZATION）を補完する。
DetectEntities（日本語 'ja'）を使用。同期上限 5000 バイトに対しチャンク分割。

DetectEntities はチャンク内オフセット（BeginOffset/EndOffset, バイトではなく
Python と同じ Unicode コードポイント基準）を返すため、それをグローバルへ再マッピングする。

本アダプタは AWS 到達が必要なため、ハーネスでは --use-comprehend 指定時のみ有効。
"""

from __future__ import annotations

from typing import List, Optional

from .spans import Span, LAYER_COMPREHEND
from .chunking import split, to_global, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS


# Comprehend 既定エンティティ型 → 正規化カテゴリ。
# 本層の主目的は ORGANIZATION / TITLE / LOCATION の補完。
# PERSON / DATE 等は他層と二重捕捉になりうる（和集合で許容）。
_TYPE_MAP = {
    "ORGANIZATION": "ORGANIZATION",
    "TITLE": "TITLE",
    "LOCATION": "FACILITY",   # 施設名・地名。住所(ADDRESS)とは別系統で広く捕捉
    "PERSON": "NAME",         # 氏名の二重捕捉（recall向上）
    "DATE": "DATE_OF_BIRTH",  # 日付の二重捕捉（誤検出はFP許容）
}

# 採用するエンティティ型（本層で意味のあるもの）。
ENABLED_TYPES = {"ORGANIZATION", "TITLE", "LOCATION", "PERSON", "DATE"}

LANGUAGE_CODE = "ja"


class ComprehendAdapter:
    def __init__(self,
                 region: Optional[str] = None,
                 score_threshold: float = 0.3,
                 max_chars: int = DEFAULT_MAX_CHARS,
                 overlap: int = DEFAULT_OVERLAP_CHARS):
        # score_threshold は低め（recall優先・§6.4 の recall側調整に相当）
        self.region = region
        self.score_threshold = score_threshold
        self.max_chars = max_chars
        self.overlap = overlap
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("comprehend", region_name=self.region)
        return self._client

    def detect(self, text: str) -> List[Span]:
        client = self._client_lazy()
        spans: List[Span] = []
        from .retry import with_retry
        for chunk in split(text, self.max_chars, self.overlap):
            resp = with_retry(lambda: client.detect_entities(
                Text=chunk.text, LanguageCode=LANGUAGE_CODE))
            for e in resp.get("Entities", []) or []:
                etype = e.get("Type", "")
                if etype not in ENABLED_TYPES:
                    continue
                if float(e.get("Score", 0.0)) < self.score_threshold:
                    continue
                b, en = e.get("BeginOffset"), e.get("EndOffset")
                if b is None or en is None or en <= b:
                    continue
                g_start, g_end = to_global(b, en, chunk)
                spans.append(Span(
                    start=g_start, end=g_end,
                    entity_type=_TYPE_MAP.get(etype, etype),
                    source=LAYER_COMPREHEND,
                    text=text[g_start:g_end],
                ))
        return spans
