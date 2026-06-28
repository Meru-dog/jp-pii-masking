"""
process.py — ファイル単位の処理オーケストレーション（C1+C2の本番経路）。

extract_text → pipeline.run（3層検出 → 和集合 → マスク）を結ぶ薄い層。
抽出テキスト・チャンク等の中間生成物はメモリ上のみで扱い、永続化しない（D-2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import extraction, pipeline
from .pipeline import DetectionResult


@dataclass
class FileResult:
    source: str
    kind: str
    char_count: int
    detection: DetectionResult


def process_file(path: str,
                 guardrails_adapter=None,
                 comprehend_adapter=None,
                 normalize_width: bool = False) -> FileResult:
    """ファイルパスを抽出→検出し、結果を返す。中間生成物は保持しない。"""
    kind = extraction.detect_kind(path)
    text = extraction.extract_text(path, normalize_width=normalize_width)
    det = pipeline.run(
        text,
        guardrails_adapter=guardrails_adapter,
        comprehend_adapter=comprehend_adapter,
    )
    return FileResult(source=path, kind=kind, char_count=len(text), detection=det)


def process_bytes(data: bytes, kind: str,
                  guardrails_adapter=None,
                  comprehend_adapter=None,
                  normalize_width: bool = False) -> DetectionResult:
    """S3オブジェクト等のバイト列をメモリ上で抽出→検出する本番経路。"""
    text = extraction.extract_from_bytes(data, kind, normalize_width=normalize_width)
    return pipeline.run(
        text,
        guardrails_adapter=guardrails_adapter,
        comprehend_adapter=comprehend_adapter,
    )
