"""自动入图钩子：新知识/事件/情绪出现 → 自动写认知网络（不靠自觉）。

- 语义记忆 → knowledge 节点 + source_of 边
- 情绪记忆 → emotion 节点 + event 节点 + experienced/triggered 边
- 重要决策（拒绝/争取）→ event 节点 + realized 边
"""
from app.cognition.audit import audit
from app.cognition.network import add_edge, add_node
from app.db import Database, db


def ensure_person_node(database=None):
    add_node("person", "person:user", {}, database=database)
    return "person:user"


def hook_semantic(fact: str, confidence: float,
                  database: Database | None = None,
                  node_name: str | None = None) -> None:
    person = ensure_person_node(database)
    node = add_node("knowledge", node_name or f"knowledge:{fact[:40]}",
                    {"confidence": confidence}, database=database)
    add_edge(person, node, "source_of", 1.0, database=database)
    audit("graph_auto", node, f"语义记忆入图 conf={confidence}", database=database)


def hook_emotional(event: str, emotion: str, intensity: float,
                   database: Database | None = None) -> None:
    person = ensure_person_node(database)
    e_node = add_node("emotion", f"情绪:{emotion}", {}, database=database)
    ev_node = add_node("event", f"事件:{event[:30]}",
                       {"intensity": intensity}, database=database)
    add_edge(ev_node, person, "experienced", 1.0, database=database)
    add_edge(ev_node, e_node, "triggered", 1.0, database=database)
    audit("graph_auto", ev_node, f"情绪事件入图 {emotion} {intensity}",
          database=database)


def hook_decision(action: str, reason: str, database: Database | None = None) -> None:
    if action not in ("REFUSE", "CONTEST"):
        return
    ev_node = add_node("event", f"决策:{action}:{reason[:20]}", {},
                       database=database)
    add_edge(ev_node, "concept:will", "realized", 0.9, database=database)
    add_node("concept", "concept:will", {}, database=database)
    audit("graph_auto", ev_node, f"决策入图 {action}", database=database)
