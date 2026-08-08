"""Gateway 变体 B（explicit 手写转发）端到端测试。

在 runtimes（Registry+weather+calc）之上叠加 explicit gateway，FastMCP client 打网关。
不依赖 DeepSeek。覆盖：跨 namespace 路由、Authorization→X-User/X-Role 透传到 Runtime、
viewer 在执行点被拒、非法 key 401。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from gateway.explicit_proxy import _bootstrap, gw
from tests._helpers import call_mcp, serve

GW_URL = "http://127.0.0.1:8200/mcp"


@pytest.fixture(scope="module")
def gov_stack(runtimes: None) -> Iterator[None]:
    """runtimes 已起好 Registry+weather+calc；这里 bootstrap explicit gateway 并起服务。"""
    asyncio.run(_bootstrap())  # gateway 拉 /servers 建路由（为每个后端工具注册代理 tool）
    gw_server = serve(gw.http_app(path="/mcp", stateless_http=True), 8200)
    yield
    gw_server.stop()


def test_gateway_exposes_namespaced_tools(gov_stack: None) -> None:
    """网关应聚合两个 Runtime 的工具，带 namespace 前缀。"""

    async def go() -> list[str]:
        async with Client(StreamableHttpTransport(GW_URL, headers={"Authorization": "Bearer key-alice"})) as c:
            return [t.name for t in await c.list_tools()]

    names = asyncio.run(go())
    assert {"weather_get_forecast", "weather_reset_cache", "calc_add", "calc_multiply"} <= set(names)


def test_alice_read_weather_and_calc(gov_stack: None) -> None:
    """admin 经网关跨 namespace：weather 读 + calc 算，身份透传到 Runtime。"""
    text, raised = call_mcp(GW_URL, "key-alice", "weather_get_forecast", {"city": "上海"})
    assert not raised and "上海" in text
    text, raised = call_mcp(GW_URL, "key-alice", "calc_add", {"a": 2, "b": 3})
    assert not raised and "5" in text


def test_bob_read_allowed(gov_stack: None) -> None:
    """viewer 读 weather：三层都放行。"""
    text, raised = call_mcp(GW_URL, "key-bob", "weather_get_forecast", {"city": "北京"})
    assert not raised and "北京" in text


def test_bob_admin_denied_at_runtime(gov_stack: None) -> None:
    """viewer 调 admin 工具：网关放行，Runtime 执行点 DENIED —— 三层分离的核心。"""
    text, raised = call_mcp(GW_URL, "key-bob", "weather_reset_cache", {})
    assert raised and "DENIED" in text


def test_alice_admin_allowed(gov_stack: None) -> None:
    """admin 调 admin 工具：两层都放行，缓存真被清空。"""
    text, raised = call_mcp(GW_URL, "key-alice", "weather_reset_cache", {})
    assert not raised and "已清空" in text


def test_bad_key_rejected_at_gateway(gov_stack: None) -> None:
    """非法 key：Gateway 边缘 401，请求不进 Runtime。"""
    text, raised = call_mcp(GW_URL, "bad-key", "calc_add", {"a": 1, "b": 2})
    assert raised and "401" in text
