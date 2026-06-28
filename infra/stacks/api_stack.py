"""
api_stack.py — API ＋ 認証（S5）。

RDD v1.1 D-7（単一テナント・少数利用者・権限分掌なし）に沿った最小構成:
- Amazon Cognito User Pool ＋ App Client（JWT 認証）。
- HTTP API（API Gateway v2）＋ JWT オーソライザ。
- API ハンドラ Lambda（基盤の UI/API ロール＝検出系権限なし・署名付きURL用のS3/KMSのみ）。

ファイル実体はブラウザ↔S3を署名付きURLで直結し、API Lambda を経由させない。
本 Lambda は VPC 外（署名付きURL発行・S3メタ操作のみで、Bedrock/Comprehend は使わない）。
"""

from __future__ import annotations

from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_kms as kms,
    aws_iam as iam,
    aws_cognito as cognito,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_authorizers as apigw_auth,
    aws_apigatewayv2_integrations as apigw_int,
)
from constructs import Construct


class ApiStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 input_bucket: s3.IBucket,
                 output_bucket: s3.IBucket,
                 key: kms.IKey,
                 ui_api_role: iam.IRole,
                 allowed_origins=None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        allowed_origins = allowed_origins or ["*"]  # 本番は UI のオリジンに絞る

        # --- Cognito ---------------------------------------------------------
        user_pool = cognito.UserPool(
            self, "UserPool",
            self_sign_up_enabled=False,        # 招待制（管理者が利用者を作成）
            sign_in_aliases=cognito.SignInAliases(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True, require_uppercase=True,
                require_digits=True, require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
        )
        user_pool_client = user_pool.add_client(
            "WebClient",
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            id_token_validity=Duration.hours(1),
            access_token_validity=Duration.hours(1),
            prevent_user_existence_errors=True,
        )

        # --- API ハンドラ Lambda --------------------------------------------
        api_fn = lambda_.Function(
            self, "ApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="api_handler.handler",
            code=lambda_.Code.from_asset("lambda/api"),
            role=ui_api_role,                  # 検出系権限を持たない分離ロール
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "INPUT_BUCKET": input_bucket.bucket_name,
                "OUTPUT_BUCKET": output_bucket.bucket_name,
                "URL_TTL_SECONDS": "900",
            },
        )

        # --- HTTP API ＋ JWT オーソライザ ------------------------------------
        authorizer = apigw_auth.HttpUserPoolAuthorizer(
            "JwtAuthorizer", user_pool,
            user_pool_clients=[user_pool_client],
        )
        http_api = apigw.HttpApi(
            self, "HttpApi",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=allowed_origins,
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST,
                               apigw.CorsHttpMethod.OPTIONS],
                allow_headers=["authorization", "content-type"],
            ),
        )
        integration = apigw_int.HttpLambdaIntegration("ApiIntegration", api_fn)

        for method, path in [
            (apigw.HttpMethod.POST, "/uploads"),
            (apigw.HttpMethod.GET, "/documents"),
            (apigw.HttpMethod.GET, "/documents/download"),
            (apigw.HttpMethod.POST, "/documents/approve"),
        ]:
            http_api.add_routes(
                path=path, methods=[method],
                integration=integration, authorizer=authorizer,
            )

        # --- 出力 ------------------------------------------------------------
        CfnOutput(self, "ApiEndpoint", value=http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
