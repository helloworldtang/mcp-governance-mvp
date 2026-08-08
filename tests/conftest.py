"""测试公用：session 级端口清理。

服务启停 helper 内联在 test_gateway_e2e.py（唯一消费者），避免跨模块导入的 pytest import-mode 坑。
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator

import pytest

_PORTS = (8100, 8200, 8300, 8301)


def _kill_ports() -> None:
    for p in _PORTS:
        subprocess.run(
            ["sh", "-c", f"lsof -ti :{p} 2>/dev/null | xargs kill -9 2>/dev/null"],
            check=False,
        )


@pytest.fixture(autouse=True, scope="session")
def _clean_ports() -> Iterator[None]:
    """整轮测试前后确保项目端口空闲（杀残留进程），避免 Errno 48。

    session 级：配合 module 级 gov_stack，不在测试间杀端口。
    """
    _kill_ports()
    time.sleep(0.3)
    yield
    _kill_ports()
