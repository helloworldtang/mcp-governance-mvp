"""Registry —— 发现层（FastAPI, :8100）。

【Java/C 读者速查】
  - FastAPI：Python 的 web 框架（≈ Java Spring Boot）。@app.get("/health") / @app.post(...) 是路由
    装饰器，把下面的 async 函数注册成「处理该 URL + 方法的回调」（≈ @RequestMapping）。
  - class RegisterReq(BaseModel)：Pydantic 数据模型（≈ Java POJO/record）。FastAPI 自动用它
    做请求体校验 + JSON 序列化；字段带默认值（tags: list[str] = []）。
  - lifespan：应用启动/关闭的生命周期钩子（≈ Spring 的 @PostConstruct/@PreDestroy）。
    @asynccontextmanager 让一个 async 函数变成「上下文管理器」：yield 之前=启动逻辑，之后=清理。
  - {**req.model_dump(), "up": True}：dict 展开 + 追加（≈ 把 Map 复制一份再 put 一个 key）。

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

# 内存目录：name -> 注册信息 + 健康状态。dict[str, dict[str, object]] ≈ Map<String, Map<String,Object>>
_catalog: dict[str, dict[str, object]] = {}


class RegisterReq(BaseModel):
    """注册请求体（FastAPI 自动按此校验入参 JSON）。"""

    name: str
    namespace: str
    url: str  # MCP streamable-http 端点（Gateway 转发目标）
    health_url: str  # 心跳探针目标
    tags: list[str] = []  # 带默认值的字段（≈ Java 字段默认值）
    capabilities: list[str] = []


async def _check_health_once(client: httpx.AsyncClient) -> None:
    """检查一轮 Runtime 健康状态并更新目录。"""
    for name, info in list(_catalog.items()):
        try:
            response = await client.get(str(info["health_url"]))
            up = response.status_code == 200
        except Exception:  # 连不上视为 down（粗粒度捕获；生产要区分超时/拒绝）
            up = False
        was_up = info.get("up")
        info["up"] = up
        if was_up != up and was_up is not None:
            log_registry(f"{name} 健康 → {'up' if up else 'down'}", "✔" if up else "✘")


async def _health_loop() -> None:
    """后台周期性 ping 每个 Runtime 的 health_url，标记 up/down。"""
    async with httpx.AsyncClient(timeout=2.0) as client:  # 异步 HTTP 客户端，离开 with 自动关闭
        while True:
            await _check_health_once(client)
            await asyncio.sleep(REGISTRY_HEALTH_INTERVAL)  # 异步 sleep（不阻塞线程）


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI 生命周期：启动后台心跳任务，退出时取消。"""
    log_registry(f"注册中心就绪 @ :{REGISTRY_PORT}")
    task = asyncio.create_task(_health_loop())  # 起一个后台协程任务（≈ 起一个 daemon 线程跑心跳）
    try:
        yield  # yield 之前=启动后、服务运行中；app 在此期间对外服务
    finally:
        task.cancel()  # 应用关闭时取消心跳任务


app = FastAPI(title="MCP Registry", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """存活探针。"""
    return {"status": "ok"}


@app.post("/register")
async def register(req: RegisterReq) -> dict[str, object]:
    """Runtime 自荐注册到目录。req 由 FastAPI 按 RegisterReq 自动解析校验。"""
    # model_dump() 把 Pydantic 对象转成 dict；{**d, "up": True} = 复制 d 再加 up 字段
    _catalog[req.name] = {**req.model_dump(), "up": True}
    log_registry(f"注册 {req.name} → ns={req.namespace} url={req.url}", "📖")
    return {"ok": True, "count": len(_catalog)}


@app.delete("/register/{name}")
async def unregister(name: str) -> dict[str, object]:
    """Runtime 下线注销。{name} 是路径参数（FastAPI 自动注入到同名形参）。"""
    if name in _catalog:
        _catalog.pop(name)  # dict.pop(key) 删除并返回；不存在会 KeyError，所以先 in 判断
        log_registry(f"注销 {name}")
    return {"ok": True, "count": len(_catalog)}


@app.get("/servers")
async def servers() -> dict[str, list[dict[str, object]]]:
    """Gateway 调用它建路由表。只返回 up 的（down 的不路由）。"""
    # 推导式：遍历目录，过滤出 up 的，每个拷一份（dict(info)）组成列表（≈ Java stream.filter.map.toList）
    return {"servers": [dict(info) for info in _catalog.values() if info.get("up", False)]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=REGISTRY_PORT, log_level="warning")
