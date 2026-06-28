"""
metrics.py — 捕捉性評価（C4）。

RDD v1.1 §7: エンティティ別 recall・漏洩率（真PIIスパンのうち未マスク割合）・
過剰マスク率を測定し、ゲート（§7.4）を判定する。

判定の考え方:
- 文字レベル漏洩率を主指標とする（マスキングは「真PII文字をどれだけ覆えたか」が本質）。
- スパンレベル recall は補助（gold スパンの被覆率 >= COVER_THRESHOLD で「検出」とみなす）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from .synthetic_data import (
    Sample, GoldSpan, REGEX_ONLY_ENTITIES, CONTEXTUAL_ENTITIES,
)
from detection.spans import MaskRegion


COVER_THRESHOLD = 0.99  # gold スパンがこの割合以上マスクされていれば「検出」


def _masked_char_set(regions: List[MaskRegion]) -> Set[int]:
    covered: Set[int] = set()
    for r in regions:
        covered.update(range(r.start, r.end))
    return covered


@dataclass
class EntityScore:
    entity_type: str
    gold_count: int = 0
    detected_count: int = 0       # スパンレベル（被覆率>=閾値）
    gold_chars: int = 0
    leaked_chars: int = 0         # 未マスクの真PII文字数

    @property
    def span_recall(self) -> float:
        return self.detected_count / self.gold_count if self.gold_count else 1.0

    @property
    def char_leak_rate(self) -> float:
        return self.leaked_chars / self.gold_chars if self.gold_chars else 0.0


@dataclass
class Report:
    per_entity: Dict[str, EntityScore] = field(default_factory=dict)
    overmask_chars: int = 0       # gold外なのにマスクした文字数
    total_masked_chars: int = 0

    @property
    def overmask_rate(self) -> float:
        return self.overmask_chars / self.total_masked_chars if self.total_masked_chars else 0.0


def evaluate(samples_and_regions: List[Tuple[Sample, List[MaskRegion]]]) -> Report:
    rep = Report()

    for sample, regions in samples_and_regions:
        masked = _masked_char_set(regions)
        gold_chars_all: Set[int] = set()

        for g in sample.gold:
            es = rep.per_entity.setdefault(g.entity_type, EntityScore(g.entity_type))
            es.gold_count += 1
            span_chars = set(range(g.start, g.end))
            gold_chars_all.update(span_chars)
            es.gold_chars += len(span_chars)

            covered = len(span_chars & masked)
            es.leaked_chars += (len(span_chars) - covered)
            if len(span_chars) and covered / len(span_chars) >= COVER_THRESHOLD:
                es.detected_count += 1

        rep.total_masked_chars += len(masked)
        rep.overmask_chars += len(masked - gold_chars_all)

    return rep


@dataclass
class VariantScore:
    entity_type: str
    variant: str
    gold_count: int = 0
    detected_count: int = 0

    @property
    def span_recall(self) -> float:
        return self.detected_count / self.gold_count if self.gold_count else 1.0


def evaluate_by_variant(
        samples_and_regions: List[Tuple[Sample, List[MaskRegion]]]
) -> Dict[Tuple[str, str], VariantScore]:
    """文脈依存エンティティを (entity_type, variant) 単位で集計する。

    「どの表記形が取りこぼされるか」を分解測定する。規則層エンティティは対象外
    （既に recall=1.0 のため）。variant が空の gold は '(unlabeled)' に集約。
    """
    out: Dict[Tuple[str, str], VariantScore] = {}
    for sample, regions in samples_and_regions:
        masked = _masked_char_set(regions)
        for g in sample.gold:
            if g.entity_type not in CONTEXTUAL_ENTITIES:
                continue
            variant = g.variant or "(unlabeled)"
            key = (g.entity_type, variant)
            vs = out.setdefault(key, VariantScore(g.entity_type, variant))
            vs.gold_count += 1
            span_chars = set(range(g.start, g.end))
            if span_chars and len(span_chars & masked) / len(span_chars) >= COVER_THRESHOLD:
                vs.detected_count += 1
    return out


@dataclass
class GateResult:
    passed: bool
    regex_ok: bool
    contextual_ok: bool
    details: List[str] = field(default_factory=list)


def gate(rep: Report,
         regex_recall_required: float = 1.0,
         contextual_recall_target: float = 0.98) -> GateResult:
    """§7.4 のゲート判定。

    - 規則層が唯一手段のエンティティ: span_recall == 1.0（漏洩ゼロ）。
    - 文脈依存エンティティ: span_recall >= 0.98。
    """
    details: List[str] = []
    regex_ok = True
    contextual_ok = True

    for etype, es in sorted(rep.per_entity.items()):
        if etype in REGEX_ONLY_ENTITIES:
            ok = es.span_recall >= regex_recall_required
            regex_ok = regex_ok and ok
            details.append(
                f"[規則] {etype}: recall={es.span_recall:.3f} "
                f"漏洩率={es.char_leak_rate:.3f} {'OK' if ok else 'NG(要1.0)'}"
            )
        elif etype in CONTEXTUAL_ENTITIES:
            ok = es.span_recall >= contextual_recall_target
            contextual_ok = contextual_ok and ok
            details.append(
                f"[文脈] {etype}: recall={es.span_recall:.3f} "
                f"漏洩率={es.char_leak_rate:.3f} {'OK' if ok else 'NG(要>=0.98)'}"
            )
        else:
            details.append(f"[他] {etype}: recall={es.span_recall:.3f}")

    return GateResult(
        passed=regex_ok and contextual_ok,
        regex_ok=regex_ok,
        contextual_ok=contextual_ok,
        details=details,
    )
