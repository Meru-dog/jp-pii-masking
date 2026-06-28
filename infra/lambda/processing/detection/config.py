"""
config.py — 検出・評価の設定外部化（S2）。

ハードコードを避け、有効化エンティティ・閾値・ゲート基準を一箇所に集約する。
本番では環境変数やファイルから上書きできるよう、dataclass で保持する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class DetectionConfig:
    # Guardrails で有効化する組み込みPII型（§6.2）
    guardrails_pii_types: List[str] = field(default_factory=lambda: [
        "NAME", "ADDRESS", "EMAIL", "PHONE",
        "CREDIT_DEBIT_CARD_NUMBER", "IP_ADDRESS", "URL",
    ])
    # Comprehend で採用するエンティティ型（§6.3）
    comprehend_types: Set[str] = field(default_factory=lambda: {
        "ORGANIZATION", "TITLE", "LOCATION", "PERSON", "DATE",
    })
    # Comprehend 検出スコア閾値（低いほど recall 寄り、§6.4）
    comprehend_score_threshold: float = 0.3
    # チャンク分割（Comprehend/Guardrails の入力上限に対応）
    max_chars: int = 1400
    overlap_chars: int = 150
    # 抽出時の全角→半角正規化（既定オフ。検出挙動を変えるため明示選択）
    normalize_width: bool = False


@dataclass
class GateConfig:
    """§7.4 ゲート基準。

    Phase 0 実測の知見を反映できるよう、個人特定の中核と周辺を分けて閾値を設定可能にする。
    既定は当初設計（中核も周辺も 0.98）。peripheral_entities に入れた型は
    peripheral_recall_target を適用し（人手レビューに委ねる前提で緩める）、
    review_only_entities に入れた型はゲート評価から除外する（全件人手レビューが担保）。

    ※ ここを変更する場合は RDD §7.4 を併せて改訂すること（要件変更のため）。
    """
    regex_recall_required: float = 1.0
    core_recall_target: float = 0.98
    peripheral_recall_target: float = 0.80
    core_entities: Set[str] = field(default_factory=lambda: {
        "NAME", "ADDRESS",
    })
    peripheral_entities: Set[str] = field(default_factory=set)
    review_only_entities: Set[str] = field(default_factory=set)


# 既定インスタンス
DEFAULT_DETECTION = DetectionConfig()
DEFAULT_GATE = GateConfig()
