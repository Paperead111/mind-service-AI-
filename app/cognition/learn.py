"""七步自主学习闭环（原则 7 的代码实现）：

①发现缺口 → ②自主决定 → ③执行查询（双源） → ④存入认知网络
→ ⑤交叉验证（一致 +0.2 置信 / 矛盾 contradicts 边） → ⑥主动应用（自然融入）
→ ⑦定时回顾（>7 天未访问节点进入复习队列）

治理：学习只写知识层与图，永不直接改锚点/人格；全程审计。
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone

from app.cognition.audit import audit
from app.cognition.network import add_edge, add_node
from app.db import Database, db
from app.logging_setup import get_logger, log_event
from app.memory.recall import jaccard
from app.memory.store import MemoryStore

log = get_logger("cognition.learn")

CONSISTENT_THRESHOLD = 0.25   # 双源答案 bigram-Jaccard ≥ 阈值 → 一致
CONSISTENT_CONFIDENCE = 0.7
CONFLICT_CONFIDENCE = 0.3
REVIEW_DAYS = 7
LEARN_DAILY_LIMIT = 3         # R5：学习限额 3/日


def _learn_quota_left(database: Database | None = None) -> int:
    """当日剩余学习额度（限额 3/日）。"""
    from app.proactive.settings import get_setting, set_setting
    dbx = database or db
    today = _now()[:10]
    date = get_setting("learn_count_date", dbx)
    if date != today:
        set_setting("learn_count_date", today, dbx)
        set_setting("learn_count", "0", dbx)
        return LEARN_DAILY_LIMIT
    try:
        used = int(get_setting("learn_count", dbx) or 0)
    except (TypeError, ValueError):
        used = 0
    return max(0, LEARN_DAILY_LIMIT - used)


def _learn_count_up(database: Database | None = None) -> None:
    from app.proactive.settings import get_setting, set_setting
    dbx = database or db
    try:
        used = int(get_setting("learn_count", dbx) or 0)
    except (TypeError, ValueError):
        used = 0
    set_setting("learn_count", str(used + 1), dbx)


async def learning_scan_tick(elapsed_seconds: float, database: Database | None = None,
                             llm=None) -> dict:
    """R5 学习扫描（tick 钩子，异步）：每次运行都留痕（跳过原因也写，成功失败都有数据）。"""
    dbx = database or db
    due = review_due(database=dbx)
    if not due:
        log_event("learning_scan", due=0, learned=0, skipped="no_due",
                  msg="学习扫描：无到期复习节点（跳过，正常）")
        return {"learned": 0, "due": 0, "skipped": "no_due"}
    quota = _learn_quota_left(dbx)
    if quota <= 0:
        log_event("learning_scan", due=len(due), learned=0, skipped="quota",
                  msg=f"学习扫描：当日额度已用完（到期 {len(due)} 条）")
        return {"learned": 0, "due": len(due), "skipped": "quota"}
    if llm is None:
        log_event("learning_scan", due=len(due), learned=0, skipped="no_llm",
                  msg="学习扫描：无 LLM 可用")
        return {"learned": 0, "due": len(due), "skipped": "no_llm"}
    # 降级 ≥L1b 熔断（不学也不耗额度）
    from app.degradation.engine import DegradationEngine
    if DegradationEngine(dbx).is_degraded():
        log_event("learning_scan", due=len(due), learned=0, skipped="degraded",
                  msg="学习扫描：降级熔断")
        return {"learned": 0, "due": len(due), "skipped": "degraded"}
    learned = 0
    for item in due[:LEARN_DAILY_LIMIT]:
        if _learn_quota_left(dbx) <= 0:
            break
        try:
            topic = item["name"] or ""
            if topic.startswith("knowledge:"):
                topic = topic[len("knowledge:"):]   # 复习名剥前缀，防 knowledge:knowledge 嵌套
            await run_learning(topic, dbx, llm=llm)
            mark_reviewed(item["name"], dbx)        # 原节点标记已复习，次日不再重复
            _learn_count_up(dbx)
            learned += 1
        except Exception:
            log.exception("自动学习失败：%s", item["name"])
    log_event("learning_scan", due=len(due), learned=learned,
              quota_left=_learn_quota_left(dbx),
              msg=f"学习扫描完成：本轮 {learned}/{min(len(due), LEARN_DAILY_LIMIT)}"
                  f"（当日剩额度 {_learn_quota_left(dbx)}）")
    return {"learned": learned, "due": len(due)}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------- ① 发现缺口 ----------

def discover(database: Database | None = None) -> list[dict]:
    """扫描低置信度语义记忆 → 学习队列；返回新入队条目。"""
    conn = (database or db).conn()
    rows = conn.execute(
        "SELECT fact FROM semantic_memories WHERE archived=0 AND confidence < 0.5"
    ).fetchall()
    added = []
    for r in rows:
        topic = r["fact"][:40]
        dup = conn.execute(
            "SELECT id FROM learn_queue WHERE topic=? AND status='pending'",
            (topic,)).fetchone()
        if dup is None:
            conn.execute(
                "INSERT INTO learn_queue (topic, status, confidence, created_at)"
                " VALUES (?,?,?,?)", (topic, "pending", 0.3, _now()))
            added.append(topic)
    conn.commit()
    return [{"topic": t} for t in added]


# ---------- ③ 默认查询（双源视角，LLM；query_fn 可注入） ----------

async def default_query(topic: str, variant: int, llm) -> str | None:
    if llm is None:
        return None
    angle = ("从实用要点角度" if variant == 1
             else "完全独立地回答（不要参考任何先前内容）")
    prompt = (f"关于「{topic}」，{angle}给出回答：3-5 条，每条一句话，"
              "只给内容，不要客套。")
    try:
        text = await asyncio.wait_for(
            llm.chat([{"role": "user", "content": prompt}], temperature=0.3),
            timeout=20)
        return (text or "").strip() or None
    except Exception as exc:
        log.warning("学习查询失败（变体 %d）：%s", variant, exc)
        return None


def _split_facts(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[。！？!?；;\n]+", text) if len(p.strip()) >= 4]


def facts_consistent(s1: str, s2: str) -> bool:
    """句级软匹配（无 LLM 时的启发式兜底）：s1 中 ≥40% 的句子在 s2 里找到相似句。"""
    f1, f2 = _split_facts(s1), _split_facts(s2)
    if not f1 or not f2:
        return False
    matched = sum(1 for a in f1 if any(jaccard(a, b) >= 0.15 for b in f2))
    return matched / len(f1) >= 0.4


async def judge_consistency(topic: str, s1: str, s2: str, llm) -> bool | None:
    """模型裁判：第三调用判断核心观点是否一致（允许表述不同/B 额外指出不确定点）。"""
    if llm is None:
        return None
    prompt = (
        f"关于「{topic}」的两段独立回答：\nA: {s1[:500]}\nB: {s2[:500]}\n"
        "判断两段的核心观点是否一致（允许表述不同，允许 B 额外指出不确定点）。"
        '只输出 JSON：{"consistent": true 或 false, "reason": 一句话}'
    )
    try:
        data = await asyncio.wait_for(
            llm.chat_json([{"role": "user", "content": prompt}],
                          temperature=0, max_tokens=2000),
            timeout=30)
        return bool(data.get("consistent"))
    except Exception as exc:
        log.warning("一致性裁判失败，回退启发式：%s", exc)
        return None


# ---------- 完整闭环 ----------

async def run_learning(topic: str, database: Database | None = None,
                       llm=None, query_fn=None) -> dict:
    """执行七步闭环，返回结果摘要。"""
    conn = (database or db).conn()
    conn.execute(
        "INSERT INTO learn_queue (topic, status, confidence, created_at)"
        " VALUES (?,?,?,?)", (topic, "processing", 0.3, _now()))
    conn.commit()

    async def ask(variant: int) -> str | None:
        if query_fn is not None:
            try:
                out = query_fn(topic, variant)
                if asyncio.iscoroutine(out):
                    out = await out
                return out
            except Exception as exc:
                log.warning("自定义查询失败：%s", exc)
                return None
        return await default_query(topic, variant, llm)

    # ②③ 双源查询（交叉验证原料）
    s1, s2 = await ask(1), await ask(2)
    if s1 is None or s2 is None:
        conn.execute(
            "UPDATE learn_queue SET status='failed' WHERE topic=? AND status='processing'",
            (topic,))
        conn.commit()
        audit("learn_failed", topic, "双源查询失败", database=database)
        return {"topic": topic, "status": "failed"}

    # ⑤ 交叉验证：模型裁判优先，启发式兜底
    heuristic = facts_consistent(s1, s2)
    judged = await judge_consistency(topic, s1, s2, llm)
    consistent = judged if judged is not None else heuristic
    log.info("一致性：裁判=%s 启发式=%s → %s", judged, heuristic, consistent)
    confidence = CONSISTENT_CONFIDENCE if consistent else CONFLICT_CONFIDENCE

    # ④ 存入认知网络：knowledge 节点 + source_of 边
    node = add_node("knowledge", f"knowledge:{topic}", {"confidence": confidence},
                    database=database)
    add_edge(node, "llm-source", "source_of", 1.0, database=database)
    # 矛盾 → contradicts 边
    if not consistent:
        alt = add_node("knowledge", f"knowledge-alt:{topic}",
                       {"confidence": 0.3}, database=database)
        add_edge(node, alt, "contradicts", 0.9, database=database)

    # 语义记忆落库（学习产出；自动入图钩子复用同一节点名，不重复建点）
    fact = s1[:300]
    MemoryStore(database).add_semantic(
        f"{topic}：{fact}", confidence=confidence, source="learning-loop",
        node_name=node)

    conn.execute(
        "UPDATE learn_queue SET status='done', confidence=?"
        " WHERE topic=? AND status='processing'", (confidence, topic))
    conn.commit()
    audit("learn_done", topic, f"双源{'一致' if consistent else '矛盾'}"
                              f" 置信度 {confidence}", database=database)
    log.info("学习完成：%s（%s，conf=%.2f）", topic,
             "一致" if consistent else "矛盾", confidence)
    return {"topic": topic, "status": "done", "consistent": consistent,
            "confidence": confidence, "fact": fact}


# ---------- ⑦ 定时回顾 ----------

def review_due(days: int = REVIEW_DAYS, database: Database | None = None) -> list[dict]:
    """>N 天未访问的 knowledge 节点 → 需要复习。"""
    conn = (database or db).conn()
    threshold = (datetime.now(timezone.utc).astimezone()
                 - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT name, last_access, activation_count FROM graph_nodes"
        " WHERE ntype='knowledge' AND (last_access IS NULL OR last_access < ?)"
        " ORDER BY activation_count", (threshold,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_reviewed(name: str, database: Database | None = None) -> None:
    conn = (database or db).conn()
    conn.execute(
        "UPDATE graph_nodes SET last_access=?, activation_count=activation_count+1"
        " WHERE name=?", (_now(), name))
    conn.commit()
