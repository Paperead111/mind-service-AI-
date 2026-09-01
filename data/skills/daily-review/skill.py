"""每日回顾技能：真正的工具函数。"""

from app.db import db


def recent_conversations(args, database=None):
    limit = int((args or {}).get("limit") or 20)
    rows = (database or db).conn().execute(
        "SELECT role, content, ts FROM conversations ORDER BY ts DESC LIMIT ?",
        (min(limit, 100),)).fetchall()
    if not rows:
        return "（记忆库里还没有对话记录）"
    lines = []
    for r in reversed(rows):
        who = "对方" if r["role"] == "user" else "她"
        lines.append(f"[{r['ts'][11:16]}] {who}: {r['content'][:80]}")
    return "\n".join(lines)


def run(db, user_text):  # 兼容旧触发路径
    return recent_conversations({"limit": 20}, database=db)
