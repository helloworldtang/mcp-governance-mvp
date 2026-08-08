"""Gateway 变体 A（native mount+create_proxy）端到端测试。

镜像 test_gateway_e2e —— 验证两个 Gateway 变体【行为一致】（同样的输入、同样的鉴权结果），
只是内部转发机制不同（声明式 vs 手写）。

关键验证点：ASGI 中间件解析 Authorization → 注入 X-User/X-Role → create_proxy 自动透传到
Runtime → 执行点鉴权。这是「变体 A 承重点」的回归保护。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from gateway.fastmcp_native import AuthInjectMiddleware, _bootstrap, _sync_routes, gw
from registry.server import _catalog
from tests._helpers import call_mcp, serve

GW_URL = "http://127.0.0.1:8200/mcp"


@pytest.fixture(scope="module")
def native_stack(runtimes: None) -> Iterator[None]:
    """runtimes 已起好 Registry+weather+calc；这里 bootstrap native gateway（mount+create_proxy）
    并用 AuthInjectMiddleware 包一层后起服务。"""
    asyncio.run(_bootstrap())  # gateway 拉 /servers，对每个 Runtime mount(create_proxy)
    # 变体 A 唯一手写处：包一层鉴权中间件（解析 Authorization → 注入 X-User/X-Role）
    app = AuthInjectMiddleware(gw.http_app(path="/mcp", stateless_http=True))
    gw_server = serve(app, 8200)
    yield
    gw_server.stop()


def test_native_exposes_namespaced_tools(native_stack: None) -> None:
    """网关应聚合两个 Runtime 的工具，带 namespace 前缀。"""

    async def go() -> list[str]:
        async with Client(StreamableHttpTransport(GW_URL, headers={"Authorization": "Bearer key-alice"})) as c:
            return [t.name for t in await c.list_tools()]

    names = asyncio.run(go())
    assert {"weather_get_forecast", "weather_reset_cache", "calc_add", "calc_multiply"} <= set(names)


def test_native_alice_read_weather_and_calc(native_stack: None) -> None:
    """admin 经网关跨 namespace：weather 读 + calc 算，身份经 create_proxy 透传到 Runtime。"""
    text, raised = call_mcp(GW_URL, "key-alice", "weather_get_forecast", {"city": "上海"})
    assert not raised and "上海" in text
    text, raised = call_mcp(GW_URL, "key-alice", "calc_add", {"a": 2, "b": 3})
    assert not raised and "5" in text


def test_native_bob_read_allowed(native_stack: None) -> None:
    """viewer 读 weather：三层都放行。"""
    text, raised = call_mcp(GW_URL, "key-bob", "weather_get_forecast", {"city": "北京"})
    assert not raised and "北京" in text


def test_native_bob_admin_denied_at_runtime(native_stack: None) -> None:
    """viewer 调 admin 工具：create_proxy 透传身份，Runtime 执行点 DENIED —— 变体 A 承重点回归。"""
    text, raised = call_mcp(GW_URL, "key-bob", "weather_reset_cache", {})
    assert raised and "DENIED" in text


def test_native_alice_admin_allowed(native_stack: None) -> None:
    """admin 调 admin 工具：两层都放行，缓存真被清空。"""
    text, raised = call_mcp(GW_URL, "key-alice", "weather_reset_cache", {})
    assert not raised and "已清空" in text


def test_native_bad_key_rejected_at_gateway(native_stack: None) -> None:
    """非法 key：AuthInjectMiddleware 边缘 401，请求不进 Runtime。"""
    text, raised = call_mcp(GW_URL, "bad-key", "calc_add", {"a": 1, "b": 2})
    assert raised and "401" in text


def test_native_trace_id_propagates_three_layers(native_stack: None, capfd: pytest.CaptureFixture[str]) -> None:
    """【变体 A 承重点回归】X-Trace-Id 经 create_proxy 自动透传：gateway + runtime 日志 + audit 同 trace。

    native 变体只手动注入 X-User/X-Role，X-Trace-Id 靠 create_proxy 透传入站 header。
    这条测试保护那个「自动透传」不被破坏。
    """
    trace = "native-trace-042"
    text, raised = call_mcp(GW_URL, "key-alice", "weather_get_forecast", {"city": "上海"}, trace_id=trace)
    assert not raised and "上海" in text

    lines = capfd.readouterr().err.splitlines()
    gw_hit = any(f"trace:{trace}" in ln and "🚪" in ln for ln in lines)
    rt_hit = any(f"trace:{trace}" in ln and "⚙️" in ln for ln in lines)
    assert gw_hit, f"trace {trace} 未出现在 gateway 日志"
    assert rt_hit, f"trace {trace} 未出现在 runtime 日志"

    from core.config import OUTPUT_DIR

    audit = (OUTPUT_DIR / "audit.jsonl").read_text(encoding="utf-8")
    assert f'"trace": "{trace}"' in audit, f"trace {trace} 未出现在 audit.jsonl"


def test_down_runtime_is_removed_from_native_gateway(native_stack: None) -> None:
    """Registry 标记 Runtime down 后，下一轮同步会摘除其 proxy provider。"""
    _catalog["weather"]["up"] = False
    asyncio.run(_sync_routes())

    async def list_names() -> list[str]:
        async with Client(StreamableHttpTransport(GW_URL, headers={"Authorization": "Bearer key-alice"})) as c:
            return [tool.name for tool in await c.list_tools()]

    assert not any(name.startswith("weather_") for name in asyncio.run(list_names()))
    _catalog["weather"]["up"] = True
    asyncio.run(_sync_routes())
