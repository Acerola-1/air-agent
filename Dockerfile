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

FROM langchain/langgraph-api:3.13

# 国内镜像源加速（官方镜像已预装 uv）
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/
ENV UV_EXTRA_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# -- 添加项目代码 --
ADD . /deps/air_agent

# -- 安装项目锁定依赖（pyproject.toml 只声明了 3 个包，实际依赖在 requirements.lock.txt）--
RUN cd /deps/air_agent \
 && PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple/ \
        --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
        -r requirements.lock.txt

# -- 安装项目本身（editable，-c /api/constraints.txt 防止覆盖 langgraph-api 版本）--
RUN cd /deps/air_agent \
 && PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir -c /api/constraints.txt --no-deps -e .

# -- 确保用户依赖没有覆盖 langgraph-api 核心包 --
RUN mkdir -p /api/langgraph_api /api/langgraph_runtime /api/langgraph_license \
 && touch /api/langgraph_api/__init__.py /api/langgraph_runtime/__init__.py /api/langgraph_license/__init__.py
RUN PYTHONDONTWRITEBYTECODE=1 uv pip install --system --no-cache-dir --no-deps -e /api

ENV LANGSERVE_GRAPHS='{"basic-qa": "/deps/air_agent/src/basic_qa/graph.py:graph", "intelligent-analysis": "/deps/air_agent/src/intelligent_analysis/graph.py:graph", "data-analysis": "/deps/air_agent/src/data_analysis/graph.py:graph", "intelligent-report": "/deps/air_agent/src/intelligent_report/graph.py:graph", "deep-research": "/deps/air_agent/src/deep_research/graph.py:graph", "intelligent-tracing": "/deps/air_agent/src/intelligent_tracing/graph.py:graph"}'

WORKDIR /deps/air_agent
