# AIOps Agent — 多阶段构建
FROM python:3.10-slim AS builder

WORKDIR /app

# 安装构建依赖
RUN pip install --no-cache-dir hatchling

# 复制项目文件
COPY pyproject.toml README.md ./
COPY src/ src/

# 构建 wheel
RUN pip wheel --no-deps --wheel-dir /wheels .

# --- 运行阶段 ---
FROM python:3.10-slim

WORKDIR /app

# 安装运行时依赖
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# 复制配置文件
COPY config/ config/

# 创建日志目录
RUN mkdir -p logs/audit logs/audit_backup data/sessions data/long_term_memory

# 非 root 用户运行
RUN useradd -m -r agent && chown -R agent:agent /app
USER agent

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

EXPOSE 8080

CMD ["python", "-m", "aiops_agent.main"]
