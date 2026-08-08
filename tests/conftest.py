"""测试公用：session 级端口清理 + module 级 runtime 栈 fixture。

`runtimes` 起 Registry+weather+calc 并互注册；两个 gateway 测试文件各自在其上叠加
自己的网关（explicit / native）。Server 启停辅助在 tests/_helpers.py。
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator

import pytest

from registry.server import _catalog
from registry.server import app as registry_app
from runtime.calc import mcp as calc_mcp
from runtime.weather import _cache
from runtime.weather import mcp as weather_mcp
from tests._helpers import register_runtime, serve

_PORTS = (8100, 8200, 8300, 8301)
_ORIG_CACHE = dict(_cache)  # 进程级备份，测试改了 weather 缓存后还原


def _kill_ports() -> None:
    for p in _PORTS:
        subprocess.run(
            ["sh", "-c", f"lsof -ti :{p} 2>/dev/null | xargs kill -9 2>/dev/null"],
            check=False,
        )


@pytest.fixture(autouse=True, scope="session")
def _clean_ports() -> Iterator[None]:
    """整轮测试前后确保项目端口空闲（杀残留进程），避免 Errno 48。"""
    _kill_ports()
    time.sleep(0.3)
    yield
    _kill_ports()


@pytest.fixture(scope="module")
def runtimes() -> Iterator[None]:
    """起 Registry + weather + calc 并互注册；yield；退出停服务 + 还原 weather 缓存。

    module 级：每个引用它的测试模块各起一份（模块间串行，端口在模块边界释放）。
    """
    _catalog.clear()
    servers = [
        serve(registry_app, 8100),
        serve(weather_mcp.http_app(path="/mcp", stateless_http=True), 8300),
        serve(calc_mcp.http_app(path="/mcp", stateless_http=True), 8301),
    ]
    register_runtime(
        "http://127.0.0.1:8100",
        "weather",
        "weather",
        "http://127.0.0.1:8300/mcp",
        "http://127.0.0.1:8300/health",
        ["read", "admin"],
    )
    register_runtime(
        "http://127.0.0.1:8100",
        "calc",
        "calc",
        "http://127.0.0.1:8301/mcp",
        "http://127.0.0.1:8301/health",
        ["read"],
    )
    yield
    for s in servers:
        s.stop()
    _cache.clear()
    _cache.update(_ORIG_CACHE)
