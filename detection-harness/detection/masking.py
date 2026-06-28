"""
masking.py — 不可逆タグ置換によるマスキング（C1）。

RDD v1.1 §9: 検出スパン（統合済み MaskRegion）をカテゴリ名タグへ置換。不可逆。
原文・マッピングは保持しない。

統合済みの非重複 MaskRegion 列を、末尾側から順に置換することで、
前方のオフセットを崩さずに安全に適用する。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .spans import MaskRegion


# 正規化カテゴリ → 表示タグ（日本語）。命名規則は Phase 0 で確定（暫定）。
_TAG = {
    "NAME": "[氏名]",
    "ADDRESS": "[住所]",
    "ORGANIZATION": "[組織名]",
    "TITLE": "[役職]",
    "FACILITY": "[施設名]",
    "EMAIL": "[メール]",
    "PHONE": "[電話番号]",
    "MYNUMBER": "[マイナンバー]",
    "CORPORATE_NUMBER": "[法人番号]",
    "BANK_ACCOUNT": "[口座番号]",
    "CREDIT_CARD": "[カード番号]",
    "POSTAL_CODE": "[郵便番号]",
    "DATE_OF_BIRTH": "[生年月日]",
    "IP_ADDRESS": "[IPアドレス]",
    "MAC_ADDRESS": "[MACアドレス]",
    "URL": "[URL]",
    "AGE": "[年齢]",
    "USERNAME": "[ユーザ名]",
    "PASSWORD": "[パスワード]",
}


def tag_for(entity_type: str) -> str:
    return _TAG.get(entity_type, f"[{entity_type}]")


def apply_mask(text: str, regions: List[MaskRegion]) -> Tuple[str, List[Dict]]:
    """text に regions のマスクを適用し、(マスク済みテキスト, 検出メタJSON) を返す。

    検出メタには元テキストは含めない（不可逆・原文非保持の方針）。
    位置とカテゴリ、寄与層のみ記録する。
    """
    ordered = sorted(regions, key=lambda r: r.start)
    # 末尾側から置換してオフセットを保つ
    result = text
    for r in sorted(ordered, key=lambda r: r.start, reverse=True):
        result = result[:r.start] + tag_for(r.entity_type) + result[r.end:]

    meta = [
        {
            "start": r.start,
            "end": r.end,
            "entity_type": r.entity_type,
            "tag": tag_for(r.entity_type),
            "sources": list(r.contributing_sources),
        }
        for r in ordered
    ]
    return result, meta
