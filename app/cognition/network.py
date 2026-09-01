"""认知网络（存在图谱）：节点 + 边 + 深度 2 扩散检索。

节点 7 类：file / skill / person / concept / event / emotion / knowledge
边 15 种：contains / created / defines / implements / experienced / triggered /
          refines / feeds / complements / part_of / related_to / source_of /
          contradicts / realized / manifests
"""
import json
from datetime import datetime, timezone

from app.db import Database, db
from app.logging_setup import get_logger

log = get_logger("cognition")

NODE_TYPES = ("file", "skill", "person", "concept", "event", "emotion", "knowledge")
EDGE_RELATIONS = ("contains", "created", "defines", "implements", "experienced",
                  "triggered", "refines", "feeds", "complements", "part_of",
                  "related_to", "source_of", "contradicts", "realized", "manifests")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def add_node(ntype: str, name: str, meta: dict | None = None,
             database: Database | None = None) -> str:
    if ntype not in NODE_TYPES:
        raise ValueError(f"未知节点类型：{ntype}")
    conn = (database or db).conn()
    conn.execute(
        "INSERT OR IGNORE INTO graph_nodes (ntype, name, meta, created_at)"
        " VALUES (?,?,?,?)",
        (ntype, name, json.dumps(meta or {}, ensure_ascii=False), _now()),
    )
    conn.commit()
    return name


def add_edge(src: str, dst: str, relation: str, weight: float = 1.0,
             database: Database | None = None) -> None:
    if relation not in EDGE_RELATIONS:
        raise ValueError(f"未知边关系：{relation}")
    conn = (database or db).conn()
    conn.execute(
        "INSERT OR IGNORE INTO graph_edges (src, dst, relation, weight, created_at)"
        " VALUES (?,?,?,?,?)",
        (src, dst, relation, weight, _now()),
    )
    conn.commit()


def neighbors(name: str, depth: int = 2, database: Database | None = None) -> dict:
    """沿边扩散检索：深度默认 2（邻居的邻居），按权重降序返回。"""
    conn = (database or db).conn()
    current = {name}
    seen = set()
    collected: list[dict] = []
    for d in range(depth):
        frontier: set[str] = set()
        for n in current:
            if n in seen:
                continue
            seen.add(n)
            rows = conn.execute(
                "SELECT src, dst, relation, weight FROM graph_edges"
                " WHERE src=? OR dst=?", (n, n),
            ).fetchall()
            for r in rows:
                other = r["dst"] if r["src"] == n else r["src"]
                if other in seen:
                    continue  # 已访问节点不回灌
                collected.append({"from": n, "to": other,
                                  "relation": r["relation"], "weight": r["weight"],
                                  "depth": d + 1})
                frontier.add(other)
        current = frontier
    collected.sort(key=lambda x: (-x["depth"], -x["weight"]))
    # 更新访问痕迹（复习闭环用）
    now = _now()
    for c in collected:
        conn.execute(
            "UPDATE graph_nodes SET last_access=?, activation_count=activation_count+1"
            " WHERE name=?", (now, c["to"]),
        )
    conn.commit()
    return {"root": name, "neighbors": collected[:50]}


def stats(database: Database | None = None) -> dict:
    conn = (database or db).conn()
    nodes = conn.execute(
        "SELECT ntype, COUNT(*) c FROM graph_nodes GROUP BY ntype").fetchall()
    edges = conn.execute(
        "SELECT COUNT(*) c FROM graph_edges").fetchone()["c"]
    total = conn.execute("SELECT COUNT(*) c FROM graph_nodes").fetchone()["c"]
    return {"nodes": total, "edges": edges,
            "by_type": {r["ntype"]: r["c"] for r in nodes}}


# ---------- R4 活图：tick 钩子（循环生长 + 联想触发） ----------

def grow_tick(elapsed_seconds: float, database: Database | None = None) -> dict:
    """活图 tick：边权重缓慢衰减 + 高困惑边的邻居联想触发（激活痕迹留档）。

    衰减：weight × 0.995^(分钟数)，下限 0.05（不删边，符合永不删除原则）。
    联想触发：top 高 pred_error 边的邻居 activation_count+1。
    """
    dbx = database or db
    conn = dbx.conn()
    factor = 0.995 ** max(0.0, elapsed_seconds / 60.0)
    conn.execute(
        "UPDATE graph_edges SET weight=MAX(0.05, ROUND(weight*?, 4))", (factor,))
    # 高困惑边（pred_error>0.5）触发邻居联想
    hot = conn.execute(
        "SELECT id, src, dst FROM graph_edges WHERE pred_error > 0.5 LIMIT 2"
    ).fetchall()
    touched = 0
    for edge in hot:
        for name in (edge["src"], edge["dst"]):
            rows = conn.execute(
                "SELECT src, dst FROM graph_edges WHERE (src=? OR dst=?) LIMIT 3",
                (name, name)).fetchall()
            for r in rows:
                other = r["dst"] if r["src"] == name else r["src"]
                conn.execute(
                    "UPDATE graph_nodes SET activation_count=activation_count+1,"
                    " last_access=? WHERE name=?", (_now(), other))
                touched += 1
    conn.commit()
    if hot:
        log.info("活图 tick：高困惑边 %d 条触发邻居联想 %d 次", len(hot), touched)
    return {"decay_factor": round(factor, 4), "hot_edges": len(hot),
            "associations": touched}
