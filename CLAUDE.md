# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

This is a Python LangGraph / DeepAgents application for air-quality business assistants. The current architecture is not the legacy `src/agent/graph.py -> nodes.py` workflow. Public graphs are independent DeepAgents graph entry points declared in `langgraph.json`:

- `basic-qa`: `src/basic_qa/graph.py:graph`
- `intelligent-analysis`: `src/intelligent_analysis/graph.py:graph`
- `data-analysis`: `src/data_analysis/graph.py:graph`
- `intelligent-report`: `src/intelligent_report/graph.py:graph`
- `deep-research`: `src/deep_research/graph.py:graph`
- `intelligent-tracing`: `src/intelligent_tracing/graph.py:graph`

Shared runtime code lives in `src/common/`. Business skills live beside each graph under `src/<graph_name>/skills/`.

## Development Commands

```bash
# Install package and dev tools
pip install -e ".[dev]"

# Tests
make test
make test TEST_FILE=tests/unit_tests/test_skill_tool_disclosure_middleware.py
make integration_tests

# Linting and formatting
make lint
make format

# Preferred targeted checks while editing
ruff check <files>
ruff format <files>
python3 -m compileall <files>
.venv/bin/python -m pytest <targeted-test-file>

# LangGraph development
langgraph dev
```

Use `python3` or `.venv/bin/python`; do not assume a `python` binary exists.

## Architecture

### Graph Entry Pattern

Each business graph calls `deepagents.create_deep_agent(...)` directly. The graph usually has:

- `ModelRegistry.deepseek_v3` as the model.
- A graph-local `SKILLS_DIR = Path(__file__).parent / "skills"`.
- A `find_skill` tool.
- `skills=["/skills/"]` and `FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)` so DeepAgents can read graph-local Skill files.
- Shared middleware for MCP refresh, resilience, time context, rich output, final output cleanup, mode routing, and skill tool disclosure.
- PostgreSQL checkpointing via `get_checkpointer()`.

`basic-qa`, `intelligent-analysis`, `deep-research`, `intelligent-report`, and `intelligent-tracing` use semantic skill routing through `common.skill_discovery.create_find_skill_tool(SkillSemanticRouter(SKILLS_DIR))`.

`data-analysis` is special: it uses `data_analysis.skill_discovery.create_data_analysis_find_skill_tool(SKILLS_DIR)` and maps frontend `configurable.menu_name` / `module` to a deterministic page skill via `data_analysis/menu_skill_mapping.py`.

### Progressive Skill Tool Disclosure

The key runtime module is `src/common/middleware/skill_tool_disclosure_middleware.py`.

It contains two middleware classes:

- `SkillToolRegistryMiddleware`: pre-registers all business tools and refreshes runtime MCP tools before model calls. It also implements `wrap_tool_call` / `awrap_tool_call` to bind a runtime-discovered tool instance when the ToolNode did not have it upfront.
- `SkillToolFilterMiddleware`: filters model-visible business tools according to the selected Skill's `allowed-tools`.

The intended flow is:

1. The model first sees `find_skill` and DeepAgents built-in tools.
2. The model calls `find_skill`.
3. `find_skill` writes Skill state, especially `selected_skill_allowed_tools`, through a `Command(update=...)`.
4. On the next model call, `SkillToolRegistryMiddleware` adds current business tools to `request.tools`.
5. `SkillToolFilterMiddleware` keeps framework tools and only exposes business tools listed in `selected_skill_allowed_tools`.

Important behavior:

- Once a Skill is selected, its allowed tools remain visible across follow-up questions and model query rewrites until `find_skill` updates the state again.
- Do not restore the old stale-question text comparison between `skill_search_question` and the latest user message. That comparison blocks useful AI behavior such as query rewriting, self-correction, and switching to a better Skill in the same conversation.
- If a selected Skill is wrong, the model may call `find_skill` again with a rewritten query. The latest `find_skill` result should replace the selected Skill state and therefore replace the exposed tool set.

### unmatched_policy

`SkillToolFilterMiddleware(unmatched_policy=...)` controls what happens after `find_skill` was attempted but no Skill / no `allowed-tools` is selected.

- `unmatched_policy="native"`: expose all registered business tools after no Skill match. This is used by `basic-qa`, `intelligent-analysis`, `deep-research`, `intelligent-report`, and `intelligent-tracing` so the agent can fall back to DeepAgents native capability and available tools.
- `unmatched_policy="strict"`: keep business tools hidden when no Skill matches. This is the default and is intentionally used by `data-analysis`, because data-analysis follows page/menu-specific logic and should not broadly expose unrelated tools on a menu mismatch.

Before changing this behavior, check the graph-specific intent. Do not globally change `data-analysis` to `native`.

### Skills

Each Skill directory uses this structure:

```text
skills/<skill-name>/
  SKILL.md
  references/
    fast.md
    expert.md
```

`SKILL.md` must include YAML frontmatter with:

- `name`
- `description`
- `allowed-tools`

The Skill body should tell the agent to read `references/fast.md` or `references/expert.md` according to runtime mode. The detailed reference file controls data fetching, validation, missing-data handling, chart calls, and output shape.

When adding or changing a Skill:

- Keep the frontmatter `description` focused on routing.
- Put actual execution rules in `references/fast.md` and `references/expert.md`.
- Ensure every required runtime tool is listed in `allowed-tools`; otherwise the model may read the Skill but be unable to call the tool.
- Do not add tools to `allowed-tools` just for documentation. It is an execution allowlist.

## DeepAgents Ending Condition

DeepAgents uses LangChain's agent loop. The loop continues while the last `AIMessage` contains pending `tool_calls`.

The agent proceeds to `after_agent` / END when:

- the last model response has no `tool_calls`;
- all executed tools are `return_direct=True`;
- structured output is completed;
- middleware explicitly routes with `jump_to="end"`.

Progressive tool disclosure does not change this condition directly, but it strongly affects whether the model can produce tool calls. If tools are accidentally hidden, the model may stop with text like "I need to call X" but no actual `tool_calls`.

## Subagent Tool Caveat

DeepAgents synchronous subagents created by the built-in `task` tool are independently compiled agents. They inherit only static `tools=` and their own middleware at construction time. Runtime tools injected into the parent agent's `request.tools` by middleware do not automatically appear inside a default or compiled subagent.

Implications:

- Parent-agent progressive disclosure does not automatically solve subagent tool visibility.
- A declarative subagent that needs business tools must receive equivalent tool registration/filter middleware.
- A `CompiledSubAgent` or remote async subagent is a black box; it must be built with its own required tools and middleware.

## Key Files

| File | Purpose |
|------|---------|
| `langgraph.json` | Public graph entry declarations |
| `src/basic_qa/graph.py` | Basic QA DeepAgent graph |
| `src/intelligent_analysis/graph.py` | Intelligent analysis DeepAgent graph |
| `src/data_analysis/graph.py` | Page/menu-driven data analysis DeepAgent graph |
| `src/deep_research/graph.py` | Deep research DeepAgent graph |
| `src/intelligent_report/graph.py` | Intelligent report DeepAgent graph |
| `src/intelligent_tracing/graph.py` | Intelligent tracing DeepAgent graph |
| `src/common/models.py` | Central model registry |
| `src/common/middleware/skill_tool_disclosure_middleware.py` | Progressive tool registration and filtering |
| `src/common/skill_discovery.py` | Semantic `find_skill` tool and Skill state |
| `src/common/skill_router.py` | Skill metadata parsing and semantic matching |
| `src/common/runtime_tools.py` | Runtime business tool providers |
| `src/common/mcp_client.py` | MCP client initialization and tool refresh |
| `src/data_analysis/skill_discovery.py` | Menu-driven `find_skill` for data-analysis |
| `src/data_analysis/menu_skill_mapping.py` | Frontend menu to Skill mapping |

## Configuration

Environment variables are loaded through `src/common/config/settings.py` and exposed as `common.config.config`.

Important shared services include:

- LLM providers configured in `src/common/models.py`.
- MCP servers configured in common settings and initialized through `src/common/mcp_client.py`.
- PostgreSQL checkpointing through `src/common/config/checkpointing.py`.
- Milvus / metadata / Text2SQL helpers under `src/common/`.

Keep secrets in `.env`; do not commit real keys.

## Coding Conventions

- Use Ruff as the formatting and linting source of truth.
- Use Google-style docstrings when docstrings are needed.
- Use `snake_case` for modules, functions, variables, and tools.
- Use `PascalCase` for classes.
- Middleware modules should use descriptive names ending in `_middleware.py`.
- Prefer existing `common` helpers and graph-local patterns over new abstractions.
- Tests belong in `tests/unit_tests/` or `tests/integration_tests/`.

## Verification Guidance

Prefer targeted checks:

- Middleware changes: `ruff check <middleware> <tests>` and targeted pytest.
- Graph wiring changes: compile graph files and run relevant graph config tests.
- Skill routing changes: run skill router/discovery tests.
- Shared model/config changes: compile affected modules and run focused import/config tests if present.

Avoid full-repository checks during investigation unless the change touches shared contracts or the user asks for exhaustive validation.
