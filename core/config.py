"""全局配置常量 —— 端口、身份映射、模型、跨进程 header 契约。

【Java/C 读者速查】
  - 模块级常量（全大写）≈ Java 的 `public static final`，被各进程 import 共享（只读）。
  - dict[str, dict[str, str]] ≈ Map<String, Map<String,String>>（嵌套 Map）。
  - str | None ≈ 「String 或 null」联合类型（Java 没有，相当于 Optional 的类型层表达）。
  - f"http://...:{PORT}"：f-string 格式化（≈ C printf / Java String.format），{变量} 插值。
  - os.environ.get(K, 默认)：读环境变量，不存在用默认（≈ System.getenv with default）。

三层各进程都 import 这一份，保证端口 / header 名 / 身份口径处处一致。
"""

import os
from pathlib import Path

# === 端口分配（一进程一端口，互不抢）===
REGISTRY_PORT = 8100
GATEWAY_PORT = 8200
RUNTIME_WEATHER_PORT = 8300
RUNTIME_CALC_PORT = 8301

REGISTRY_URL = f"http://127.0.0.1:{REGISTRY_PORT}"
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}/mcp"
WEATHER_URL = f"http://127.0.0.1:{RUNTIME_WEATHER_PORT}/mcp"
CALC_URL = f"http://127.0.0.1:{RUNTIME_CALC_PORT}/mcp"

# === 跨进程身份契约（2026-07-28 无状态协议：身份随每个请求走 header）===
# Client 在 Authorization 带入站 key → Gateway 解析后注入 X-User/X-Role 给 Runtime。
HDR_USER = "X-User"
HDR_ROLE = "X-Role"
HDR_AUTH = "Authorization"
HDR_TRACE = "X-Trace-Id"  # 全链路 trace：Client 生成，Gateway 透传，Runtime 连同身份一起打

# === 身份映射（教学用：静态 key→{user,role}，替代真实 OAuth）===
#   admin  = 全部工具可调（含 admin 标签）
#   viewer = 只能调 read 标签工具；admin 工具在 Runtime 执行点被拒
API_KEYS: dict[str, dict[str, str]] = {
    "key-alice": {"user": "alice", "role": "admin"},
    "key-bob": {"user": "bob", "role": "viewer"},
}

# 工具标签口径（Runtime 打标、Gateway 可据此过滤）
TAG_READ = "read"
TAG_ADMIN = "admin"


def resolve_key(api_key: str | None) -> dict[str, str] | None:
    """把入站 Bearer key 解析成 {user, role}；不认识返回 None（→ 401）。"""
    if not api_key:
        return None
    return API_KEYS.get(api_key.removeprefix("Bearer ").strip())


# === DeepSeek（OpenAI 兼容协议；Client 的 ReAct Agent 用）===
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


# === Registry 心跳间隔（后台任务用）===
REGISTRY_HEALTH_INTERVAL = 5.0  # 秒

# === 输出（与 sibling demo 一致：时间戳转写落在项目根 output/）===
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
