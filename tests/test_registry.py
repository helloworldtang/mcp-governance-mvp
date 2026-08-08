"""Registry 发现层测试 —— FastAPI TestClient（in-process，不起端口）。

覆盖：存活探针、Runtime 自荐注册、Gateway 拉 /servers、下线注销。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from registry.server import _catalog, app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """每个测试前清空内存目录，避免模块级 _catalog 跨测试串味。"""
    _catalog.clear()
    with TestClient(app) as c:  # 触发 lifespan（启心跳后台任务）
        yield c


def _register(client: TestClient, name: str, ns: str, port: int) -> Any:
    return client.post(
        "/register",
        json={
            "name": name,
            "namespace": ns,
            "url": f"http://127.0.0.1:{port}/mcp",
            "health_url": f"http://127.0.0.1:{port}/health",
            "tags": ["read"],
            "capabilities": ["tools"],
        },
    ).json()


def test_health(client: TestClient) -> None:
    """存活探针返回 ok。"""
    assert client.get("/health").json() == {"status": "ok"}


def test_register_and_list(client: TestClient) -> None:
    """注册两个 Runtime，/servers 应返回两个且都 up。"""
    assert _register(client, "weather", "weather", 8300)["count"] == 1
    assert _register(client, "calc", "calc", 8301)["count"] == 2

    servers = client.get("/servers").json()["servers"]
    assert {s["name"] for s in servers} == {"weather", "calc"}
    assert all(s["up"] for s in servers)  # 注册即 up


def test_unregister(client: TestClient) -> None:
    """下线 weather 后，/servers 只剩 calc。"""
    _register(client, "weather", "weather", 8300)
    _register(client, "calc", "calc", 8301)

    assert client.delete("/register/weather").json()["count"] == 1
    servers = client.get("/servers").json()["servers"]
    assert [s["name"] for s in servers] == ["calc"]


def test_servers_empty_when_nothing_registered(client: TestClient) -> None:
    """空目录时 /servers 返回空列表（Gateway 启动早于 Runtime 的边界情况）。"""
    assert client.get("/servers").json()["servers"] == []
