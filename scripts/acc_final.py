"""验收收尾：单样本分组均值（#8 趋势）+ #29 修复后复检。"""
import asyncio
import json
import os
import re
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
FRAGMENTS = ["然后呢", "嗯", "你觉得呢", "为什么", "好吧", "真的吗", "之后呢",
             "我不太想去", "可是很累", "再想想", "那就去呗", "几点出发",
             "带什么", "水呢", "行", "然后", "还有呢", "没了吧", "行吧", "睡了"]


def budget() -> float:
    return float(db.conn().execute(
        "SELECT budget FROM homeostatic_state WHERE id=1").fetchone()["budget"])


async def main():
    save_checkpoint(db, tick=888)
    # --- #8 单样本：分组均值 ---
    db.conn().execute("UPDATE homeostatic_state SET budget=0.7 WHERE id=1")
    db.conn().commit()
    pairs = []
    for i, msg in enumerate(TOPICS, 1):
        reply, _ = await _chat_core(ChatRequest(message=msg))
        pairs.append({"round": i, "budget": budget(), "len": len(reply or "")})
    first5 = sum(p["len"] for p in pairs[:5]) / 5
    last5 = sum(p["len"] for p in pairs[-5:]) / 5
    mid = sum(p["len"] for p in pairs[7:12]) / 5
    print(f"#8 单样本：前5轮均值 {first5:.1f} 字 / 中5轮 {mid:.1f} / 后5轮 {last5:.1f}")
    print(f"#8 budget 轨迹：{pairs[0]['budget']:.3f} → {pairs[-1]['budget']:.3f}")
    with open("data/logs/acc_8_detail.json", "w", encoding="utf-8") as f:
        json.dump({"first5_mean": first5, "mid5_mean": mid, "last5_mean": last5,
                   "pairs": pairs}, f, ensure_ascii=False, indent=2)

    # --- #29 复检（新 cohesion：引号内合并） ---
    zero = 0
    for i, frag in enumerate(FRAGMENTS, 1):
        reply, decision = await _chat_core(ChatRequest(message=frag))
        sents = [s for s in re.split(r"[。！？]", reply or "") if s.strip()]
        bad = False
        if len(sents) >= 2:
            second = sents[1].strip().lstrip('"「」』，,。')
            from app.discourse.flow import (ELABORATION_OPENINGS, TRANSITIONS)
            if not (second.startswith(tuple(TRANSITIONS))
                    or second.startswith(tuple(ELABORATION_OPENINGS))
                    or "，" in (reply or "")):
                bad = True
        if bad:
            zero += 1
            print(f"  [29] ⚠ 第{i}轮「{frag}」：{reply}")
    print(f"#29 复检：20 轮，零衔接 {zero} 处")
    rollback_to_last_checkpoint(db)
    print("已回滚到验收前检查点")


asyncio.run(main())
