"""记忆压缩与索引晋升：周压缩任务。

规则（照搬定稿）：扫描最近 N 天对话主题/标签与情景记忆标签，
出现 3+ 次的模式晋升进 memory_index；记忆条目只归档不删除。
"""
import json
from datetime import datetime, timedelta, timezone

from app.db import db
from app.logging_setup import get_logger

log = get_logger("memory")


def _count(items: list[str]) -> dict[str, int]:
    counter: dict[str, int] = {}
    for it in items:
        if it:
            counter[it] = counter.get(it, 0) + 1
    return counter


def weekly_compaction(days: int = 7, min_freq: int = 3,
                      database: "Database | None" = None) -> dict:
    """统计最近 days 天的主题与标签，出现 min_freq+ 次的晋升入索引。

    返回 {"scanned": n, "promoted": [{topic, count}]}
    """
    conn = (database or db).conn()
    since = (datetime.now(timezone.utc).astimezone()
             - timedelta(days=days)).isoformat(timespec="seconds")

    topics: list[str] = []
    for r in conn.execute(
        "SELECT topic FROM conversations WHERE ts >= ? AND topic IS NOT NULL", (since,)
    ).fetchall():
        topics.append(r["topic"])
    for r in conn.execute(
        "SELECT tags FROM episodic_memories WHERE created_at >= ?", (since,)
    ).fetchall():
        try:
            tags = json.loads(r["tags"])
        except (json.JSONDecodeError, TypeError):
            continue
        topics.extend(str(t) for t in tags)

    counter = _count(topics)
    promoted = []
    for topic, count in counter.items():
        if count >= min_freq:
            conn.execute(
                "INSERT INTO memory_index (topic, ref, promote_count, created_at) VALUES (?,?,?,?)"
                " ON CONFLICT(topic) DO UPDATE SET promote_count=promote_count+?",
                (topic, f"topic:{topic}", count, datetime.now(timezone.utc).astimezone()
                 .isoformat(timespec="seconds"), count),
            )
            promoted.append({"topic": topic, "count": count})
    conn.commit()
    log.info("周压缩完成：扫描主题 %d 个，晋升 %d 条", len(counter), len(promoted))
    return {"scanned": len(counter), "promoted": promoted}
