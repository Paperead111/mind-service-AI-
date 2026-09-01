"""全局配置。

两级配置，职责分离：
- 环境级（密钥/连接）：.env，不进代码仓库
- 参数级（阈值/权重/触发器）：data/config/*.yaml，改参数不动代码（P1 起逐个加入）
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = DATA_DIR / "config"
LOG_DIR = DATA_DIR / "logs"
PERSONA_DIR = DATA_DIR / "persona"
DB_PATH = DATA_DIR / "mind.db"


class Settings(BaseSettings):
    """环境级配置，从 mind-service/.env 读取。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek API（Key 由用户自己填入 .env，代码与日志绝不打印）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    model_id: str = "deepseek-v4-flash"

    # 服务
    service_host: str = "127.0.0.1"
    service_port: int = 8000

    # 人格容器
    persona_id: str = "default"          # data/persona/ 下的人格包目录名

    # LLM 调用行为（DeepSeek）
    llm_timeout: float = 60.0           # 单次请求超时（秒）
    llm_max_retries: int = 2            # 失败重试次数
    llm_temperature: float = 0.7        # 普通对话温度
    llm_max_tokens: int = 2048          # 单次回复上限
    llm_rate_limit_per_minute: int = 60 # 每分钟调用上限

    # 日志（完全详细：大容量 + 多备份，防高频全量日志快速轮转丢历史）
    log_level: str = "INFO"
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 10

    # R0 启动硬化：Key 连通性探测（轻量 models 查询，失败报红退出）
    r0_key_probe: bool = True
    r0_probe_timeout: float = 5.0

    # R1 生命循环底座（60s tick，绝对时间校准）
    life_loop_enabled: bool = True
    life_tick_seconds: float = 60.0
    silent_planning_interval_ticks: int = 5   # R16 静默决策模拟：每 5 tick
    checkpoint_interval_ticks: int = 10       # R17 检查点：每 10 tick

    # ash 架构移植（A–E）开关（均可独立回退）
    retrieval_first_enabled: bool = True      # A：检索优先生成
    weighted_context_enabled: bool = True     # B：分层加权上下文
    rolling_summary_enabled: bool = True      # B：滚动会话摘要
    rolling_summary_interval_turns: int = 20  # B：每 N 轮压缩一次
    fidelity_enabled: bool = True             # C：人格保真裁判
    fidelity_min_score: float = 3.0           # C：低于此分重生成
    flow_journal_enabled: bool = True         # D：心流日记
    flow_journal_daily_limit: int = 3         # D：每日念头限额
    persona_proposals_enabled: bool = True    # E：人格经验提案


settings = Settings()
