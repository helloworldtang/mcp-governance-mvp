"""Runtime: calc —— 执行层（FastMCP streamable-http, :8301）。

只有 read 工具：用来演示「同是 read 标签、不同 namespace」，Gateway 按 namespace 路由。
"""

import httpx
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.audit import audit
from core.config import REGISTRY_URL, RUNTIME_CALC_PORT
from core.log_util import log_runtime
from runtime.authz import current_identity

NAME = "calc"
URL = f"http://127.0.0.1:{RUNTIME_CALC_PORT}/mcp"
HEALTH_URL = f"http://127.0.0.1:{RUNTIME_CALC_PORT}/health"

mcp = FastMCP(NAME)


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": NAME})


@mcp.tool(tags={"read"})
async def add(a: float, b: float) -> str:
    """两数相加。"""
    user, role, trace = current_identity()
    r = a + b
    log_runtime(NAME, f"add(a={a}, b={b}) ← {user}({role}) → {r}", "✔", trace=trace)
    audit(trace, user, role, "calc_add", "allow", f"{a}+{b}={r}")
    return f"{a} + {b} = {r}"


@mcp.tool(tags={"read"})
async def multiply(a: float, b: float) -> str:
    """两数相乘。"""
    user, role, trace = current_identity()
    r = a * b
    log_runtime(NAME, f"multiply(a={a}, b={b}) ← {user}({role}) → {r}", "✔", trace=trace)
    audit(trace, user, role, "calc_multiply", "allow", f"{a}×{b}={r}")
    return f"{a} × {b} = {r}"


def _self_register() -> None:
    payload = {
        "name": NAME,
        "namespace": NAME,
        "url": URL,
        "health_url": HEALTH_URL,
        "tags": ["read"],
        "capabilities": ["tools"],
    }
    try:
        r = httpx.post(f"{REGISTRY_URL}/register", json=payload, timeout=5)
        log_runtime(NAME, f"自注册到 Registry (HTTP {r.status_code}) url={URL}", "📖")
    except Exception as e:
        log_runtime(NAME, f"自注册失败（Registry 起了吗？）: {e}", "✘")


if __name__ == "__main__":
    import uvicorn

    _self_register()
    app = mcp.http_app(path="/mcp", stateless_http=True)
    log_runtime(NAME, f"启动 @ :{RUNTIME_CALC_PORT}  端点 streamable-http /mcp (stateless)")
    uvicorn.run(app, host="127.0.0.1", port=RUNTIME_CALC_PORT, log_level="warning")
