"""Runtime 执行点鉴权 —— 从本次请求的 HTTP header 读出 Gateway 注入的 X-User/X-Role/X-Trace-Id。

这是 Runtime 层（区别于 Gateway 层）的核心卖点：在【执行点】做 per-user 细粒度判定。
Gateway 只做了「这个 key 合法吗」的粗鉴权；这里回答「这个用户能调这个工具吗」。

机制依赖 2026-07-28 无状态协议：身份 + trace 随每个请求走 header，FastMCP 的
get_http_request() 能在 tool 内读到原始请求头（HTTP transport 下，与 MCP session 是否建立无关）。
"""

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request


def current_identity() -> tuple[str, str, str]:
    """返回 (user, role, trace)。读不到（直连、或 Gateway 未注入）时回退 anonymous/viewer。"""
    req = get_http_request()
    if req is None:
        return "anonymous", "viewer", "-"
    h = {k.lower(): v for k, v in req.headers.items()}
    user = h.get("x-user", "anonymous")
    role = h.get("x-role", "viewer")
    trace = h.get("x-trace-id", "-")
    return user, role, trace


def require_admin() -> tuple[str, str, str]:
    """admin 门禁：非 admin 在执行点直接抛 ToolError（→ 经 Gateway 回传给 Client）。"""
    user, role, trace = current_identity()
    if role != "admin":
        raise ToolError(f"DENIED: 用户 {user}（role={role}）无 admin 权限，该工具仅在 Runtime 执行点对 admin 开放")
    return user, role, trace
