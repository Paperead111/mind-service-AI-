"""服务入口：FastAPI 路由接线。

原则：这里只接线，不掺业务。业务全部在各自模块里（decisions/、memory/、…）。
"""
import sys
from contextlib import asynccontextmanager

# 日志地基必须先于任何 app 模块加载（否则导入期的 PRAGMA/种子日志被吞）
from app.logging_setup import get_logger, setup_logging
setup_logging()
log = get_logger("main")

from fastapi import FastAPI

from app.api import router as api_router, tasks_service, ws_manager
from app.config import settings
from app.db import db
from app.llm.client import DeepSeekClient
from app.persona.layer import PersonaLayer
from app.proactive.engine import HeartbeatEngine
from app.proactive.scheduler import HeartbeatScheduler
from app.ui import ui_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "mind-service 启动 | model=%s | %s:%s | key_configured=%s",
        settings.model_id,
        settings.service_host,
        settings.service_port,
        bool(settings.deepseek_api_key),
    )
    llm_client = DeepSeekClient()

    # ---------- R0 启动硬化：Key 熔断（实例化常驻状态前先做连通性探测） ----------
    if settings.r0_key_probe:
        probe = await llm_client.probe(timeout=settings.r0_probe_timeout)
        if probe["detail"] == "no_key":
            log.warning("未配置 DEEPSEEK_API_KEY：以无 LLM 模式运行（对话会明确失败，不伪装说话）")
        elif probe["detail"] == "auth":
            log.critical("Key 连通性探测：HTTP %s —— API Key 无效，拒绝进入主循环",
                         probe["status"])
            print(
                "\033[31m[start] DEEPSEEK_API_KEY 无效（HTTP %s）。"
                "请检查 mind-service/.env 后重试。服务不进入降级，直接退出。\033[0m"
                % probe["status"],
                file=sys.stderr,
            )
            raise SystemExit(2)
        elif probe["detail"].startswith("network"):
            log.warning("Key 连通性探测不可达（%s）：无法验证 Key，继续启动，"
                        "运行时三级降级接管", probe["detail"])
        elif probe["status"] is not None and probe["status"] >= 400:
            log.warning("Key 连通性探测异常（HTTP %s），继续启动，运行时降级接管",
                        probe["status"])
        else:
            log.info("Key 连通性探测通过（HTTP 200，models 查询）")

    # ---------- R1 生命循环底座：常驻 60s tick（后台持续演化，不依赖用户消息） ----------
    from app.cognition.learn import learning_scan_tick
    from app.cognition.network import grow_tick
    from app.emotion.subjective import drift as subjective_drift
    from app.life.loop import LifeLoop
    from app.life.maintenance import tick_maintenance
    life_loop = LifeLoop(db)
    life_loop.register_hook("maintenance", lambda elapsed: tick_maintenance(elapsed, db))
    life_loop.register_hook("subjective", lambda elapsed: subjective_drift(elapsed, db))
    life_loop.register_hook("graph_growth", lambda elapsed: grow_tick(elapsed, db))
    life_loop.register_hook("learning_scan",
                            lambda elapsed: learning_scan_tick(elapsed, db, llm_client))
    # D 心流日记 + E 人格经验提案（ash 自主性/人格更新移植，异步钩子）
    from app.life.flowjournal import maybe_generate_thought
    from app.identity.persona_proposals import maybe_propose
    life_loop.register_hook("flow_journal",
                            lambda elapsed: maybe_generate_thought(elapsed, db, llm_client))
    life_loop.register_hook("persona_proposals",
                            lambda elapsed: maybe_propose(db, llm_client))
    app.state.life_loop = life_loop
    await life_loop.start()

    # ---------- R18′ 三级降级：探测循环 + 恢复续传钩子 + 主动熔断 ----------
    from app.degradation.engine import degradation
    degradation.llm = llm_client
    degradation.on_recovered.append(tasks_service.resume_suspended)
    await degradation.start()

    # 主动对话心跳：启动即挂后台循环（先跑一次，之后每 30 分钟）
    heartbeat = HeartbeatEngine(db, PersonaLayer(), llm_client,
                                on_send=ws_manager.broadcast_proactive)
    scheduler = HeartbeatScheduler(heartbeat)
    scheduler.start()
    app.state.scheduler = scheduler
    app.state.ws_manager = ws_manager
    # 异步任务队列（/v1/chat/async）
    await tasks_service.start()
    yield
    await tasks_service.stop()
    scheduler.stop()
    await life_loop.stop()
    from app.degradation.engine import degradation
    await degradation.stop()
    log.info("mind-service 关闭")


app = FastAPI(title="mind-service", version="0.1.0", lifespan=lifespan)
app.include_router(ui_router)
app.include_router(api_router)


@app.get("/v1/health")
async def health():
    log.debug("health check")
    return {
        "status": "ok",
        "version": "0.1.0",
        "model_id": settings.model_id,
        "deepseek_key_configured": bool(settings.deepseek_api_key),
    }
