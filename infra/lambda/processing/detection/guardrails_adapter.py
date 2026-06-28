"""
guardrails_adapter.py — Bedrock Guardrails 機微情報フィルタのアダプタ（C2）。

RDD v1.1 §6.2: FM を呼び出さず ApplyGuardrail API で単体のPIIマスカーとして使用。
呼び出しログは無効（§8）。5000バイト上限に対しチャンク分割し、検出スパンを
文書全体オフセットへ再マッピングする。

本アダプタは AWS 到達が必要なため、ハーネスでは --use-guardrails 指定時のみ有効。
boto3 が無い／資格情報が無い環境では呼び出さない。
"""

from __future__ import annotations

from typing import List, Optional

from .spans import Span, LAYER_GUARDRAILS
from .chunking import split, to_global, DEFAULT_MAX_CHARS, DEFAULT_OVERLAP_CHARS


# Guardrails 組み込みPII型 → 本システムの正規化カテゴリ名。
# ※ ORGANIZATION / TITLE / FACILITY は Guardrails に型が無い（→ Comprehend が担当）。
_TYPE_MAP = {
    "NAME": "NAME",
    "ADDRESS": "ADDRESS",
    "EMAIL": "EMAIL",
    "PHONE": "PHONE",
    "AGE": "AGE",
    "USERNAME": "USERNAME",
    "PASSWORD": "PASSWORD",
    "CREDIT_DEBIT_CARD_NUMBER": "CREDIT_CARD",
    "CREDIT_DEBIT_CARD_CVV": "CREDIT_CARD",
    "CREDIT_DEBIT_CARD_EXPIRY": "CREDIT_CARD",
    "PIN": "BANK_ACCOUNT",
    "INTERNATIONAL_BANK_ACCOUNT_NUMBER": "BANK_ACCOUNT",
    "SWIFT_CODE": "BANK_ACCOUNT",
    "IP_ADDRESS": "IP_ADDRESS",
    "MAC_ADDRESS": "MAC_ADDRESS",
    "URL": "URL",
}

# 有効化する組み込みPII型（本件で意味のあるもの）。
ENABLED_PII_TYPES = [
    "NAME", "ADDRESS", "EMAIL", "PHONE",
    "CREDIT_DEBIT_CARD_NUMBER", "IP_ADDRESS", "URL",
]


class GuardrailsAdapter:
    def __init__(self,
                 guardrail_id: str,
                 guardrail_version: str = "DRAFT",
                 region: Optional[str] = None,
                 max_chars: int = DEFAULT_MAX_CHARS,
                 overlap: int = DEFAULT_OVERLAP_CHARS):
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version
        self.region = region
        self.max_chars = max_chars
        self.overlap = overlap
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import boto3  # 遅延 import（AWS不要時に依存しない）
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def detect(self, text: str) -> List[Span]:
        """text 全体をチャンク分割し ApplyGuardrail で評価、検出スパンを返す。

        action=NONE（検出のみ・マスクはしない）で trace から PII スパンを取得する。
        ※ マスク自体は本システムのマスキング工程（masking.py）で一括実施するため、
          ここでは検出位置のみ取得する。
        """
        client = self._client_lazy()
        spans: List[Span] = []

        from .retry import with_retry
        for chunk in split(text, self.max_chars, self.overlap):
            resp = with_retry(lambda: client.apply_guardrail(
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.guardrail_version,
                source="INPUT",
                content=[{"text": {"text": chunk.text}}],
            ))
            spans.extend(self._parse(resp, chunk, text))
        return spans

    def _parse(self, resp: dict, chunk, full_text: str) -> List[Span]:
        """ApplyGuardrail レスポンスの assessments から PII スパンを抽出。

        レスポンス構造はバージョンにより差があるため、piiEntities の
        match 文字列をチャンク内で検索してオフセットを得る防御的実装とする。
        （trace に開始位置が含まれる場合はそれを優先してよい。）
        """
        out: List[Span] = []
        assessments = resp.get("assessments", []) or []
        for a in assessments:
            sip = a.get("sensitiveInformationPolicy", {}) or {}
            for pii in sip.get("piiEntities", []) or []:
                raw_type = pii.get("type", "")
                matched = pii.get("match", "")
                norm = _TYPE_MAP.get(raw_type, raw_type)
                if not matched:
                    continue
                # チャンク内で match を検索（複数出現は全件）
                search_from = 0
                while True:
                    idx = chunk.text.find(matched, search_from)
                    if idx == -1:
                        break
                    g_start, g_end = to_global(idx, idx + len(matched), chunk)
                    out.append(Span(
                        start=g_start, end=g_end,
                        entity_type=norm, source=LAYER_GUARDRAILS,
                        text=matched,
                    ))
                    search_from = idx + max(1, len(matched))
        return out
