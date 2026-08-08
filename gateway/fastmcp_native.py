"""Gateway 变体 A —— FastMCP 原生 mount(create_proxy)（声明式 / 一行一后端）。

和变体 B 的对比轴：**转发机制是声明式还是手写**。
  - A: 每个 Runtime 一行 `gw.mount(create_proxy(url), namespace=ns)`。FastMCP 的 ProxyProvider
        自动 list 后端工具、按 namespace 暴露（weather_get_forecast 这种前缀）、转发调用，
        并【把入站 HTTP header 透传给后端】（承重点已验证：client 的 X-User 到了 weather）。
  - B: 手写 _forward() 逐跳转发；schema 还得自己重建。

鉴权是变体 A 唯一「手写」的部分：一层 ASGI 中间件解析 Authorization → 注入 X-User/X-Role
到入站 scope，create_proxy 随后透传给 Runtime（Runtime 用 get_http_headers 读到）。
"""

import asyncio

import httpx
from fastmcp import FastMCP
from fastmcp.server import create_proxy
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.config import API_KEYS, GATEWAY_PORT, REGISTRY_URL
from core.log_util import log_gateway

gw = FastMCP("gateway-native")


class AuthInjectMiddleware:
    """ASGI 中间件：Gateway 层粗鉴权 + 身份注入。

    - 已知 key：把 X-User/X-Role 写入入站 scope headers，create_proxy 会透传到 Runtime；
    - 未知 key：直接回 401，请求不进入 MCP 处理（Gateway 边缘拒绝）。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            # /health 等运维端点不鉴权（否则探活/负载均衡会被挡在外面）
            if scope.get("path") in ("/health", "/"):
                return await self.app(scope, receive, send)
            hd = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            key = hd.get("authorization", "").removeprefix("Bearer ").strip()
            ident = API_KEYS.get(key)
            if not ident:
                await self._reject_401(send, key)
                return
            trace = hd.get("x-trace-id", "-")
            # 注入身份；create_proxy 会把这些 header（含 X-Trace-Id）随请求透传给后端 Runtime
            scope["headers"] = list(scope.get("headers", [])) + [
                (b"x-user", ident["user"].encode()),
                (b"x-role", ident["role"].encode()),
            ]
            log_gateway(
                f"鉴权 {ident['user']}({ident['role']}) → 注入身份（trace 随请求透传）",
                trace=trace,
            )
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject_401(send: Send, key: str) -> None:
        body = b'{"jsonrpc":"2.0","id":null,"error":{"code":-32001,"message":"401 Unauthorized: unknown API key"}}'
        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
        log_gateway(f"401 拒绝未知 key '{key[:12] or '<空>'}'", "🔒", "✘")


async def _bootstrap() -> None:
    """启动时拉 Registry，对每个 Runtime 一行 mount(create_proxy)。"""
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{REGISTRY_URL}/servers")
        servers = r.json().get("servers", [])
    for srv in servers:
        ns, url = srv["namespace"], srv["url"]
        gw.mount(create_proxy(url), namespace=ns)  # 声明式：一行挂一个后端
        log_gateway(f"mount(create_proxy({url}), namespace={ns})", "📖")


@gw.custom_route("/health", methods=["GET"])
async def _health(_req: Request) -> JSONResponse:
    """存活探针（不经鉴权 middleware 放行）。"""
    return JSONResponse({"status": "ok", "variant": "native"})


if __name__ == "__main__":
    import uvicorn

    asyncio.run(_bootstrap())
    app: ASGIApp = gw.http_app(path="/mcp", stateless_http=True)
    app = AuthInjectMiddleware(app)  # 包一层鉴权 —— 变体 A 唯一手写处
    log_gateway(f"原生网关就绪 @ :{GATEWAY_PORT}  端点 /mcp  (stateless)")
    uvicorn.run(app, host="127.0.0.1", port=GATEWAY_PORT, log_level="warning")
