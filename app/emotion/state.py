"""情绪系统（分离版）：感知用户情绪 与 自身情绪状态 彻底分离。

- detect：规则检测（关键词+强度词）——只产出「用户感知情绪」
- perceive：① 把用户情绪写入 user_perceived_*（只观察）② 自身情绪按动力学演化
- 自身情绪动力学（内部状态 + 外部刺激）：
    Δself_valence = α·(perceived − self)·g(budget, p_self) + β·(baseline − self)
    g = budget×p_self：自身状态越差，对外部情绪的跟随越弱；β 拉回人格基线；
    单次变化限幅（惯性/阻尼，二阶思想）
- modulation：自身情绪 → 认知参数（门控/风险/注意力只看自己，不看用户）
- expression_style：按 感知vs自身 差异给出表达风格枚举（共情/关切/警觉/低落/中性）
- 闪光灯：强度>80 仍按用户情绪固化（那是对方的时刻，不是她的）
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

# ---------- 自身情绪动力学参数（分离版） ----------
ALPHA_FOLLOW = 0.25      # 对外部情绪（感知结果）的跟随系数
BETA_BASELINE = 0.05     # 向人格基线回归的拉力
BASELINE_VALENCE = 0.15  # 人格基线：默认温暖、偏正、平静
BASELINE_AROUSAL = 0.4
MAX_SELF_DELTA = 0.12    # 惯性/阻尼：单次自身情绪变化速度上限

# 安抚响应映射（8 情绪全覆盖）
SOOTHE = {"sadness": "陪伴：在就行，不急着讲道理",
          "fear": "安全感：简洁在场，不追问",
          "anger": "被听见：先承认情绪，再谈事情",
          "disgust": "不评判：给空间，不替对方定性",
          "joy": "陪笑：一起庆祝，不扫兴",
          "surprise": "一起消化：先接住再回应",
          "anticipation": "轻推一把：别让它凉了",
          "trust": "接住：别消耗这份信任"}

# 表达层句法提示（叠加在人格风格上；基于自身情绪）
EXPRESSION = {"fear": "短句+省略号", "joy": "长句+感叹", "sadness": "短+停顿",
              "anger": "按强度分层（平静指出/立场直说/需要空间/事后复盘）",
              "surprise": "先停顿再回应", "disgust": "保持距离感，不评价",
              "anticipation": "语气上扬", "trust": "放松、直接"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def detect(text: str) -> dict:
    """规则检测：返回 {emotion(英文键), emotion_cn, intensity, valence, arousal}。
    这是「用户感知情绪」的检测，不写入自身状态。"""
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


def expression_style(perceived_v: float, perceived_intensity: float,
                     self_v: float, self_a: float) -> str:
    """表达风格枚举（结构化，非话术）：按 感知vs自身 差异规则生成。"""
    if perceived_v < -0.4 and self_v >= -0.2:
        return "concerned_support"   # 对方低落、自己稳定 → 关切支持，不代入
    if perceived_intensity >= 60 and abs(perceived_v - self_v) < 0.3:
        return "empathic"            # 同频共情
    if self_a > 0.8:
        return "alert"               # 自己警觉
    if self_v < -0.3:
        return "subdued"             # 自己低落克制
    return "neutral_support"


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

    # ---------- 感知 + 自身动力学 ----------

    def perceive(self, text: str, detected: dict | None = None) -> dict:
        """接收一句话：检测（用户感知）→ 写感知层 → 自身情绪按动力学演化。

        detected：R8 判断层（LLM）给出的检测结果（同一轮情绪只判一次）。
        返回：感知结果 + 自身情绪 + 表达风格 + 调制/安抚。
        """
        d = detected or detect(text)
        if d["emotion"] is None:
            s = self.state()
            return {**d, "modulation": self.modulation(), "soothe": None,
                    "perceived": {"valence": s["user_perceived_valence"],
                                  "arousal": s["user_perceived_arousal"],
                                  "emotion_cn": None},
                    "self_emotion": {"valence": s["valence"],
                                     "arousal": s["arousal"],
                                     "dominant": s["dominant"]},
                    "expression_style": expression_style(
                        s["user_perceived_valence"], 0, s["valence"], s["arousal"])}
        self.update(d["emotion"], d["intensity"], d["valence"], d["arousal"])
        # 闪光灯：强度>80 → 固化情绪记忆（那是对方的时刻，记下来，不等于变成她的）
        if d["intensity"] >= 80:
            from app.memory.store import MemoryStore
            MemoryStore(self.db).add_emotional(
                text[:60], d["emotion_cn"], d["intensity"])
        soothe = self._soothe_check()
        s = self.state()
        return {**d, "modulation": self.modulation(), "soothe": soothe,
                "perceived": {"valence": s["user_perceived_valence"],
                              "arousal": s["user_perceived_arousal"],
                              "emotion_cn": d["emotion_cn"]},
                "self_emotion": {"valence": s["valence"],
                                 "arousal": s["arousal"],
                                 "dominant": s["dominant"]},
                "expression_style": expression_style(
                    s["user_perceived_valence"], d["intensity"],
                    s["valence"], s["arousal"]),
                "state": {k: round(v, 3) for k, v in s.items() if k in EMOTIONS}}

    def perceive_frozen(self, text: str) -> dict:
        """盲区三：降级（L1b/L2）期间只检测不更新——感知与自身都不写。"""
        d = detect(text)
        return {**d, "modulation": self.modulation(), "soothe": None,
                "frozen": True}

    def update(self, emotion: str, intensity: float, valence: float,
               arousal: float) -> dict:
        """自身情绪动力学（内部状态 + 外部刺激）。

        Δself = α·(perceived − self)·g(budget,p_self) + β·(baseline − self)
        限幅 MAX_SELF_DELTA（惯性）。用户情绪只作输入，不覆盖自身。
        """
        conn = self.db.conn()
        s = self.state()
        g = self._coupling_gain()
        # 感知层：平滑记录用户情绪（只观察）
        perceived_v = round(max(-1.0, min(1.0, 0.5 * s["user_perceived_valence"] + 0.5 * valence)), 3)
        perceived_a = round(max(0.0, min(1.0, 0.5 * s["user_perceived_arousal"] + 0.5 * arousal)), 3)
        # 自身层：动力学
        self_v = s["valence"] + ALPHA_FOLLOW * g * (perceived_v - s["valence"]) \
            + BETA_BASELINE * (BASELINE_VALENCE - s["valence"])
        self_a = s["arousal"] + ALPHA_FOLLOW * g * (perceived_a - s["arousal"]) \
            + BETA_BASELINE * (BASELINE_AROUSAL - s["arousal"])
        self_v = self._limit_delta(s["valence"], self_v)
        self_a = self._limit_delta(s["arousal"], self_a)
        self_v = round(max(-1.0, min(1.0, self_v)), 3)
        self_a = round(max(0.0, min(1.0, self_a)), 3)
        # 8 维：向感知情绪靠拢（0.6 混合自带惯性；8 维是她的真实体验，
        # 不受 budget 门控——门控只作用于 valence/arousal 的跟随幅度）
        target = min(1.0, intensity / 100)
        new_val = round(s[emotion] + (target - s[emotion]) * 0.6, 3)
        conn.execute(
            f"UPDATE emotion_state SET {emotion}=?, valence=?, arousal=?,"
            " user_perceived_valence=?, user_perceived_arousal=?, updated_at=? WHERE id=1",
            (new_val, self_v, self_a, perceived_v, perceived_a, _now()))
        conn.commit()
        self._refresh_dominant()
        return self.state()

    def _coupling_gain(self) -> float:
        """g(budget, p_self)：自身状态不佳时对外部情绪的跟随能力减弱。"""
        try:
            h = self.db.conn().execute(
                "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()
            m = self.db.conn().execute(
                "SELECT p_self FROM self_model WHERE id=1").fetchone()
            budget = float(h["budget"]) if h else 0.7
            p_self = float(m["p_self"]) if m else 0.85
        except Exception:
            budget, p_self = 0.7, 0.85
        return round(max(0.0, min(1.0, budget * p_self)), 3)

    @staticmethod
    def _limit_delta(current: float, target: float, cap: float) -> float:
        delta = target - current
        if abs(delta) > cap:
            delta = cap if delta > 0 else -cap
        return current + delta

    def _refresh_dominant(self) -> None:
        conn = self.db.conn()
        s = self.state()
        vals = {k: s[k] for k in EMOTIONS}
        top = max(vals, key=vals.get)
        dominant = EMOTION_CN[top] if vals[top] > 0.15 else "平静"
        conn.execute("UPDATE emotion_state SET dominant=? WHERE id=1", (dominant,))
        conn.commit()

    # ---------- 衰减 ----------

    def decay(self, rounds: int = 1) -> dict:
        return self._decay_factor((1 - DECAY_RATE) ** rounds)

    def decay_seconds(self, elapsed_seconds: float) -> dict:
        """R1 tick 用：按实际经过秒数折算衰减轮数（绝对时间校准）。"""
        rounds = max(0.0, elapsed_seconds / 60.0)
        return self._decay_factor((1 - DECAY_RATE) ** rounds)

    def _decay_factor(self, factor: float) -> dict:
        """自身 8 维 ×factor；自身 valence/arousal 与感知层都向各自基线回归。"""
        conn = self.db.conn()
        s = self.state()
        sets = ", ".join(f"{k}=ROUND({k}*{factor},4)" for k in EMOTIONS)
        self_v = round(BASELINE_VALENCE + (s["valence"] - BASELINE_VALENCE) * factor, 4)
        self_a = round(BASELINE_AROUSAL + (s["arousal"] - BASELINE_AROUSAL) * factor, 4)
        per_v = round(s["user_perceived_valence"] * factor, 4)
        per_a = round(0.4 + (s["user_perceived_arousal"] - 0.4) * factor, 4)
        conn.execute(
            f"UPDATE emotion_state SET {sets}, valence=?, arousal=?,"
            " user_perceived_valence=?, user_perceived_arousal=?, updated_at=? WHERE id=1",
            (self_v, self_a, per_v, per_a, _now()))
        conn.commit()
        self._refresh_dominant()
        return self.state()

    # ---------- 自身情绪 → 认知参数（只看自己） ----------

    def modulation(self) -> dict:
        """自身情绪真实影响决策参数；用户情绪不参与（分离的关键）。"""
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
        """连续 3 轮用户负面情绪 → 返回安抚映射（基于感知，作用于表达）。"""
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
