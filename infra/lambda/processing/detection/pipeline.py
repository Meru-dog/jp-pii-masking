"""
pipeline.py — 検出パイプラインのオーケストレーション（C1 + C2）。

text を入力に、有効化された検出層（規則層 ∪ Guardrails ∪ Comprehend）を実行し、
和集合統合 → マスキングまでを行う。各マネージド層は任意（None なら無効）。

規則層は常時有効（日本固有識別子の唯一の捕捉手段のため）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict

from .spans import Span, MaskRegion, union_spans
from . import regex_layer
from .masking import apply_mask


@dataclass
class DetectionResult:
    masked_text: str
    regions: List[MaskRegion]
    spans_by_layer: Dict[str, List[Span]] = field(default_factory=dict)
    meta: List[Dict] = field(default_factory=list)


def run(text: str,
        guardrails_adapter=None,
        comprehend_adapter=None) -> DetectionResult:
    """3層和集合検出 → マスキングを実行する。

    guardrails_adapter / comprehend_adapter は detect(text)->List[Span] を持つ
    オブジェクト、または None。
    """
    spans_by_layer: Dict[str, List[Span]] = {}

    # 規則層（常時）
    regex_spans = regex_layer.detect(text)
    spans_by_layer["regex"] = regex_spans

    all_spans: List[Span] = list(regex_spans)

    # Guardrails（任意）
    if guardrails_adapter is not None:
        g = guardrails_adapter.detect(text)
        spans_by_layer["guardrails"] = g
        all_spans.extend(g)

    # Comprehend（任意）
    if comprehend_adapter is not None:
        c = comprehend_adapter.detect(text)
        spans_by_layer["comprehend"] = c
        all_spans.extend(c)

    regions = union_spans(all_spans)
    masked_text, meta = apply_mask(text, regions)

    return DetectionResult(
        masked_text=masked_text,
        regions=regions,
        spans_by_layer=spans_by_layer,
        meta=meta,
    )
