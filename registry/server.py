"""Registry —— 发现层（FastAPI, :8100）。

职责：维护 MCP Runtime 的元数据清单 + 健康状态，给 Gateway 提供服务发现。
  POST   /register         Runtime 自荐注册
  GET    /servers          Gateway 启动时拉它建路由（含健康状态）
  DELETE /register/{name}  Runtime 下线
  GET    /health           自身存活探针

不做的事（生产 Registry 才需要）：持久化、版本协商、多租户、schema 校验。
本 demo 内存字典够用，进程重启即清空 —— 这是教学上的刻意简化。
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from core.config import REGISTRY_HEALTH_INTERVAL, REGISTRY_PORT
from core.log_util import log_registry

# 内存目录：name -> 注册信息 + 健康状态
_catalog: dict[str, dict[str, object]] = {}


class RegisterReq(BaseModel):
    name: str
    namespace: str
    url: str  # MCP streamable-http 端点（Gateway 转发目标）
    health_url: str  # 心跳探针目标
    tags: list[str] = []
    capabilities: list[str] = []


async def _health_loop() -> None:
    """后台周期性 ping 每个 Runtime 的 health_url，标记 up/down。"""
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            for name, info in list(_catalog.items()):
                try:
                    r = await client.get(str(info["health_url"]))
                    up = r.status_code == 200
                except Exception:
                    up = False
                was_up = info.get("up")
                info["up"] = up
                if was_up != up and was_up is not None:  # 状态翻转才打日志，避免刷屏
                    log_registry(f"{name} 健康 → {'up' if up else 'down'}", "✔" if up else "✘")
            await asyncio.sleep(REGISTRY_HEALTH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI 生命周期：启动后台心跳任务，退出时取消。"""
    log_registry(f"注册中心就绪 @ :{REGISTRY_PORT}")
    task = asyncio.create_task(_health_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="MCP Registry", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """存活探针。"""
    return {"status": "ok"}


@app.post("/register")
async def register(req: RegisterReq) -> dict[str, object]:
    """Runtime 自荐注册到目录。"""
    _catalog[req.name] = {**req.model_dump(), "up": True}
    log_registry(f"注册 {req.name} → ns={req.namespace} url={req.url}", "📖")
    return {"ok": True, "count": len(_catalog)}


@app.delete("/register/{name}")
async def unregister(name: str) -> dict[str, object]:
    """Runtime 下线注销。"""
    if name in _catalog:
        _catalog.pop(name)
        log_registry(f"注销 {name}")
    return {"ok": True, "count": len(_catalog)}


@app.get("/servers")
async def servers() -> dict[str, list[dict[str, object]]]:
    """Gateway 调用它建路由表。只返回 up 的（down 的不路由）。"""
    return {"servers": [dict(info) for info in _catalog.values() if info.get("up", False)]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=REGISTRY_PORT, log_level="warning")
