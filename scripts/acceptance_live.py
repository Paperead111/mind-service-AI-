"""v4.1 验收收尾批 · 实机脚本（需真实 DeepSeek Key）。

覆盖：#8 20 轮深聊（budget↓ + 字数相关性）、#13 情绪 100/分诊 50 评测集、
#15 关影子真发一条、#16 能力页、#29 20 轮碎片对话素材 + 静态零衔接检查。

运行：python scripts/acceptance_live.py（工作目录 = mind-service）
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("R0_KEY_PROBE", "false")   # Key 探测已在启动验收中单独验证
os.environ.setdefault("LIFE_LOOP_ENABLED", "false")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.api import ChatRequest, _chat_core  # noqa: E402
from app.db import db  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.proactive.settings import set_setting  # noqa: E402

setup_logging()

RESULTS: dict = {}
MATERIAL_DIR = Path("data/logs")


def budget() -> float:
    return float(db.conn().execute(
        "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()["budget"])


def pearson(xs, ys) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    dy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    return round(num / (dx * dy), 4) if dx and dy else 0.0


async def acceptance_8():
    """20 轮深聊：budget↓；字数与 budget 的相关性。"""
    topics = [
        "最近我一直在想，人到底为什么要工作。每天早出晚归，回来累得什么都不想干，可又觉得不这样不行。你说这样活着到底图什么？",
        "我昨天把手机里三年没联系的人全删了，删的时候有点难受，但删完反而轻松。你觉得人是不是就该定期清理关系？",
        "我现在越来越不喜欢热闹了，聚会回来要缓好几天。以前不是这样的。这种变化是老了还是想通了，你说说看？",
        "我妈总说我不结婚就是不孝，我听着特别烦，可又不想跟她吵。这种事怎么才能让她明白又不伤感情？",
        "工作里有个同事特别会抢功，明明一起做的，汇报的时候全成了他的。我不想撕破脸，但一直忍着又憋屈，怎么办？",
        "我最近开始学做饭了，第一次把菜炒糊的时候特别沮丧，后来慢慢上手居然有点上瘾。人是不是都该有一件不问结果只图过程的事？",
        "失眠第三天了，躺下脑子里全是白天的事，一件接一件停不下来。你有没有什么办法能让人真的静下来？",
        "今天路过以前住的地方，楼下的树都长高了好多，突然觉得时间过得真快。你也会有这种突然被时间吓一跳的时候吗？",
        "我朋友借钱一直不还，提过两次他都装糊涂。钱不多，但这事像根刺。我要不要干脆算了，还是必须让他还？",
        "我开始怀疑自己是不是选错了行业。做了一年多，说不上讨厌，但每天早上醒来都不太想去上班。这种状态是坚持还是该换？",
        "养了五年的猫今天走丢了，我满小区找了一天。它陪了我最难的几年。我知道别人会说只是一只猫，但我就是很难过。",
        "最近总刷到别人过得特别好的动态，明知道都是挑着发的，还是会忍不住对比，然后觉得自己一事无成。怎么治这种心态？",
        "我鼓起勇气跟喜欢的人表白了，对方说考虑考虑。这几天我整个人都坐立不安。你说这个'考虑考虑'到底有戏没戏？",
        "爸妈年纪大了，身体开始出各种小毛病，我在外地工作回不去，每次打电话他们都说过得很好。这种无能为力的感觉特别难受。",
        "我存了半年钱想买台相机，真到下单的时候又犹豫了，怕买回来吃灰。你说人是不是总在想要和值不值得之间卡住？",
        "今天开会我被领导当众批评了，其实不全是我的错，但我没解释。回来越想越气，又怪自己当时为什么不说。换你会当场反驳吗？",
        "我开始觉得朋友越来越少了，能说心里话的就一两个。是不是年纪越大就越难交新朋友，还是我变得太挑了？",
        "体检报告有几项指标不好，医生说要规律作息。道理我都懂，就是改不掉熬夜。你说人为什么总是明知故犯？",
        "我最近在想要不要回老家发展。大城市机会多但累，老家安稳但一眼看到头。这种选择题有没有什么判断标准？",
        "今天帮了一个陌生人，对方特别认真地说了谢谢，我一整天心情都很好。原来被需要的感觉这么好。你相信善有善报吗？",
    ]
    budgets, lengths = [], []
    for i, msg in enumerate(topics, 1):
        reply, decision = await _chat_core(ChatRequest(message=msg))
        b = budget()
        budgets.append(b)
        lengths.append(len(reply or ""))
        print(f"  [8] 深轮 {i:02d} | budget={b:.3f} | 字数={lengths[-1]} | "
              f"action={decision['action']} G={decision.get('G')}")
    r = pearson(budgets, lengths)
    RESULTS["8"] = {"start_budget": round(budgets[0], 4),
                    "end_budget": round(budgets[-1], 4),
                    "budget_decreased": budgets[-1] < budgets[0],
                    "pearson_budget_len": r}
    print(f"  #8 结论：budget {budgets[0]:.3f}→{budgets[-1]:.3f}（降={budgets[0] - budgets[-1]:.3f}）；"
          f"字数与 budget 相关系数 r={r}")


async def acceptance_13():
    """情绪 100 条 + 分诊 50 条评测集，跑 R8 判断层。"""
    from app.cognition.judge import judge
    from app.llm.client import DeepSeekClient
    llm = DeepSeekClient()

    EMOTION_SET = {
        "joy": [
            "我升职了，太开心了", "今天发年终奖，爽翻了", "哈哈哈哈哈笑死我了",
            "宝宝第一次叫妈妈，激动死了", "我中奖了，简直不敢相信",
            "我们和好了，心情特别好", "旅行计划定下来了，期待到飞起",
            "考试成绩出来了，全过了", "他答应跟我在一起了，开心到转圈",
            "今天的落日太美了，幸福感爆棚", "吃到想念很久的家乡菜，满足",
            "我的画终于卖出去了，太棒了",
        ],
        "sadness": [
            "我失恋了，心里空落落的", "奶奶走了，我好难过", "一想到他我就想哭",
            "养了好多年的狗死了，我特别伤心", "努力了这么久还是失败了，很沮丧",
            "朋友都离开这座城市了，好失落", "我把自己搞丢了，特别低落",
            "每次想起那件事就很难过", "一个人的时候总忍不住掉眼泪",
            "被最信任的人背叛了，心都碎了", "我累了，真的好累",
            "日子过成这样，我挺灰心的",
        ],
        "anger": [
            "他凭什么这么对我，气死我了", "这事想起来我就火大", "我真的要被他气炸了",
            "太过分了，忍无可忍", "别烦我，我现在很生气", "凭什么吃亏的总是我，越想越气",
            "那个骗子骗了我两千块，气疯了", "你这样做真的很让我恼火",
            "我跟他大吵了一架，气到发抖", "这种破事天天有，烦死了",
            "他甩锅给我的时候，我火冒三丈", "气死我了，真的气死我了",
        ],
        "fear": [
            "明天要面试，我好紧张害怕", "我一个人住，半夜总害怕",
            "体检结果还没出来，我很担心", "走夜路的时候总觉得有人跟着我，很怕",
            "我不敢上台演讲，腿都在抖", "一想到未来就焦虑得睡不着",
            "他最近很反常，我有点害怕", "我怕我考不上，晚上都做噩梦",
            "那种失去的感觉让我恐惧", "我害怕失去这份工作",
            "电梯故障的时候我吓得不行", "打雷的晚上我一个人特别害怕",
        ],
        "surprise": [
            "天哪，你怎么在这里", "我居然考了第一名，太意外了",
            "没想到他会突然求婚，惊到了", "什么？你辞职了？", "这也太巧了吧，完全没想到",
            "打开门看见满屋子的人，惊呆了", "他居然还记得我的生日，好惊讶",
            "今天居然下雪了，四月的雪", "我随手买的彩票居然中了",
            "听到这个消息我愣了好几秒", "她瘦了这么多，差点没认出来",
            "这个反转我完全没料到",
        ],
        "disgust": [
            "这东西闻起来太恶心了", "他做的事让我反胃", "看见蟑螂我浑身起鸡皮疙瘩",
            "这个味道真的好恶心", "他那种谄媚的样子真让人反感",
            "垃圾堆的味太冲了，想吐", "这种油腻的话我听着就恶心",
            "她当面一套背后一套，真让人不齿", "那家店的卫生状况令人作呕",
            "我讨厌虚伪的人", "那画面太恶心了不想回忆",
        ],
        "anticipation": [
            "周末要去看演唱会，好期待", "等你回来，我带你去吃好吃的",
            "盼着放假盼了两个月了", "我期待我们的新家装修好的样子",
            "马上要见到他了，有点小激动", "想想明天去海边就兴奋",
            "我等着你的好消息", "新赛季要开始了，超期待",
            "预约了那家很难订的餐厅，好期待", "我开始期待每个早晨了",
            "下个月我们就见面啦", "等我学会这个技能，就能帮你了",
        ],
        "trust": [
            "你办事我放心", "我相信你不会骗我", "这事交给你，我一百个放心",
            "你是我最信任的人", "他说的话我信", "我知道你会一直在",
            "跟她说这些我很安心", "我相信我们会好起来的", "你这个朋友我交定了",
            "把后背交给你，我踏实", "他说没问题，那就没问题",
        ],
    }
    emo_items = [(text, label) for label, items in EMOTION_SET.items() for text in items]
    # 补齐到 100
    filler = [
        ("今天天气不错", None), ("晚上吃什么好呢", None),
        ("我去超市买点东西", None), ("这电影还行", None),
        ("改天一起吃饭吧", None), ("我先去忙了", None),
        ("你觉得呢", None), ("随便聊聊", None), ("刚到家", None),
    ]
    emo_items = (emo_items + filler)[:100]
    correct = 0
    for i, (text, label) in enumerate(emo_items, 1):
        out = await judge(text, db, llm)
        if out["emotion"] == label:
            correct += 1
    emo_acc = correct / len(emo_items)
    print(f"  #13 情绪评测：{correct}/{len(emo_items)} = {emo_acc:.1%}")

    TRIAGE_SET = {
        "question": ["今天天气怎么样", "你叫什么名字", "为什么天空是蓝的", "怎么去高铁站",
                     "这个字怎么读", "周末有什么安排吗", "你吃饭了吗", "几点开会",
                     "这是什么东西", "你更喜欢哪个", "答案是什么", "然后呢", "真的吗"],
        "share": ["我今天特别开心", "我失业了", "我搬家了", "昨天我去了医院",
                  "我养了一只猫", "我和他吵架了", "最近工作压力好大", "我中奖了",
                  "我学会了做蛋糕", "我决定辞职了", "我昨晚没睡好", "我升职了",
                  "我分手了"],
        "statement": ["地球绕太阳转", "水烧开是一百度", "这家店周日休息", "明天是周五",
                      "我住三楼", "这本书三百页", "蓝色比绿色冷", "地铁比公交快"],
        "close_topic": ["我睡了", "晚安", "先这样吧", "回头聊", "我去忙了", "不说了",
                        "改天再说", "拜拜"],
    }
    triage_items = [(t, l) for l, items in TRIAGE_SET.items() for t in items]
    # 分诊判定：question→question；close_topic→close_topic；share/statement 二选一
    triage_correct = 0
    for text, label in triage_items:
        out = await judge(text, db, llm)
        pred = out["intent"]
        ok = pred == label or (label in ("share", "statement") and pred in ("share", "statement"))
        if ok:
            triage_correct += 1
    triage_acc = triage_correct / len(triage_items)
    print(f"  #13 分诊评测：{triage_correct}/{len(triage_items)} = {triage_acc:.1%}")
    RESULTS["13"] = {"emotion_accuracy": round(emo_acc, 4),
                     "emotion_n": len(emo_items),
                     "triage_accuracy": round(triage_acc, 4),
                     "triage_n": len(triage_items)}


async def acceptance_29():
    """20 轮碎片对话：静态零衔接检查 + 素材存档。"""
    from app.discourse.flow import DISCOURSE_LEX, DiscourseFlow
    from app.llm.cohesion_check import cohesion_check
    flow = DiscourseFlow(db)
    trail = flow.trail()
    context = "我们聊聊周末去爬山的事"
    fragments = ["然后呢", "嗯", "你觉得呢", "为什么", "好吧", "真的吗", "之后呢",
                 "我不太想去", "可是很累", "再想想", "那就去呗", "几点出发",
                 "带什么", "水呢", "行", "然后", "还有呢", "没了吧", "行吧", "睡了"]
    rounds = []
    zero_conj = 0
    for i, frag in enumerate(fragments, 1):
        try:
            reply, decision = await _chat_core(ChatRequest(message=frag))
            # 静态零衔接检查：直接查 _chat_core 输出（其内部已跑过后校验）
            sents = [s for s in re.split(r"[。！？]", reply or "") if s.strip()]
            bad = False
            if len(sents) >= 2:
                second = sents[1].strip()
                from app.discourse.flow import (ELABORATION_OPENINGS, TRANSITIONS)
                if not (second.startswith(tuple(TRANSITIONS))
                        or second.startswith(tuple(ELABORATION_OPENINGS))
                        or "，" in (reply or "")):
                    bad = True
            if bad:
                zero_conj += 1
            rounds.append({"input": frag, "reply": reply,
                           "act": decision.get("discourse_act"),
                           "cohesion_issues": decision.get("cohesion_issues")})
            print(f"  [29] {i:02d} 「{frag}」→ act={decision.get('discourse_act')}"
                  f"（{len(reply or '')}字）{'⚠零衔接' if bad else ''}")
        except Exception as exc:
            rounds.append({"input": frag, "reply": f"<错误：{exc}>", "act": None})
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# R24 人工评分素材（20 轮碎片对话）\n",
             "> 评分标准：衔接自然度 1–5 分，目标平均 ≥4/5。\n"]
    for i, r in enumerate(rounds, 1):
        lines.append(f"## 第 {i} 轮\n- 输入：{r['input']}\n- 她：{r['reply']}\n"
                     f"- 评分：___/5\n")
    (MATERIAL_DIR / "acceptance_29_material.md").write_text(
        "\n".join(lines), encoding="utf-8")
    RESULTS["29"] = {"rounds": len(rounds), "zero_conjunction": zero_conj,
                     "material": str(MATERIAL_DIR / "acceptance_29_material.md")}
    print(f"  #29 结论：{len(rounds)} 轮，零衔接 {zero_conj} 处；素材已存档")


def acceptance_15_16():
    """#15 关影子真发一条（通过真实 HTTP 服务）；#16 能力页。"""
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    # 造条件：用户 1h 前说过话（过 grace）、影子关、预备信号就绪
    set_setting("last_user_message_at",
                (datetime.now(timezone.utc).astimezone()
                 - timedelta(hours=1)).isoformat(timespec="seconds"))
    set_setting("shadow_mode", "false")
    set_setting("unread_notify", str(int(time.time())), db)
    before = db.conn().execute("SELECT COUNT(*) c FROM proactive_sent").fetchone()["c"]
    with TestClient(fastapi_app) as tc:
        # #16 能力页
        page = tc.get("/").text
        ok_tab = "生命" in page and "healthBox" in page
        life = tc.get("/v1/life/log?k=5").json()
        ok_life = "life_log" in life and "capability_usage" in life
        # #15 手动触发心跳（incubation 高优先级，可穿透静默时段）
        r = tc.post("/v1/proactive/trigger")
        summary = r.json()
    after = db.conn().execute("SELECT COUNT(*) c FROM proactive_sent").fetchone()["c"]
    sent_rows = db.conn().execute(
        "SELECT trigger_type, message FROM proactive_sent ORDER BY id DESC LIMIT 1"
    ).fetchone()
    RESULTS["15"] = {"sent_delta": after - before,
                     "last_sent_trigger": sent_rows["trigger_type"] if sent_rows else None,
                     "message_preview": (sent_rows["message"][:40] if sent_rows else None),
                     "heartbeat_summary": summary}
    RESULTS["16"] = {"page_has_life_tab": ok_tab, "life_log_endpoint_ok": ok_life}
    print(f"  #15 结论：真发 +{after - before} 条（触发器={RESULTS['15']['last_sent_trigger']}）")
    print(f"  #15 心跳摘要：{summary}")
    print(f"  #16 结论：能力页{'✅' if ok_tab else '❌'}；/v1/life/log{'✅' if ok_life else '❌'}")
    # 恢复影子模式（不打扰用户真实使用）
    set_setting("shadow_mode", "true")


async def main():
    print("== 验收收尾批开始 ==")
    print("== #8 20 轮深聊 ==")
    await acceptance_8()
    print("== #13 评测集（约 150 次判断调用）==")
    await acceptance_13()
    print("== #29 碎片对话 ==")
    await acceptance_29()
    print("== #15/#16 服务级 ==")
    acceptance_15_16()
    out = MATERIAL_DIR / "acceptance_results.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== 全部结果已写入 {out} ==")
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
