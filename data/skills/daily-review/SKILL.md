---
name: 每日回顾
description: 取回最近的对话记录，用来做小结
triggers:
  - 今天聊了什么
  - 回顾一下
  - 帮我总结
tools:
  - name: recent_conversations
    description: 取回最近若干条对话记录
    parameters:
      type: object
      properties:
        limit:
          type: integer
          description: 取回条数，默认 20
---
## 使用说明
- 对方想回顾时，调用工具 recent_conversations 取回记录
- 用自己的话做小结（分段、短句），点出值得记住的事
