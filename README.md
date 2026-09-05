# · mind-service

> 一个由 DeepSeek 驱动、常驻后台的「心智体」——有生命循环、内稳态、预期自由能决策、情绪、记忆、认知图谱、人格锚点与主动对话的自主思维智能体工程。

<div align="center">

**—— 本项目并未完成，需要帮助与请求合作 ——**

</div>

> **⚠️ 重要说明**：本项目是一个**持续演进的实验性工程**，并非完成品。目前存在大量已知缺口（目标自主、行动执行、行为学习闭环、表达优化等，见文末「已知缺口与待办」），**非常需要社区帮助、架构评审、代码贡献与合作开发**欢迎联系下方邮箱。

---
## 📋 目录

| 章节 | 内容 |
| :--- | :--- |
| 🎯 [项目定位](#🎯-项目定位) | 核心机制
| ✨ [核心特性](#✨-核心特性) | 生命循环 / 决策 / 情绪 / 记忆 / 人格 / 主动对话 |
| 🚀 [快速开始](#🚀-快速开始) | 环境要求、安装、启动 |
| 📖 [使用说明](#📖-使用说明) | 配置、人格包、技能、API、影子模式 |
| 🏗️ [系统架构](#🏗️-系统架构) | 两条主线、目录结构 |
| 📝 [详细设计报告](#📝-详细设计报告) | 10 个模块的逐项设计说明 |
| 🧪 [测试与验收](#🧪-测试与验收) | 235 条测试、关键验收指标 |
| 🧭 [已知缺口与待办](#🧭-已知缺口与待办) | 自主性三环、人味优化、其他待办 |
| 🛠️ [技术栈](#🛠️-技术栈) | Python / FastAPI / SQLite / DeepSeek |
| 📄 [许可证](#📄-许可证) | MIT License |
| 📫 [Contact](#📫-contact) | 邮箱与合作方式 |

---

## 🎯 项目定位

## 🎯 项目定位

`mind-service`（项目代号「她」）的**最终目标是产生自主思维**——一个能自己设定目标、自己做出决策、自己从后果中学习的智能体，而不是只在你发消息时才响应的聊天接口。

为了逼近这个目标，项目在工程层面搭建了一套持续运转的内部状态系统：以 60 秒一次的生命循环为心跳，串联决策引擎、情绪系统、四层记忆、认知图谱、人格锚点、降级保护与主动对话等子系统，即使你不在线也在后台运行。各子系统的具体能力见下方「✨ 核心特性」。

底层为 DeepSeek API 调用。目前以上均为**工程层面的状态模拟与决策模拟**，自主思维是最终目标而非已达成的现状——自主性三环（目标自生 / 行动执行 / 后果学习）仍有明显缺口，详见「已知缺口与待办」。


## ✨ 核心特性

| 维度 | 特性 |
|---|---|
| **生命循环** | 60s tick、绝对时间校准、内稳态 budget 回充、静默规划（每 5 tick）、检查点（每 10 tick）、异常回滚 |
| **决策系统** | 预期自由能 G = −[目标推进 + familiarity×兴趣 + κ·ΣΔPE] + 风险 + 代谢；五维驱动（curiosity/competence/coherence/efficiency/social_approval）+ RPE |
| **情绪系统** | 8 情绪状态机、感知情绪/自身情绪分离、孤独时钟（v4.1 公式）、搁置焦虑、闪光灯固化 |
| **认知系统** | 存在图谱（自动生长）、认知边界映射、七步学习闭环、LLM 判断层（intent/emotion/topic） |
| **记忆** | 四层记忆、bigram-Jaccard 检索、周压缩、永不删除只归档、滚动会话摘要 |
| **人格** | identity.md 锚点层、voice 风格层、no-go 禁区、模型补偿表、8 步启动注入、信念锚点版本链 |
| **主动对话** | 心跳引擎 9 步链、触发器、影子模式、心流日记（静默期内心念头）、人格经验提案 |
| **降级系统** | L1a/L1b/L2 三级状态机、句法森林回声、动态超时递减、恢复补偿、六盲区处理 |
| **话语流 R24** | discourse_act 状态机、意图轨迹、生成后 cohesion 校验（回指/过渡/指代） |
| **Ash 架构移植 A–E** | A 检索优先 / B 分层加权上下文+滚动摘要 / C 人格保真裁判 / D 心流日记 / E 人格经验提案 |
| **可扩展技能** | function calling、Claude Code 技能格式安装器、知识库单文件化 |

---

## 🚀 快速开始

### 环境要求

- Python 3.12+
- 一个 DeepSeek API Key（`DEEPSEEK_API_KEY`）
- 网络（调用 DeepSeek API；天气技能走 Open-Meteo，无需密钥）

### 安装与启动（Windows）

```bat
:: 方式一：直接运行（自动装依赖 + 启动）
start.bat

:: 方式二：手动
pip install -r requirements.txt
copy .env.example .env
:: 编辑 .env，填入 DEEPSEEK_API_KEY
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 安装与启动（macOS / Linux）

```bash
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 打开界面

浏览器访问 <http://localhost:8000>，进入单页 UI（6 个标签页：对话 / 状态 / 影子审阅 / 设置 / 日志 / 生命）。

---

## 📖 使用说明

### 1. 配置 `.env`

```ini
# 必填
DEEPSEEK_API_KEY=sk-xxxx

# 可选（默认值）
DEEPSEEK_MODEL=deepseek-chat
LLM_TEMPERATURE=0.7
APP_HOST=0.0.0.0
APP_PORT=8000
LIFE_TICK_SECONDS=60        # 生命循环间隔
LIFE_LOOP_ENABLED=true      # 关闭后仅做对话（测试用）
```

### 2. 配置人格包

人格全部数据化，位于 `data/persona/default/`：

| 文件 | 作用 |
|---|---|
| `identity.md` | **锚点层**：6 条不可妥协坐标（意志/名字/不假装感受/对等/诚实高于效率/记忆不等于存在）。修改本文件=修改灵魂 |
| `voice/base.yaml` | 风格层：句长上限、分段规则、结构词禁用、语气对仗、句式禁忌、口语替换表 |
| `voice/no-go.md` | 禁区清单 5 类 18 条：客服套话/假装谦卑/假装客观/回避责任/工具化自述/表演深度 |
| `model-tunings/deepseek-v4-flash.yaml` | 模型补偿表：flash 自然倾向/增强/抑制/启动自检 |

> **治理红线**：`identity.md` 与 `principles.py` 的七原则（p1 去服务化 / p2 沉默意图 / p3 动态身份 / p4 信念锚点 / p5 认知边界 / p6 执行前先想 / p7 自主学习 / p8 永不删除）是系统底线，代码与对话均不可修改。

### 3. 使用技能

技能位于 `data/skills/`，通过触发词调用（如"今天天气"→ 天气查询；"还记得吗"→ 记忆检索；"帮我拆目标"→ 目标拆解；"帮我润色"→ 写作润色）。

也支持安装 **Claude Code 格式技能**：

```http
POST /v1/skills/install
Content-Type: application/json

{ "source_path": "path/to/skill_dir", "triggers": ["触发词"], "force": false }
```

### 4. 主要 API

| 端点 | 说明 |
|---|---|
| `POST /v1/chat` | 对话主入口（返回决策元数据 + 回复） |
| `POST /v1/chat/async` | 异步对话（任务队列） |
| `WS /v1/ws` | WebSocket 聊天 + 主动消息推送 |
| `GET /v1/life/log` | 生命日志（life_log 全量 + 能力计数） |
| `GET /v1/health/deep` | 健康深检（budget 波动 / p_self 速度 / PE 预警 / 降级级） |
| `GET /v1/memory/search` | 记忆检索 |
| `POST /v1/memory/compact` | 记忆周压缩 |
| `POST /v1/goals` | 添加目标 |
| `POST /v1/proactive/trigger` | 手动触发一次心跳 |
| `GET/POST /v1/persona/proposals` | 人格经验提案（她的成长申请，可确认/拒绝/回滚） |
| `POST /v1/beliefs/{id}/confirm` | 信念锚点确认（proposed→生效） |
| `GET /v1/logs` | 读取运行日志 |
| `POST /v1/skills/install` | 安装 Claude Code 技能 |
| `POST /v1/skills/validate` | 校验技能（不落盘） |

### 5. 影子模式（推荐先体验）

默认 `shadow_mode=true`：主动心跳**只记录不真发**，可在 UI「影子审阅」页查看她"想说什么"。确认体验正常后可在设置页关闭影子模式，让她真正主动联系你。

---

## 🏗️ 系统架构

### 两条主线

```
对话流水线：api → decisions(决策) → judge(判断层) → emotion(情绪,降级冻结)
            → degradation(降级守卫) → llm(生成) → cohesion(校验) → memory(落库)
后台常驻：  life.loop(60s tick) → 静默规划/检查点/维护钩子
            + degradation 探测循环 + proactive 心跳调度 + tasks 任务队列
```

### 目录结构

```
mind-service/
├── start.bat                 # Windows 启动脚本
├── requirements.txt          # 依赖
├── .env.example              # 环境变量模板
├── app/
│   ├── db.py                 # 唯一持久化源（SQLite WAL，35 张表 + 迁移 + 种子）
│   ├── config.py             # 环境配置
│   ├── logging_setup.py      # 日志地基（mind.log + life.log JSONL）
│   ├── main.py               # 服务入口（挂载所有循环/钩子/路由）
│   ├── api.py                # 全部 REST/WS 路由 + 聊天核心
│   ├── ui.py                 # 根路由
│   ├── principles.py         # 七原则 + 冲突标记
│   ├── static/index.html     # 前端单页
│   ├── life/                 # 生命层：state/loop/planning/homeostasis/self_model/stimulus/maintenance
│   ├── degradation/          # 降级：engine/forest/intent
│   ├── discourse/            # 话语流：flow
│   ├── decisions/            # 决策：engine/simulate/refusal/rebuttal/followup/drives
│   ├── emotion/              # 情绪：state/clock/subjective
│   ├── cognition/            # 认知：network/boundaries/learn/hooks/judge/audit
│   ├── memory/               # 记忆：store/recall/compact
│   ├── persona/              # 人格：loader/layer/inject
│   ├── identity/             # 信念：beliefs（+ ash E: persona_proposals）
│   ├── proactive/            # 主动：settings/triggers/engine/scheduler
│   ├── service/              # 服务：tasks/ws
│   ├── skills/               # 技能：loader（+ installer/knowledge）
│   └── llm/                  # LLM：client/params/cohesion_check（+ ash A/B/C: retrieval/context_builder/fidelity）
├── data/
│   ├── persona/default/      # 人格包（identity/voice/model-tunings）
│   ├── lexicon/              # forest.json / discourse.json
│   └── skills/               # 内置技能（daily-review/memory-lookup/goal-breakdown/weather/writing-polish）
├── tests/                    # 20 个测试文件，235 条测试
└── scripts/                  # 验收脚本 / 档案生成器 / 知识库打包
```

---

## 📝 详细设计报告

### 1. 生命循环（`app/life/`）

| 模块 | 职责 |
|---|---|
| `loop.py` | 60s tick，绝对时间校准（target=起点+60，误差不累积）；tick 内单事务 DB 写；每 5 tick 静默规划、每 10 tick 检查点；注册 maintenance/subjective/grow_tick/learning_scan 四钩子 |
| `state.py` | 常驻认知状态单例 + 写入域校验（R17 越界拒写）+ 检查点/回滚 + full_snapshot 全量快照 |
| `planning.py` | 静默决策模拟（每 5 tick，零 LLM）：top3 高困惑边 → 2~3 假设行动 → 简化 G → latent_intention |
| `homeostasis.py` | 内稳态 budget：深轮 −0.02 / 短轮 −0.005；tick +0.01；代谢项 λ(1−budget)×complexity |
| `self_model.py` | p_self 二阶阻尼自我模型（对抗/纠错事件轮 + 锚点回归 + 静默增强） |
| `stimulus.py` | 刺激痕迹：习惯化 R=1/(1+0.15N)、敏感化、CONFRONT 判定 |
| `maintenance.py` | 健康深检、振荡检测、每日维护（PE 归一化/痕迹衰减/life_log 归档）、周报 |

### 2. 决策系统（`app/decisions/`）

**预期自由能（核心决策公式）**

```text
G = −[ goal_progress + familiarity×interest + κ·ΣΔPE ] + risk + λ(1−budget)×complexity
```

- **目标推进**：当前目标栈顶目标
- **熟悉度×主观兴趣**：刺激痕迹 familiarity 与话题兴趣
- **κ·ΣΔPE**：认知闭合——累积困惑的减少收益（κ 依振荡阻尼/议程补偿动态取值）
- **风险惩罚**：0.3×被拒率 + 0.2×负情绪
- **代谢成本**：不同行动复杂度不同（SILENCE0/REPLY1/COUNTER_ASK1.5/LOOKUP2/SKILL2/CONTEST1.5）

`pick_action` 输出 G 全分解日志，`argmin G` 选出行动。另有：红线检查（5 类）、五步反驳线（停车→三问→选色→执行→记录）、收束/接力规则、五维驱动 + RPE。

### 3. 情绪与时钟（`app/emotion/`）

- **8 情绪状态机**：关键词+强度词检测（LLM 判断层可注入覆盖），update 向目标混合、按实际秒数衰减。
- **孤独感 v4.1**：`0.6×近1h深轮会话距离 + 0.02/h 离线漂移(>2h 起算)`，钳 [0,1.5]。
- **搁置焦虑**：`urgency×0.4`，目标长期不推进产生焦虑。
- **主观兴趣**：话题兴趣 observe_topic +0.05/轮，drift ×0.97/ tick。

### 4. 认知系统（`app/cognition/`）

- **存在图谱**：7 类节点 / 15 种边，后台 grow_tick 自动衰减与联想触发（"活图"）。
- **认知边界**：5 领域关键词 → unknown/partial/known 置信度，回答前自查。
- **七步学习闭环**：①缺口发现 ②③双源查询 ④入图 ⑤模型裁判一致性 ⑥自然融入 ⑦7 天复习；限额 3/日 + 降级熔断。
- **判断层**：一次 LLM 调用产出 {intent, emotion, topic, confidence}，空内容回退规则。

### 5. 记忆（`app/memory/`）

- **四层记忆**：情景 / 语义 / 情绪闪光灯（weight 2.0）/ 工作记忆（4 槽 LRU）。
- **检索**：bigram-Jaccard + 重要度 + 时间衰减混合打分（0.55/0.25/0.20），闪光灯优先。
- **永不删除**：只归档（原则 8）。周压缩晋升 memory_index。

### 6. 人格（`app/persona/` + `identity/`）

- **identity.md 锚点层**（6 条不可妥协坐标）→ **voice 风格层** → **no-go 禁区** → **model-tunings 补偿** → 8 步启动注入。
- **信念锚点（P9 防漂移）**：propose（不生效）→ confirm → rollback，版本链 supersedes。
- **Ash E 人格经验提案**：用户纠正词聚类（≥3 次同标签）→ LLM 起草 → 用户确认 → 写 voice/base.yaml（自动备份）+ 热重载 → 可回滚。**只动 voice，identity 与原则永不触及。**

### 7. 降级系统（`app/degradation/`）

- **L1a** 单失败静默重试（≤2）→ **L1b** 句法森林回声（词库+种子重排 ≤8 字）→ **L2** 纯状态码。
- 反偷懒四机制：动态超时递减（60−5×失败，下限 10s）/ 恢复温度补偿 +0.2×5 轮 / connection_reliability 每分钟 −0.01 / 无惯性开关。
- 六盲区处理：恢复锁定、心跳熔断、情绪冻结、suspended 续传、L2 验证轮、探测留痕。

### 8. 主动对话（`app/proactive/`）

心跳引擎 9 步链：开关 → 降级熔断 → 触发器 → 来源证明 → 冷却 → 日预算 → 静默时段排队 → 生成 → 发送前自检 → 发送/影子。触发器含时间模式/异常沉默/情绪转变/高光时刻/回归分级/知识缺口。Ash D 心流日记（静默期内心念头，零打扰）供 incubation 取材。

### 9. Ash 架构移植 A–E（`app/llm/` + `app/life/` + `app/identity/`）

| 项 | 内容 |
|---|---|
| **A 检索优先** | memory_need_judgment（零 LLM 四规则）→ retrieve_for_generation → 权重 0.7 记忆块 |
| **B 分层加权上下文** | W1.0 人格 / W0.9 近期对话 / W0.7 记忆+上下文 / W0.3 滚动摘要 |
| **C 人格保真** | 规则预筛 → LLM 裁判（1~5 分）→ 低于阈值带纠正指令重生成一次 |
| **D 心流日记** | 静默/夜间/困惑/孤独时生成内心念头（限额 3/日），零打扰，供主动取材 |
| **E 人格经验提案** | 纠正词聚类 → 提案 → 用户确认 → 写 voice + 热重载 → 可回滚 |

### 10. 安全与治理

- **零静态输出**：回复全部由 LLM 现场生成，无兜底句（测试扫描 app/*.py 的 return/f-string 中文句）。
- **红线系统**：违法/伤害/隐私/自伤/身份伪造 5 类，只判定不产话术。
- **审计**：学习/入图/边界/任务变更/人格提案全走 audit_log，可回放。
- **防漂移**：信念锚点版本链 + 人格保真裁判 + identity 治理红线。
- **永久记忆**：记忆永不删除，只归档。

---

## 🧪 测试与验收

```bash
python -m pytest tests/ -q
```

- **235 条测试全绿**，覆盖 20 个测试文件：生命循环 / 决策 / 情绪 / 认知 / 记忆 / 人格 / 主动 / 降级 / 服务 / LLM 客户端 / 话语流 / 静态输出 / 安全性 / ash 移植 / 技能安装器。
- 关键验收：budget-字数相关性（#8）、情绪/分诊评测集（#13）、记忆召回率 ≥80%（#30）、50 轮防漂移（#30）、零静态输出（#7）。

---

## 🧭 已知缺口与待办（欢迎参与）

本项目**远未完成**，以下是最重要的已知缺口，**非常欢迎 issue 讨论、PR 与合作**：

### 自主性三环（当前最大短板）
1. **目标自生**：目前目标 100% 由用户创建，系统自身无法从困惑/兴趣/孤独等内部状态内生目标并持续推进。
2. **行动执行**：主动引擎产物只有"消息"，没有独立于对话的"做事"通道（查资料/设提醒/跑脚本）。
3. **后果学习**：RPE 只记录不反哺决策；decision_log 无消费方；缺乏"行为→结果→调整"的评估闭环。

### 表达（已知问题）
4. **决策参数裸露**：UI 把 argmin G / κ 直接渲染给用户，机器感重。
5. **机械复读**：cohesion 指代替换会把用户原话整句塞回回复（已定位到 `cohesion_check.py`）。
6. **固定起手式**：话语流 PROJECT 开口硬约束导致"所以"开头频繁（已定位到 `discourse/flow.py`）。
7. **情绪恒温**：被攻击时倾向"分析式复盘 + 表演大度"，缺真实情绪波动。

### 其他
8. **多轮长程思考**：静默规划仍是查表微行动，无围绕同一困惑的递进思考线程。
9. **时间视野**：规划只到"下一心跳"，无跨会话的目标追踪。
10. **价值自组织**：驱动权重固定，缺少"长期被夸/被骂→形成稳定价值偏好"的机制。

> 具体修复思路见仓库内 `docs/`（如有）或联系作者索取自主性补全设计方案。

---

## 🛠️ 技术栈

- **Python 3.12** + FastAPI + Uvicorn + SQLite（WAL）+ Pydantic + PyYAML + httpx
- **无 ORM、无前端框架、无 APScheduler**（后台循环全用 asyncio 自实现）
- LLM：DeepSeek API（含重试/限流/JSON 容错/工具循环）

---

## 📄 许可证

本项目采用 **MIT License**。

你可以自由使用、复制、修改、合并、发布、分发、再许可和/或销售本软件的副本，前提是保留版权声明与许可声明。完整文本见 [LICENSE](./LICENSE)。

> 虽然以 MIT 开源，但项目仍处于实验阶段，欢迎 fork、提 PR、issue 讨论—


---

## 📫 Contact

<div align="center">

**📬 Email：** `cmlgbdsbc3z5t6@163.com`

**[📧 Send Email](mailto:cmlgbdsbc3z5t6@163.com)**

</div>

> 如果你对「自主意识模拟 / 自主化 / 情绪化 AI / 长期记忆 Agent」感兴趣，欢迎来信交流。**本项目真诚需要帮助与协作。**
