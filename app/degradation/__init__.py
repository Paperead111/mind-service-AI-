"""降级包（R18′）：三级动态降级 + 句法森林 + 反偷懒 + 六盲区闭环。

- engine.py：L1a/L1b/L2 状态机、探测循环、恢复锁定、温度补偿、
  connection_reliability、情绪冻结/恢复、主动熔断补偿、suspended 续传钩子
- forest.py：句法森林组合器（词库+种子，状态锚定强制化，语义指纹去重）
- intent.py：纯规则意图探测（零 LLM）
"""
