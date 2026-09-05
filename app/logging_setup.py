"""日志地基（v4.1 升级：完全详细的结构化日志）。

三层输出：
1. 控制台：人类可读，毫秒时间戳（UTF-8）
2. data/logs/mind.log：主日志，轮转（RotatingFileHandler）
3. data/logs/life.log：JSONL 结构化事件流（tick/决策 G 全分解/降级/校验/检查点），
   供"后台日志页"与后期分析直接机器读取，字段完整不截断

log_event(event, msg, **fields)：一行可读 + 一行 JSON 全字段。
"""
import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from app.config import LOG_DIR, settings

# Windows 控制台输出统一 UTF-8，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # 非 Windows 或无 reconfigure 时忽略
    pass

MAIN_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


class JsonLineFormatter(logging.Formatter):
    """JSONL：一行一个事件，全字段、不截断。"""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", ""),
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            data.update({k: v for k, v in fields.items() if k != "message"})
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("mind")
    root.setLevel(settings.log_level.upper())
    root.propagate = False
    if root.handlers:  # 幂等：重复调用不叠加 handler
        return

    fmt = logging.Formatter(MAIN_FMT, datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        LOG_DIR / "mind.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # JSONL 结构化事件流（完全详细）：挂在 mind.event 与 mind 两个 logger 上
    json_handler = RotatingFileHandler(
        LOG_DIR / "life.log",
        maxBytes=settings.log_max_bytes * 5,
        backupCount=5,
        encoding="utf-8",
    )
    json_handler.setFormatter(JsonLineFormatter())
    event_logger = logging.getLogger("mind.event")
    event_logger.setLevel(settings.log_level.upper())
    event_logger.propagate = False
    event_logger.addHandler(json_handler)
    event_logger.addHandler(file_handler)  # 事件同时进主日志（可读版）


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mind.{name}")


def log_event(event: str, msg: str = "", **fields) -> None:
    """结构化事件：主日志一行可读；life.log 一行 JSON 全字段。

    用法：log_event("tick", tick=12, budget=0.7, msg="...")
    """
    event_logger = logging.getLogger("mind.event")
    parts = [f"[{event}]"]
    if msg:
        parts.append(msg)
    for k, v in fields.items():
        parts.append(f"{k}={v}")
    event_logger.log(logging.INFO, " ".join(str(p) for p in parts),
                     extra={"event": event, "fields": {**fields, "msg": msg}})
