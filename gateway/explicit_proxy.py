"""Gateway 变体 B —— 显式命令式转发（FastMCP 边缘 server + 手写转发）。

【Java/C 读者速查】本文件用到的 Python 特性：
  - 装饰器 @gw.custom_route(...)：类似 Java 注解，把下面的函数注册成「处理 GET /health 的回调」。
  - async def / await：协程。await = 等一个异步结果但不阻塞线程（类似 Java CompletableFuture / C 协程）。
  - async with X as c:：异步的 try-with-resources，离开块自动调清理（关闭连接）。
  - dict[str, str] ≈ Java Map<String,String>；tuple[str,str,str] ≈ 固定长度的三元组（类似 record）。
  - f"...{var}..."：格式化字符串，类似 C 的 printf / Java 的 String.format。
  - **kwargs：收集任意「关键字参数」成一个 dict（类似 Java 的 Map<String,Object> 形参）。
  - __xxx__（双下划线 dunder）：Python 对象的内部属性，如 __signature__、__annotations__。
  - asyncio.run(coro)：从同步代码启动一个异步函数的事件循环。
  - if __name__ == "__main__": ≈ Java 的 public static void main —— 直接运行才执行，被 import 时不执行。

和变体 A 的对比轴：**转发机制是声明式还是手写**。
  - A: gw.mount(create_proxy(url), namespace=...) —— 一行/后端，转发是 create_proxy 的黑盒。
  - B: 启动时拉 Registry → 为每个后端工具【按其 inputSchema 重建签名】注册一个本地代理 tool；
        每次调用走 _forward()：解析 Authorization → 连后端（带 X-User/X-Role）→ call_tool → 回传。
        转发的每一跳都写在 _forward() 里，和 Kong / Microsoft mcp-gateway 的真实形态一一对应。

两者对 Client 完全一致；区别只在 Gateway 内部实现。
"""

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
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

from core.config import (
    API_KEYS,
    GATEWAY_PORT,
    GATEWAY_REFRESH_INTERVAL,
    GATEWAY_RUNTIME_TOKEN,
    REGISTRY_URL,
)
from core.log_util import log_gateway


@asynccontextmanager
async def _lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    """启动路由刷新任务，并在 Gateway 退出时取消。"""
    task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# 构造一个 MCP server 实例（≈ new 一个服务对象）；它对外暴露工具，对内连后端
gw = FastMCP("gateway-explicit", lifespan=_lifespan)

# namespace → 健康后端 MCP url（按 Registry 快照刷新）。dict ≈ Java HashMap，模块级变量 ≈ static 字段
_backends: dict[str, str] = {}
_registered_tools: dict[str, set[str]] = {}
_route_lock = asyncio.Lock()

# JSON Schema type → Python type（重建代理 tool 签名用）。常量全大写 ≈ Java static final
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
    # get_http_request() 返回当前请求对象（由 FastMCP 用 contextvar 注入，类似线程上下文）
    req = get_http_request()
    if req is None:
        # raise ≈ Java throw；ToolError 是 MCP 协议的错误类型，会被框架转成错误响应回客户端
        raise ToolError("401 Unauthorized: 无 HTTP 请求上下文")
    # 推导式：把请求头拷成全小写 key 的 dict（HTTP 头大小写不敏感，统一小写便于取值）
    h = {k.lower(): v for k, v in req.headers.items()}
    # 去掉 "Bearer " 前缀，取出真正的 key 字符串
    key = h.get("authorization", "").removeprefix("Bearer ").strip()
    ident = API_KEYS.get(key)  # 查 key→{user,role} 映射；不存在返回 None
    if not ident:
        raise ToolError(f"401 Unauthorized: 未知 API key '{key[:12] or '<空>'}...'")
    trace = h.get("x-trace-id", "-")
    # tuple 多返回值（Java 要用类/record 包装；Python 直接返回元组，调用方解包）
    return ident["user"], ident["role"], trace


async def _forward(namespace: str, tool_name: str, arguments: dict[str, Any]) -> str:
    """【显式转发 —— 这就是变体 A 里 create_proxy 替你做的事，这里逐行写出来】

    1. 解析入站身份 + trace（Gateway 层的粗鉴权）
    2. 用 X-User/X-Role/X-Trace-Id 连后端（把身份带到 Runtime 执行点）
    3. 调原始工具，回传文本

    ⚠️ 为何每次都新建 Client、不复用连接？—— 抽象错配，不是疏忽。
    FastMCP 的 Client 语义是「一 Client = 一 MCP session = 一份身份」，headers 在建 session
    时固化（见 fastmcp/client/transports/http.py 的 connect_session：headers = dict(self.headers)）。
    本项目身份随每个请求走 header（per-user 鉴权是核心卖点），复用 Client = 第二个请求套用第一个的
    身份 = 串号 → 直接摧毁 bob/alice 隔离。业界（微软 mcp-gateway 等）走反向代理透传：连接池不绑身份
    + per-request header，两者正交，无此矛盾。变体 A 的 create_proxy 就是反向代理抽象（ProxyProvider
    流透传 + scope 注入身份），故无此死结。详见 README「为什么变体 B 不能复用上游连接」。
    """
    user, role, trace = _resolve_identity()  # 解包三元组
    url = _backends[namespace]
    log_gateway(f"路由 {namespace}_{tool_name} → {url}   身份 {user}({role})", trace=trace)
    # 构造到后端的 MCP 客户端 transport（指定 url + 随请求带的自定义头）
    transport = StreamableHttpTransport(
        url,
        headers={
            "X-User": user,
            "X-Role": role,
            "X-Trace-Id": trace,
            "X-Gateway-Token": GATEWAY_RUNTIME_TOKEN,
        },
    )
    async with Client(transport) as bc:  # 建立连接；离开 with 自动断开（≈ try-with-resources）
        result = await bc.call_tool(tool_name, arguments)  # 远程调用后端工具，await 等结果
    # result.content 是返回内容列表（MCP 工具可返回多段）；这里取第一段的文本
    text = result.content[0].text if result.content else str(result)
    # hasattr ≈ Java 反射 field.exists()；判断后端是否返回了错误（如 viewer 被拒）
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
    props: dict[str, Any] = schema.get("properties", {})  # 工具参数定义
    required = set(schema.get("required", []))  # 必填参数名集合（set ≈ Java HashSet）
    params: list[inspect.Parameter] = []  # 收集 inspect.Parameter（签名里的一个参数）
    annotations: dict[str, type] = {}  # 参数名 → Python 类型（pydantic 据此推导 schema）
    for pname, pdef in props.items():  # 遍历每个参数定义
        ptype = _TYPE_MAP.get(pdef.get("type", "string"), str)  # JSON Schema type → Python type
        annotations[pname] = ptype
        if pname in required:
            params.append(inspect.Parameter(pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ptype))
        else:
            # 可选参数带默认值（pdef.get("default") 可能返回 None）
            params.append(
                inspect.Parameter(
                    pname,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=ptype,
                    default=pdef.get("default"),
                )
            )

    # 闭包：_proxy 捕获外层的 namespace/bt_name，被调用时转发到 _forward
    async def _proxy(**kwargs: Any) -> str:
        # **kwargs 把调用方传的参数收成 dict，原样转给后端
        return await _forward(namespace, bt_name, kwargs)

    # 关键魔法：手动给 _proxy 装上「签名」和「类型注解」，让 FastMCP 把它当成
    # 一个带 city:str 这类参数的工具暴露给客户端（否则 FastMCP 看不到参数）。
    _proxy.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    _proxy.__annotations__ = annotations
    # 用 FastMCP 的 FunctionTool.from_function 把它包装成正式工具对象（指定对外的名字/描述）
    return FunctionTool.from_function(_proxy, name=f"{namespace}_{bt_name}", description=description)


# 装饰器：注册一个【非 MCP 的普通 HTTP 路由】/health（运维探活用，不走 MCP 协议）
@gw.custom_route("/health", methods=["GET"])
async def _health(_request: Request) -> JSONResponse:
    """存活探针。"""
    return JSONResponse({"status": "ok", "variant": "explicit", "backends": list(_backends)})


def _remove_namespace(namespace: str) -> None:
    """移除某 Runtime 对应的全部本地代理工具。"""
    for tool_name in _registered_tools.pop(namespace, set()):
        gw.local_provider.remove_tool(tool_name)
    _backends.pop(namespace, None)


async def _sync_routes() -> None:
    """按 Registry 当前健康快照增删路由。"""
    async with httpx.AsyncClient(timeout=5) as c:  # 异步 HTTP 客户端（≈ Java AsyncHttpClient）
        r = await c.get(f"{REGISTRY_URL}/servers")
        r.raise_for_status()
        servers = r.json().get("servers", [])  # 解析 JSON 响应；.get(key, 默认值) 防 KeyError
    desired = {str(server["namespace"]): str(server["url"]) for server in servers}

    async with _route_lock:
        for namespace in set(_backends) - set(desired):
            _remove_namespace(namespace)
            log_gateway(f"摘除 down Runtime → {namespace}", "✘")

        for namespace, url in desired.items():
            if _backends.get(namespace) == url:
                continue
            if namespace in _backends:
                _remove_namespace(namespace)
            async with Client(StreamableHttpTransport(url)) as backend_client:
                tools = await backend_client.list_tools()
            names: set[str] = set()
            for tool in tools:
                proxy = _build_proxy_tool(namespace, tool.name, tool.inputSchema, tool.description or tool.name)
                gw.add_tool(proxy)
                names.add(proxy.name)
            _backends[namespace] = url
            _registered_tools[namespace] = names
            log_gateway(f"注册 {len(tools)} 个代理 tool ← {namespace}({url})", "📖")


async def _refresh_loop() -> None:
    """周期同步 Registry；短暂控制面故障时保留上一份可用路由。"""
    while True:
        try:
            await _sync_routes()
        except Exception as exc:
            log_gateway(f"刷新 Registry 失败，保留现有路由: {exc}", "✘")
        await asyncio.sleep(GATEWAY_REFRESH_INTERVAL)


async def _bootstrap() -> None:
    """启动时完成首次路由同步。"""
    await _sync_routes()


if __name__ == "__main__":
    # 直接 `python -m gateway.explicit_proxy` 运行时才执行（被 import 时不执行）
    import uvicorn

    asyncio.run(_bootstrap())  # 先把后端工具拉进来注册
    app = gw.http_app(path="/mcp", stateless_http=True)  # 把 MCP server 包成 ASGI 应用（stateless=无状态）
    log_gateway(f"显式网关就绪 @ :{GATEWAY_PORT}  端点 /mcp  (stateless)")
    # uvicorn 是 ASGI 服务器（≈ Java 的内嵌 Tomcat），监听端口跑 app
    uvicorn.run(app, host="127.0.0.1", port=GATEWAY_PORT, log_level="warning")
