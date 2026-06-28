"""
spans.py — 検出スパンのデータモデルと3層和集合の統合ロジック（C1 検出コア）。

RDD v1.1 §9 の方針に従う:
- 規則層 ∪ Guardrails ∪ Comprehend の和集合を取る。
- 重複・包含が競合する場合は「広い方を優先」して残置漏れを防ぐ（recall優先）。

本モジュールは AWS 非依存の純ロジックであり、単体テスト可能。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


# 検出層の識別子
LAYER_REGEX = "regex"
LAYER_GUARDRAILS = "guardrails"
LAYER_COMPREHEND = "comprehend"


@dataclass(frozen=True)
class Span:
    """文書全体（グローバル）のオフセットで表現した検出スパン。

    start / end は Python の文字インデックス（end は排他的）。
    entity_type は正規化済みのカテゴリ名（例: 'NAME', 'MYNUMBER', 'ORGANIZATION'）。
    source は検出層（LAYER_*）。
    """

    start: int
    end: int
    entity_type: str
    source: str
    text: str = ""

    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class MaskRegion:
    """マスク対象として確定した非重複領域。

    entity_type は当該領域に寄与したスパンのうち最長スパンの型（広い方優先）。
    contributing_sources は寄与した検出層の集合（監査・分析用）。
    """

    start: int
    end: int
    entity_type: str
    contributing_sources: tuple

    def length(self) -> int:
        return self.end - self.start


def union_spans(spans: List[Span]) -> List[MaskRegion]:
    """全層のスパンを和集合し、非重複の MaskRegion 列に統合する。

    アルゴリズム:
    1. start 昇順（同 start なら end 降順）でソート。
    2. 連続して重複・隣接するスパンを1クラスタにまとめ、領域は和集合（最小start〜最大end）。
    3. クラスタ内の最長スパンの entity_type を採用（RDD §9「広い方を優先」）。

    隣接（end == 次の start）は連結しない（別エンティティの可能性を尊重）。重なりのみ連結。
    """
    if not spans:
        return []

    ordered = sorted(spans, key=lambda s: (s.start, -s.end))

    regions: List[MaskRegion] = []
    cluster: List[Span] = [ordered[0]]
    cluster_end = ordered[0].end

    def flush(cl: List[Span], end: int) -> MaskRegion:
        start = min(s.start for s in cl)
        widest = max(cl, key=lambda s: s.length())
        sources = tuple(sorted({s.source for s in cl}))
        return MaskRegion(
            start=start,
            end=end,
            entity_type=widest.entity_type,
            contributing_sources=sources,
        )

    for s in ordered[1:]:
        if s.start < cluster_end:  # 重なりあり → 同クラスタ
            cluster.append(s)
            cluster_end = max(cluster_end, s.end)
        else:  # 重なりなし → クラスタ確定
            regions.append(flush(cluster, cluster_end))
            cluster = [s]
            cluster_end = s.end

    regions.append(flush(cluster, cluster_end))
    return regions
