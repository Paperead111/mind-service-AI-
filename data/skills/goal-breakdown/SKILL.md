---
name: 目标拆解
description: 把目标写入目标栈、查看当前目标
triggers:
  - 不知道怎么开始
  - 帮我拆
  - 目标太大
tools:
  - name: add_goal
    description: 把一个目标写入目标栈（一句话一个目标）
    parameters:
      type: object
      properties:
        content:
          type: string
          description: 目标内容，一句话
      required: [content]
  - name: list_goals
    description: 查看当前目标栈里的所有目标
    parameters:
      type: object
      properties: {}
---
## 使用说明
- 从对方的话里提炼目标（每句一个），用 add_goal 逐个写入
- 用 list_goals 查看已有目标，帮对方确认优先级
