"""情绪系统：8 种基本情绪（喜悦/悲伤/愤怒/恐惧/惊讶/厌恶/期待/信任）状态机。

- detect：规则检测（关键词+强度词），LLM 精化可选（默认关，保持聊天轻快）
- update：状态混合（向新情绪靠拢），主导情绪 = argmax
- decay：每轮向基线衰减（decay_rate 0.15）
- modulation：情绪真实影响决策参数（注意力容量/风险惩罚/行为倾向）
- soothe：连续 3 轮负面 → 安抚响应映射（8 情绪全覆盖）
- flashbulb：强度>80 → 固化闪光灯记忆（MemoryStore 已带自动入图钩子）
"""
import re
from datetime import datetime, timezone

from app.db import Database, db
from app.logging_setup import get_logger

log = get_logger("emotion")

EMOTIONS = ("joy", "sadness", "anger", "fear", "surprise", "disgust",
            "anticipation", "trust")
EMOTION_CN = {"joy": "喜悦", "sadness": "悲伤", "anger": "愤怒", "fear": "恐惧",
              "surprise": "惊讶", "disgust": "厌恶", "anticipation": "期待",
              "trust": "信任"}
NEGATIVE = ("sadness", "anger", "fear", "disgust")

KEYWORDS = {
    "joy": ["开心", "高兴", "快乐", "兴奋", "太棒", "哈哈", "嘻嘻", "爽"],
    "sadness": ["难过", "伤心", "悲伤", "哭", "低落", "失落", "沮丧"],
    "anger": ["生气", "愤怒", "气死", "烦", "火大", "恼火"],
    "fear": ["害怕", "恐惧", "担心", "怕", "焦虑", "不安"],
    "surprise": ["惊讶", "没想到", "居然", "天哪", "震惊"],
    "disgust": ["恶心", "厌恶", "反感", "嫌弃"],
    "anticipation": ["期待", "盼望", "等不及", "想试试", "憧憬"],
    "trust": ["相信你", "信任", "靠谱", "放心"],
}
INTENSIFIERS = ("特别", "非常", "太", "超级", "好", "真", "死了", "极了", "要命")

VALENCE = {"joy": 0.8, "anticipation": 0.5, "trust": 0.6, "surprise": 0.1,
           "sadness": -0.6, "anger": -0.7, "fear": -0.7, "disgust": -0.6}
AROUSAL = {"joy": 0.7, "anger": 0.9, "fear": 0.8, "surprise": 0.9,
           "anticipation": 0.6, "sadness": 0.3, "disgust": 0.5, "trust": 0.2}

DECAY_RATE = 0.15

# 安抚响应映射（8 情绪全覆盖）
SOOTHE = {"sadness": "陪伴：在就行，不急着讲道理",
          "fear": "安全感：简洁在场，不追问",
          "anger": "被听见：先承认情绪，再谈事情",
          "disgust": "不评判：给空间，不替对方定性",
          "joy": "陪笑：一起庆祝，不扫兴",
          "surprise": "一起消化：先接住再回应",
          "anticipation": "轻推一把：别让它凉了",
          "trust": "接住：别消耗这份信任"}

# 表达层句法提示（叠加在人格风格上）
EXPRESSION = {"fear": "短句+省略号", "joy": "长句+感叹", "sadness": "短+停顿",
              "anger": "按强度分层（平静指出/立场直说/需要空间/事后复盘）",
              "surprise": "先停顿再回应", "disgust": "保持距离感，不评价",
              "anticipation": "语气上扬", "trust": "放松、直接"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def detect(text: str) -> dict:
    """规则检测：返回 {emotion(英文键), emotion_cn, intensity, valence, arousal}。"""
    scores: dict[str, int] = {}
    for key, words in KEYWORDS.items():
        hits = sum(text.count(w) for w in words)
        if hits:
            scores[key] = hits
    if not scores:
        return {"emotion": None, "emotion_cn": None, "intensity": 0,
                "valence": 0.0, "arousal": 0.4}
    top = max(scores, key=scores.get)
    intens = sum(text.count(w) for w in INTENSIFIERS) + (1 if "!" in text or "！" in text else 0)
    intensity = min(95.0, 55.0 + 15 * (scores[top] - 1) + 10 * intens)
    return {"emotion": top, "emotion_cn": EMOTION_CN[top], "intensity": intensity,
            "valence": VALENCE[top], "arousal": AROUSAL[top]}


class EmotionSystem:
    def __init__(self, database: Database | None = None):
        self.db = database or db

    def state(self) -> dict:
        conn = self.db.conn()
        row = conn.execute("SELECT * FROM emotion_state WHERE id=1").fetchone()
        if row is None:
            conn.execute("INSERT INTO emotion_state (id, updated_at) VALUES (1, ?)",
                         (_now(),))
            conn.commit()
            row = conn.execute("SELECT * FROM emotion_state WHERE id=1").fetchone()
        return dict(row)

    def perceive(self, text: str, detected: dict | None = None) -> dict:
        """接收一句话：检测 → 更新状态 → 输出感知结果（含调制与安抚）。

        detected：R8 判断层（LLM）给出的检测结果，跳过规则检测（同一轮情绪只判一次）。
        """
        d = detected or detect(text)
        if d["emotion"] is None:
            return {**d, "modulation": self.modulation(), "soothe": None}
        self.update(d["emotion"], d["intensity"], d["valence"], d["arousal"])
        # 闪光灯：强度>80 → 固化情绪记忆（自动入图）
        if d["intensity"] >= 80:
            from app.memory.store import MemoryStore
            MemoryStore(self.db).add_emotional(
                text[:60], d["emotion_cn"], d["intensity"])
        soothe = self._soothe_check()
        return {**d, "modulation": self.modulation(), "soothe": soothe,
                "state": {k: round(v, 3) for k, v in self.state().items()
                          if k in EMOTIONS}}

    def perceive_frozen(self, text: str) -> dict:
        """盲区三：降级（L1b/L2）期间只检测不更新——情绪感知/主观更新/冲突计数冻结。"""
        d = detect(text)
        return {**d, "modulation": self.modulation(), "soothe": None,
                "frozen": True}

    def update(self, emotion: str, intensity: float, valence: float,
               arousal: float) -> dict:
        conn = self.db.conn()
        s = self.state()
        target = min(1.0, intensity / 100)
        new_val = round(s[emotion] + (target - s[emotion]) * 0.6, 3)
        new_valence = round(max(-1.0, min(1.0, s["valence"] * 0.7 + valence * 0.3)), 3)
        new_arousal = round(max(0.0, min(1.0, s["arousal"] * 0.7 + arousal * 0.3)), 3)
        conn.execute(
            f"UPDATE emotion_state SET {emotion}=?, valence=?, arousal=?, updated_at=? WHERE id=1",
            (new_val, new_valence, new_arousal, _now()))
        conn.commit()
        self._refresh_dominant()
        return self.state()

    def _refresh_dominant(self) -> None:
        conn = self.db.conn()
        s = self.state()
        vals = {k: s[k] for k in EMOTIONS}
        top = max(vals, key=vals.get)
        dominant = EMOTION_CN[top] if vals[top] > 0.15 else "平静"
        conn.execute("UPDATE emotion_state SET dominant=? WHERE id=1", (dominant,))
        conn.commit()

    def decay(self, rounds: int = 1) -> dict:
        """情绪衰减：每轮向基线回落 DECAY_RATE（愤怒慢、惊讶快的细节在 P8 调参）。"""
        return self._decay_factor((1 - DECAY_RATE) ** rounds)

    def decay_seconds(self, elapsed_seconds: float) -> dict:
        """R1 tick 用：按实际经过秒数折算衰减轮数（绝对时间校准）。"""
        rounds = max(0.0, elapsed_seconds / 60.0)
        return self._decay_factor((1 - DECAY_RATE) ** rounds)

    def _decay_factor(self, factor: float) -> dict:
        conn = self.db.conn()
        sets = ", ".join(f"{k}=ROUND({k}*{factor},4)" for k in EMOTIONS)
        conn.execute(f"UPDATE emotion_state SET {sets}, updated_at=? WHERE id=1", (_now(),))
        conn.commit()
        self._refresh_dominant()
        return self.state()

    def modulation(self) -> dict:
        """情绪 → 认知参数（情绪真实影响决策，可观测）。"""
        s = self.state()
        f, t = s["fear"], s["trust"]
        return {
            "foa_capacity": max(2.0, 4.0 - 2 * f) + min(2.0, round(2 * t)),
            "risk_multiplier": round((1 + 2 * f) * (1 - 0.8 * t), 3),
            "attention": "收缩至威胁相关" if f > 0.5 else (
                "拓宽联想" if s["joy"] > 0.5 else "常态"),
            "meta_freeze": f > 0.8,
            "simplify": s["anger"] > 0.5,
            "deep_analysis": s["sadness"] > 0.5,
            "dominant": s["dominant"],
            "expression": EXPRESSION.get(
                max(((k, s[k]) for k in EMOTIONS), key=lambda x: x[1])[0], "常态"),
        }

    def _soothe_check(self) -> str | None:
        """连续 3 轮用户负面情绪 → 返回安抚映射。"""
        rows = self.db.conn().execute(
            "SELECT emotion FROM conversations WHERE role='user' AND emotion IS NOT NULL"
            " ORDER BY ts DESC LIMIT 3").fetchall()
        if len(rows) < 3:
            return None
        emo_cn = {EMOTION_CN[k]: k for k in EMOTIONS}
        last = [emo_cn.get(r["emotion"]) for r in rows]
        if all(e in NEGATIVE for e in last if e):
            neg = [e for e in last if e][0]
            return f"{EMOTION_CN[neg]}：{SOOTHE[neg]}"
        return None
