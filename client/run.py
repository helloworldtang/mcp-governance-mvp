"""Client CLI —— 以指定用户身份让 DeepSeek ReAct Agent 执行一个任务。

用法:
  uv run python -m client.run "查一下上海天气" --user bob
  uv run python -m client.run "把天气缓存清掉" --user alice   # admin 才成

DEEPSEEK_API_KEY 必填（.env）；--user 默认读 DEMO_USER 环境变量，再退回 bob。
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime

from client.agent import run_task
from core.config import OUTPUT_DIR
from core.log_util import log_client


def _check_env() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        sys.exit("✘ 未设置 DEEPSEEK_API_KEY（看 .env.example）")


async def _main(task: str, user: str) -> int:
    answer = await run_task(task, user)

    # 转写落盘（与 sibling demo 一致：output/<ts>.txt）
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    path = OUTPUT_DIR / f"{ts}.{user}.txt"
    path.write_text(f"任务: {task}\n用户: {user}\n\n答复:\n{answer}\n", encoding="utf-8")
    log_client(f"转写已存: {path}")
    return 0


def main() -> None:
    _check_env()
    ap = argparse.ArgumentParser(description="MCP 三层治理 demo · Client (DeepSeek ReAct Agent)")
    ap.add_argument("task", help='交给 Agent 的任务，如 "查一下上海天气"')
    ap.add_argument(
        "--user",
        default=os.environ.get("DEMO_USER", "bob"),
        help="以谁的身份调用 (alice=admin | bob=viewer)，默认 DEMO_USER 或 bob",
    )
    args = ap.parse_args()
    sys.exit(asyncio.run(_main(args.task, args.user)))


if __name__ == "__main__":
    main()
