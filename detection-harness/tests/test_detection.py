"""
検出コア（C1）と堅牢化（C2）の単体テスト。

実行: python -m unittest discover -s tests -v
AWS には接続しない（規則層・統合・分割・マスク・抽出・リトライの純ロジックを検証）。
"""

import io
import os
import tempfile
import unittest

from detection.spans import Span, union_spans, LAYER_REGEX, LAYER_GUARDRAILS, LAYER_COMPREHEND
from detection import regex_layer
from detection.chunking import split, to_global, Chunk
from detection.masking import apply_mask, tag_for
from detection.spans import MaskRegion
from detection import extraction
from detection import retry


class TestUnionSpans(unittest.TestCase):
    def test_containment_prefers_wider(self):
        r = union_spans([Span(10, 20, "A", LAYER_REGEX), Span(12, 15, "B", LAYER_GUARDRAILS)])
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0].start, r[0].end), (10, 20))
        self.assertEqual(r[0].entity_type, "A")
        self.assertEqual(r[0].contributing_sources, ("guardrails", "regex"))

    def test_partial_overlap_unions_extent(self):
        r = union_spans([Span(0, 10, "A", LAYER_REGEX), Span(8, 16, "B", LAYER_COMPREHEND)])
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0].start, r[0].end), (0, 16))

    def test_adjacent_not_merged(self):
        r = union_spans([Span(0, 5, "A", LAYER_REGEX), Span(5, 10, "B", LAYER_GUARDRAILS)])
        self.assertEqual(len(r), 2)

    def test_empty(self):
        self.assertEqual(union_spans([]), [])

    def test_tie_length_single_region(self):
        r = union_spans([Span(0, 13, "CREDIT_CARD", LAYER_REGEX),
                         Span(0, 13, "CORPORATE_NUMBER", LAYER_REGEX)])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].end, 13)


class TestRegexLayer(unittest.TestCase):
    def test_mynumber_12_digits(self):
        spans = regex_layer.detect("個人番号 987654321098 です")
        self.assertTrue(any(s.entity_type == "MYNUMBER" for s in spans))

    def test_corporate_number_13_digits(self):
        spans = regex_layer.detect("法人番号 1234567890123")
        self.assertTrue(any(s.entity_type == "CORPORATE_NUMBER" for s in spans))

    def test_email_and_url(self):
        spans = regex_layer.detect("連絡 taro@example.co.jp / https://example.com/x")
        types = {s.entity_type for s in spans}
        self.assertIn("EMAIL", types)
        self.assertIn("URL", types)

    def test_phone_variants(self):
        for ph in ["03-1234-5678", "090-8765-4321", "045-678-9012", "050-1111-2222"]:
            spans = regex_layer.detect(f"電話 {ph}")
            self.assertTrue(any(s.entity_type == "PHONE" for s in spans), ph)

    def test_dob_wareki_and_seireki(self):
        for d in ["昭和60年4月1日", "1992年12月5日", "2020-01-15"]:
            spans = regex_layer.detect(f"生年月日 {d}")
            self.assertTrue(any(s.entity_type == "DATE_OF_BIRTH" for s in spans), d)

    def test_postal_code(self):
        spans = regex_layer.detect("〒530-0001")
        self.assertTrue(any(s.entity_type == "POSTAL_CODE" for s in spans))


class TestChunking(unittest.TestCase):
    def test_offsets_roundtrip(self):
        text = "あ" * 3000
        chunks = split(text, max_chars=1000, overlap=100)
        # 全文字がいずれかのチャンクで覆われる
        covered = set()
        for c in chunks:
            for i in range(len(c.text)):
                g0, _ = to_global(i, i + 1, c)
                covered.add(g0)
        self.assertEqual(covered, set(range(len(text))))

    def test_to_global(self):
        c = Chunk(text="xyz", offset=100)
        self.assertEqual(to_global(1, 3, c), (101, 103))

    def test_overlap_validation(self):
        with self.assertRaises(ValueError):
            split("abc", max_chars=10, overlap=10)


class TestMasking(unittest.TestCase):
    def test_right_to_left_offset_preserved(self):
        text = "名前A 電話B"
        regions = [
            MaskRegion(0, 3, "NAME", ("regex",)),
            MaskRegion(4, 7, "PHONE", ("regex",)),
        ]
        masked, meta = apply_mask(text, regions)
        self.assertIn(tag_for("NAME"), masked)
        self.assertIn(tag_for("PHONE"), masked)
        # メタに原文を含めない（不可逆・原文非保持）
        for m in meta:
            self.assertNotIn("text", m)

    def test_meta_records_positions(self):
        text = "abcdefg"
        regions = [MaskRegion(2, 5, "NAME", ("guardrails",))]
        _, meta = apply_mask(text, regions)
        self.assertEqual(meta[0]["start"], 2)
        self.assertEqual(meta[0]["end"], 5)
        self.assertEqual(meta[0]["sources"], ["guardrails"])


class TestExtraction(unittest.TestCase):
    def test_text_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("氏名：山田太郎\r\n電話：03-1234-5678")
            out = extraction.extract_text(p)
            self.assertIn("山田太郎", out)
            self.assertNotIn("\r", out)  # 改行正規化

    def test_markdown_keeps_raw(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# 見出し\n氏名 山田太郎")
            out = extraction.extract_text(p)
            self.assertIn("# 見出し", out)  # 記法は剥がさない

    def test_normalize_width(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("１２３４５６７８９０１２")  # 全角12桁
            out = extraction.extract_text(p, normalize_width=True)
            self.assertIn("123456789012", out)

    def test_docx_roundtrip(self):
        try:
            import docx
        except ImportError:
            self.skipTest("python-docx 未導入")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.docx")
            doc = docx.Document()
            doc.add_paragraph("氏名：田中花子")
            doc.save(p)
            out = extraction.extract_text(p)
            self.assertIn("田中花子", out)

    def test_unsupported_format(self):
        with self.assertRaises(extraction.UnsupportedFormat):
            extraction.detect_kind("foo.xlsx")


class TestRetry(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        class ThrottlingException(Exception):
            pass

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ThrottlingException("slow down")
            return "ok"

        out = retry.with_retry(flaky, max_attempts=5, base_delay=0.0, max_delay=0.0)
        self.assertEqual(out, "ok")
        self.assertEqual(calls["n"], 3)

    def test_non_retryable_raises_immediately(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise ValueError("bad input")

        with self.assertRaises(ValueError):
            retry.with_retry(boom, max_attempts=5, base_delay=0.0)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
