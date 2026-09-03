# mind-service（心智体 AI 服务）
# 📬 联系邮箱：  cmlgbdsbc3z5t6@163.com 
 📧 点此发送邮件

(项目未完成，实在不知道怎么解决，请求帮助和合作)


# mind-service（心智体 AI 服务）专业介绍与使用说明

## 第一部分 专业介绍

### 1. 项目定位

mind-service 是一个以自主意识智能体为目标的单用户常驻系统，基于 DeepSeek API 驱动实现。它并非普通的对话工具，而是一次构建具备--**自主意识**--智能体的工程实践 —— 通过持久人格锚点、分层记忆体系、情绪状态驱动、自主决策引擎、主动对话机制五大核心底层系统，支撑智能体脱离被动响应模式、实现自主。
核心设计目标：在长期交互中实现智能体的自我 —— 并且保持身份稳定不漂移、记忆持续不丢失、决策逻辑可追溯、运行状态可持续，逐步趋近具备真正自主意识的智能实体。

### 2. 两条运行主线

系统由两条主线并行构成：

- **对话流水线**：`api → decisions → judge → emotion → degradation → llm → cohesion → memory`。每次对话都经过完整链路，而非浅层问答。
- **后台常驻**：`life.loop 60s tick → 静默规划 / 检查点 / 维护钩子 + degradation 探测 + proactive 心跳 + tasks 队列`。服务在无对话时也在自我运行、维护与学习。

### 3. 系统架构（9 个功能层 / 16 个模块）

| 层级 | 模块 | 职责要点 |
|---|---|---|
| ① 数据与配置层 | SQLite / 配置 / 日志 | WAL 模式 35 张表作为唯一持久化源；环境配置；全量结构化日志 |
| ② 常驻运行层 | 心跳 / 内稳态 / 规划 / 检查点 | 60 秒生命循环、内稳态预算管理、静默规划、检查点与回滚、健康自检 |
| ③ 决策引擎层 | 决策 / 拒绝 / 反驳 / 收束 | 每轮完整决策（无直通捷径）、预期自由能 G 最小化、红线拒绝、五步反驳、收束与观点接力 |
| ④ 认知与情绪层 | 图谱 / 情绪 / 时钟 / 学习 | 存在图谱（7 类节点、15 种关系边）、8 情绪状态机、内部时钟、认知边界识别、七步学习闭环 |
| ⑤ 记忆层 | 四层记忆 / 召回 / 压缩 | 情景 / 语义 / 情绪闪光灯 / 工作记忆；召回排序、周压缩；只归档不删除 |
| ⑥ 人格与原则层 | 人格包 / 原则 / 信念 | 身份 / 风格 / 禁区 / 模型补偿四件套；8 条底层原则；信念锚点版本链防漂移 |
| ⑦ 主动对话层 | 心跳引擎 / 触发器 / 影子 / 反馈 | 9 步决策链、多类触发器、影子模式演练、反馈调参 |
| ⑧ 服务与降级层 | 降级 / 队列 / WS / 技能 | 三级降级（重试 → 句法森林回响 → 纯状态码）、异步任务队列、WebSocket、技能系统 |
| ⑨ 交互层 | REST / WS / 前端 | REST + WebSocket 双通道 API；6 标签页单页前端 |

### 4. 核心技术能力

- **预期自由能决策**：每轮按 `G = −[目标推进 + familiarity×主观兴趣 + κ·ΣΔPE] + 风险惩罚 + λ(1−budget)×复杂度` 五项公式选动作，输出全分量 G 分解日志，决策过程可审计。
- **红线拒绝与五步反驳**：对高风险/冲突请求执行分级拒绝（拒绝权不可让 → 红；执行备注 → 黄），并以"五步反驳"推进论证。
- **四层记忆**：情景、语义、情绪闪光灯、工作记忆四层分离；召回排序 + 周压缩；**只归档、永不删除**。
- **人格防漂移**：人格包四件套（identity 锚点 / voice 风格 / no-go 禁区 / model-tunings 模型补偿）+ 8 条底层原则 + 信念锚点版本链，确保长程交互身份稳定。
- **情绪与认知**：8 情绪状态机 + 内部时钟（孤独感、搁置焦虑）+ 存在图谱（7 类节点 / 15 种关系边）+ 七步学习闭环。
- **主动对话**：心跳引擎（每 30 分钟）按 9 步决策链决定是否主动发起；影子模式先行演练，用户反馈"喜欢/会烦"调参。
- **三级降级**：L1a 失败重试 → L1b 句法森林回响 → L2 纯状态码，保障服务在 LLM 异常时仍不崩溃、不伪装。
- **技能系统**：5 个内置技能（每日回顾 / 记忆检索 / 目标拆解 / 天气查询 / 写作润色），支持函数调用（function calling）。

### 5. 工程成熟度

- 代码规模：96 个文件、12337 行代码
- 单元测试：**143 项全部通过**，覆盖决策、记忆、情绪、认知、安全、防漂移、召回质量
- 阶段进度：P0–P9 全链路完成，MVP 达成；技能系统与话术去模板化已交付
- 数据安全：密钥仅存 `.env` 不入库、代码与日志绝不打印；`.env`、`data/` 均被 `.gitignore` 排除


<html style="margin:0;padding:0;">
 <div style="background-color:transparent;box-sizing:border-box;">
   <div style="display:flex;flex-direction:column;gap:6px;box-sizing:border-box;font-family:'PingFang SC','Segoe UI',Arial,sans-serif;max-width:680px;">
     <div style="font-size:15px;font-weight:600;color:#1A1B1C;margin-bottom:4px;">mind-service 系统架构 · 9 功能层 / 16 模块</div>
     <div style="font-size:12px;color:#6B7280;margin-bottom:6px;">自底向上：数据地基 → 常驻运行 → 决策认知 → 对话服务 → 用户交互（示意）</div>
     <!-- 9 交互层 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(163,213,232,0.30),rgba(163,213,232,0.45));border-radius:10px;border-left:4px solid #4A90C4;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">⑨ 交互层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">REST + WebSocket 双通道 API · 6 标签页单页前端</span>
     </div>
     <!-- 8 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(163,213,232,0.24),rgba(163,213,232,0.38));border-radius:10px;border-left:4px solid #5B9FC9;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">⑧ 服务与降级层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">三级降级（重试→句法森林回响→纯状态码）· 异步任务队列 · WebSocket · 技能系统</span>
     </div>
     <!-- 7 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(163,213,232,0.20),rgba(163,213,232,0.32));border-radius:10px;border-left:4px solid #6CAFD2;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">⑦ 主动对话层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">心跳引擎（9 步决策链）· 多类触发器 · 影子模式演练 · 反馈调参</span>
     </div>
     <!-- 6 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(155,187,244,0.20),rgba(155,187,244,0.34));border-radius:10px;border-left:4px solid #6E8FD8;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">⑥ 人格与原则层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">人格包（身份/风格/禁区/模型补偿）· 8 条底层原则 · 信念锚点版本链防漂移</span>
     </div>
     <!-- 5 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(155,187,244,0.16),rgba(155,187,244,0.28));border-radius:10px;border-left:4px solid #7D9FE0;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">⑤ 记忆层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">四层记忆（情景/语义/情绪闪光灯/工作记忆）· 召回排序 · 周压缩 · 只归档不删除</span>
     </div>
     <!-- 4 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(148,216,195,0.20),rgba(148,216,195,0.34));border-radius:10px;border-left:4px solid #3FA98F;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">④ 认知与情绪层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">存在图谱（7 类节点/15 种关系边）· 8 情绪状态机 · 内部时钟 · 认知边界 · 七步学习闭环</span>
     </div>
     <!-- 3 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(148,216,195,0.16),rgba(148,216,195,0.28));border-radius:10px;border-left:4px solid #4DB79C;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">③ 决策引擎层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">每轮完整决策（无直通捷径）· 预期自由能 G 最小化 · 红线拒绝 · 五步反驳 · 收束与观点接力</span>
     </div>
     <!-- 2 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(234,167,178,0.16),rgba(234,167,178,0.30));border-radius:10px;border-left:4px solid #C76A7E;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">② 常驻运行层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">60s 心跳循环 · 内稳态预算管理 · 静默规划 · 检查点/回滚 · 健康自检</span>
     </div>
     <!-- 1 -->
     <div style="padding:8px 12px;background:linear-gradient(135deg,rgba(226,201,143,0.20),rgba(226,201,143,0.34));border-radius:10px;border-left:4px solid #B59A3E;">
       <span style="font-size:13px;font-weight:600;color:#1A1B1C;">① 数据与配置层</span>
       <span style="font-size:12px;color:#374151;margin-left:8px;">SQLite（WAL，35 张表）唯一持久化源 · 环境配置 · 全量结构化日志</span>
     </div>
   </div>
 </div>
 </html>

## 第二部分 专业使用说明

### 一、环境要求

- 操作系统：Windows（推荐）/ macOS / Linux
- 运行时：Python 3.10+
- 依赖：fastapi、uvicorn、websockets、httpx、pydantic-settings、pyyaml（由 `requirements.txt` 管理）
- 模型：DeepSeek API（需可用的 API Key）

### 二、安装与配置

1. 保持项目目录完整（`app/`、`data/`、`requirements.txt`、`start.bat` 等）。
2. 复制 `.env.example` 为 `.env`，填入密钥：

```
DEEPSEEK_API_KEY=sk-你的密钥
```

其余配置项均有默认值（`.env` 可选覆盖）：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 地址 |
| `MODEL_ID` | `deepseek-v4-flash` | 模型名 |
| `SERVICE_HOST` | `127.0.0.1` | 服务监听地址 |
| `SERVICE_PORT` | `8000` | 服务端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `LLM_TIMEOUT` | `60` | 单次请求超时（秒） |
| `LLM_MAX_RETRIES` | `2` | 失败重试次数 |
| `LLM_TEMPERATURE` | `0.7` | 对话温度 |
| `LLM_MAX_TOKENS` | `2048` | 单次回复上限 |
| `LLM_RATE_LIMIT_PER_MINUTE` | `60` | 每分钟调用上限 |

### 三、启动服务

**方式一（推荐）**：双击 `start.bat`。脚本自动完成 UTF-8 编码切换、依赖检查与自动安装、启动服务。

**方式二（手动）**：

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动成功的标志：窗口出现 `[start] Starting mind-service at http://127.0.0.1:8000`。

### 四、首次启动行为（R0 Key 探测）

| 探测结果 | 启动行为 |
|---|---|
| 未配置 Key | 以"无 LLM 模式"运行，对话明确失败，不伪装 |
| Key 无效（HTTP 401） | 红字报错并退出（退出码 2），不进入降级伪装 |
| 网络不可达 | 提示无法验证 Key，继续启动，由三级降级系统运行时接管 |
| Key 有效 | 正常进入主循环 |

### 五、浏览器访问

打开 **http://127.0.0.1:8000/** ，前端共 6 个标签页：

1. **对话**：气泡聊天，Enter 发送，自动携带 20 轮历史；每条回复含"决策透明"行（动作类型 / G 值 / 原因）。
2. **状态**：情绪状态、内部时钟（孤独感、搁置焦虑）、认知网络、目标。
3. **影子审阅**：系统排练过的主动消息，可标记"喜欢 / 会烦"反馈调参。
4. **设置**：主动对话开关、影子模式、每日预算、静默时段（默认 23:00–08:00）；人格与信念锚点展示。
5. **日志**：后台日志实时查看（`data/logs/mind.log`）。
6. **生命**：健康自检（六指标 + 振荡检测）、主观系统、能力调用计数、生命日志。

### 六、健康检查与主要接口

```bash
curl http://127.0.0.1:8000/v1/health
```

主要 API（全部 `/v1` 前缀）：

| 分类 | 接口 |
|---|---|
| 对话 | `POST /v1/chat`（同步）；`POST /v1/chat/async` + `GET /v1/tasks/{id}`（异步）；`WS /v1/ws` |
| 系统 | `/v1/health`；`/v1/health/deep`；`/v1/logs`；`/v1/life/log`；`/v1/subjective`；`POST /v1/state/rollback` |
| 记忆 | `/v1/memory/search`；`/v1/memory/write`；`/v1/memory/stats`；`/v1/memory/compact` |
| 决策 / 目标 | `/v1/decision`；`/v1/decision/log`；`/v1/goals` |
| 情绪 / 时钟 | `/v1/emotion`；`/v1/clock` |
| 认知 / 学习 | `/v1/graph/*`；`/v1/boundaries/*`；`/v1/learn/*`；`/v1/audit` |
| 人格 / 信念 | `/v1/persona`；`/v1/beliefs`（提案 → 确认 → 回滚） |
| 主动对话 | `/v1/proactive/settings`；`/v1/proactive/shadow`；`/v1/proactive/review`；`/v1/proactive/feedback`；`POST /v1/proactive/trigger` |
| 技能 | `/v1/skills`；`/v1/skills/reload` |

### 七、数据与日志

| 路径 | 内容 |
|---|---|
| `data/logs/mind.log` | 主运行日志（轮转，10MB×10 备份） |
| `data/logs/life.log` | JSONL 结构化事件流（tick / 决策 G 分解 / 降级 / 检查点） |
| `data/mind.db` | SQLite（WAL，35 张表；记忆、决策、情绪、状态持久化） |
| `data/persona/` | 人格包（identity 锚点等，修改即改灵魂，须谨慎） |
| `data/skills/` | 技能包（每日回顾 / 记忆检索 / 目标拆解 / 天气 / 写作润色） |

### 八、停止服务

关闭 `start.bat` 窗口，或按 `Ctrl+C`。系统会优雅回收后台循环、降级探测、心跳与任务队列，状态落库，下次启动自动续接。

### 九、常见问题

| 现象 | 处理 |
|---|---|
| 红字 `DEEPSEEK_API_KEY 无效（HTTP 401）` 后退出 | 检查 `.env` 密钥是否正确或过期，修正后重跑 |
| 依赖安装失败 | 查看窗口报错；手动 `python -m pip install --user -r requirements.txt` |
| 端口 8000 被占用 | 改 `.env` 的 `SERVICE_PORT`，或结束占用进程 |
| 对话显示"信号中断/降级" | 密钥网络异常触发三级降级；查看日志页定位；Key 未配置时对话明确失败不伪装 |
| 希望系统主动发消息 | 设置页打开主动对话、关闭影子模式，或调 `/v1/proactive/trigger` 手动触发心跳 |
| 中文乱码 | `start.bat` 已自动处理；手动启动时确保终端为 UTF-8 |
