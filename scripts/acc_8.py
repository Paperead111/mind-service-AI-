"""#8 三采样：20 轮深聊 × 3 组，取 budget-字数相关系数中位数（P2-11 裁定）。

运行前保存检查点，跑完回滚，不污染真实状态。
"""
import asyncio
import os
import sys

os.environ.setdefault("R0_KEY_PROBE", "false")
os.environ.setdefault("LIFE_LOOP_ENABLED", "false")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.api import ChatRequest, _chat_core
from app.db import db
from app.logging_setup import setup_logging
from app.life.state import rollback_to_last_checkpoint, save_checkpoint

setup_logging()

TOPICS = [
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


async def one_sample(sample: int):
    db.conn().execute("UPDATE homeostatic_state SET budget=0.7 WHERE id=1")
    db.conn().commit()
    budgets, lengths = [], []
    for i, msg in enumerate(TOPICS, 1):
        reply, decision = await _chat_core(ChatRequest(message=msg))
        budgets.append(budget())
        lengths.append(len(reply or ""))
        print(f"  [样本{sample}] 轮{i:02d} budget={budgets[-1]:.3f} 字数={lengths[-1]}")
    r = pearson(budgets, lengths)
    print(f"  [样本{sample}] r={r}（budget {budgets[0]:.3f}→{budgets[-1]:.3f}）")
    return r


async def main():
    save_checkpoint(db, tick=999)
    rs = []
    for s in (1, 2, 3):
        rs.append(await one_sample(s))
    rs.sort()
    median = rs[len(rs) // 2]
    print(f"== #8 三采样 r = {rs}，中位数 = {median} ==")
    rollback_to_last_checkpoint(db)
    print("已回滚到验收前检查点")


asyncio.run(main())
