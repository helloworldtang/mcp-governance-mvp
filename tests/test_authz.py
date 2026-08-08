"""Runtime 执行点鉴权单元测试 —— monkeypatch get_http_request，验证决策逻辑。

不依赖 HTTP：current_identity / require_admin 是纯函数（读 req.headers）。
覆盖：admin 放行、viewer 在执行点被拒、无请求上下文回退 anonymous。
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from core.config import GATEWAY_RUNTIME_TOKEN
from runtime import authz


class _FakeReq:
    """最小化的 Starlette Request 替身：只要 .headers（dict）。"""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_admin_identity_and_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """admin 身份被正确读出，require_admin 放行。"""
    monkeypatch.setattr(
        authz,
        "get_http_request",
        lambda: _FakeReq(
            {
                "X-User": "alice",
                "X-Role": "admin",
                "X-Trace-Id": "t1",
                "X-Gateway-Token": GATEWAY_RUNTIME_TOKEN,
            }
        ),
    )
    assert authz.current_identity() == ("alice", "admin", "t1")
    assert authz.require_admin() == ("alice", "admin", "t1")  # 不抛即放行


def test_viewer_denied_at_execution_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """viewer 调 admin 工具：require_admin 在执行点抛 ToolError —— 三层分离的价值。"""
    monkeypatch.setattr(
        authz,
        "get_http_request",
        lambda: _FakeReq({"X-User": "bob", "X-Role": "viewer", "X-Gateway-Token": GATEWAY_RUNTIME_TOKEN}),
    )
    assert authz.current_identity() == ("bob", "viewer", "-")  # trace 缺省 -
    with pytest.raises(ToolError, match="DENIED"):
        authz.require_admin()


def test_anonymous_when_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 HTTP 上下文（如直连/内存测试）→ 回退 anonymous/viewer，admin 门禁必拒。"""
    monkeypatch.setattr(authz, "get_http_request", lambda: None)
    assert authz.current_identity() == ("anonymous", "viewer", "-")
    with pytest.raises(ToolError):
        authz.require_admin()


def test_header_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """大小写不敏感：X-User 与 x-user 等价（Gateway 注入与 Runtime 读取的契约）。"""
    monkeypatch.setattr(
        authz,
        "get_http_request",
        lambda: _FakeReq({"x-user": "alice", "x-role": "admin", "x-gateway-token": GATEWAY_RUNTIME_TOKEN}),
    )
    assert authz.current_identity() == ("alice", "admin", "-")


def test_forged_identity_without_gateway_token_is_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """直连 Runtime 伪造 admin 头时，没有网关凭证仍只能得到 viewer。"""
    monkeypatch.setattr(
        authz,
        "get_http_request",
        lambda: _FakeReq({"X-User": "mallory", "X-Role": "admin", "X-Trace-Id": "forged"}),
    )
    assert authz.current_identity() == ("anonymous", "viewer", "forged")
    with pytest.raises(ToolError, match="DENIED"):
        authz.require_admin()
