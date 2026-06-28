"""
processing_stack.py — 処理関数ホスト（S4）。

RDD v1.1 §8 の「処理経路にインターネットegressなし」を達成する段。
- VPC（プライベートサブネットのみ・NATなし＝egレスなし／コスト抑制）。
- VPCエンドポイント：S3(Gateway・無料)、Bedrock-runtime/Comprehend/KMS/CloudWatch Logs(Interface)。
- 検出コアをコンテナ化した Lambda（VPC内・基盤スタックの処理ロールを使用）。
- 入力バケットへのアップロードを EventBridge で捕捉し Lambda を起動。

基盤スタック（FoundationStack）の構成は CDK のクロススタック参照で受け取る。

注意（コスト）: Interface エンドポイントは時間課金。月50文書規模では本スタックが固定費の主因。
"""

from __future__ import annotations

from typing import Optional

from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_kms as kms,
    aws_iam as iam,
    aws_events as events,
    aws_events_targets as targets,
    aws_ecr_assets as ecr_assets,
)
from constructs import Construct


class ProcessingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 input_bucket: s3.IBucket,
                 output_bucket: s3.IBucket,
                 key: kms.IKey,
                 processing_role: iam.IRole,
                 guardrail_id: Optional[str] = None,
                 guardrail_version: str = "1",
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- VPC（プライベートのみ・NATなし） --------------------------------
        vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=2,
            nat_gateways=0,  # egレスなし・コスト抑制
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="private-isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # --- VPCエンドポイント -----------------------------------------------
        vpc.add_gateway_endpoint(
            "S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3,
        )
        for sid, svc in [
            ("BedrockRuntimeEndpoint", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME),
            ("ComprehendEndpoint", ec2.InterfaceVpcEndpointAwsService.COMPREHEND),
            ("KmsEndpoint", ec2.InterfaceVpcEndpointAwsService.KMS),
            ("LogsEndpoint", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS),
        ]:
            vpc.add_interface_endpoint(sid, service=svc, private_dns_enabled=True)

        # --- 処理 Lambda（コンテナイメージ） ---------------------------------
        env = {
            "OUTPUT_BUCKET": output_bucket.bucket_name,
            "GUARDRAIL_VERSION": guardrail_version,
            "COMPREHEND_THRESHOLD": "0.3",
            "NORMALIZE_WIDTH": "false",
        }
        if guardrail_id:
            env["GUARDRAIL_ID"] = guardrail_id

        # ビルドホストと Lambda 実行アーキテクチャを一致させる（不一致は InvalidEntrypoint の原因）。
        # 既定 arm64（Apple Silicon）。Intel等は -c lambda_arch=x86_64 で切替。
        arch = (self.node.try_get_context("lambda_arch") or "arm64").lower()
        if arch == "x86_64":
            lambda_arch = lambda_.Architecture.X86_64
            build_platform = ecr_assets.Platform.LINUX_AMD64
        else:
            lambda_arch = lambda_.Architecture.ARM_64
            build_platform = ecr_assets.Platform.LINUX_ARM64

        fn = lambda_.DockerImageFunction(
            self, "ProcessingFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                "lambda/processing",
                platform=build_platform,
            ),
            architecture=lambda_arch,
            role=processing_role,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            timeout=Duration.minutes(5),
            memory_size=2048,
            environment=env,
        )

        # VPC実行に要る ENI 管理権限を処理ロールに補う
        processing_role.add_to_principal_policy(iam.PolicyStatement(
            actions=[
                "ec2:CreateNetworkInterface",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DeleteNetworkInterface",
                "ec2:AssignPrivateIpAddresses",
                "ec2:UnassignPrivateIpAddresses",
            ],
            resources=["*"],
        ))

        # --- EventBridge 起動（入力バケットの Object Created） ----------------
        rule = events.Rule(
            self, "InputObjectCreatedRule",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={"bucket": {"name": [input_bucket.bucket_name]}},
            ),
        )
        rule.add_target(targets.LambdaFunction(fn))

        CfnOutput(self, "ProcessingFunctionName", value=fn.function_name)
        CfnOutput(self, "VpcId", value=vpc.vpc_id)
