"""
run_harness.py — Phase 0 第二段 実測ハーネスのCLIエントリ（S1）。

既定では規則層のみで全工程（検出→和集合→マスク→評価→ゲート判定）を実行する。
--use-guardrails / --use-comprehend を付けると、利用者のAWSアカウントで
マネージド2層を有効化し、3層和集合の recall を実測できる。

使用例:
  # 規則層のみ（AWS不要・手元検証）
  python run_harness.py

  # 3層（AWS必要・東京リージョン・Guardrail構成済み前提）
  python run_harness.py \
      --use-guardrails --guardrail-id <ID> --guardrail-version DRAFT \
      --use-comprehend --region ap-northeast-1
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Tuple

from detection import pipeline
from evaluation import synthetic_data, metrics


def build_args():
    p = argparse.ArgumentParser(description="日本語PIIマスキング Phase0 実測ハーネス")
    p.add_argument("--use-guardrails", action="store_true")
    p.add_argument("--guardrail-id", default=None)
    p.add_argument("--guardrail-version", default="DRAFT")
    p.add_argument("--use-comprehend", action="store_true")
    p.add_argument("--region", default=None)
    p.add_argument("--comprehend-threshold", type=float, default=0.3)
    p.add_argument("--show-masked", action="store_true",
                   help="各サンプルのマスク済みテキストを表示")
    p.add_argument("--by-variant", action="store_true",
                   help="文脈依存エンティティを表記形(variant)別に分解表示")
    p.add_argument("--json", action="store_true", help="結果をJSONで出力")
    return p.parse_args()


def make_adapters(args):
    g_adapter = None
    c_adapter = None
    if args.use_guardrails:
        if not args.guardrail_id:
            print("ERROR: --use-guardrails には --guardrail-id が必要です", file=sys.stderr)
            sys.exit(2)
        from detection.guardrails_adapter import GuardrailsAdapter
        g_adapter = GuardrailsAdapter(
            guardrail_id=args.guardrail_id,
            guardrail_version=args.guardrail_version,
            region=args.region,
        )
    if args.use_comprehend:
        from detection.comprehend_adapter import ComprehendAdapter
        c_adapter = ComprehendAdapter(
            region=args.region,
            score_threshold=args.comprehend_threshold,
        )
    return g_adapter, c_adapter


def main():
    args = build_args()
    g_adapter, c_adapter = make_adapters(args)

    active_layers = ["regex"]
    if g_adapter:
        active_layers.append("guardrails")
    if c_adapter:
        active_layers.append("comprehend")

    samples = synthetic_data.all_samples()
    sr: List[Tuple[synthetic_data.Sample, list]] = []

    for s in samples:
        res = pipeline.run(s.text, guardrails_adapter=g_adapter, comprehend_adapter=c_adapter)
        sr.append((s, res.regions))
        if args.show_masked:
            print(f"\n===== {s.name} : マスク済み =====")
            print(res.masked_text)

    rep = metrics.evaluate(sr)
    gate = metrics.gate(rep)

    if args.json:
        out = {
            "active_layers": active_layers,
            "per_entity": {
                et: {
                    "gold_count": es.gold_count,
                    "span_recall": round(es.span_recall, 4),
                    "char_leak_rate": round(es.char_leak_rate, 4),
                }
                for et, es in rep.per_entity.items()
            },
            "overmask_rate": round(rep.overmask_rate, 4),
            "gate_passed": gate.passed,
            "regex_ok": gate.regex_ok,
            "contextual_ok": gate.contextual_ok,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print("=" * 64)
    print(f"有効化された検出層: {' ∪ '.join(active_layers)}")
    print("=" * 64)
    print("\n[エンティティ別スコア]")
    for line in gate.details:
        print("  " + line)
    print(f"\n過剰マスク率: {rep.overmask_rate:.3f}（FP許容方針のため従指標）")

    if args.by_variant:
        bv = metrics.evaluate_by_variant(sr)
        print("\n[文脈依存エンティティ：表記形(variant)別 recall]")
        last_type = None
        for (etype, variant), vs in sorted(bv.items()):
            if etype != last_type:
                print(f"  {etype}")
                last_type = etype
            flag = "OK" if vs.span_recall >= 0.98 else "NG"
            print(f"    - {variant:<22} recall={vs.span_recall:.3f} "
                  f"({vs.detected_count}/{vs.gold_count}) {flag}")
    print("\n[ゲート判定 §7.4]")
    print(f"  規則層エンティティ recall=1.0 : {'OK' if gate.regex_ok else 'NG'}")
    print(f"  文脈依存エンティティ recall>=0.98 : {'OK' if gate.contextual_ok else 'NG'}")
    print(f"  総合: {'GO' if gate.passed else 'NO-GO'}")
    if not gate.passed and active_layers == ["regex"]:
        print("\n  注: 規則層のみの実行です。文脈依存（氏名・住所・組織名・役職・施設名）は")
        print("      Guardrails / Comprehend を有効化しないと検出されません（設計どおり）。")


if __name__ == "__main__":
    main()
