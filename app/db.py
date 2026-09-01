"""数据库层：SQLite + WAL，mind-service 的唯一持久化状态源。

原则：
- 每线程一个连接（本机单用户，读写量小，同步调用即可）
- 记忆相关表"永不删除"：不提供 DELETE，废弃只标 archived
"""
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import DB_PATH


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL DEFAULT 'local',
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  emotion TEXT,
  intensity REAL,
  topic TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodic_memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  summary TEXT,
  tags TEXT NOT NULL DEFAULT '[]',
  importance REAL NOT NULL DEFAULT 0.5,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_access TEXT
);

CREATE TABLE IF NOT EXISTS semantic_memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fact TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5,
  source TEXT,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emotional_memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL,
  emotion TEXT NOT NULL,
  intensity REAL NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS working_memory (
  slot INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 4),
  wm_type TEXT NOT NULL,
  content TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT '中',
  last_access TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL UNIQUE,
  ref TEXT NOT NULL,
  promote_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id TEXT,
  layer INTEGER NOT NULL,
  input_type TEXT,
  action TEXT,
  reason TEXT,
  hypotheses TEXT,
  chosen_g REAL,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drive_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  curiosity REAL NOT NULL DEFAULT 0.0,
  competence REAL NOT NULL DEFAULT 0.0,
  coherence REAL NOT NULL DEFAULT 0.0,
  efficiency REAL NOT NULL DEFAULT 0.0,
  social_approval REAL NOT NULL DEFAULT 0.5,
  reinforced TEXT NOT NULL DEFAULT '[]',
  suppressed TEXT NOT NULL DEFAULT '[]',
  danger_paths TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 3,
  progress REAL NOT NULL DEFAULT 0.0,
  max_tolerable_idle_hours REAL NOT NULL DEFAULT 24.0,
  last_progress_at TEXT,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger TEXT,
  action TEXT,
  response TEXT,
  engagement TEXT,
  note TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followup_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_reply_ref TEXT,
  outcome TEXT,
  content TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_outcomes (
  action TEXT PRIMARY KEY,
  accepted INTEGER NOT NULL DEFAULT 0,
  rejected INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS intention_patterns (
  pattern TEXT NOT NULL,
  intent TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (pattern, intent)
);

CREATE TABLE IF NOT EXISTS triggers (
  type TEXT PRIMARY KEY,
  conf REAL NOT NULL,
  base_conf REAL NOT NULL,
  priority TEXT NOT NULL DEFAULT '低',
  cooldown_until TEXT,
  last_fired TEXT,
  fire_count INTEGER NOT NULL DEFAULT 0,
  positive_streak INTEGER NOT NULL DEFAULT 0,
  negative_streak INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS proactive_sent (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  trigger_type TEXT,
  source_ref TEXT,
  conf REAL,
  message TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_type TEXT,
  source_ref TEXT,
  conf REAL,
  planned_message TEXT,
  review TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_deferred (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_type TEXT,
  source_ref TEXT,
  conf REAL,
  priority TEXT,
  resolved INTEGER NOT NULL DEFAULT 0,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ntype TEXT NOT NULL,
  name TEXT NOT NULL UNIQUE,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  last_access TEXT,
  activation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS graph_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  src TEXT NOT NULL,
  dst TEXT NOT NULL,
  relation TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL,
  UNIQUE(src, dst, relation)
);

CREATE TABLE IF NOT EXISTS boundaries (
  domain TEXT PRIMARY KEY,
  confidence TEXT NOT NULL DEFAULT 'unknown',
  evidence_count INTEGER NOT NULL DEFAULT 0,
  correct_version TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learn_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  confidence REAL NOT NULL DEFAULT 0.3,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL DEFAULT 'system',
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emotion_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  valence REAL NOT NULL DEFAULT 0.0,
  arousal REAL NOT NULL DEFAULT 0.5,
  dominance REAL NOT NULL DEFAULT 0.5,
  joy REAL NOT NULL DEFAULT 0.0,
  sadness REAL NOT NULL DEFAULT 0.0,
  anger REAL NOT NULL DEFAULT 0.0,
  fear REAL NOT NULL DEFAULT 0.0,
  surprise REAL NOT NULL DEFAULT 0.0,
  disgust REAL NOT NULL DEFAULT 0.0,
  anticipation REAL NOT NULL DEFAULT 0.0,
  trust REAL NOT NULL DEFAULT 0.5,
  dominant TEXT NOT NULL DEFAULT '平静',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL,
  session_id TEXT NOT NULL DEFAULT 'local',
  status TEXT NOT NULL DEFAULT 'pending',
  input TEXT NOT NULL,
  output TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS beliefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  strength REAL NOT NULL DEFAULT 0.9,
  evidence TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  supersedes INTEGER,
  created_at TEXT NOT NULL
);

-- ============ 重构新增（R0–R24）============

CREATE TABLE IF NOT EXISTS life_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tick INTEGER,
  event TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_life_log_ts ON life_log(ts);

CREATE TABLE IF NOT EXISTS capability_usage (
  capability TEXT PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS homeostatic_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  budget REAL NOT NULL DEFAULT 0.7,
  state_version INTEGER NOT NULL DEFAULT 1,
  last_tick_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS self_model (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  p_self REAL NOT NULL DEFAULT 0.85,
  velocity REAL NOT NULL DEFAULT 0.0,
  recovery_fade INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repetition_trace (
  rtype TEXT NOT NULL,
  pattern TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  last_at TEXT,
  r REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (rtype, pattern)
);

CREATE TABLE IF NOT EXISTS state_checkpoint (
  id INTEGER PRIMARY KEY,
  state_version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS life_log_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ============ ash 架构移植（A–E）============

CREATE TABLE IF NOT EXISTS conversation_summary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL DEFAULT 'local',
  summary TEXT NOT NULL,
  span_start TEXT,
  span_end TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thoughts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'flow',
  prep_g REAL,
  surfaced INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  surfaced_at TEXT
);

CREATE TABLE IF NOT EXISTS personality_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL,
  field TEXT,
  current TEXT,
  proposed TEXT,
  reason TEXT,
  evidence TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  decided_at TEXT
);
"""


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(str(self.path), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # R0 启动硬化：写同步显式固化（防 60s tick 被磁盘 fsync 拖长）
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            self._local.conn = conn
            self.init_schema()
        return self._local.conn

    def init_schema(self) -> None:
        conn = self.conn()
        conn.executescript(SCHEMA)
        self._migrate()
        self._seed()
        conn.commit()
        # R0：启动日志打印当前 PRAGMA 状态（验收 #2）
        from app.logging_setup import get_logger
        log = get_logger("db")
        log.info(
            "SQLite PRAGMA | journal_mode=%s synchronous=%s wal_autocheckpoint=%s | db=%s",
            conn.execute("PRAGMA journal_mode").fetchone()[0],
            conn.execute("PRAGMA synchronous").fetchone()[0],
            conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0],
            self.path,
        )

    def _migrate(self) -> None:
        """旧库平滑升级：为既有表补新列（v4.1 重构新增），幂等。"""
        conn = self.conn()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(graph_edges)")}
        if "pred_error" not in cols:
            conn.execute(
                "ALTER TABLE graph_edges ADD COLUMN pred_error REAL NOT NULL DEFAULT 0.0")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)")}
        if "is_degraded" not in cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN is_degraded INTEGER NOT NULL DEFAULT 0")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        if "partial_context" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN partial_context TEXT")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(decision_log)")}
        if "budget" not in cols:
            conn.execute("ALTER TABLE decision_log ADD COLUMN budget REAL")

    def _seed(self) -> None:
        """默认设置 + 触发器初始置信度 + R0 初始认知边（只插入一次）。"""
        from app.proactive.settings import ensure_defaults
        ensure_defaults(self)
        conn = self.conn()
        # R0 冷启动种子：认知网络至少一条边（"自我"—related_to→"存在"，pred_error=0.3），
        # 否则静默规划取 highest_pe_edge 为 None，前几小时 latent_intention 永远为空。
        conn.execute(
            "INSERT OR IGNORE INTO graph_nodes (ntype, name, meta, created_at)"
            " VALUES ('concept','自我','{}',?)", (_now(),))
        conn.execute(
            "INSERT OR IGNORE INTO graph_nodes (ntype, name, meta, created_at)"
            " VALUES ('concept','存在','{}',?)", (_now(),))
        conn.execute(
            "INSERT OR IGNORE INTO graph_edges (src, dst, relation, weight, pred_error, created_at)"
            " VALUES ('自我','存在','related_to',1.0,0.3,?)", (_now(),))
        # 内稳态/自我模型单行种子
        conn.execute(
            "INSERT OR IGNORE INTO homeostatic_state (id, updated_at) VALUES (1, ?)", (_now(),))
        conn.execute(
            "INSERT OR IGNORE INTO self_model (id, updated_at) VALUES (1, ?)", (_now(),))
        TRIGGERS = [
            ("time-night", 0.8, "中"), ("time-day", 0.3, "低"),
            ("silence", 0.6, "中"), ("emotion-shift", 0.7, "高"),
            ("highlight", 0.6, "高"), ("return-1h", 0.3, "低"),
            ("return-6h", 0.5, "低"), ("return-24h", 0.7, "中"),
            ("return-over", 0.9, "高"), ("knowledge-gap", 0.5, "低"),
            ("associative", 0.55, "低"),
        ]
        for t, conf, prio in TRIGGERS:
            self.conn().execute(
                "INSERT OR IGNORE INTO triggers (type, conf, base_conf, priority)"
                " VALUES (?,?,?,?)",
                (t, conf, conf, prio),
            )
        self.conn().commit()

    def close(self) -> None:
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn


# 全局单例
db = Database()
