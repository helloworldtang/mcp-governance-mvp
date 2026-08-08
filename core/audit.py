"""数据面审计日志 —— 把每次工具调用(谁、调了啥、allow/deny、结果)落盘成 JSONL。

这是 Runtime 层"审计"职责的落地：stderr 日志会随进程退出消失，审计行是
【持久化、可回放、可对账】的数据面留痕。生产里它会进 SIEM / append-only 存储；
本 demo 落 output/audit.jsonl 够教学。
"""

import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from core.config import OUTPUT_DIR

_AUDIT_PATH: Path = OUTPUT_DIR / "audit.jsonl"
_LOCK = Lock()  # 多 Runtime 进程并发追加，加锁避免半行交错（生产用专门的 audit sink）


def audit(
    trace: str,
    user: str,
    role: str,
    tool: str,
    decision: str,  # "allow" | "deny"
    detail: str = "",
) -> None:
    """追加一条审计记录。decision ∈ {allow, deny}。"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    rec = {
        "ts": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "trace": trace,
        "user": user,
        "role": role,
        "tool": tool,
        "decision": decision,
        "detail": detail[:120],
    }
    line = json.dumps(rec, ensure_ascii=False)
    with _LOCK, open(_AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
