#!/usr/bin/env bash
# 同一任务跑两个 Gateway 变体，对比 Gateway 层日志（教学点：声明式 vs 手写转发）。
#
# 用法: ./compare_gateways.sh "<任务>" [--user bob]
#   ./compare_gateways.sh "把天气缓存清掉" --user bob
#
# 对比轴：
#   native   mount(create_proxy) —— 路由/转发是黑盒，Gateway 日志只有「就绪 + 鉴权注入」
#   explicit 手写 _forward()      —— 逐跳可见，每个工具调用都打「路由 → ←」两行
set -uo pipefail

TASK="${1:-把天气缓存清掉}"; shift 2>/dev/null || true
USER_ARGS=("$@")
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"

GATEWAY_LOG=/tmp/mcp_gateway.log
EXTRACT='🚪|⚙️|ACTION|OBSERVE|完成'

sep() { printf '=%.0s' {1..70}; echo; }

for V in native explicit; do
  sep
  echo "  变体 $V  ——  任务: $TASK  用户参数: ${USER_ARGS[*]:-（默认 bob）}"
  sep
  ./run_demo.sh "$V" "$TASK" ${USER_ARGS[@]+"${USER_ARGS[@]}"} 2>&1 \
    | grep -E "$EXTRACT" \
    | grep -vE "清理进程" \
    | sed 's/^/  /'
  echo ""
done

echo "对比结论："
echo "  - native   的 🚪[gateway] 行很少 —— 路由/转发/命名空间全在 create_proxy 黑盒里"
echo "  - explicit 的 🚪[gateway] 每次调用都有「路由 →」「←  ✔」两行 —— 每跳都写在 _forward()"
echo "  - 两者 ⚙️[runtime] 行一致：身份都正确送达执行点，per-user 鉴权结果相同"
