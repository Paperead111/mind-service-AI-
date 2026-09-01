# -*- coding: utf-8 -*-
"""E · 人格经验提案（ash「personality updates from experiences」移植，
守她的治理底线：提案 → 用户确认 → 生效；可回滚；永不悄悄改人格）。

触发（零 LLM 规则）：近 24h 内用户纠正词（太啰嗦/太短/像客服/太正式…）
出现 ≥3 次且同标签 → 生成一条 voice 规则调整提案（LLM 起草 proposed 值）。
确认 = 修改 data/persona/<id>/voice/base.yaml（改前备份 .bak-时间戳）并重载人格层；
拒绝 = 只标记；回滚 = 恢复最近备份。
只允许动 voice/base.yaml；identity.md 与原则永不触及。
"""
import re
import shutil
from datetime import datetime, timedelta, timezone

import yaml

from app.config import PERSONA_DIR, settings
from app.db import Database, db
from app.logging_setup import get_logger, log_event

log = get_logger("identity.proposals")

CORRECTION_TAGS = {
    "太啰嗦": ["太啰嗦", "啰嗦", "说重点", "太长了", "少说点", "别绕"],
    "太短": ["太短", "敷衍", "多说点", "展开", "太简短"],
    "像客服": ["像客服", "客服腔", "太客气", "太官方", "机器人"],
    "太正式": ["太正式", "文绉绉", "太书面"],
    "太冷": ["太冷", "冷漠", "不关心我", "没有温度"],
}
CORRECT_WINDOW_HOURS = 24
CORRECT_MIN_N = 3


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _scan_corrections(database: Database | None = None) -> dict[str, list[str]]:
    """近 24h 用户纠正词按标签聚类。"""
    dbx = database or db
    cutoff = (datetime.now(timezone.utc).astimezone()
              - timedelta(hours=CORRECT_WINDOW_HOURS)).isoformat(timespec="seconds")
    rows = dbx.conn().execute(
        "SELECT content FROM conversations WHERE role='user' AND ts >= ?", (cutoff,)
    ).fetchall()
    hits: dict[str, list[str]] = {}
    for r in rows:
        text = r["content"] or ""
        for tag, markers in CORRECTION_TAGS.items():
            if any(m in text for m in markers):
                hits.setdefault(tag, []).append(text[:60])
    return {tag: ev for tag, ev in hits.items() if len(ev) >= CORRECT_MIN_N}


def due(database: Database | None = None) -> tuple[bool, str, list[str]]:
    """是否有值得提案的纠正模式。"""
    if not settings.persona_proposals_enabled:
        return False, "disabled", []
    tags = _scan_corrections(database)
    if not tags:
        return False, "", []
    # 同一标签最近已有一张未决提案 → 不重复提
    pending = (database or db).conn().execute(
        "SELECT target FROM personality_proposals WHERE status='pending'"
    ).fetchall()
    pending_tags = {p["target"] for p in pending}
    fresh = {t: ev for t, ev in tags.items() if t not in pending_tags}
    if not fresh:
        return False, "", []
    tag = max(fresh, key=lambda t: len(fresh[t]))
    return True, tag, fresh[tag]


async def maybe_propose(database: Database | None = None, llm=None) -> dict | None:
    """触发判定 + LLM 起草提案（不生效）。"""
    dbx = database or db
    ok, tag, evidence = due(dbx)
    if not ok:
        return None
    proposed = None
    if llm is not None:
        try:
            content = await llm.chat_json(
                [{"role": "system", "content":
                  '只输出 JSON：{"rule": 新增的一条说话规则（≤30字，口语），'
                  '"reason": 为什么（≤30字）}'},
                 {"role": "user", "content":
                  f"她最近 24 小时被多次说「{tag}」（证据：{'；'.join(evidence[:3])}）。"
                  "为她的说话规则新增一条，直接改善这个问题。"}],
                temperature=0.3, max_tokens=400)
            proposed = str(content.get("rule", "")).strip()
            reason = str(content.get("reason", "")).strip()
        except Exception as exc:
            log.warning("提案起草失败，回退模板：%s", exc)
            proposed, reason = "", ""
    if not proposed:
        proposed = "被说「" + tag + "」时，先认领再调整，不辩解"
        reason = "规则回退模板（LLM 起草失败）"
    cur = dbx.conn().execute(
        "SELECT id FROM personality_proposals WHERE status='pending' AND target=?",
        (tag,)).fetchone()
    if cur:
        return None
    dbx.conn().execute(
        "INSERT INTO personality_proposals (target, field, current, proposed, reason,"
        " evidence, created_at) VALUES (?,?,?,?,?,?,?)",
        (tag, "voice.tone_rules", "（无对应规则）", proposed, reason,
         str(evidence[:5]), _now()))
    dbx.conn().commit()
    log_event("persona_proposal", tag=tag, proposed=proposed,
              msg=f"人格经验提案（待确认）：{tag} → {proposed}")
    return {"target": tag, "proposed": proposed, "reason": reason,
            "evidence": evidence[:5]}


def list_proposals(database: Database | None = None) -> list[dict]:
    rows = (database or db).conn().execute(
        "SELECT * FROM personality_proposals ORDER BY id DESC LIMIT 20").fetchall()
    return [dict(r) for r in rows]


def _base_yaml_path(persona_id: str | None = None) -> object:
    from app.config import settings as _s
    return PERSONA_DIR / (persona_id or _s.persona_id) / "voice" / "base.yaml"


def _apply_to_yaml(new_rule: str, persona_id: str | None = None) -> str:
    """把新规则追加进 tone_rules；先备份。返回备份路径。"""
    path = _base_yaml_path(persona_id)
    backup = path.with_name(f"base.yaml.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(path, backup)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = list(data.get("tone_rules") or [])
    if new_rule not in rules:
        rules.append(new_rule)
        data["tone_rules"] = rules
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    return str(backup)


def confirm(proposal_id: int, database: Database | None = None,
            persona_layer=None) -> dict:
    """用户确认：改 voice/base.yaml（备份）+ 重载人格层 + 提案置 confirmed。"""
    dbx = database or db
    row = dbx.conn().execute(
        "SELECT * FROM personality_proposals WHERE id=? AND status='pending'",
        (proposal_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "提案不存在或已处理"}
    try:
        backup = _apply_to_yaml(row["proposed"])
    except Exception as exc:
        log.exception("人格规则写入失败")
        return {"ok": False, "error": f"写入失败：{exc}"}
    dbx.conn().execute(
        "UPDATE personality_proposals SET status='confirmed', decided_at=? WHERE id=?",
        (_now(), proposal_id))
    dbx.conn().commit()
    if persona_layer is not None:
        try:
            persona_layer.reload()
        except Exception:
            log.exception("人格层重载失败（规则已写入，重启后生效）")
    log_event("persona_proposal_confirmed", proposal_id=proposal_id,
              backup=backup, msg="人格规则已生效（备份可回滚）")
    return {"ok": True, "backup": backup}


def reject(proposal_id: int, database: Database | None = None) -> dict:
    dbx = database or db
    dbx.conn().execute(
        "UPDATE personality_proposals SET status='rejected', decided_at=? WHERE id=?"
        " AND status='pending'", (_now(), proposal_id))
    dbx.conn().commit()
    return {"ok": True}


def rollback(proposal_id: int, database: Database | None = None,
             persona_layer=None) -> dict:
    """回滚：恢复最近一次备份，提案置 rolled_back。"""
    dbx = database or db
    row = dbx.conn().execute(
        "SELECT * FROM personality_proposals WHERE id=? AND status='confirmed'",
        (proposal_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "该提案未确认或已回滚"}
    path = _base_yaml_path()
    backups = sorted(path.parent.glob("base.yaml.bak-*"), reverse=True)
    if not backups:
        return {"ok": False, "error": "没有备份可回滚"}
    try:
        shutil.copy2(backups[0], path)
    except Exception as exc:
        return {"ok": False, "error": f"回滚失败：{exc}"}
    dbx.conn().execute(
        "UPDATE personality_proposals SET status='rolled_back', decided_at=? WHERE id=?",
        (_now(), proposal_id))
    dbx.conn().commit()
    if persona_layer is not None:
        try:
            persona_layer.reload()
        except Exception:
            log.exception("人格层重载失败（文件已回滚，重启后生效）")
    log_event("persona_proposal_rolled_back", proposal_id=proposal_id,
              restored=str(backups[0]), msg="人格规则已回滚到备份")
    return {"ok": True, "restored": str(backups[0])}
