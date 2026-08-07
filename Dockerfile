# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Air Agent — LangGraph API runtime image
#
# Base image: official `langchain/langgraph-api` (Python 3.13 + preinstalled
# LangGraph API server). Use Apple Container `container build -t <tag> .`;
# the image is also compatible with any OCI-compliant builder.
#
# Build (Apple Container):
#   container build -t air-agent/langgraph-api:latest .
#   # or with a pip mirror for mainland China networks:
#   container build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/ \
#       -t air-agent/langgraph-api:latest .
#
# Base is pinned to a specific tag for reproducibility. The upstream default
# registry path is `docker.io/langchain/langgraph-api:<tag>`; override with
# `--build-arg BASE_IMAGE=...` if you need to mirror it through a private
# registry (e.g. `docker.m.daocloud.io/langchain/langgraph-api:3.13`).
# ---------------------------------------------------------------------------

ARG BASE_IMAGE=langchain/langgraph-api:3.13
FROM ${BASE_IMAGE}

# Mirror-friendly pip index (overridable at build time for e.g. CI running
# outside mainland China, or against a private index).
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/
ARG PIP_EXTRA_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# Install uv first (faster, deterministic installs from requirements.lock.txt).
# Keep this as a separate layer — it rarely changes between project rebuilds.
RUN pip install --no-cache-dir \
    --index-url "${PIP_INDEX_URL}" \
    --extra-index-url "${PIP_EXTRA_INDEX_URL}" \
    uv

COPY requirements.lock.txt pyproject.toml README.md LICENSE ./

RUN uv pip install --system \
    --index-strategy unsafe-best-match \
    --index-url "${PIP_INDEX_URL}" \
    --extra-index-url "${PIP_EXTRA_INDEX_URL}" \
    -r requirements.lock.txt

# Application source and editable install (no-deps to avoid re-resolving the
# full locked environment — versions are already pinned by requirements.lock).
COPY src/ ./src/
COPY langgraph.json ./langgraph.json
RUN uv pip install --system --no-deps \
    --index-url "${PIP_INDEX_URL}" \
    --extra-index-url "${PIP_EXTRA_INDEX_URL}" \
    -e .

ENV PYTHONPATH=/app:/app/src

# Default entrypoint defers to the runtime orchestrator (docker-compose.yaml
# explicitly sets `command: ["langgraph", "dev", ...]` when running as a
# service). A bare `container run -it --rm <this-image> /bin/sh` also works
# for interactive debugging.
CMD ["--help"]
