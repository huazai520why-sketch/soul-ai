#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Soul 每日免费领 Soul 币（签到）—— 幂等，一天只领一次。

入口路径（2026-09-30 实测）：
    星球 tab(79,1263)
      → 顶部卡片「签到免费领Soul币」(≈464,178)
      → 福利页「免费Soul币」(≈516,354)
      → 「立即签到」(≈360,749)
      → 结果页：「今日已签到」+「Soul币 +N」+「已连续签到N天」

⚠️ 安全约束（别删）：
  福利页里混着「特价道具卡 / 购买3张 / 购买5张 / 立即使用 / 新手体验价 / 2.3折」等**付费项**，
  本脚本一律 **不点** —— 命中即报错退出（宁可吵闹失败，也不许点错花真钱）。

幂等：进到签到页先看有没有「今日已签到」，有就直接返回，不再点「立即签到」。

用法：
    bash soul.sh soul_checkin.py          # 正常领
    bash soul.sh soul_checkin.py --dry    # 只走流程不点「立即签到」（调试用）

退出码：0=已签到/领取成功  2=页面走不通（要看日志）  3=疑似点到付费项（已中止）
"""
import sys, os, time, io

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 付费/高危词：命中即中止（出现在"我要点的目标"里就拒绝）
DANGER = ("购买", "立即使用", "体验价", "折", "开通", "支付", "充值", "续费")

TAB_PLANET = (99, 1579)          # 底导航「星球」兜底坐标（2026-09-30 ×1.25 重标定）

LOG = os.path.join(BASE, "logs", "checkin.log")


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    print(msg)


def screen(tag=""):
    import soul_read as rd
    ns = rd.items()
    if tag:
        print("  [屏·%s] %d 项" % (tag, len(ns)))
    return ns


def pick(ns, keywords, ymax=None, ymin=None):
    """在 OCR 项里按关键词依次找；返回 (文本,x,y)"""
    for kw in keywords:
        for t, cx, cy in ns:
            if kw in t:
                if ymax is not None and cy > ymax:
                    continue
                if ymin is not None and cy < ymin:
                    continue
                return (t, cx, cy)
    return None


def guard(t):
    """点到付费项就炸"""
    for d in DANGER:
        if d in t:
            log("⛔ 中止：目标文本疑似付费项 %r（含 %r），没点" % (t, d))
            return False
    return True


def main(argv):
    dry = "--dry" in argv
    import soul
    import soul_reply as R

    soul.ensure_foreground()

    # 1) 先摆正到聊天列表（已知好状态）
    ok = False
    for i in range(3):
        ok = R._goto_chat_list()
        if ok:
            break
        time.sleep(1.5)
    if not ok:
        log("❌ 起手没回到聊天列表，放弃（避免点错页）")
        return 2
    log("① 已回聊天列表")

    # 2) 进星球 tab
    ns = screen("聊天列表")
    tab = pick(ns, ["星球"], ymin=1100)
    tx, ty = (tab[1], tab[2]) if tab else TAB_PLANET
    log("② 点星球 tab (%d,%d)%s" % (tx, ty, "" if tab else " [OCR未命中→用兜底坐标]"))
    soul.tap(tx, ty)
    time.sleep(2.5)

    # 3) 点「签到免费领Soul币」卡片（在页面上半部）
    ns = screen("星球")
    ent = pick(ns, ["签到免费领", "免费领Soul币", "领Soul币", "签到"], ymax=400)
    if not ent:
        log("❌ 星球页没找到签到入口，当前页文字：" + " | ".join(t for t, _, _ in ns[:25]))
        return 2
    if not guard(ent[0]):
        return 3
    log("③ 点入口 %r (%d,%d)" % (ent[0], ent[1], ent[2]))
    soul.tap(ent[1], ent[2])
    time.sleep(2.5)

    # 4) 福利页 → 「免费Soul币」
    ns = screen("福利页")
    card = pick(ns, ["免费Soul币", "签到领Soul币"])
    if not card:
        # 可能直接就落在签到页了
        if pick(ns, ["立即签到", "今日已签到"]):
            log("   直接落在签到页，跳过卡片")
        else:
            log("❌ 福利页没找到「免费Soul币」，当前页文字：" + " | ".join(t for t, _, _ in ns[:25]))
            return 2
    else:
        if not guard(card[0]):
            return 3
        log("④ 点卡片 %r (%d,%d)" % (card[0], card[1], card[2]))
        soul.tap(card[1], card[2])
        time.sleep(2.5)
        ns = screen("签到页")

    # 5) 幂等判断 + 签到
    done = pick(ns, ["今日已签到"])
    if done:
        log("✅ 今天已经签过了（%r），不重复点" % (done[0],))
        R._goto_chat_list()
        return 0

    btn = pick(ns, ["立即签到"])
    if not btn:
        log("❌ 没找到「立即签到」，也没显示已签到 —— 页面不对。文字：" + " | ".join(t for t, _, _ in ns[:25]))
        return 2
    if not guard(btn[0]):
        return 3
    if dry:
        log("--dry：只演练，未点「立即签到」")
        R._goto_chat_list()
        return 0

    log("⑤ 点「立即签到」(%d,%d)" % (btn[1], btn[2]))
    soul.tap(btn[1], btn[2])
    time.sleep(3.0)

    # 6) 核验
    ns = screen("结果页")
    got = pick(ns, ["Soul币 +", "Soul币+", "+1", "+2", "+3", "+5", "+10"])
    signed = pick(ns, ["今日已签到", "已连续签到", "已签到"])
    if signed:
        log("✅ 签到成功：%s%s" % (signed[0], (" ｜ " + got[0]) if got else ""))
        R._goto_chat_list()
        return 0
    log("⚠️ 点了但没核验到「已签到」，页面文字：" + " | ".join(t for t, _, _ in ns[:25]))
    R._goto_chat_list()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
