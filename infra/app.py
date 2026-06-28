#!/usr/bin/env python3
"""
app.py — CDK アプリのエントリポイント（JP PII Masking）。

スタック:
- JpPiiMaskingFoundation : S3バケット・CMK・最小権限IAM（S3段）。
- JpPiiMaskingProcessing : VPC・エンドポイント・コンテナLambda・EventBridge起動（S4段）。

Processing は Foundation の構成をクロススタック参照で取り込む。
guardrail_id は context（-c guardrail_id=...）で渡す。
"""

import os

import aws_cdk as cdk

from stacks.foundation_stack import FoundationStack
from stacks.processing_stack import ProcessingStack
from stacks.api_stack import ApiStack


app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "ap-northeast-1"),
)

guardrail_id = app.node.try_get_context("guardrail_id")
guardrail_version = app.node.try_get_context("guardrail_version") or "1"

foundation = FoundationStack(app, "JpPiiMaskingFoundation", env=env)

ProcessingStack(
    app, "JpPiiMaskingProcessing",
    env=env,
    input_bucket=foundation.input_bucket,
    output_bucket=foundation.output_bucket,
    key=foundation.key,
    processing_role=foundation.processing_role,
    guardrail_id=guardrail_id,
    guardrail_version=guardrail_version,
)

ApiStack(
    app, "JpPiiMaskingApi",
    env=env,
    input_bucket=foundation.input_bucket,
    output_bucket=foundation.output_bucket,
    key=foundation.key,
    ui_api_role=foundation.ui_api_role,
)

app.synth()
