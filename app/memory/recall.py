"""记忆检索：中文友好的 bigram-Jaccard 相似度 + 重要度 + 时间衰减。

向量检索适配器（embedding）为可插拔设计，装上本地 embedding 模型后
可替换 sim() 实现；当前无外部依赖即可用的字符 bigram 方案对中文同义改写
召回足够起步，且与爱丽丝 soul-archive 的 bigram-Jaccard 去重同源。
"""
import re
from datetime import datetime, timezone

from app.db import db
from app.logging_setup import get_logger

log = get_logger("memory")


def _tokens(text: str) -> set[str]:
    t = re.sub(r"[\s，。！？、；：""''（）()【】\[\]…—]+", "", text or "")
    grams = {t[i:i + 2] for i in range(len(t) - 1)}
    grams.update(t)  # 单字也入集合，增强短词召回
    return grams


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _recency_decay(created_at: str | None) -> float:
    """1 天内 = 1.0，随天数衰减到 0.1 下限。"""
    if not created_at:
        return 0.5
    try:
        dt = datetime.fromisoformat(created_at)
        days = (datetime.now(timezone.utc).astimezone() - dt.astimezone()).total_seconds() / 86400
    except ValueError:
        return 0.5
    return max(0.1, 1.0 / (1.0 + days))


def recall(query: str, k: int = 5, database: "Database | None" = None) -> list[dict]:
    """混合打分检索：0.55×相似度 + 0.25×重要度 + 0.20×时间衰减。"""
    conn = (database or db).conn()
    eps = conn.execute(
        "SELECT id, content, summary, importance, created_at FROM episodic_memories"
        " WHERE archived=0"
    ).fetchall()
    sems = conn.execute(
        "SELECT id, fact, confidence, created_at FROM semantic_memories WHERE archived=0"
    ).fetchall()

    scored: list[dict] = []
    for r in eps:
        text = (r["content"] or "") + " " + (r["summary"] or "")
        score = (0.55 * jaccard(query, text)
                 + 0.25 * (r["importance"] or 0.5)
                 + 0.20 * _recency_decay(r["created_at"]))
        scored.append({
            "kind": "episodic", "id": r["id"], "content": r["content"][:200],
            "score": round(score, 4),
        })
    for r in sems:
        score = (0.55 * jaccard(query, r["fact"])
                 + 0.25 * (r["confidence"] or 0.5)
                 + 0.20 * _recency_decay(r["created_at"]))
        scored.append({
            "kind": "semantic", "id": r["id"], "content": r["fact"][:200],
            "score": round(score, 4),
        })

    scored.sort(key=lambda x: (-x["score"], -x["id"]))
    top = scored[:k]

    # 更新 last_access（记忆被想起）
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for item in top:
        if item["kind"] == "episodic":
            conn.execute("UPDATE episodic_memories SET last_access=? WHERE id=?",
                         (now, item["id"]))
    conn.commit()
    return top


def recall_flashbulb(emotion: str | None = None, k: int = 3,
                     database: "Database | None" = None) -> list[dict]:
    """情绪匹配的闪光灯记忆（weight=2.0）优先召回。"""
    conn = (database or db).conn()
    if emotion:
        rows = conn.execute(
            "SELECT * FROM emotional_memories WHERE archived=0 AND emotion=?"
            " ORDER BY weight DESC, intensity DESC LIMIT ?",
            (emotion, k),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM emotional_memories WHERE archived=0"
            " ORDER BY weight DESC, intensity DESC LIMIT ?",
            (k,),
        ).fetchall()
    return [dict(r) for r in rows]
