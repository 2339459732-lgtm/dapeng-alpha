FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# curl 供 HEALTHCHECK 使用；gcc 供少数需要编译的依赖（可多阶段优化，这里以可复现为先）
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 运行期目录：镜像内先建好，实际数据由 compose 挂载覆盖
RUN mkdir -p data logs backups

EXPOSE 8900

# 容器内监听 0.0.0.0；未配置 ACCESS_KEY 时 server.main 会拒绝启动（有意为之）
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8900/api/health || exit 1

CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8900"]
