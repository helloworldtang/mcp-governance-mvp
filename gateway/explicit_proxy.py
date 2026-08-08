"""Gateway 变体 B —— 显式命令式转发（FastMCP 边缘 server + 手写转发）。

和变体 A 的对比轴：**转发机制是声明式还是手写**。
  - A: gw.mount(create_proxy(url), namespace=...) —— 一行/后端，转发是 create_proxy 的黑盒。
  - B: 启动时拉 Registry → 为每个后端工具【按其 inputSchema 重建签名】注册一个本地代理 tool；
        每次调用走 _forward()：解析 Authorization → 连后端（带 X-User/X-Role）→ call_tool → 回传。
        转发的每一跳都写在 _forward() 里，和 Kong / Microsoft mcp-gateway 的真实形态一一对应。

两者对 Client 完全一致；区别只在 Gateway 内部实现。
"""

import asyncio
import inspect
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import FunctionTool
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import API_KEYS, GATEWAY_PORT, REGISTRY_URL
from core.log_util import log_gateway

gw = FastMCP("gateway-explicit")

# namespace → 后端 MCP url（启动时从 Registry 拉）
_backends: dict[str, str] = {}

# JSON Schema type → Python type（重建代理 tool 签名用）
_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _resolve_identity() -> tuple[str, str, str]:
    """从入站请求头解析出 (user, role, trace)。非法 key → 抛 ToolError（→ 回传 Client）。

    ⚠️ 避坑：FastMCP 的 get_http_headers() 会【刻意剔除 Authorization 等标准头】，
    鉴权头必须走 get_http_request().headers 拿原始请求头。
    """
    req = get_http_request()
    if req is None:
        raise ToolError("401 Unauthorized: 无 HTTP 请求上下文")
    h = {k.lower(): v for k, v in req.headers.items()}
    key = h.get("authorization", "").removeprefix("Bearer ").strip()
    ident = API_KEYS.get(key)
    if not ident:
        raise ToolError(f"401 Unauthorized: 未知 API key '{key[:12] or '<空>'}...'")
    trace = h.get("x-trace-id", "-")
    return ident["user"], ident["role"], trace


async def _forward(namespace: str, tool_name: str, arguments: dict[str, Any]) -> str:
    """【显式转发 —— 这就是变体 A 里 create_proxy 替你做的事，这里逐行写出来】

    1. 解析入站身份 + trace（Gateway 层的粗鉴权）
    2. 用 X-User/X-Role/X-Trace-Id 连后端（把身份带到 Runtime 执行点）
    3. 调原始工具，回传文本
    """
    user, role, trace = _resolve_identity()
    url = _backends[namespace]
    log_gateway(f"路由 {namespace}_{tool_name} → {url}   身份 {user}({role})", trace=trace)
    transport = StreamableHttpTransport(url, headers={"X-User": user, "X-Role": role, "X-Trace-Id": trace})
    async with Client(transport) as bc:
        result = await bc.call_tool(tool_name, arguments)
    text = result.content[0].text if result.content else str(result)
    denied = result.is_error if hasattr(result, "is_error") else False
    log_gateway(
        f"← {namespace}_{tool_name}   {user}({role})   {'✘ DENIED' if denied else '✔'}",
        trace=trace,
    )
    return text


def _build_proxy_tool(namespace: str, bt_name: str, schema: dict[str, Any], description: str) -> FunctionTool:
    """按后端工具的 inputSchema 重建签名，造一个本地代理 FunctionTool。

    FastMCP 用 get_type_hints + inspect.signature 推导 schema，所以两边都要注入。
    这一步是「通用网关」的必经之路：你拿到的是别处的 schema，得还原成可调用的签名。
    """
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    params: list[inspect.Parameter] = []
    annotations: dict[str, type] = {}
    for pname, pdef in props.items():
        ptype = _TYPE_MAP.get(pdef.get("type", "string"), str)
        annotations[pname] = ptype
        if pname in required:
            params.append(inspect.Parameter(pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ptype))
        else:
            params.append(
                inspect.Parameter(
                    pname,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=ptype,
                    default=pdef.get("default"),
                )
            )

    async def _proxy(**kwargs: Any) -> str:
        return await _forward(namespace, bt_name, kwargs)

    _proxy.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    _proxy.__annotations__ = annotations
    return FunctionTool.from_function(_proxy, name=f"{namespace}_{bt_name}", description=description)


@gw.custom_route("/health", methods=["GET"])
async def _health(_request: Request) -> JSONResponse:
    """存活探针。"""
    return JSONResponse({"status": "ok", "variant": "explicit", "backends": list(_backends)})


async def _bootstrap() -> None:
    """启动时从 Registry 拉清单，为每个后端工具注册代理 tool。"""
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{REGISTRY_URL}/servers")
        servers = r.json().get("servers", [])
    if not servers:
        log_gateway("Registry 里还没有 up 的 Runtime（先起 Runtime 再起 Gateway）", "✘")
    for srv in servers:
        ns, url = srv["namespace"], srv["url"]
        _backends[ns] = url
        async with Client(StreamableHttpTransport(url)) as bc:
            tools = await bc.list_tools()
        for t in tools:
            gw.add_tool(_build_proxy_tool(ns, t.name, t.inputSchema, t.description or t.name))
        log_gateway(f"注册 {len(tools)} 个代理 tool ← {ns}({url})", "📖")


if __name__ == "__main__":
    import uvicorn

    asyncio.run(_bootstrap())
    app = gw.http_app(path="/mcp", stateless_http=True)
    log_gateway(f"显式网关就绪 @ :{GATEWAY_PORT}  端点 /mcp  (stateless)")
    uvicorn.run(app, host="127.0.0.1", port=GATEWAY_PORT, log_level="warning")
