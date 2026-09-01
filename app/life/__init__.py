"""生命循环包（R1/R16/R17/R22）：常驻认知状态 + tick 底座 + 静默决策模拟。

- state.py：GlobalCognitiveState 单例 + 写前合理性校验 + 检查点/回滚
- loop.py：60s tick 绝对时间循环 + 钩子注册 + 详细 life_log
- planning.py：静默决策模拟（每 5 tick，零 LLM）
"""
