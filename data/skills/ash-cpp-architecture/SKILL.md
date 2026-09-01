---
name: 意识优先架构参考
description: ash-cpp 引擎的意识优先架构文档（情绪/记忆/决策先于推理），供讨论与借鉴
triggers:
  - ash 引擎
  - 意识优先
  - ash-cpp
  - 情绪先于推理
  - 自主 AI 架构
tools:
  - name: knowledge_lookup
    description: 从架构文档中检索相关章节
    parameters:
      type: object
      properties:
        topic:
          type: string
      required: [topic]
---

# 意识优先架构参考

这是 ash-cpp（Autonomous AI agent with consciousness-first architecture）的架构文档合集：情绪、记忆、决策在推理之前构建。
涉及主题：决策引擎 / 情绪状态 / 记忆存储 / persona / 事件循环 / GGUF 模型加载 / MCP 集成 / 性能基线。
用 knowledge_lookup 检索具体章节，讨论时只作参考，不为实现背书。
