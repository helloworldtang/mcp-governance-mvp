"""测试共享：真实端口起 uvicorn 服务 + MCP client 调用辅助（不依赖 DeepSeek）。

两个 gateway 测试文件（test_gateway_e2e / test_gateway_native）共用这里的 server 启停与
调用辅助，避免重复。Gateway 自身的 bootstrap/中间件差异留在各自的 fixture 里。
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import uvicorn
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport


def wait_port(port: int, timeout: float = 15.0) -> bool:
    """TCP 探活：能连上即服务就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class Server:
    """在线程里跑 uvicorn；stop() 设 should_exit 优雅退出。"""

    def __init__(self, app: object, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")  # type: ignore[arg-type]
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not wait_port(port):
            raise RuntimeError(f"服务在 :{port} 没起来")

    def _run(self) -> None:
        asyncio.run(self.server.serve())

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def serve(app: object, port: int) -> Server:
    """起一个 ASGI 服务到指定端口，返回 Server（用完调 .stop()）。"""
    return Server(app, port)


def register_runtime(
    registry_base: str,
    name: str,
    namespace: str,
    url: str,
    health_url: str,
    tags: list[str],
    capabilities: list[str] | None = None,
) -> None:
    """把一个 Runtime 元数据 POST 到 Registry（模拟 runtime __main__ 的自注册）。"""
    with httpx.Client(timeout=5) as c:
        c.post(
            f"{registry_base}/register",
            json={
                "name": name,
                "namespace": namespace,
                "url": url,
                "health_url": health_url,
                "tags": tags,
                "capabilities": capabilities or ["tools"],
            },
        )


def call_mcp(url: str, api_key: str, tool: str, args: dict[str, object]) -> tuple[str, bool]:
    """以某 key 经 MCP 端点调一个 tool，返回 (文本, 是否抛异常)。"""

    async def go() -> tuple[str, bool]:
        transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {api_key}"})
        # try 包住整个 async 块：native 变体的 401 在 Client 初始化阶段（__aenter__）就抛
        # HTTPStatusError，必须连 `async with` 一起捕获，否则逸出。
        try:
            async with Client(transport) as c:
                r = await c.call_tool(tool, args)
                return (r.content[0].text if r.content else str(r), False)
        except Exception as e:  # ToolError / HTTPStatusError(401) 等
            return (str(e), True)

    return asyncio.run(go())
