"""DeepSeek ReAct Agent —— 三层治理的调用方（Client 腿）。

连到 Gateway 的 streamable-http 端点，带 Authorization: Bearer <key>。
Gateway 解析 key → 注入 X-User/X-Role → 路由到 Runtime → Runtime 在执行点做 per-user 鉴权。

本模块只负责「以某用户身份把任务交给 Agent」，ReAct 选工具的循环由 langgraph create_react_agent 驱动。
"""

import os
import secrets

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import SecretStr

from core.config import API_KEYS, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, GATEWAY_URL
from core.log_util import log_action, log_client, log_observe

_SERVER = "gateway"


def _key_for_user(user: str) -> str:
    """反查 user → API key；找不到抛 ValueError（罗列可选用户）。"""
    for k, v in API_KEYS.items():
        if v["user"] == user:
            return k
    raise ValueError(f"未知用户 {user!r}；可选: {[v['user'] for v in API_KEYS.values()]}")


def build_llm() -> ChatOpenAI:
    """DeepSeek（OpenAI 兼容协议）；temperature=0 让工具选择稳定、可复现。"""
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=SecretStr(os.environ["DEEPSEEK_API_KEY"]),
        base_url=DEEPSEEK_BASE_URL,
        temperature=0,
    )


def _mcp_client(api_key: str, trace_id: str) -> MultiServerMCPClient:
    """连 Gateway，每个请求都带 Authorization + X-Trace-Id —— 身份依据 + 全链路 trace。"""
    # langchain-mcp-adapters 的 StreamableHttpConnection TypedDict 与 SSEConnection 在 mypy 下歧义，
    # 运行期正确，框架侧 typing 缺陷，此处定点忽略。
    return MultiServerMCPClient(
        {
            _SERVER: {  # type: ignore[dict-item, misc]
                "transport": "http",
                "url": GATEWAY_URL,
                "headers": {
                    "Authorization": f"Bearer {api_key}",
                    "X-Trace-Id": trace_id,
                },
            }
        }
    )


def _log_trace(messages: list[BaseMessage], trace_id: str) -> None:
    """回放 ReAct 轨迹：AI 选工具 = ACTION，工具返回 = OBSERVE。"""
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                args = ", ".join(f"{k}={v!r}" for k, v in (tc.get("args") or {}).items())
                log_action(tc.get("name", ""), args)
        elif isinstance(m, ToolMessage):
            log_observe(m.name or "", str(m.content))


async def run_task(task: str, user: str) -> str:
    """以 {user} 身份执行任务，返回 Agent 的最终答复文本。一次任务一个 trace ID 串起三层。"""
    api_key = _key_for_user(user)
    role = API_KEYS[api_key]["role"]
    trace_id = secrets.token_hex(3)  # 6 位 hex，够 demo 辨识
    log_client(f"身份 {user}(role={role})  任务: {task}", trace=trace_id)

    client = _mcp_client(api_key, trace_id)
    tools = await client.get_tools()
    log_client(f"从 Gateway 发现 {len(tools)} 个工具: {[t.name for t in tools]}", trace=trace_id)

    agent = create_react_agent(build_llm(), tools)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": task}]})

    messages = result["messages"]
    _log_trace(messages, trace_id)

    final = messages[-1].content if messages else ""
    log_client(f"完成: {str(final)[:140]}", trace=trace_id)
    return final if isinstance(final, str) else str(final)
