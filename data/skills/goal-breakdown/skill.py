"""目标拆解技能：真正的工具函数。"""

from datetime import datetime, timezone

from app.db import db


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def add_goal(args, database=None):
    content = str((args or {}).get("content", "")).strip()
    if not content:
        return "（目标内容为空，没有写入）"
    (database or db).conn().execute(
        "INSERT INTO goals (content, priority, progress, last_progress_at, status)"
        " VALUES (?,3,0.0,?, 'active')", (content, _now()))
    (database or db).conn().commit()
    return f"已写入目标：{content}"


def list_goals(args=None, database=None):
    rows = (database or db).conn().execute(
        "SELECT content, progress FROM goals WHERE status='active'"
        " ORDER BY priority DESC, id DESC").fetchall()
    if not rows:
        return "（当前没有活跃目标）"
    return "\n".join(f"- {r['content']}（进度 {int(r['progress'] * 100)}%）"
                     for r in rows)


def run(db, user_text):  # 兼容旧触发路径
    return add_goal({"content": user_text[:30]}, database=db)
