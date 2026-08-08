"""Runtime: weather —— 执行层（FastMCP streamable-http, :8300）。

两个工具，体现 per-user 鉴权差异：
  get_forecast  tag=read    任何身份可调
  reset_cache   tag=admin   仅 admin；viewer 在执行点被 require_admin() 拒

启动时把自身元数据 POST 到 Registry（自注册），Gateway 启动后从 Registry 拉它建路由。
"""

import httpx
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.audit import audit
from core.config import REGISTRY_URL, RUNTIME_WEATHER_PORT
from core.log_util import log_runtime
from runtime.authz import current_identity

NAME = "weather"
URL = f"http://127.0.0.1:{RUNTIME_WEATHER_PORT}/mcp"
HEALTH_URL = f"http://127.0.0.1:{RUNTIME_WEATHER_PORT}/health"

# 假装这是从上游抓来的缓存
_cache: dict[str, str] = {
    "shanghai": "上海 晴 28℃",
    "上海": "上海 晴 28℃",
    "beijing": "北京 多云 25℃",
    "北京": "北京 多云 25℃",
    "hangzhou": "杭州 小雨 22℃",
    "杭州": "杭州 小雨 22℃",
}

mcp = FastMCP(NAME)


@mcp.custom_route("/health", methods=["GET"])
async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": NAME})


@mcp.tool(tags={"read"})
async def get_forecast(city: str) -> str:
    """查询指定城市的天气预报。city 可传拼音或中文，如 shanghai / 上海。"""
    user, role, trace = current_identity()
    key = city.lower()
    result = _cache.get(key) or _cache.get(city, f"暂无 {city} 的天气数据")
    log_runtime(NAME, f"get_forecast(city={city}) ← {user}({role}) → {result}", "✔", trace=trace)
    audit(trace, user, role, "weather_get_forecast", "allow", f"city={city} → {result}")
    return result


@mcp.tool(tags={"admin"})
async def reset_cache() -> str:
    """清空天气缓存。仅 admin 角色可调用；viewer 会在 Runtime 执行点被拒。"""
    user, role, trace = current_identity()
    if role != "admin":
        # 执行点 deny：审计 + 抛 ToolError（→ 经 Gateway 回传 Client）
        log_runtime(NAME, f"reset_cache() ← {user}({role}) → DENIED", "🔒", "✘", trace=trace)
        audit(trace, user, role, "weather_reset_cache", "deny", f"role={role} 非 admin")
        from fastmcp.exceptions import ToolError

        raise ToolError(f"DENIED: 用户 {user}（role={role}）无 admin 权限，该工具仅在 Runtime 执行点对 admin 开放")
    n = len(_cache)
    _cache.clear()
    log_runtime(NAME, f"reset_cache() ← {user}({role}) → 已清空 {n} 条", "✔", trace=trace)
    audit(trace, user, role, "weather_reset_cache", "allow", f"cleared {n}")
    return f"已清空 {n} 条天气缓存"


def _self_register() -> None:
    payload = {
        "name": NAME,
        "namespace": NAME,
        "url": URL,
        "health_url": HEALTH_URL,
        "tags": ["read", "admin"],
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
    # stateless_http=True：对齐 2026-07-28 无状态协议，无需 Mcp-Session-Id，
    # 也让 Gateway 反代时不必维护会话（每请求独立）。
    app = mcp.http_app(path="/mcp", stateless_http=True)
    log_runtime(NAME, f"启动 @ :{RUNTIME_WEATHER_PORT}  端点 streamable-http /mcp (stateless)")
    uvicorn.run(app, host="127.0.0.1", port=RUNTIME_WEATHER_PORT, log_level="warning")
