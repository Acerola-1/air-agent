# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.13 **LangGraph API** (standardized REST) project with 6 public graphs declared in `langgraph.json`:

| Graph ID            | Entry file                        | Purpose               |
|---------------------|-----------------------------------|-----------------------|
| `basic-qa`          | `src/basic_qa/graph.py:graph`           | Q&A + semantic skill routing |
| `intelligent-analysis` | `src/intelligent_analysis/graph.py:graph` | Smart analysis skill routing |
| `data-analysis`     | `src/data_analysis/graph.py:graph`      | Menu/page-driven deterministic analysis (uses `menu_skill_mapping.py`) |
| `intelligent-report` | `src/intelligent_report/graph.py:graph`  | Report generation |
| `deep-research`     | `src/deep_research/graph.py:graph`      | Deep research workflow |
| `intelligent-tracing` | `src/intelligent_tracing/graph.py:graph`| Tracing / root cause |

Shared runtime is under `src/common/`:

- `src/common/config/` — settings + SQLite/PostgreSQL checkpointing
- `src/common/middleware/` — 10 middlewares (MCP/resilience/time-context/rich-output/final-cleanup/mode-routing/skill-tool-disclosure/permission/*)
- `src/common/{models,skill_discovery,skill_router,mcp_client,runtime_tools,permission,tools}.py`
- Business skills live per-graph under `src/<graph-id>/skills/<skill-name>/` with `SKILL.md` + `references/{fast,expert}.md`.

Frontend is a Next.js 16 assistant-ui app under `frontend/`, proxying `/api/*` → langgraph API `:2024`. Design/reference notes → `docs/`. Tests → `tests/unit_tests/`, `tests/integration_tests/`.

## Build, Test, and Development Commands

- `pip install -e ".[dev]"`: install the package and development tools from `pyproject.toml`.
- `make format`: run Ruff formatting and import fixes over the repository.
- `make lint`: run Ruff checks, Ruff format diff, import checks, and strict mypy.
- `make test`: run unit tests from `tests/unit_tests/` by default.
- `make test TEST_FILE=tests/unit_tests/test_example.py`: run a specific test file or directory.
- `make integration_tests`: run integration tests from `tests/integration_tests/`.

For LangGraph Studio/local graph execution, keep `.env` populated and use the graph names defined in `langgraph.json`: `agent` and `data_analysis`.

## Local Verification Preferences

Use `python3` or `.venv/bin/python` for Python commands; do not assume a `python` binary exists. Prefer fast, targeted verification while iterating:

- `ruff check <files>` and `ruff format <files>` for linting/formatting.
- `basedpyright <files>` when type checking is needed; `pyright <files>` is an acceptable fallback if `basedpyright` is unavailable.
- Targeted `pytest` only for tests directly affected by the change.

Avoid routine full-repository or long-running checks during investigation unless the change touches shared contracts or the user explicitly asks for exhaustive validation.

## OpenSpec 文档规范

编写或更新 `openspec/changes/**` 与 `openspec/specs/**` 下的 OpenSpec 文档时，正文、需求说明、场景描述、设计说明和任务描述应使用中文。为保证 OpenSpec CLI 能正确解析，可以保留 schema 要求的固定英文结构标记，例如 `## ADDED Requirements`、`### Requirement:`、`#### Scenario:`、`**WHEN**`、`**THEN**`、`**AND**` 以及任务清单的 `- [ ]` 格式。

## Coding Style & Naming Conventions

Use Ruff as the source of formatting truth. Follow Google-style docstrings when docstrings are needed. Keep Python modules and files in `snake_case`; use `PascalCase` for classes and `snake_case` for functions, variables, and tools. Middleware modules should use descriptive names ending in `_middleware.py`. Skill directories should use lowercase kebab-case, matching existing examples such as `air-quality-basic-query`.

## Testing Guidelines

Use pytest. Place focused unit tests in `tests/unit_tests/` and broader service or graph-flow tests in `tests/integration_tests/`. Name files `test_*.py` and test functions `test_*`. Prefer small fixtures and explicit assertions around graph state, middleware behavior, and tool outputs. Run `make test` before submitting routine changes and `make integration_tests` when touching persistence, external services, or graph wiring.

## Commit & Pull Request Guidelines

Recent commits use Chinese bracketed prefixes, for example `[新增] 添加旧版图表数据兼容中间件` and `[优化] 重构项目结构并改进代码质量`. Keep that convention: start with a concise category such as `[新增]`, `[优化]`, `[修复]`, or `[文档]`, followed by an imperative summary.

Pull requests should include a short purpose statement, key implementation notes, tests run, and any configuration or `.env` changes. Include screenshots or sample outputs when changing user-visible graph responses, rich output formatting, or generated artifacts.

## Security & Configuration Tips

Do not commit secrets from `.env`, logs, or local cache directories. Keep real local values in `.env`; track configuration shape and non-secret defaults in `.env.example` so environment changes are visible in git. Treat `requirements.lock.txt`, `uv.lock`, `langgraph.json`, and Docker files as shared environment contracts; update them deliberately and mention related runtime impacts in the PR.

## 各因子污染等级说明

等级对应关系：

- 1=优
- 2=良
- 3=轻度污染
- 4=中度污染
- 5=重度污染
- 6=严重污染

时间区分说明：

- 2026-01-01 之前的数据，按旧边界判断
- 2026-01-01 及之后的日均数据，按新边界判断
- 2026-01-01 01:00:00 及之后的小时数据，按新边界判断

已知边界：

CO 日均：
1级 0–2，2级 2–4，3级 4–14，4级 14–24，5级 24–36，6级 >36

CO 小时：
1级 0–5，2级 5–10，3级 10–35，4级 35–60，5级 60–90，6级 >90

NO2 日均：
1级 0–40，2级 40–80，3级 80–180，4级 180–280，5级 280–565，6级 >565

NO2 小时：
1级 0–100，2级 100–200，3级 200–700，4级 700–1200，5级 1200–2340，6级 >2340

O3 日均：
1级 0–100，2级 100–160，3级 160–215，4级 215–265，5级 265–800，6级 >800

O3 小时：
1级 0–160，2级 160–200，3级 200–300，4级 300–400，5级 400–800，6级 >800

O3 8小时：
1级 0–100，2级 100–160，3级 160–215，4级 215–265，5级 265–800，6级 >800

SO2 日均：
1级 0–50，2级 50–150，3级 150–475，4级 475–800，5级 800–1600，6级 >1600

SO2 小时：
1级 0–150，2级 150–500，3级 500–650，4级 650–800，5级 800，6级 >800

PM10 旧边界（日均/小时）：
1级 0–50，2级 50–150，3级 150–250，4级 250–350，5级 350–420，6级 >420

PM2.5 旧边界（日均/小时）：
小时：1级 0–35，2级 35–75，3级 75–115，4级 115–150，5级 150–250，6级 >250

## Container & Deployment (Apple Container + Container-Compose)

**Runtime choice**: This project does NOT use Docker Desktop. macOS-local container runtime is Apple's `container` CLI (OCI-compatible, installed via signed pkg); multi-service orchestration uses `container-compose` (Mcrich23/Container-Compose, Swift, parse docker-compose.yaml).

### Apple Container (container CLI) — Quick reference

- Start/stop runtime: `container system start` / `container system stop` / `container system status`
- List containers/images/volumes: `container ls` / `container images` / `container volumes ls`
- Inspect / logs / exec: `container logs -f <name>` / `container exec -it <name> -- <cmd>` / `container inspect <name>`
- Lifecycle: `container run -d --name X -p HOST:CTR -e K=V -v VOL:/path image:tag` / `container stop|start|rm <name>` / `container image rm <tag>`
- Build from Dockerfile: `container build -t <tag> -f Dockerfile [--build-arg K=V] [--no-cache] .`
- **Known constraints (agent must remember)**:
  - No Docker Engine API / no `docker.sock` → Portainer, Testcontainers, docker SDK, Podman Desktop GUI do NOT work against it
  - No `docker compose` binary built in; use `container-compose` instead
  - `container-compose` only implements `up / down / build / version` subcommands. For `logs / ps / exec / restart` use native `container` commands.
  - PostgreSQL volume: set `PGDATA` to a **child** of the mount (e.g. `/var/lib/postgresql/data/pgdata`) — otherwise initdb fails on `lost+found` at the volume root. This is identical to Docker Desktop behavior.

### container-compose (Mcrich23/Container-Compose) — Quick reference

- **Install (user step)**: Release binary from GitHub → `chmod +x container-compose && sudo cp container-compose /usr/local/bin/`
- **Supported subcommands only**: `container-compose up [-d] [--build] [--no-cache] [--profile NAME] [<svc>]` / `container-compose down [<svc>]` / `container-compose build [--no-cache] [<svc>]` / `container-compose version`
- **Global options**: `-f,--file PATH`, `--profile NAME` (repeatable; also accepts env `COMPOSE_PROFILES=a,b,c`), `--env-file PATH`
- **Service fields supported** (verified from Swift structs): `image, build{context,dockerfile,args}, restart, healthcheck{test,interval,timeout,retries,start_period}, volumes[], environment{dict}, env_file[], ports[], command[], entrypoint[], depends_on[] | depends_on.<svc>.condition=service_started|service_healthy|service_completed_successfully +restart, user, container_name, hostname, working_dir, privileged, read_only, platform, mem_limit, stdin_open, tty, extra_hosts[], labels{}, networks[], profiles[], secrets, configs`
- **Top-level fields**: `name, version(optional), services{}, volumes{}, networks{}, configs{}, secrets{}`
- **Service discovery**: container-compose automatically uses the `<service>` name as the hostname inside containers (macOS 26 Tahoe optimal; README warns macOS 15 Sequoia DNS may need manual setup). Container names on the host are `<projectName>-<serviceName>`.
- **Key behavioral differences vs docker compose (agent must not get wrong)**:
  1. Foreground `up` (no `-d`): killing the process **does NOT stop the containers**. Always use `-d` + explicit `down`.
  2. No `ps / logs / restart / exec / pull` subcommands. Use `container ls | grep <project>-`, `container logs -f <project>-<svc>`, `container stop/start <project>-<svc>`, `container exec -it <project>-<svc> -- <cmd>` instead.
- **Project-specific convention (see docker-compose.yaml at repo root)**:
  - Project name: `air-agent`
  - `postgres` service (17-alpine, port 15432→5432, healthcheck via `pg_isready`, named volume `pgdata` with child-PGDATA pattern). **Always started by default** (no profile gate) — local development uses it for the checkpointer.
  - `langgraph-api` service (builds from repo Dockerfile, depends_on postgres `condition: service_healthy`, env_file `.env`, port 2024) — **gated behind `profiles: [prod]`**. Never auto-starts in local dev where we use `.venv/bin/langgraph dev`.
  - So: **Local dev flow = `container-compose up -d` (only PG starts) + `run-local.sh` venv frontend+langgraph**. Full-container staging flow = `container-compose up -d --build --profile prod`.
