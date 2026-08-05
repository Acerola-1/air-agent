# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.13 LangGraph agent project. Primary application code lives in `src/agent/`, with graph entry points configured in `langgraph.json` as `src/agent/agent.py:graph` and `src/data_analysis_assistant/graph.py:graph`. Configuration and persistence helpers are under `src/agent/config/`; middleware lives in `src/agent/middleware/`; reusable skill prompts and references live in `src/agent/skills/`. Static data belongs in `static/`, and design/reference notes belong in `docs/`. Tests are expected under `tests/unit_tests/` and `tests/integration_tests/`.

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
