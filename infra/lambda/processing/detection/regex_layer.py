"""
regex_layer.py — 規則層（C2の決定論的検出・独立工程）。

RDD v1.1 §3.1 / §6.1 / Phase0対応表 に基づき、Guardrails・Comprehend の
いずれの組み込み型にも存在しない「日本固有の構造化PII」を確実に捕捉する。
本層はこれらエンティティの唯一の捕捉手段であるため、recall を最優先し、
FP許容方針のもとで広めに捕捉する（チェックディジット検証は行わない）。

注意: 本パターン群は Phase 0 の出発点であり、実測（recall）の結果に応じて
§6.4 の手順で網羅を追加して調整する想定。
"""

from __future__ import annotations

import re
from typing import List

from .spans import Span, LAYER_REGEX


# 各エンティティの正規表現。順序は適用順（重複は和集合で解決されるため厳密でなくてよい）。
# (entity_type, compiled_pattern)
_PATTERNS = [
    # メールアドレス
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    # URL
    ("URL", re.compile(r"https?://[^\s　、。）」』]+")),
    # IPv4
    ("IP_ADDRESS", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")),
    # 生年月日（和暦）— 元号 + 年月日（元年も許容）
    ("DATE_OF_BIRTH", re.compile(
        r"(?:明治|大正|昭和|平成|令和)\s?(?:元|\d{1,2})年\s?\d{1,2}月\s?\d{1,2}日"
    )),
    # 生年月日（西暦・漢字）
    ("DATE_OF_BIRTH", re.compile(r"\d{4}\s?年\s?\d{1,2}\s?月\s?\d{1,2}\s?日")),
    # 生年月日（西暦・区切り記号）
    ("DATE_OF_BIRTH", re.compile(r"(?<!\d)\d{4}[/\-]\d{1,2}[/\-]\d{1,2}(?!\d)")),
    # 郵便番号（〒XXX-XXXX）
    ("POSTAL_CODE", re.compile(r"〒?\s?\d{3}-\d{4}(?!\d)")),
    # クレジットカード番号（13〜16桁、空白/ハイフン区切り許容）
    ("CREDIT_CARD", re.compile(
        r"(?<!\d)(?:\d[ \-]?){13,16}(?<=\d)"
    )),
    # 法人番号（13桁）
    ("CORPORATE_NUMBER", re.compile(r"(?<!\d)\d{13}(?!\d)")),
    # マイナンバー（12桁、4-4-4 区切り許容）
    ("MYNUMBER", re.compile(r"(?<!\d)\d{4}[ \-]?\d{4}[ \-]?\d{4}(?!\d)")),
    # 銀行口座番号（文脈語 + 6〜8桁）
    ("BANK_ACCOUNT", re.compile(
        r"(?:口座番号|普通預金|当座預金|普通|当座)[\s:：]*\d{6,8}"
    )),
    # 電話番号（国内: 0始まり、+81、ハイフン/空白区切り）
    ("PHONE", re.compile(
        r"(?<![\d\-])(?:\+81[ \-]?|0)\d{1,4}[ \-]?\d{1,4}[ \-]?\d{3,4}(?![\d\-])"
    )),
]


def detect(text: str) -> List[Span]:
    """規則層で text 全体を走査し、検出スパン（グローバルオフセット）を返す。

    規則層は文書全体に直接適用できるためチャンク分割は不要。
    """
    spans: List[Span] = []
    for entity_type, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if end <= start:
                continue
            spans.append(
                Span(
                    start=start,
                    end=end,
                    entity_type=entity_type,
                    source=LAYER_REGEX,
                    text=text[start:end],
                )
            )
    return spans
