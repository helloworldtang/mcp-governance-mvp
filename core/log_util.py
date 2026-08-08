"""三层结构化 emoji 日志 —— 让 Registry/Gateway/Runtime 职责在输出里肉眼可分。

前缀约定（对应三层 + ReAct 动作）：
  📖 [registry]  注册 / 发现 / 心跳
  🚪 [gateway]   鉴权 / 路由 / 转发 / 审计
  ⚙️  [runtime]   执行 / per-user 鉴权 / 审计
  ▶  [client]    ACTION（Agent 选了哪个工具）
  ◀  [client]    OBSERVE（工具返回）
  ✔ / ✘          成功 / 被拒 / 失败
"""

import sys
from datetime import datetime

# 三层前缀（与 sibling 的 [node] 标签一致，但加了 emoji + 中文层名便于一眼分辨）
LAYER_PREFIX = {
    "registry": "📖 [registry]",
    "gateway": "🚪 [gateway] ",
    "runtime": "⚙️  [runtime] ",
    "client": "🤖 [client]  ",
}


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(layer: str, msg: str, *emoji: str, trace: str = "") -> None:
    """layer ∈ {registry, gateway, runtime, client}；末尾的 emoji 用作状态标记（✔/✘/🔒）。
    trace 非空时在时间后显示 [trace:xxxx]，便于跨三层追同一次请求。
    """
    prefix = LAYER_PREFIX.get(layer, f"[{layer}]")
    mark = " " + " ".join(emoji) if emoji else ""
    tr = f"[trace:{trace}] " if trace else ""
    print(f"{_ts()} {tr}{prefix} {msg}{mark}", flush=True, file=sys.stderr)


def log_registry(msg: str, *e: str, trace: str = "") -> None:
    log("registry", msg, *e, trace=trace)


def log_gateway(msg: str, *e: str, trace: str = "") -> None:
    log("gateway", msg, *e, trace=trace)


def log_runtime(name: str, msg: str, *e: str, trace: str = "") -> None:
    """Runtime 带服务名（weather / calc）便于区分两个 Runtime 进程。"""
    prefix = LAYER_PREFIX["runtime"]
    mark = " " + " ".join(e) if e else ""
    tr = f"[trace:{trace}] " if trace else ""
    print(f"{_ts()} {tr}{prefix}[{name}] {msg}{mark}", flush=True, file=sys.stderr)


def log_client(msg: str, *e: str, trace: str = "") -> None:
    log("client", msg, *e, trace=trace)


def log_action(tool: str, args: str = "") -> None:
    """ReAct Agent 选工具（对应 sibling 的 ▶ ACTION）。"""
    arg = f"({args})" if args else "()"
    print(f"{_ts()}   ▶  [client] ACTION   {tool}{arg}", flush=True, file=sys.stderr)


def log_observe(tool: str, result: str = "") -> None:
    """工具返回（对应 sibling 的 ◀ OBSERVE），结果截断。"""
    preview = (result or "").replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    print(f"{_ts()}   ◀  [client] OBSERVE  {tool}: {preview}", flush=True, file=sys.stderr)
