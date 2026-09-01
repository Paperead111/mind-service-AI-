"""认知边界映射：我知道什么 / 知道一半 / 不知道。

- detect_domain：规则领域检测（回答前查表用）
- 更新规则：答对→置信升；被纠正→置信降并记录正确版本
"""
from datetime import datetime, timezone

from app.db import Database, db

DOMAIN_KEYWORDS = {
    "编程": ("python", "代码", "编程", "bug", "接口", "函数"),
    "医学": ("生病", "症状", "吃药", "医生", "检查"),
    "法律": ("法律", "合同", "起诉", "仲裁"),
    "金融": ("股票", "基金", "理财", "投资", "币"),
    "游戏": ("原神", "游戏", "攻略", "角色"),
}

CONFIDENCE_ORDER = {"unknown": 0, "partial": 1, "known": 2}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def detect_domain(text: str) -> str:
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(k in text for k in keywords):
            return domain
    return "general"


def lookup(domain: str, database: Database | None = None) -> dict | None:
    row = (database or db).conn().execute(
        "SELECT * FROM boundaries WHERE domain=?", (domain,)
    ).fetchone()
    return dict(row) if row else None


def boundary_check(text: str, database: Database | None = None) -> dict:
    """回答前自查：返回 {domain, confidence, rule}。"""
    domain = detect_domain(text)
    row = lookup(domain, database)
    if row is None:
        return {"domain": domain, "confidence": "unknown",
                "rule": "不知道就说不知道，并触发学习"}
    conf = row["confidence"]
    if conf == "unknown":
        return {"domain": domain, "confidence": "unknown",
                "rule": "不知道就说不知道，并触发学习"}
    if conf == "partial":
        return {"domain": domain, "confidence": "partial",
                "rule": "标注'我在猜'，说明不确定的部分"}
    return {"domain": domain, "confidence": "known", "rule": "可以回答"}


def answered_correctly(domain: str, database: Database | None = None) -> None:
    """答对：证据 +1，置信升一档。"""
    conn = (database or db).conn()
    row = lookup(domain, database)
    if row is None:
        conn.execute(
            "INSERT INTO boundaries (domain, confidence, evidence_count, updated_at)"
            " VALUES (?,?,?,?)", (domain, "partial", 1, _now()))
    else:
        new_conf = "known" if row["confidence"] == "partial" else row["confidence"]
        conn.execute(
            "UPDATE boundaries SET confidence=?, evidence_count=evidence_count+1,"
            " updated_at=? WHERE domain=?",
            (new_conf, _now(), domain))
    conn.commit()


def corrected(domain: str, correct_version: str,
              database: Database | None = None) -> None:
    """被打脸/被纠正：置信降档并记录正确版本。"""
    conn = (database or db).conn()
    row = lookup(domain, database)
    if row is None:
        conn.execute(
            "INSERT INTO boundaries (domain, confidence, evidence_count,"
            " correct_version, updated_at) VALUES (?,?,?,?,?)",
            (domain, "partial", 1, correct_version, _now()))
    else:
        new_conf = "partial" if row["confidence"] == "known" else "unknown"
        conn.execute(
            "UPDATE boundaries SET confidence=?, correct_version=?,"
            " evidence_count=evidence_count+1, updated_at=? WHERE domain=?",
            (new_conf, correct_version, _now(), domain))
    conn.commit()


def list_all(database: Database | None = None) -> list[dict]:
    rows = (database or db).conn().execute(
        "SELECT * FROM boundaries ORDER BY domain").fetchall()
    return [dict(r) for r in rows]
