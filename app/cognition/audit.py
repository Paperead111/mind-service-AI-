"""审计：所有学习/图写入/边界变更留痕（防漂移、可回放）。"""
from datetime import datetime, timezone

from app.db import Database, db


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def audit(action: str, target: str | None = None, detail: str = "",
          actor: str = "system", database: Database | None = None) -> None:
    (database or db).conn().execute(
        "INSERT INTO audit_log (actor, action, target, detail, ts)"
        " VALUES (?,?,?,?,?)",
        (actor, action, target, detail[:500], _now()),
    )
    (database or db).conn().commit()
