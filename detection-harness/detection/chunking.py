"""
chunking.py — マネージド検出層向けのチャンク分割とオフセット再マッピング（C1）。

Guardrails (ApplyGuardrail) / Comprehend (DetectEntities) は入力サイズに上限がある
（Comprehend 同期は 5000 バイト UTF-8、日本語で約1600字）。5ページ規模の文書を
分割して各 API に渡し、チャンクローカルのスパンを文書全体（グローバル）の
オフセットへ正確に戻す必要がある。

境界を跨ぐエンティティの取りこぼしを避けるため、オーバーラップ付きで分割する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


# Comprehend 同期 DetectEntities の上限は 5000 バイト。安全側に文字数で制御する。
# 日本語は UTF-8 で 1文字3バイトになり得るため、1チャンク = 1400字（≈4200バイト）を既定。
DEFAULT_MAX_CHARS = 1400
DEFAULT_OVERLAP_CHARS = 150


@dataclass(frozen=True)
class Chunk:
    text: str
    offset: int  # 文書全体における本チャンク先頭のグローバル位置


def split(text: str,
          max_chars: int = DEFAULT_MAX_CHARS,
          overlap: int = DEFAULT_OVERLAP_CHARS) -> List[Chunk]:
    """text をオーバーラップ付きで分割する。

    各チャンクは max_chars 以下。隣接チャンクは overlap 文字だけ重複させ、
    境界に跨るエンティティを両チャンクのどちらかで捕捉できるようにする。
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must satisfy 0 <= overlap < max_chars")

    if not text:
        return []

    chunks: List[Chunk] = []
    pos = 0
    n = len(text)
    step = max_chars - overlap
    while pos < n:
        end = min(pos + max_chars, n)
        chunks.append(Chunk(text=text[pos:end], offset=pos))
        if end == n:
            break
        pos += step
    return chunks


def to_global(start: int, end: int, chunk: Chunk):
    """チャンクローカルのオフセットを文書全体のオフセットへ変換する。"""
    return chunk.offset + start, chunk.offset + end
