"""
foundation_stack.py — 秘匿性基盤スタック（S3 / S3バケット・CMK・最小権限IAM）。

RDD v1.1 §8 のうち、本段階（S3：段階構築の第一段）で実体化する範囲:
- 入力/出力 S3 バケット：パブリックアクセス全面ブロック・CMK暗号化・TLS必須・バージョニング・
  ライフサイクルによる中間生成物/成果物の短期失効（D-2 保持期間最短化）。
- KMS CMK：自動ローテーション有効、鍵ポリシーで利用主体限定。
- IAM：処理ジョブ実行ロールと UI/API ロールを分離し、最小権限のみ付与。

本段階で「作らない」もの（後段に送る）:
- VPC / インターフェースVPCエンドポイント（Bedrock/Comprehend）→ S4（処理関数をVPCに載せる段）。
  ※ 時間課金が発生するため、処理関数の結合まで寝かせる（過剰設計回避）。
- Bedrock モデル呼び出しログは「有効化しない」ことで無効を担保（本スタックでは作らない）。

context（cdk.json または -c で上書き）:
- guardrail_id   : 既存 Guardrail の ID（IAM の Resource 絞り込みに使用）
- retain         : "true"（既定）で保持、"false" で破棄容易化（PoC teardown 用）
"""

from __future__ import annotations

from aws_cdk import (
    Stack,
    RemovalPolicy,
    Duration,
    CfnOutput,
    aws_s3 as s3,
    aws_kms as kms,
    aws_iam as iam,
)
from constructs import Construct


class FoundationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        retain = (self.node.try_get_context("retain") or "true").lower() != "false"
        removal = RemovalPolicy.RETAIN if retain else RemovalPolicy.DESTROY
        auto_delete = not retain  # 破棄容易化時のみオブジェクト自動削除

        guardrail_id = self.node.try_get_context("guardrail_id")  # 任意
        # UI のオリジン（ブラウザ直アップロード/ダウンロードの CORS 許可先）。
        # 既定 "*"（開発用）。本番は CloudFront 等の配信オリジンに絞る。
        ui_origin = self.node.try_get_context("ui_origin") or "*"

        cors_rules = [s3.CorsRule(
            allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT, s3.HttpMethods.HEAD],
            allowed_origins=[ui_origin],
            allowed_headers=["*"],
            exposed_headers=["ETag"],
            max_age=3000,
        )]

        # --- KMS CMK ---------------------------------------------------------
        key = kms.Key(
            self, "PiiMaskingKey",
            description="CMK for JP PII masking (S3 objects, intermediate artifacts)",
            enable_key_rotation=True,
            removal_policy=removal,
            alias="alias/jp-pii-masking",
        )

        # --- S3 バケット（入力・出力） ---------------------------------------
        common_bucket_args = dict(
            encryption=s3.BucketEncryption.KMS,
            encryption_key=key,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,                      # 非TLSアクセスを拒否
            versioned=True,
            cors=cors_rules,                       # ブラウザ直アップロード/DL用
            removal_policy=removal,
            auto_delete_objects=auto_delete,
        )

        input_bucket = s3.Bucket(
            self, "InputBucket",
            event_bridge_enabled=True,  # EventBridge起動（S4で使用）
            lifecycle_rules=[
                # 中間生成物（作業用プレフィックス）は短期失効（D-2）
                s3.LifecycleRule(
                    id="expire-work",
                    prefix="work/",
                    expiration=Duration.days(1),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                ),
                # 入力原本は短期保持（処理後に残さない方針）
                s3.LifecycleRule(
                    id="expire-input",
                    expiration=Duration.days(7),
                    noncurrent_version_expiration=Duration.days(1),
                ),
            ],
            **common_bucket_args,
        )

        output_bucket = s3.Bucket(
            self, "OutputBucket",
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-output",
                    expiration=Duration.days(30),
                    noncurrent_version_expiration=Duration.days(7),
                ),
            ],
            **common_bucket_args,
        )

        # --- IAM ロール（処理ジョブ実行用） ----------------------------------
        # 後段(S4)で Lambda に割り当てる前提。assumed_by は lambda を既定にしておく。
        processing_role = iam.Role(
            self, "ProcessingRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="JP PII masking processing job role (extract+detect+mask)",
        )
        # 入力読取 / 出力書込（プレフィックスは運用で limited。ここではバケット単位の最小限）
        input_bucket.grant_read(processing_role)
        output_bucket.grant_write(processing_role)
        key.grant_encrypt_decrypt(processing_role)
        # マネージド検出（Comprehend はリソースレベル権限非対応のため * ）
        processing_role.add_to_policy(iam.PolicyStatement(
            sid="ComprehendDetectEntities",
            actions=["comprehend:DetectEntities"],
            resources=["*"],
        ))
        # Guardrails（guardrail_id があれば ARN で絞る。無ければ in-region 全 guardrail）
        guardrail_resource = (
            f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/{guardrail_id}"
            if guardrail_id else
            f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/*"
        )
        processing_role.add_to_policy(iam.PolicyStatement(
            sid="ApplyGuardrail",
            actions=["bedrock:ApplyGuardrail"],
            resources=[guardrail_resource],
        ))
        # CloudWatch Logs（最小）
        processing_role.add_to_policy(iam.PolicyStatement(
            sid="Logs",
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:*"],
        ))

        # --- IAM ロール（UI/API 用） -----------------------------------------
        # 署名付きURL発行・一覧・成果物取得のみ。検出系の権限は持たせない（分離）。
        ui_api_role = iam.Role(
            self, "UiApiRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="JP PII masking UI/API role (presigned URL, list, fetch)",
        )
        input_bucket.grant_put(ui_api_role)      # アップロード用署名付きURL
        output_bucket.grant_read(ui_api_role)    # 成果物の閲覧・取得
        output_bucket.grant_put(ui_api_role)     # レビュー承認（メタJSON更新）
        # 署名付きURLでの SSE-KMS 連携に必要
        key.grant_encrypt_decrypt(ui_api_role)
        # CloudWatch Logs（API Lambda の実行ログ）
        ui_api_role.add_to_policy(iam.PolicyStatement(
            sid="Logs",
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:*"],
        ))

        # --- 出力 ------------------------------------------------------------
        CfnOutput(self, "InputBucketName", value=input_bucket.bucket_name)
        CfnOutput(self, "OutputBucketName", value=output_bucket.bucket_name)
        CfnOutput(self, "KmsKeyArn", value=key.key_arn)
        CfnOutput(self, "ProcessingRoleArn", value=processing_role.role_arn)
        CfnOutput(self, "UiApiRoleArn", value=ui_api_role.role_arn)

        # --- 属性公開（クロススタック参照用） --------------------------------
        self.input_bucket = input_bucket
        self.output_bucket = output_bucket
        self.key = key
        self.processing_role = processing_role
        self.ui_api_role = ui_api_role
