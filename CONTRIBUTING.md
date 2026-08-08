# 贡献指南

感谢你愿意给这个 MCP 三层治理 MVP 提改进！这份指南让你快速上手开发环境与规范。

## 开发环境

```bash
git clone https://github.com/helloworldtang/mcp-governance-mvp.git
cd mcp-governance-mvp
uv sync                       # 装运行依赖 + dev 工具（ruff/mypy/pytest）
export DEEPSEEK_API_KEY=sk-... # 仅跑 run.sh（带 LLM 的端到端）时需要；pytest 不需要
```

前置只要 [uv](https://docs.astral.sh/uv/)；Python 3.12 由 uv 自动拉取。

## 三道质量门（提交前必过）

```bash
uv run ruff check .                       # 1. lint
uv run ruff format --check .              # 2. 格式（改代码后跑 `ruff format .` 自动修）
uv run mypy . --ignore-missing-imports    # 3. 类型（strict）
uv run pytest                             # 4. 测试（26 个，不起 LLM，~3.5s）
```

CI（`.github/workflows/ci.yml`）会自动跑这四步；本地先过一遍省得来回。

规约来自 [python-code-style](https://skills.sh/wshobson/agents/python-code-style) skill：行长 120、双引号、PEP8 命名、导入三分组(stdlib→三方→本地)、绝对导入、Google-style docstring、所有 public API 带类型注解。

## 跑端到端（带 LLM）

```bash
./run.sh explicit "查一下上海天气" --user bob      # 需 DEEPSEEK_API_KEY
./compare_gateways.sh "查一下北京天气" --user bob  # 两 Gateway 变体并排
```

## 测试怎么写

- 不依赖 DeepSeek：直接用 FastMCP client 打各层 HTTP 端点，或用 FastAPI TestClient（见 `tests/`）。
- 涉及起服务的测试，用 `_Server`（线程内 uvicorn）+ 端口探活，参考 `tests/test_gateway_e2e.py`。
- Registry CRUD 用 `tests/test_registry.py` 的 TestClient 模式（in-process，最快）。

## 提交规范

- 用 [Conventional Commits](https://www.conventionalcommits.org/)：`feat: ...` / `fix: ...` / `docs: ...` / `refactor: ...` / `test: ...` / `chore: ...`。
- **commit message 绝不加 `Co-Authored-By` 或任何机器署名**（这是项目维护者的明确偏好）。
- 只提交项目必须的文件；`output/`、`.claude/`、`.venv/`、`.env` 已在 `.gitignore`，别手动加进去。
- 提交前 `git status` + `git diff --cached` 复核暂存内容，确认无敏感信息（真 API key 等）。

## 项目结构速览（改哪里）

| 想改什么 | 看哪里 |
|---|---|
| 加一个 Runtime 工具 | `runtime/weather.py`（仿 `@mcp.tool`），并在 `_self_register` 注册 |
| 加一个 Gateway 变体 | 仿 `gateway/explicit_proxy.py` 或 `fastmcp_native.py` |
| 改身份/角色 | `core/config.py` 的 `API_KEYS` |
| 改端口 | `core/config.py` 顶部端口常量（4 个进程各一个） |
| 加测试 | `tests/`（见上） |

每个源文件顶部都有【Java/C 读者速查】注释块，解释该文件用到的 Python 特性，不熟 Python 也能看懂。
