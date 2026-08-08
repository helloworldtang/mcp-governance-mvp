"""Gateway 端到端测试 —— 起真实 4 服务（Registry+weather+calc+gateway-explicit），FastMCP client 打网关。

不依赖 DeepSeek：直接用 FastMCP client 带 Authorization 头调网关代理 tool，
覆盖：跨 namespace 路由、Authorization→X-User/X-Role 透传到 Runtime、viewer 在执行点被拒、非法 key 401。
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from gateway.explicit_proxy import _bootstrap, gw
from registry.server import _catalog
from registry.server import app as registry_app
from runtime.calc import mcp as calc_mcp
from runtime.weather import _cache
from runtime.weather import mcp as weather_mcp

GW_URL = "http://127.0.0.1:8200/mcp"
_ORIG_CACHE = dict(_cache)  # 进程级备份，测试改了缓存后还原


def _wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class _Server:
    """在线程里跑 uvicorn；stop() 设 should_exit 优雅退出。"""

    def __init__(self, app: object, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")  # type: ignore[arg-type]
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not _wait_port(port):
            raise RuntimeError(f"服务在 :{port} 没起来")

    def _run(self) -> None:
        asyncio.run(self.server.serve())

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _serve(app: object, port: int) -> _Server:
    return _Server(app, port)


@pytest.fixture(scope="module")
def gov_stack() -> Iterator[None]:
    """起 Registry + weather + calc，注册到 Registry，bootstrap gateway，起 gateway。"""
    _catalog.clear()
    servers = [
        _serve(registry_app, 8100),
        _serve(weather_mcp.http_app(path="/mcp", stateless_http=True), 8300),
        _serve(calc_mcp.http_app(path="/mcp", stateless_http=True), 8301),
    ]
    # 注册 weather/calc（模拟 runtime __main__ 里的 _self_register）
    with httpx.Client(timeout=5) as c:
        c.post(
            "http://127.0.0.1:8100/register",
            json={
                "name": "weather",
                "namespace": "weather",
                "url": "http://127.0.0.1:8300/mcp",
                "health_url": "http://127.0.0.1:8300/health",
                "tags": ["read", "admin"],
                "capabilities": ["tools"],
            },
        )
        c.post(
            "http://127.0.0.1:8100/register",
            json={
                "name": "calc",
                "namespace": "calc",
                "url": "http://127.0.0.1:8301/mcp",
                "health_url": "http://127.0.0.1:8301/health",
                "tags": ["read"],
                "capabilities": ["tools"],
            },
        )
    asyncio.run(_bootstrap())  # gateway 拉 /servers 建路由（注册代理 tool）
    servers.append(_serve(gw.http_app(path="/mcp", stateless_http=True), 8200))
    yield
    for s in servers:
        s.stop()
    _cache.clear()
    _cache.update(_ORIG_CACHE)  # 还原缓存，不污染同进程其它测试


def _call(api_key: str, tool: str, args: dict[str, object]) -> tuple[str, bool]:
    """以某 key 经网关调一个 tool，返回 (文本, 是否抛异常)。"""

    async def go() -> tuple[str, bool]:
        transport = StreamableHttpTransport(GW_URL, headers={"Authorization": f"Bearer {api_key}"})
        async with Client(transport) as c:
            try:
                r = await c.call_tool(tool, args)
                return (r.content[0].text if r.content else str(r), False)
            except Exception as e:  # ToolError / 401 等
                return (str(e), True)

    return asyncio.run(go())


def test_gateway_exposes_namespaced_tools(gov_stack: None) -> None:
    """网关应聚合两个 Runtime 的工具，带 namespace 前缀。"""

    async def go() -> list[str]:
        async with Client(StreamableHttpTransport(GW_URL, headers={"Authorization": "Bearer key-alice"})) as c:
            return [t.name for t in await c.list_tools()]

    names = asyncio.run(go())
    assert {"weather_get_forecast", "weather_reset_cache", "calc_add", "calc_multiply"} <= set(names)


def test_alice_read_weather_and_calc(gov_stack: None) -> None:
    """admin 经网关跨 namespace：weather 读 + calc 算，身份透传到 Runtime。"""
    text, raised = _call("key-alice", "weather_get_forecast", {"city": "上海"})
    assert not raised and "上海" in text
    text, raised = _call("key-alice", "calc_add", {"a": 2, "b": 3})
    assert not raised and "5" in text


def test_bob_read_allowed(gov_stack: None) -> None:
    """viewer 读 weather：三层都放行。"""
    text, raised = _call("key-bob", "weather_get_forecast", {"city": "北京"})
    assert not raised and "北京" in text


def test_bob_admin_denied_at_runtime(gov_stack: None) -> None:
    """viewer 调 admin 工具：网关放行，Runtime 执行点 DENIED —— 三层分离的核心。"""
    text, raised = _call("key-bob", "weather_reset_cache", {})
    assert raised and "DENIED" in text


def test_alice_admin_allowed(gov_stack: None) -> None:
    """admin 调 admin 工具：两层都放行，缓存真被清空。"""
    text, raised = _call("key-alice", "weather_reset_cache", {})
    assert not raised and "已清空" in text


def test_bad_key_rejected_at_gateway(gov_stack: None) -> None:
    """非法 key：Gateway 边缘 401，请求不进 Runtime。"""
    text, raised = _call("bad-key", "calc_add", {"a": 1, "b": 2})
    assert raised and "401" in text
