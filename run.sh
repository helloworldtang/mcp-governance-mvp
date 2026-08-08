#!/usr/bin/env bash
# 一键跑通 MCP 三层治理 demo：Registry → Runtime(weather+calc) → Gateway($1) → Client(DeepSeek)
#
# 用法:
#   ./run.sh <native|explicit> "<任务>" [--user alice|bob]
#   ./run.sh explicit "查一下上海天气" --user bob
#   ./run.sh native   "把天气缓存清掉" --user alice
#
# 各进程日志：/tmp/mcp_{registry,weather,calc,gateway}.log
set -uo pipefail

VARIANT="${1:-explicit}"; shift 2>/dev/null || true
TASK="${1:-查一下上海天气}"; shift 2>/dev/null || true
CLIENT_ARGS=("$@")

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ "$VARIANT" != "native" ] && [ "$VARIANT" != "explicit" ]; then
  echo "✘ 第一个参数必须是 native 或 explicit（got: $VARIANT）"
  exit 2
fi

cleanup() {
  echo ""
  echo "=== 清理进程（按端口）==="
  for p in 8100 8200 8300 8301; do
    lsof -ti :$p 2>/dev/null | xargs kill -9 2>/dev/null
  done
}
trap cleanup EXIT INT TERM

# 等待一个 URL 返回 2xx，最多 ~N 秒
wait_url() {
  local url="$1" tries="${2:-40}"
  for i in $(seq 1 "$tries"); do
    if curl -sf -o /dev/null "$url" 2>/dev/null; then return 0; fi
    sleep 0.3
  done
  return 1
}

echo "=== 0. 清理旧进程 ==="
cleanup; sleep 0.6

echo "=== 1. 启动 Registry (:8100) ==="
uv run python -m registry.server > /tmp/mcp_registry.log 2>&1 &
wait_url http://127.0.0.1:8100/health 20 || { echo "✘ Registry 起不来，看 /tmp/mcp_registry.log"; tail -5 /tmp/mcp_registry.log; exit 1; }
echo "   ✔ Registry 就绪"

echo "=== 2. 启动 Runtime: weather(:8300) + calc(:8301)（自注册到 Registry）==="
uv run python -m runtime.weather > /tmp/mcp_weather.log 2>&1 &
uv run python -m runtime.calc   > /tmp/mcp_calc.log   2>&1 &
wait_url http://127.0.0.1:8300/health 40 || { echo "✘ weather Runtime 起不来"; exit 1; }
wait_url http://127.0.0.1:8301/health 40 || { echo "✘ calc Runtime 起不来"; exit 1; }
for i in $(seq 1 40); do
  n=$(curl -s http://127.0.0.1:8100/servers 2>/dev/null | python3 -c "import sys,json;print(len(json.load(sys.stdin)['servers']))" 2>/dev/null || echo 0)
  [ "$n" = "2" ] && break
  sleep 0.3
done
[ "$n" = "2" ] || { echo "✘ Registry 未发现全部 Runtime"; exit 1; }
echo "   ✔ Registry 已发现 $n 个 Runtime"

echo "=== 3. 启动 Gateway ($VARIANT, :8200) ==="
GW_MODULE="gateway.fastmcp_native"
[ "$VARIANT" = "explicit" ] && GW_MODULE="gateway.explicit_proxy"
uv run python -m "$GW_MODULE" > /tmp/mcp_gateway.log 2>&1 &
wait_url http://127.0.0.1:8200/health 40 || { echo "✘ Gateway 起不来，看 /tmp/mcp_gateway.log"; tail -15 /tmp/mcp_gateway.log; exit 1; }
echo "   ✔ Gateway 就绪"

echo "=== 4. Client (DeepSeek ReAct Agent) 执行任务 ==="
echo "   任务: $TASK"
echo "   参数: ${CLIENT_ARGS[*]:-（默认 --user bob）}"
echo "------------------------------------------------------------"
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}" uv run python -m client.run "$TASK" ${CLIENT_ARGS[@]+"${CLIENT_ARGS[@]}"}
RC=$?
echo "------------------------------------------------------------"

echo ""
echo "=== 本次各层日志摘要（三层职责肉眼可分）==="
echo "--- 📖 [registry] 发现/心跳 ---"
grep -hE "注册 |→|健康" /tmp/mcp_registry.log | tail -6
echo "--- 🚪 [gateway] 鉴权/路由/转发 ---"
grep -hE "注册|路由|←|就绪|401" /tmp/mcp_gateway.log | tail -14
echo "--- ⚙️  [runtime weather] 执行/per-user 鉴权 ---"
grep -hE "get_forecast|reset_cache|DENIED" /tmp/mcp_weather.log | tail -8
echo "--- ⚙️  [runtime calc] 执行 ---"
grep -hE "add|multiply" /tmp/mcp_calc.log | tail -4
exit $RC
