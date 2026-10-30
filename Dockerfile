# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Air Agent — 官方 langgraph-api 镜像 + 项目代码
#
# 由 `langgraph dockerfile` 生成，手动调整为 py3.13 + 国内镜像源。
# 官方镜像内置 Go core-server + Python data-plane，需要 Redis 做迁移锁。
# 配合 docker-compose.yaml 中的 redis 服务一起使用。
#
# Build:
#   container build -t air-agent/langgraph-api:latest .
#   # 或通过 compose:
#   container-compose --profile prod build
# ---------------------------------------------------------------------------

# 基础镜像固定 digest：tag `3.13` 于 2026-10-20 解析为下列多架构索引
# （linux/arm64 manifest revision 8d6666b、Python 3.13.16、OTel 1.42.1）。
# 重建必须以该 digest 为准；升级基础镜像需重新核对 /api/constraints.txt。
FROM langchain/langgraph-api:3.13@sha256:c171cf1546cf8af4cfbd01c8d1989f0a91569da81d0c5b1ba9316cdef87f53ab

# 国内镜像源加速（官方镜像已预装 uv）
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/
ENV UV_EXTRA_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# -- 添加项目代码 --
ADD . /deps/air_agent

# -- 安装项目锁定依赖（pyproject.toml + uv.lock 为真相源, 由 uv export 生成安装清单）--
RUN cd /deps/air_agent \
 && PYTHONDONTWRITEBYTECODE=1 uv export --frozen --no-dev -o /tmp/requirements-export.txt \
 && uv pip install --system --no-cache-dir \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple/ \
        --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
        --index-strategy unsafe-best-match \
        -r /tmp/requirements-export.txt \
 && rm -f /tmp/requirements-export.txt

# -- 安装项目本身（editable，-c /api/constraints.txt 防止覆盖 langgraph-api 版本）--
RUN cd /deps/air_agent \
 && PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir -c /api/constraints.txt --no-deps -e .

# -- 确保用户依赖没有覆盖 langgraph-api 核心包 --
RUN mkdir -p /api/langgraph_api /api/langgraph_runtime /api/langgraph_license \
 && touch /api/langgraph_api/__init__.py /api/langgraph_runtime/__init__.py /api/langgraph_license/__init__.py
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir --no-deps -e /api

ENV LANGSERVE_GRAPHS='{"basic-qa": "/deps/air_agent/src/basic_qa/graph.py:graph", "data-analysis": "/deps/air_agent/src/data_analysis/graph.py:graph", "intelligent-tracing": "/deps/air_agent/src/intelligent_tracing/graph.py:graph"}'

ENV LANGGRAPH_HTTP='{"app": "/deps/air_agent/src/application/http.py:app"}'

# -- 企业模式原生认证（add-tenant-identity-access；noop 已退出）--
# RUNTIME_TICKET_KEYS / RUNTIME_DATABASE_URI / RUNTIME_TICKET_AUDIENCE 由
# env_file .env 与 compose 注入；密钥不进镜像。
ENV LANGGRAPH_AUTH_TYPE='custom'
ENV LANGGRAPH_AUTH='{"path": "/deps/air_agent/src/application/langgraph_auth.py:auth", "disable_studio_auth": true}'

WORKDIR /deps/air_agent
