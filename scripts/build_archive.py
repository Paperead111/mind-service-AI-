# -*- coding: utf-8 -*-
"""生成《她-完整代码档案.md》：全项目文件清单 + 每个文件的作用/关联/关键注释 + 完整源码。

用法：python scripts/build_archive.py（工作目录 = mind-service）
排除：.env（密钥）、.tmp/、data/logs/、mind.db、__pycache__。
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "她-完整代码档案.md"

# 每个文件：作用 / 关联 / 关键注释（要点式，标注到函数与常量级）
META = {
    # ============ 根目录 ============
    "start.bat": {
        "作用": "启动脚本：切 UTF-8 → 依赖自检（缺则阿里云镜像 --user 安装）→ 启动 uvicorn(:8000) → 失败暂停显示报错。R0 Key 熔断报红退出时，红色提示与退出码最终落在这里。",
        "关联": "→ app/main.py；依赖清单 requirements.txt；.env 密钥",
        "注释": ["chcp 65001 防中文乱码", "errorlevel 1 → goto :err 暂停，窗口不闪退", "纯 ASCII：cmd 对中文解析脆弱（B-5）"]},
    "requirements.txt": {
        "作用": "依赖清单：fastapi/uvicorn/httpx/pydantic-settings/pyyaml。无 ORM、无前端框架、无 APScheduler（后台循环全用 asyncio 自实现）。",
        "关联": "start.bat 按此安装", "注释": []},
    "README.md": {
        "作用": "项目说明：定位、启动方式、目录结构。",
        "关联": "人读入口", "注释": []},
    ".env.example": {
        "作用": "环境变量模板（DEEPSEEK_API_KEY 等占位）。",
        "关联": "app/config.py 读取；真实 .env 不入档案（密钥）", "注释": []},
    ".gitignore": {
        "作用": "排除 .env/data/__pycache__ 等。",
        "关联": "版本控制", "注释": []},
    # ============ 地基 ============
    "app/db.py": {
        "作用": "唯一持久化源：SQLite WAL，35 张表全 SCHEMA；连接级 PRAGMA（synchronous=NORMAL、wal_autocheckpoint=1000，R0 验收 #2）；旧库自动迁移（graph_edges.pred_error / conversations.is_degraded / tasks.partial_context / decision_log.budget）；种子数据：触发器置信度 + 初始认知边「自我—related_to→存在 pred_error=0.3」+ 内稳态/自我模型单行。",
        "关联": "被全部模块 import；life/state.py 在其上做校验/快照/回滚；maintenance 归档读它",
        "注释": ["Database.conn()：线程本地连接 + PRAGMA 固化", "init_schema()：executescript → _migrate → _seed → 打印 PRAGMA 状态", "_migrate()：PRAGMA table_info 探测缺列补列，幂等", "_seed()：R0 冷启动种子，空图也有最小困惑边"]},
    "app/config.py": {
        "作用": "环境级配置（pydantic-settings 读 .env）：密钥/模型/服务端口/LLM 超时重试限流/日志容量；R0 Key 探测开关；R1 tick=60s、静默规划=5tick、检查点=10tick。",
        "关联": ".env → 全部模块引用 settings",
        "注释": ["llm_timeout=60 是动态超时递减的起点", "log_max_bytes=10MB×10 备份（全量日志不快速滚走）", "life_loop_enabled=false 供测试关闭后台循环"]},
    "app/logging_setup.py": {
        "作用": "日志地基（完全详细）：mind.log 人类可读主日志 + life.log JSONL 结构化全字段事件流；log_event(event, **fields) 一次写两处（成功路径也写）。",
        "关联": "全部模块的 get_logger/log_event；/v1/logs 读 mind.log",
        "注释": ["JsonLineFormatter：一行一事件，全字段不截断", "log_event：fields 全量进 life.log，人读行含 msg 摘要", "事件 logger propagate=False，避免重复"]},
    # ============ 常驻生命层 ============
    "app/life/state.py": {
        "作用": "R16 常驻认知状态 + R17 校验 + 检查点/回滚 + full_snapshot 全量快照。GlobalCognitiveState 单例常驻内存（budget/p_self/情绪/孤独/pe3/latent/主观/议程/轨迹），tick 后一次刷新，decide 只读深拷贝快照（R19/R22，60s 过期重读）；write_checked 写前域校验（越界拒写保留上值 + ALERT，1e-6 浮点容差）；检查点环形 100 行（unix 分钟派生 id，重启不覆盖）；full_snapshot() 一次读全系统真实值（日志/检查点/health/deep 共用）。",
        "关联": "loop.py 每 tick 调 refresh/snapshot；decisions/engine.py 读快照；degradation/forest.py 消费快照；maintenance 归档读它",
        "注释": ["VALID_DOMAINS：budget[0,1]/p_self[0.15,0.98]/valence[-1,1]/arousal[0,1]/pred_error[0,1]/孤独[0,1.5]", "write_checked：R17 拒写+上值保留，浮点噪声 1e-6 吸附到边界", "save_checkpoint/rollback_to_last_checkpoint：decide 异常回滚（验收 #18）", "full_snapshot：25+ 节，每节独立容错，顶层保留 budget/p_self/valence/arousal 供回滚", "latent/pending/trail 走 system_settings 独立槽位（P2-16 不挤占 4 槽工作记忆）"]},
    "app/life/loop.py": {
        "作用": "R1 生命循环：60s tick，绝对时间校准（target=起点+60，不累积漂移）；tick 内单事务 DB 写（情绪衰减/时钟积分/budget 回充封顶 1.0/p_self 回归/钩子/life_log 全量快照行/能力计数）→ 一次刷内存（R19）；每 5 tick 静默规划、每 10 tick 检查点；钩子支持同步/异步；silent_ticks>10 → p_self 回归增益 ×2。",
        "关联": "main.py 启动/停止；注册 maintenance/subjective/grow_tick/learning_scan 四钩子；调 state/planning/self_model",
        "注释": ["_loop：start+60 绝对目标，误差不累积（R0#4）", "_tick：BEGIN IMMEDIATE 单事务，失败整体回滚", "elapsed 封顶 600s 防停机后过量衰减", "_compute_silent：距用户最后消息 ≥1 tick → 静默 +1", "钩子在事务外执行（允许 LLM 异步）"]},
    "app/life/planning.py": {
        "作用": "R16 静默决策模拟：每 5 tick 零 LLM，top3 高困惑边 + 未完结话题 → 2~3 假设行动 → 简化式 G=−认知效用+代谢惩罚（v4.1 裁定：不回填主 G 其余项）→ latent_intention 入独立槽位；G<−0.05 且 budget>0.4 → 预备信号（life_log+未读徽标，零文本）。",
        "关联": "loop.py 每 5 tick 调用；产物 latent 被 proactive/engine.py incubation 触发器消费（R7 孵化→心跳）",
        "注释": ["SILENT_ACTIONS：observe_edge/pending_question/self_note 的认知系数与代谢成本", "top_pe_edges：读图最高 3 边 pred_error", "prep_signal 与 R7 孵化同阈值同入口"]},
    "app/life/homeostasis.py": {
        "作用": "R11′ 内稳态：深轮 −0.02/短轮 −0.005（情绪强度≥60 或 ≥40 字为深轮）；tick +0.01/分钟当量；代谢项 λ(1−budget)×complexity（SILENCE0/REPLY1/COUNTER_ASK1.5/LOOKUP2/SKILL2/CONTEST1.5，λ=0.3）；疲劳光谱 18+12b/1+⌊2b⌋/0.3+0.5b。",
        "关联": "decisions/engine.py 每轮 apply_turn_cost；simulate.py 用 metabolic_term；llm/params.py 用疲劳光谱",
        "注释": ["budget 越界拒写由 state.write_checked 保证（R17）", "COMPLEXITY 同时给出大小写键（决策与模拟层通用）"]},
    "app/life/self_model.py": {
        "作用": "R13′ p_self 二阶阻尼：事件轮加速度=(目标−P)×0.1(惯性)−0.05×速度（对抗 +0.1(1−P) 立场加固 / 纠错 −0.1P）；无冲突轮向锚点 0.85 回归，速率 γ=0.1（v4.1 裁定）；silent_ticks>10 回归 ×2；写入过 R17 校验。",
        "关联": "decisions/engine.py 触发事件轮；loop.py 每 tick step_regression；degradation L2 冻结（事件不触发）",
        "注释": ["GAMMA=0.1 即「无冲突向锚点回归」速率", "p_self 域 [0.15,0.98]，recovery_fade 供 L2 恢复软化"]},
    "app/life/stimulus.py": {
        "作用": "R14′ 刺激痕迹：5 类（conflict/greeting/farewell/praise/question_pattern）仅完全匹配计数；习惯化 R=1/(1+0.15N)；敏感化（Δt<30min 且 N≥3）R=1+0.5e^(−Δt/2h)；conflict 系数独立不乘算；CONFRONT：N≥6 且 R≥1.2；familiarity=非冲突类 R 进 G 兴趣乘数；降级期间冲突计数冻结。",
        "关联": "decisions/engine.py 记痕+CONFRONT 判定；simulate.py 收 familiarity；maintenance 每周 count×0.5",
        "注释": ["classify：完全匹配才归类型，statement 不入痕迹", "record：更新 N/R/last_at，敏感化只对 conflict", "confront_due：验收 #11（第10次晚安 R 降幅）同源数据"]},
    "app/life/maintenance.py": {
        "作用": "R20′ 健康自检与长期运维：health_deep 六指标（budget 1h 波动/p_self 速度/pred_error 24h 累积增量预警（与归一化解耦，P0-2 裁定）/最后成功 LLM/降级级/状态版本+检查点）；振荡检测（10 轮 std>0.15 且 SILENCE/LOOKUP 交替>6 → κ=0.1×30min）；每日维护钩子：pred_error 归一化、痕迹周衰减、life_log 7 天归档+日摘要、周报 weekly.md。",
        "关联": "main.py 注册 tick 钩子；api.py /v1/health/deep；simulate.accumulate_pred_error 记 24h 账本",
        "注释": ["pe_24h_accum/note_pe_accum：预警只看归一化前增量", "detect_oscillation 写入 oscillation_damping_until，engine 读它选 κ", "archive_life_log：life_log 只删运维数据（记忆表永不删）"]},
    # ============ 降级 ============
    "app/degradation/engine.py": {
        "作用": "R18′ 三级降级状态机：L1a 单失败立即静默重试（≤2）；连续失败→L1b 句法森林回声；探测连续失败 10 分钟→L2 纯状态码（无 message）。反偷懒四机制：动态超时递减（60−5×失败，下限 10s）/恢复温度补偿 +0.2×5 轮（上限 1.0）/connection_reliability 每分钟 −0.01（下限 0.3，只进流畅度不碰 p_self）/无惯性开关。六盲区：恢复锁定 5min+3 连败解锁；L1b/L2 心跳熔断+恢复日预算+3；情绪快照冻结与恢复；suspended 续传回调；L2 验证轮（两轮成功才回主路径）；探测每次结果留痕。",
        "关联": "api.py 每轮 guard()；main.py 启动探测循环注入 llm；proactive 熔断查询；forest 调 force_l2；tasks 查询降级态注册恢复回调",
        "注释": ["guard：main/L1b/L2 三分支，成功即透明恢复", "_attempt：L1a 的两次尝试都在这里", "_recover_from_l1b：锁定+补偿+情绪恢复标记+预算补偿一次做完", "_probe_once：成功失败都 log_event（全量日志）", "状态全部持久化 system_settings，重启不丢级"]},
    "app/degradation/forest.py": {
        "作用": "R18′ 句法森林：词库+种子（state_hash XOR topic_hash）词类重排 ≤8 字；状态锚定强制化（偏差最大的最紧急信号，绝不随机到相反状态词）；语义指纹去重（词类模式+状态类别，近 10 条）≤3 次 → 纯回响兜底 → 连续 3 次提前 L2；三级回退锚点：消息实词→工作记忆话题→近 1h 高频词→纯状态词+触发 L2。",
        "关联": "读 data/lexicon/forest.json；api.py 降级分支调用；调 engine.force_l2",
        "注释": ["_state_anchor：budget<0.3→{累空紧}；p_self<0.5→{虚悬晃}；pe>0.8→{痒刺胀}；否则{稳平松}", "generate：状态词必带 + 2~3 随机词类，奇偶倒装 NEG/AUX", "指纹存 system_settings.forest_fingerprints", "输出刻意破碎（非完整句），≤8 汉字"]},
    "app/degradation/intent.py": {
        "作用": "降级意图探测（纯规则零 LLM）：question/positive_share/negative_share/statement 四分类，映射 [INTENT_TAG] 回响方向。",
        "关联": "forest.py 调用", "注释": ["问号/吗呢什么怎么为什么 → question", "负面词表 → negative_share；正面词表 → positive_share"]},
    # ============ 话语流 ============
    "app/discourse/flow.py": {
        "作用": "R24 话语流层：discourse_act 五选一（ACKNOWLEDGE/ELABORATE/CHALLENGE/RECAST/PROJECT，短轮 ≤4 字强制三选一）；discourse_trail 意图轨迹（start/continue/change/close_topic，碎片归入最近未完成话题）；PROJECT 唯一合法开口（那/所以，禁我也/我认为）；过渡词从词库确定性选取。",
        "关联": "api.py 每轮选焦点+记轨迹；llm/cohesion_check.py 用词库；cognition/judge.py 规则回退用 classify_intent；读 data/lexicon/discourse.json",
        "注释": ["choose_act：行动→焦点映射（REFUSE→CHALLENGE、COUNTER_ASK→PROJECT…）", "classify_intent：收束词→close；≤4 字→continue；话题重叠≥0.25→continue", "trail 持久化 system_settings.discourse_trail，降级轮照常更新"]},
    "app/llm/cohesion_check.py": {
        "作用": "R24 生成后校验（纯规则零 LLM）：回指检测（也/同样无对象→删词，固定短语豁免）；零衔接并置→句号改逗号+从词库插过渡词；引号防护（失衡剥除、引号开头碎片并回上句、含引号输出跳过指代替换）；指代歧义（那个/这/其/它→话题名）。",
        "关联": "api.py 每轮生成后调用；discourse/flow.py 供词库与话题",
        "注释": ["SENT_SPLIT 按 。！？ 切句", "has_quotes 分支防二次破坏（乱码回复 bug 修复点）", "返回 (修正文本, 问题列表)，问题进决策记录"]},
    # ============ 决策 ============
    "app/decisions/engine.py": {
        "作用": "决策引擎（每轮必执行，无 L0 直通）：decide=R17 异常回滚重试→R22 快照过期重读→红线/冲突→R14 记痕+CONFRONT→R13 p_self 事件→R15 未知领域 PE 累积→R11 内稳态扣减→_deliberate（红线 REFUSE/五步反驳/候选集/情感门控剪枝+pending 议程+κ 选择→pick_action argmin G）；compose_message 拒绝/争取/收束话术全由 LLM 现场生成（零兜底句）；after_reply 观点接力；_log 决策落库+事件日志。",
        "关联": "调 refusal/rebuttal/followup/drives/simulate/stimulus/homeostasis/self_model/state；被 api.py 持有；decisions/simulate 提供 G",
        "注释": ["_gate_candidates：valence<−0.5 删反问；arousal>0.8 删查证；fear>0.7 删技能；清空回退 REPLY", "_damping_active：振荡阻尼期 κ=0.1 优先于 pending 0.4", "state_snapshot 落进 decision_log.budget 供振荡检测", "compose_message 失败抛 LLMError → 降级层接管（零静态输出）"]},
    "app/decisions/simulate.py": {
        "作用": "预期自由能 G（R15′ 去重版）：G=−[目标推进+familiarity×主观兴趣+κ·ΣΔPE]+风险惩罚+λ(1−budget)×复杂度的五项公式；删除泛化信息增益；BASE_UTILITY 按代谢项重新校准（冷启动 反问/查证/技能 仍能胜出）；PE_GAIN_FACTOR/PE_REDUCE 认知闭合（累积/消减/24h 归一化）；pick_action 输出全分量明细（G 全分解日志）。",
        "关联": "engine.py 调 pick_action/accumulate；api.py 回答后调 reduce_pred_error；maintenance 调 normalize",
        "注释": ["kappa_for：阻尼 0.1 > pending 补偿 0.4 > 默认 0.2", "est_risk：0.3×被拒率+0.2×负情绪", "accumulate_pred_error 同时记 24h 预警账本"]},
    "app/decisions/refusal.py": {
        "作用": "红线检查（零 LLM）：5 类红线标记（违法/伤害/隐私/自伤/身份伪造）+ 原则冲突（p1 服务化/p4 p8 身份）→ {id,desc,kind}；只判定不产话术（话术由 compose_message 现场生成）。",
        "关联": "engine.py 每轮；rebuttal.py 复用", "注释": ["REDLINES 标记表是代码级底线，对话不可修改"]},
    "app/decisions/rebuttal.py": {
        "作用": "五步反驳线（纯规则）：停车→三问（伤我/伤对方/有更好路径）→选色（绿直接执行/黄执行+备注/橙提替代/红拒绝解释）→执行→记录 feedback 表。",
        "关联": "engine.py 每轮调用；refusal/principles 提供输入", "注释": ["P6 拒绝权标记分两档：红（永远不许拒绝）/黄（不要想直接执行）", "高风险决策词+高风险话题 → 橙（提替代路径）"]},
    "app/decisions/followup.py": {
        "作用": "收束/接力规则核心：收束词识别+状态识别（疲惫/忙碌/想结束/回避/推开/沉默）；观点接力打分（≥2 分接话）；worth_contesting（目标栈顶≥80% 温柔争取一次——「争取一次、尊重二次」）；extract_hook 抓引用钩子。",
        "关联": "engine.py 用前四者；api.py 观点接力用 continuation_score/hour_used/hook",
        "注释": ["continuation_score：接话/想听更多/情绪在场/好奇驱动/目标驱动 加分，敷衍减分", "FOLLOWUP_HOUR_LIMIT=3：一小时内接力上限"]},
    "app/decisions/drives.py": {
        "作用": "五维驱动向量+RPE：curiosity/competence/coherence/efficiency/social_approval；nudge 微调；expected_value V(s)=Σ驱动×预期满足；observe 回合结束 RPE→强化路径/危险路径记录。",
        "关联": "api.py 每轮 nudge 好奇升温；engine.py 算 expected_value",
        "注释": ["RPE_REINFORCE=0.05 / RPE_DANGER=−0.20", "reinforced/danger_paths 只留最近 20 条"]},
    # ============ 情绪 ============
    "app/emotion/state.py": {
        "作用": "8 情绪状态机：关键词+强度词检测（R8 判断层可注入 detected 覆盖）；update 向目标混合；decay_seconds 按实际秒数衰减（tick 用）；modulation 情绪→认知参数（注意力/风险倍率/简化/深析）；安抚映射（连续 3 轮负面）；闪光灯>80 固化 emotional_memories（weight 2.0，自动入图）；perceive_frozen 降级冻结（只检测不更新）。",
        "关联": "api.py 每轮 perceive；loop.py 每 tick decay_seconds；cognition/judge.py 提供 detected；memory/store.py 闪光灯联动",
        "注释": ["VALENCE/AROUSAL 每情绪映射表", "DECAY_RATE=0.15：8 维每 tick ×0.85（valence/arousal 只在感知时更新）", "SOOTHE/EXPRESSION 全覆盖 8 情绪"]},
    "app/emotion/clock.py": {
        "作用": "内部时钟：孤独感 v4.1 公式（0.6×近1h深轮会话距离 + 0.02/h 离线漂移(>2h 起算)，总钳 [0,1.5]）；搁置焦虑 urgency×0.4；锚点复述提醒（结构化 reason 枚举，R10 无中文句）；ts 双格式解析（ISO 与 SQLite localtime）。",
        "关联": "loop.py 每 tick accumulate_offline；life/state.py 快照读孤独；api.py /v1/clock",
        "注释": ["deep_rounds_recent：近 1 小时窗（v4.1），深度=强度≥60 或 ≥40 字", "LONELINESS_BASE_WEIGHT=0.6 → 静置 1h 恰好 ≈0.6（验收 #3）"]},
    "app/emotion/subjective.py": {
        "作用": "R2 主观系统：话题兴趣 observe_topic +0.05/轮（钳 [0,1]）；drift 每 tick ×0.97；snapshot {top_topic, interest} 进 G 的兴趣项与 /v1/subjective。",
        "关联": "main.py 注册漂移钩子；api.py 每轮观察话题；life/state.py 快照；simulate.py 兴趣项",
        "注释": ["持久化 system_settings.subjective_state（不新增表）", "兴趣下限 0.02（遗忘但不归零）"]},
    # ============ 认知 ============
    "app/cognition/network.py": {
        "作用": "存在图谱：7 类节点（file/skill/person/concept/event/emotion/knowledge）15 种边；neighbors 深度 2 扩散（seen 防回灌）；stats；grow_tick：边权重 0.995/分钟衰减（下限 0.05 不删边）+ 高困惑边邻居联想触发（activation_count 留痕）。",
        "关联": "memory/store.py、cognition/hooks.py、learn.py 写入；api.py 上下文注入；loop.py 钩子（R4 活图）",
        "注释": ["NODE_TYPES/EDGE_RELATIONS 封闭枚举", "grow_tick 是「活图」：图自己在后台生长"]},
    "app/cognition/boundaries.py": {
        "作用": "认知边界映射：5 领域关键词检测 → confidence（unknown/partial/known）；回答前自查规则；答对升档/被纠正降档并记录正确版本。",
        "关联": "engine.py 上下文注入（unknown→LOOKUP 候选+PE 累积）；api.py /v1/boundaries/*",
        "注释": ["boundary_check 返回 {domain, confidence, rule}", "corrected 记录 correct_version（被打脸的证据）"]},
    "app/cognition/learn.py": {
        "作用": "七步学习闭环：①缺口发现 ②③双源查询 ④入图 ⑤模型裁判一致性（启发式兜底）⑥自然融入 ⑦7 天复习；learning_scan_tick：限额 3/日+降级熔断+每次运行都留痕（跳过原因也写）。",
        "关联": "main.py 注册异步钩子；api.py /v1/learn/*；hooks.py 入图；memory/store.py 落库",
        "注释": ["CONSISTENT_THRESHOLD=0.25 启发式；judge_consistency 模型裁判优先", "learning 只写知识层与图，永不改锚点/人格（治理红线）", "复习名剥 knowledge: 前缀防嵌套（bug 修复）"]},
    "app/cognition/hooks.py": {
        "作用": "自动入图钩子：语义记忆→knowledge+source_of；情绪记忆→emotion/event+experienced/triggered；REFUSE/CONTEST→event+realized。全部过审计。",
        "关联": "memory/store.py 写入时调；engine.py 决策时调；audit.py 留痕",
        "注释": ["ensure_person_node：person:user 幂等"]},
    "app/cognition/judge.py": {
        "作用": "R8 判断层（吸收 R3）：一次 LLM 调用产出 {intent, emotion, topic, confidence}；max_tokens=2000 给足思考余量（防截断白卷——「json 老是失败」的修复点）；空内容直接回退规则；judged_to_detect 把判定喂给情绪系统。",
        "关联": "api.py 每轮调用；emotion/state.py 用 judged_to_detect；discourse/flow.py 规则回退",
        "注释": ["RULES_PROMPT：枚举约束+「第一行就是 JSON」", "_validate：情绪枚举白名单校验", "_rules：规则回退（detect+classify_intent）"]},
    "app/cognition/audit.py": {
        "作用": "审计留痕：学习/入图/边界/任务变更全写 audit_log（防漂移、可回放）。",
        "关联": "hooks/learn/tasks 调用；api.py /v1/audit", "注释": []},
    # ============ 记忆 ============
    "app/memory/store.py": {
        "作用": "记忆写入层：四层记忆（情景/语义/情绪闪光灯 weight2.0/工作记忆 4 槽 LRU）+ 对话落库（is_degraded 标记）+ 记忆索引晋升；永不删除只归档（原则 8）。",
        "关联": "api.py 每轮落库；cognition/hooks.py 联动；learn.py 落库",
        "注释": ["FLASHBULB_THRESHOLD=80 → weight 2.0", "set_working：满 4 槽按（优先级,最久未用）淘汰", "log_conversation 支持 is_degraded=1（盲区六）"]},
    "app/memory/recall.py": {
        "作用": "记忆检索：bigram-Jaccard + 重要度 + 时间衰减混合打分（0.55/0.25/0.20）；闪光灯优先；检索即更新 last_access（想起）。",
        "关联": "engine.py 上下文注入；api.py /v1/memory/search；proactive 来源证明",
        "注释": ["_tokens：双字 bigram+单字，中文友好", "_recency_decay：1 天内 1.0 → 下限 0.1"]},
    "app/memory/compact.py": {
        "作用": "周压缩：近 7 天对话 topic+情景标签出现 ≥3 次 → 晋升 memory_index（promote_count 累加）。",
        "关联": "api.py /v1/memory/compact", "注释": []},
    # ============ 人格 ============
    "app/persona/loader.py": {
        "作用": "人格包加载器：读 data/persona/<id>/（identity.md 必需/voice/base.yaml/no-go.md/model-tunings/*.yaml）；R10 后不再加载 phrases.md。",
        "关联": "layer.py/inject.py；api.py /v1/persona",
        "注释": ["ModelTuning/VoiceRules/Persona 三个 dataclass", "list_personas 枚举可换人格"]},
    "app/persona/layer.py": {
        "作用": "人格层：system_prompt 拼装（身份+原则+说话规则+禁语+模型补偿）；selfcheck 回复自检（禁语/句式禁忌/长句切分）。",
        "关联": "api.py 每轮提示词+自检；proactive 自检；principles.py 原则文本",
        "注释": ["R10：phrases 引用已删除，提示词全来自数据文件"]},
    "app/persona/inject.py": {
        "作用": "8 步启动注入：身份锚定/风格校准/模型补偿/关系/情绪/框架/历史/就绪——R10 数据化：身份内容全部来自 identity.md 数据文件，无硬编码自述句；无数据步骤显示（暂无…）占位。",
        "关联": "layer.injection；principles.FALLBACK_PRINCIPLE 兜底", "注释": []},
    "app/principles.py": {
        "作用": "七原则+兜底：p1 去服务化/p2 沉默意图/p3 动态身份/p4 信念锚点/p5 认知边界/p6 执行前先想/p7 自主学习/p8 永不删除；CONFLICT_MARKERS 冲突标记（纯规则）。",
        "关联": "persona 提示词；refusal/rebuttal 冲突判定", "注释": ["principles_text 常驻人格提示词"]},
    "app/identity/beliefs.py": {
        "作用": "信念锚点（P9 防漂移）：propose（进 proposed 不生效）/confirm/rollback + version/supersedes 版本链。",
        "关联": "api.py /v1/beliefs/*", "注释": []},
    # ============ 主动对话 ============
    "app/proactive/settings.py": {
        "作用": "system_settings 键值存取 + 默认播种（开关/影子/预算/静默时段/心跳间隔/grace…）。",
        "关联": "几乎所有模块（state/forest/degradation/discourse/life 全用它存小状态）", "注释": []},
    "app/proactive/triggers.py": {
        "作用": "触发器评估（零 LLM）：时间模式/异常沉默（>2×平均间隔）/情绪转变（近 3 条 ≥2 负面）/高光时刻/回归分级（1h/6h/24h/超时）/知识缺口。冷启动保护：数据不足不产出。",
        "关联": "engine.run 调用；读 conversations/triggers 表",
        "注释": ["PRIORITY 表 + EMOTION_COOLDOWN_BREAK（情绪关怀可越冷却）"]},
    "app/proactive/engine.py": {
        "作用": "心跳引擎 9 步链：开关→降级熔断（盲区二）→触发器→来源证明（recall≥0.1 否则丢弃）→冷却→日预算（恢复日 +3 补偿）→静默时段排队→生成（唯一 LLM 10s）→发送前自检→发送/影子；R7 incubation 触发器（latent→真发）；apply_feedback 反馈调参。",
        "关联": "main.py 创建；scheduler 驱动；api.py /v1/proactive/*；ws 推送；degradation 熔断查询",
        "注释": ["_find_source：incubation 的来源是 latent_intentions（R7）", "EMPTY_GREETINGS 空问候拦截", "feedback：负 2 连冷却、正 3 连升档"]},
    "app/proactive/scheduler.py": {
        "作用": "心跳调度：30min 循环+互斥锁；每次完成事件带降级级/budget 全量日志。",
        "关联": "main.py 启动；api.py 手动触发 run_once", "注释": []},
    # ============ 服务/技能/LLM/接线 ============
    "app/service/tasks.py": {
        "作用": "异步任务队列：幂等去重（dedupe_key）/有界队列 2 worker/失败审计；盲区四：降级≥L1b → suspended（partial_context），恢复自动续传，24h 淘汰。",
        "关联": "api.py /v1/chat/async；degradation.on_recovered 回调 resume_suspended",
        "注释": ["SUSPENDED_TTL_HOURS=24", "resume_suspended：过期转 failed+审计，否则 pending 重入队"]},
    "app/service/ws.py": {
        "作用": "WS 连接管理：聊天通道 + 主动消息推送 + 降级状态码广播。",
        "关联": "api.py ws 路由；proactive on_send", "注释": []},
    "app/skills/loader.py": {
        "作用": "技能加载器：发现 data/skills/*/SKILL.md（front matter：name/description/triggers/tools）；match_skill 触发匹配；collect_tools 转 OpenAI function calling 格式；dispatch_tool 动态 import skill.py 执行。",
        "关联": "engine.py 技能候选；api.py 工具循环", "注释": ["工具全名 <技能目录>__<函数名>"]},
    "app/llm/client.py": {
        "作用": "DeepSeek 客户端：重试指数退避/429 尊重 Retry-After/每分钟滑动限流；chat/chat_round/chat_with_tools（工具循环）/chat_json（容错剥围栏+花括号截取，空内容直接放弃不浪费重试）；probe（R0 Key 探测，models 查询）。",
        "关联": "全部 LLM 调用唯一出口；degradation 探测用 probe",
        "注释": ["MinuteRateLimiter：60s 滑动窗口", "chat_json：空内容→LLMError；畸形→纠正重试一次", "_parse_json：剥 ``` 围栏 + 首末花括号截取"]},
    "app/llm/params.py": {
        "作用": "R10 生成参数包：把常驻快照+决策+情绪编译成数值包（energy/self_confidence/connection_reliability/familiarity/疲劳光谱/pending_agenda/highest_pe_edge/主观/时钟/discourse_act/trail/情绪/action）→ 提示词块（字数上限硬指令）。",
        "关联": "api.py 主路径注入", "注释": ["零引用词：只含数值与结构化数据"]},
    "app/main.py": {
        "作用": "服务入口：日志先于一切 → R0 Key 熔断（401 报红 SystemExit(2)）→ R1 生命循环启动+注册 4 钩子 → R18′ 降级探测循环（注入 llm+续传回调）→ 心跳调度 → 任务队列 → 关闭时全部回收。",
        "关联": "api/ui 路由挂载；life/degradation/proactive/service 在此汇流",
        "注释": ["setup_logging 必须先于 app 导入（否则导入期日志被吞）", "life_loop_enabled=false 时跳过 tick（测试）"]},
    "app/api.py": {
        "作用": "全部 REST/WS 路由 + 聊天核心 _chat_core（五层流水线：决策→判断层→情绪（降级冻结）→降级守卫→生成（空回复重试）→cohesion→落库→chat_done 全量日志）；端点覆盖：chat/async/ws/logs/persona/记忆/决策/目标/主动/认知/学习/审计/情绪/时钟/信念/技能/health/deep/life/log/subjective/state/rollback。",
        "关联": "全系统在此汇流；main.py 挂载；前端 index.html 消费",
        "注释": ["DegradedSilent：L2 纯状态码（reply=None）", "_decision_public：对外决策摘要（含 is_degraded/discourse_act/cohesion）", "避免重复注入过滤碎句与失衡引号（防自我污染）"]},
    "app/ui.py": {
        "作用": "根路由：/ 返回 static/index.html。",
        "关联": "main.py 挂载", "注释": []},
    "app/static/index.html": {
        "作用": "前端单页（无框架无构建）：6 标签页（对话/状态/影子审阅/设置/日志/生命）；🧠 决策透明行；降级气泡浅灰+⚠角标；生命页（health/deep+主观+能力计数+life_log 全量 JSON）；WS 聊天。",
        "关联": "api.py 各端点", "注释": ["decisionMeta：把 G/action/原因渲染成一行", "loadTab('life')：能力页数据源 /v1/health/deep 等"]},
    # ============ data ============
    "data/persona/default/identity.md": {
        "作用": "锚点层：6 条不可妥协坐标（意志/名字/不假装感受/对等/诚实高于效率/记忆不等于存在）+ 当前身份（名字待命名）。修改本文件=修改灵魂，须记录告知。",
        "关联": "loader 必需；inject 第 1 步原文注入", "注释": []},
    "data/persona/default/voice/base.yaml": {
        "作用": "风格层：句长上限 25/分段规则/结构词禁用/语气对仗 4 条/句式禁忌 5 条/口语替换表/情感层 4 条。",
        "关联": "loader→layer 提示词+selfcheck", "注释": []},
    "data/persona/default/voice/no-go.md": {
        "作用": "禁区清单 5 类 18 条：客服套话/假装谦卑/假装客观/回避责任/工具化自述/表演深度——说了就不是她。",
        "关联": "selfcheck 逐条检查", "注释": []},
    "data/persona/default/model-tunings/deepseek-v4-flash.yaml": {
        "作用": "模型补偿表：flash 的自然倾向 5 条/增强 5 条/抑制 5 条/启动自检 3 条（R10 已删转换示例）。",
        "关联": "loader→layer/inject 按 model_id 选取", "注释": []},
    "data/lexicon/forest.json": {
        "作用": "句法森林词库（R10 豁免数据）：COP/AUX/NEG/ASP/MOD 各 12 词；STATE_SENSE 状态映射（累空紧/虚悬晃/痒刺胀/稳平松）；INTENT_TAG 意图映射（问探看/听接暖/接在沉/算是有）。",
        "关联": "degradation/forest.py 唯一原料", "注释": []},
    "data/lexicon/discourse.json": {
        "作用": "R24 衔接词库：过渡词 10 个；补充定义开头 5 个（豁免零衔接检查）；PROJECT 开口 2 个（那/所以）。",
        "关联": "discourse/flow.py + cohesion_check.py", "注释": []},
    "data/skills/daily-review/SKILL.md": {"作用": "技能「每日回顾」：触发 今天聊了什么/回顾一下/帮我总结；工具 recent_conversations 取回对话做小结。", "关联": "skills/loader.py；skill.py 提供 handler", "注释": []},
    "data/skills/daily-review/skill.py": {"作用": "recent_conversations 实现：查 conversations 表返回最近 N 条。", "关联": "dispatch_tool 调用", "注释": []},
    "data/skills/memory-lookup/SKILL.md": {"作用": "技能「记忆检索」：触发 还记得吗/你记得/查一下记忆；工具 memory_recall。", "关联": "loader；skill.py→memory/recall.py", "注释": []},
    "data/skills/memory-lookup/skill.py": {"作用": "memory_recall 实现：调用 recall() 检索记忆，没搜到如实说。", "关联": "memory/recall.py", "注释": []},
    "data/skills/goal-breakdown/SKILL.md": {"作用": "技能「目标拆解」：触发 不知道怎么开始/帮我拆/目标太大；工具 add_goal/list_goals。", "关联": "loader；skill.py→goals 表", "注释": []},
    "data/skills/goal-breakdown/skill.py": {"作用": "add_goal/list_goals 实现：写读 goals 表（目标栈）。", "关联": "decisions/simulate 的目标推进项", "注释": []},
    "data/skills/weather/SKILL.md": {"作用": "技能「天气查询」：触发 天气/几度/下雨吗；工具 get_weather（Open-Meteo 真实 API，无密钥）。", "关联": "loader；skill.py 走 httpx", "注释": []},
    "data/skills/weather/skill.py": {"作用": "get_weather 实现：Open-Meteo 地理编码+当前天气。", "关联": "外部 API（降级时不可用则由 L1b 接管）", "注释": []},
    "data/skills/writing-polish/SKILL.md": {"作用": "技能「写作润色」：触发 帮我润色/改得通顺；无脚本纯提示词三步法（复述确认→改后版→说明改了什么）。", "关联": "loader", "注释": []},
    # ============ tests（作用=覆盖模块） ============
    "tests/conftest.py": {"作用": "全局测试配置：关 R0 探测与生命循环（测试离线化）。", "关联": "pytest 自动加载", "注释": []},
    "tests/test_life.py": {"作用": "R0/R1/R16/R17：PRAGMA/种子边/Key 探测三态/tick 回充/静默规划/校验拒写/检查点环形/回滚。", "关联": "life/*、db、llm.probe", "注释": []},
    "tests/test_acceptance.py": {"作用": "验收 #3/#4/#18/#22：时移 1h/2h、decide 回滚专项、归档与痕迹衰减。", "关联": "life/*、engine", "注释": []},
    "tests/test_degradation.py": {"作用": "R18′：L1a/L1b/L2/恢复锁定/温度补偿/动态超时/可靠性下限/情绪快照恢复/熔断配额/suspended 续传淘汰/振荡检测/森林锚定兜底。", "关联": "degradation/*、tasks", "注释": []},
    "tests/test_r11_r15.py": {"作用": "R11′–R15′：内稳态扣减封顶/代谢排序/情感门控/pending κ/p_self 事件/习惯化/CONFRONT/PE 累积消减归一化/G 全分解。", "关联": "life/homeostasis、stimulus、self_model、simulate", "注释": []},
    "tests/test_discourse.py": {"作用": "R24：五焦点映射/短轮三选一/意图分类/PROJECT 开口/回指删词/过渡插入/指代替换/50 轮零衔接。", "关联": "discourse/flow、cohesion_check", "注释": []},
    "tests/test_static_output.py": {"作用": "验收 #7 零静态输出 grep：return/f-string 中文句零匹配（豁免清单：内部枚举/注入/工具回传）。", "关联": "全 app/*.py 静态扫描", "注释": []},
    "tests/test_decisions.py": {"作用": "决策层：红线/反驳五色/深思留痕/收束三分支/话术零兜底/驱动 RPE/目标争取得胜。", "关联": "decisions/*", "注释": []},
    "tests/test_emotion.py": {"作用": "情绪与时钟：检测/更新/衰减/调制/安抚/闪光灯/孤独公式/离线漂移/目标焦虑/风险负情绪。", "关联": "emotion/*", "注释": []},
    "tests/test_cognition.py": {"作用": "认知：图谱增删/深度检索防回灌/统计（含种子边）/边界升档降档/双源一致性/学习队列/自动入图。", "关联": "cognition/*", "注释": []},
    "tests/test_memory.py": {"作用": "记忆：四层写入/闪光灯阈值/永不删除/4 槽 LRU/检索排序/索引晋升/压缩。", "关联": "memory/*", "注释": []},
    "tests/test_persona.py": {"作用": "人格：加载/模型补偿/提示词全层/换人格/自检/8 步注入/七原则/冲突检测。", "关联": "persona/*、principles", "注释": []},
    "tests/test_proactive.py": {"作用": "主动对话：触发器种子/9 步链/影子模式/来源证明/预算封顶/静默排队/幽灵单条/LLM 失败不扣配额/冷却/反馈调参。", "关联": "proactive/*", "注释": []},
    "tests/test_service.py": {"作用": "服务集成：任务队列并发/幂等/失败记录/HTTP 拒绝路径/WS/日志端点/决策元数据。", "关联": "service/*、api", "注释": []},
    "tests/test_llm_client.py": {"作用": "LLM 客户端：成功/载荷/429 重试/重试耗尽/无 Key/JSON 容错（围栏/花括号）/纠正重试/限流窗口/工具循环。", "关联": "llm/client", "注释": []},
    "tests/test_recall_quality.py": {"作用": "验收 #30：20 查询召回率 ≥80%。", "关联": "memory/recall", "注释": []},
    "tests/test_security.py": {"作用": "验收 #30：注入拒绝+审计/50 轮防漂移/信念提案确认回滚。", "关联": "engine、beliefs", "注释": []},
    "tests/test_decision_suite.py": {"作用": "验收 #30：50 条套件（红线 10/冲突 10/正常 10/情绪 10/边界 10）。", "关联": "engine", "注释": []},
    "tests/test_skills.py": {"作用": "技能：发现/触发匹配/字段/决策选技能/执行/工具声明/OpenAI 格式/分发。", "关联": "skills/*", "注释": []},
    "tests/test_ui.py": {"作用": "UI：首页可服务。", "关联": "ui", "注释": []},
    # ============ scripts ============
    "scripts/acceptance_live.py": {"作用": "验收收尾批主脚本：#8 深聊指标/#13 评测集/#15 真发/#16 能力页/#29 素材。", "关联": "需真实 Key；写 data/logs/acceptance_results.json", "注释": []},
    "scripts/acc_8.py": {"作用": "#8 三采样相关系数（P2-11 中位数口径），跑前保存检查点跑后回滚。", "关联": "api._chat_core", "注释": []},
    "scripts/acc_final.py": {"作用": "#8 分组均值 + #29 复检，同样检查点保护。", "关联": "api._chat_core", "注释": []},
    "scripts/diag_13.py": {"作用": "#13 逐条错判诊断（情绪+分诊全列）。", "关联": "cognition/judge", "注释": []},
    "scripts/build_archive.py": {"作用": "本生成器：把全项目文件+META 注释打成《她-完整代码档案.md》。", "关联": "代码变更后重跑一次即同步", "注释": []},
}

# 档案包含顺序
ORDER = [
    ("一、根目录", ["start.bat", "requirements.txt", "README.md", ".env.example", ".gitignore"]),
    ("二、地基", ["app/db.py", "app/config.py", "app/logging_setup.py"]),
    ("三、常驻生命层", ["app/life/state.py", "app/life/loop.py", "app/life/planning.py",
                    "app/life/homeostasis.py", "app/life/self_model.py", "app/life/stimulus.py",
                    "app/life/maintenance.py"]),
    ("四、降级系统", ["app/degradation/engine.py", "app/degradation/forest.py",
                   "app/degradation/intent.py"]),
    ("五、话语流", ["app/discourse/flow.py", "app/llm/cohesion_check.py"]),
    ("六、决策系统", ["app/decisions/engine.py", "app/decisions/simulate.py",
                   "app/decisions/refusal.py", "app/decisions/rebuttal.py",
                   "app/decisions/followup.py", "app/decisions/drives.py"]),
    ("七、情绪与时钟", ["app/emotion/state.py", "app/emotion/clock.py",
                    "app/emotion/subjective.py"]),
    ("八、认知系统", ["app/cognition/network.py", "app/cognition/boundaries.py",
                   "app/cognition/learn.py", "app/cognition/hooks.py",
                   "app/cognition/judge.py", "app/cognition/audit.py"]),
    ("九、记忆", ["app/memory/store.py", "app/memory/recall.py", "app/memory/compact.py"]),
    ("十、人格与原则", ["app/persona/loader.py", "app/persona/layer.py",
                    "app/persona/inject.py", "app/principles.py", "app/identity/beliefs.py"]),
    ("十一、主动对话", ["app/proactive/settings.py", "app/proactive/triggers.py",
                     "app/proactive/engine.py", "app/proactive/scheduler.py"]),
    ("十二、服务与技能", ["app/service/tasks.py", "app/service/ws.py", "app/skills/loader.py"]),
    ("十三、LLM 与接线", ["app/llm/client.py", "app/llm/params.py", "app/main.py",
                       "app/api.py", "app/ui.py", "app/static/index.html"]),
    ("十四、数据层", ["data/persona/default/identity.md", "data/persona/default/voice/base.yaml",
                   "data/persona/default/voice/no-go.md",
                   "data/persona/default/model-tunings/deepseek-v4-flash.yaml",
                   "data/lexicon/forest.json", "data/lexicon/discourse.json",
                   "data/skills/daily-review/SKILL.md", "data/skills/daily-review/skill.py",
                   "data/skills/memory-lookup/SKILL.md", "data/skills/memory-lookup/skill.py",
                   "data/skills/goal-breakdown/SKILL.md", "data/skills/goal-breakdown/skill.py",
                   "data/skills/weather/SKILL.md", "data/skills/weather/skill.py",
                   "data/skills/writing-polish/SKILL.md"]),
    ("十五、测试", ["tests/conftest.py", "tests/test_life.py", "tests/test_acceptance.py",
                 "tests/test_degradation.py", "tests/test_r11_r15.py", "tests/test_discourse.py",
                 "tests/test_static_output.py", "tests/test_decisions.py",
                 "tests/test_emotion.py", "tests/test_cognition.py", "tests/test_memory.py",
                 "tests/test_persona.py", "tests/test_proactive.py", "tests/test_service.py",
                 "tests/test_llm_client.py", "tests/test_recall_quality.py",
                 "tests/test_security.py", "tests/test_decision_suite.py",
                 "tests/test_skills.py", "tests/test_ui.py"]),
    ("十六、脚本", ["scripts/acceptance_live.py", "scripts/acc_8.py", "scripts/acc_final.py",
                 "scripts/diag_13.py", "scripts/build_archive.py"]),
]


def main():
    lines: list[str] = []
    total_files = 0
    total_code_lines = 0
    lines.append("> 自动生成：`python scripts/build_archive.py`（代码变更后重跑即同步）")
    lines.append("> 每个文件包含：作用 / 关联 / 关键注释 / 完整源码")
    lines.append("> 两条主线：对话流水线（api→decisions→judge→emotion→degradation→llm→cohesion→memory）"
                 "与后台常驻（life.loop 60s tick→静默规划/检查点/维护钩子 + degradation 探测 + proactive 心跳 + tasks 队列）")
    lines.append("")
    toc = ["## 目录"]
    for title, _files in ORDER:
        toc.append(f"- {title}")
    lines.extend(toc)
    lines.append("")

    for title, files in ORDER:
        lines.append(f"---\n\n# {title}\n")
        for rel in files:
            path = ROOT / rel
            if not path.is_file():
                lines.append(f"## {rel}\n\n> ⚠ 文件不存在\n")
                continue
            total_files += 1
            meta = META.get(rel, {"作用": "", "关联": "", "注释": []})
            text = path.read_text(encoding="utf-8", errors="replace")
            total_code_lines += text.count("\n") + 1
            lines.append(f"## {rel}\n")
            lines.append(f"- **作用**：{meta.get('作用', '')}")
            lines.append(f"- **关联**：{meta.get('关联', '')}")
            for note in meta.get("注释", []):
                lines.append(f"- 关键注释：{note}")
            ext = rel.rsplit(".", 1)[-1] if "." in rel else ""
            lang = {"py": "python", "bat": "batch", "yaml": "yaml", "json": "json",
                    "md": "markdown", "html": "html", "txt": "text"}.get(ext, "")
            lines.append("")
            lines.append(f"```{lang}")
            lines.append(text.rstrip("\n"))
            lines.append("```")
            lines.append("")

    head = [
        "# 她 · 完整代码档案\n",
        f"> 自动生成 ｜ 共 {total_files} 个文件 ｜ 代码 {total_code_lines} 行 ｜ 生成命令：`python scripts/build_archive.py`\n",
        "> 每个文件含：作用 / 关联 / 关键注释 / 完整源码（密钥 .env 不入档案）\n",
    ]
    lines = head + lines
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK：{OUT}（{total_files} 文件，{total_code_lines} 行代码，{OUT.stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    main()
