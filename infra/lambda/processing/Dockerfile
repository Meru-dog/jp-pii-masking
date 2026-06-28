# 処理関数 Lambda のコンテナイメージ。
# Lambda 公式ベースイメージ（Python 3.12）を使用し、ローカル環境(3.14)に依存しない。
FROM public.ecr.aws/lambda/python:3.12

# 依存をインストール（検出コアが使用）
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# 検出コアとハンドラを同梱
COPY detection ${LAMBDA_TASK_ROOT}/detection
COPY handler.py ${LAMBDA_TASK_ROOT}/

# Lambda ハンドラのエントリポイント
CMD ["handler.handler"]
