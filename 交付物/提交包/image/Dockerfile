# 路径 A：纯规则引擎，不需要加速卡，DCU 与 A10 服务器均可运行。
# 所有依赖在构建期装入；运行期无网络、单次运行、无状态。
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spec ./spec
COPY src ./src
COPY agent ./agent

# 构建期校验：规格可解析，且规则与实现逐条对应
RUN python -m sqa validate-spec

# LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 由评测平台运行时注入，不写入镜像
ENTRYPOINT ["python", "-m", "sqa"]
CMD ["run", "--input", "/data", "--output", "/output"]
