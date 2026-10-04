# -*- coding: utf-8 -*-
"""Soul 自动化调度器（每小时跑一轮）—— 带**时段判断**

用户规则（2026-09-29）：
  · **白天 08:00~19:00：只聊天（回消息），不匹配、不广场、不主动奇遇铃**
  · **夜间 19:00~次日 08:00：完整流程**（奇遇铃 → 回消息 → 匹配3个 → 广场）

优先级（雷打不动）：**奇遇铃 > 回消息 > 匹配 > 广场**
  · 奇遇铃「挡路了也要先奇遇铃」（点「立即私聊」，绝不关闭）
  · 有新消息优先处理，仅次于奇遇铃（匹配/广场中途来消息也要立刻中断去回）

本脚本**只做研判**：告诉你"这一轮该干什么、有哪些活"，不代替 AI 拟回复内容。
用法：
  python soul_auto.py            # 输出本轮任务清单
  python soul_auto.py --json     # 同上，JSON 格式
"""
import sys, io, os, json
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DAY_START, DAY_END = 8, 19          # 白天 08:00~19:00 只聊天


def is_day():
    return DAY_START <= datetime.now().hour < DAY_END


def _agostr(h):
    """小时数 → 人话；None/超大值 → 无记录"""
    if h is None or h >= 1e8:
        return "无记录"
    if h < 1:
        return f"{h*60:.0f}分钟前"
    if h < 48:
        return f"{h:.0f}h前"
    return f"{h/24:.0f}天前"


def main(as_json=False, brief=False):
    import soul_global_lock as GL

    # ⭐⭐ 全局单实例锁（2026-09-29）：**上一轮没跑完，这一轮绝不能开始**
    #    否则两个实例同时操作模拟器 → 抢输入框 / 重复发送 / 点错人。
    #    判据用"心跳"：谁在跑谁 touch()；心跳 15 分钟没更新才认为上一轮不在了。
    busy, linfo = GL.lock_status()
    if busy:
        msg = f"⛔ 上一轮自动化尚未结束 → 本轮跳过。{linfo.get('why')}"
        print(msg)
        if as_json:
            print(json.dumps({"skip": True, "reason": linfo.get("why"),
                              "lock": linfo}, ensure_ascii=False, indent=1))
        return
    ok, _ = GL.start_round("auto-研判")
    if not ok:
        msg = "⛔ 拿不到全局锁 → 本轮跳过"
        print(msg)
        if as_json:
            print(json.dumps({"skip": True, "reason": msg}, ensure_ascii=False, indent=1))
        return

    import soul_monitor as M
    bell = M.check_love_bell()
    pend = M.check_pending()
    GL.touch("auto-研判完成")

    day = is_day()
    tasks = []
    if day:
        note = "白天（08:00~19:00）→ **只聊天回消息**；不匹配、不广场、不主动奇遇铃"
        if pend.get("ok") and pend["items"]:
            tasks.append({"prio": 2, "act": "reply",
                          "detail": f"回 {len(pend['items'])} 条待回消息",
                          "items": pend["items"]})
        tasks.append({"prio": 9, "act": "stop",
                      "detail": "白天无其他任务；回完即结束本轮"})
    else:
        note = "夜间（19:00~08:00）→ 完整流程"
        # ① 奇遇铃：**必须检测"当前真的弹着窗"**，不能只看文件时间
        #    （文件只记录"最近弹过"，已处理过的铃会一直被误判为待处理）
        bell_live = False
        try:
            import soul
            soul.ensure_foreground()
            bell_live = soul.is_love_bell()
        except Exception as e:
            print(f"[warn] 奇遇铃弹窗检测失败（保守当作无弹窗）: {e!r}")
        if bell_live:
            tasks.append({"prio": 1, "act": "love_bell",
                          "detail": "🔔 奇遇铃正在弹窗（第一优先，点「立即私聊」）",
                          "show_time": bell.get("show_time")})
        # ② 回消息
        if pend.get("ok") and pend["items"]:
            tasks.append({"prio": 2, "act": "reply",
                          "detail": f"回 {len(pend['items'])} 条待回消息",
                          "items": pend["items"]})
        # ③ 没消息才匹配
        if not (pend.get("ok") and pend["items"]):
            tasks.append({"prio": 3, "act": "match",
                          "detail": "匹配 3 个并发开场白（soul_match.py 3）"})
            # ④ 匹配完还没人回 → 广场
            tasks.append({"prio": 4, "act": "square",
                          "detail": "广场评论（≤10 分钟，评论入口≈(570,367)）"})

    # ⭐ 关系档案（2026-09-30 接入）：开采 im_user_bean 里闲置的关系字段
    #    数据新鲜度 OK —— 上面的 check_pending() 内部已 pull() 过两个库。
    #    整个块**不允许阻断本轮**：读不到就跳过，只提示。
    _focus_disp, _hot, _rv = {}, [], []
    _hotd, _rvd, _focusd, _inv, _rdy = [], [], {}, [], []
    try:
        import soul_stats as S
        _focus_disp = S.focus_map()
        _hot = S.activity(24)
        # 凉了要唤醒：只要「她真说过话」的（她发≥5），纯搭讪不占版面
        _rv = [u for u in S.revive(12, 72) if u["_her"] >= 5]
        # ⭐ 投入度（2026-09-30 接入）：判「谁真对我有兴趣」只认「她主动打探我几次」，
        #    不认字数/条数 —— 风止遇你 均 11.2 字却只问过 1 次，是反例。
        _inv = S.invest(12)
        _rdy = S.ready_list()
        _hotd = [S.to_dict(u) for u in _hot]
        _rvd = [S.to_dict(u) for u in _rv]
        for _n in _focus_disp:
            _pu = S.profile(_n)
            if _pu:
                _focusd[_n] = S.to_dict(_pu)
    except Exception as _e:
        print(f"[warn] 关系档案读取失败（不阻断本轮）: {_e!r}")

    result = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "period": "day" if day else "night",
        "note": note,
        "bell": bell,
        "pending_count": len(pend.get("items") or []) if pend.get("ok") else -1,
        "pending_ok": pend.get("ok", False),
        "tasks": tasks,
        "focus": _focusd,
        "hot24": _hotd,
        "revive": _rvd,
        "invest": [S.to_dict(u) for u in _inv],
        "ready": [S.to_dict(u) for u in _rdy],
        "lock": GL.read_lock(),
    }

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return

    # ── 精简模式（2026-09-30）：给每轮开局省 token ──────────────────────
    #    保留**唯一必须先读**的 ⭐重点对象段（含用户指定对象的档案+状态），
    #    其余三个榜单压成一行名字；需要细节时用 `soul_stats.py who 昵称`。
    #    实测：完整约 2400 字符 → 精简约 800 字符，省 ~1600/轮。
    if brief:
        _p = pend.get("items") or [] if pend.get("ok") else []
        print("=" * 62)
        print(f"【本轮】{result['period']} | 待回 {len(_p) if pend.get('ok') else '?'} | "
              f"奇遇铃 {'弹窗中' if bell.get('active') else '无'} | "
              f"够格推进 {len(_rdy)}人 | 凉了待唤醒 {len(_rv)}人")
        for _it in _p[:5]:
            print(f"   待回: {_it.get('name','')}  {str(_it.get('text') or '')[:30]}")
        if _focus_disp:
            print("-" * 62)
            print("⭐ 重点对象（必读）:")
            for _n, _v in _focus_disp.items():
                print(f"   ★ {_n} [{_v.get('level','重点')}]")
                _pu = _focusd.get(_n)
                if _pu:
                    _rel = ("ta已关注我" if _pu.get("followed") else "ta未关注") + "/" + \
                           ("我已关注" if _pu.get("follow") else "我未关注")
                    print(f"     {_rel} | 她发{_pu['her_msgs']}条·轮{_pu['rounds']}·{_pu['src']} | "
                          f"她主动问过我 {_pu['her_ask']} 次")
                    print(f"     最后 {_pu['last_who']} {_agostr(_pu.get('last_ago_h'))}: "
                          f"{str(_pu.get('last_text') or '')[:30]}")
                if _v.get("brief"):
                    print(f"     状态：{_v['brief']}")
        if _rdy:
            print(f"✅可推进: {' / '.join(u['_name'][:10] for u in _rdy)}")
        if _rv:
            print(f"🔁可唤醒: {' / '.join(u['_name'][:10] for u in _rv[:8])}")
        print(f"任务 P{tasks[0]['prio']}: {tasks[0]['detail']}" if tasks else "任务: 无")
        print("→ 需要谁的细节：`bash soul.sh soul_stats.py who 昵称`；"
              "去掉 --brief 看完整五段")
        print("=" * 62)
        return

    print("=" * 62)
    print(f"【Soul 自动化研判】{result['time']}  ——  {note}")
    print("=" * 62)
    if bell.get("ok"):
        flag = "今日已弹" if bell["active"] else "今日未弹"
        print(f"奇遇铃: {flag} | show_love_time={bell.get('show_time')} | 最后动作 {bell.get('mtime_str')}")
    else:
        print(f"奇遇铃: ⚠️ {bell.get('why')}")
    if pend.get("ok"):
        print(f"待回消息: {len(pend['items'])} 条")
        for it in pend["items"][:10]:
            print(f"   [{it.get('time','')}] {it.get('name')}: {str(it.get('text',''))[:30]}")
    else:
        print(f"待回消息: ⚠️ {pend.get('why')} （**不可当作无消息**）")
    print("-" * 62)
    # ⭐ 用户指定重点对象（读 .soul_focus.json；文件不存在/坏了都不阻断本轮）
    if _focus_disp:
        print("⭐ 用户指定重点对象（优先处理；必须走完整四步 + 第5闸自检）:")
        for _n, _v in _focus_disp.items():
            print(f"   ★ {_n}  [{_v.get('level','重点')}] 自 {_v.get('since','')}")
            _pu = _focusd.get(_n)
            if _pu:
                _rel = ("ta已关注我" if _pu.get("followed") else "ta未关注") + "/" + \
                       ("我已关注" if _pu.get("follow") else "我未关注")
                print(f"      档案：{_rel} | 她发{_pu['her_msgs']}条·轮{_pu['rounds']}·{_pu['src']} | "
                      f"最后 {_pu['last_who']} {_agostr(_pu.get('last_ago_h'))}")
                print(f"      最后一句：{str(_pu['last_text'])[:34]}")
            if _v.get("brief"):
                print(f"      状态：{_v['brief']}")
        print("-" * 62)
    # ⭐【够格推进】—— 唯一允许「更直接表达意愿 / 提议一次具体见面」的名单（通常个位数）
    if _rdy:
        print(f"⭐【够格推进】{len(_rdy)} 人达标（她主动问过我≥2 且 均字≥6 且 她发≥20 且 72h 内动过）:")
        for u in _rdy:
            print(f"   问{u['_ask']:>2}次 均{u['_avg']:>4}字 她发{u['_her']:>3}  "
                  f"{u['_name'][:14]:<14} {_agostr(u['_ago'])}")
        print("   → 只有这些人可以「说清想认识 / 提一次具体且可拒绝的见面」；")
        print("     其余人本轮只做正常对话，不许推进，更不许催、不许纠缠。")
    else:
        print("⭐【够格推进】0 人达标 —— 本轮不向任何人推进关系（不是话术问题，是投入不够）")
    print("-" * 62)
    # 【投入度】谁真对我这个人有兴趣 —— 只认「她主动打探我几次」，不认字数
    if _inv:
        print("【投入度】她主动打探我次数（判谁有兴趣只认这个；长句≠有兴趣）:")
        for u in _inv[:6]:
            print(f"   问{u['_ask']:>2}次 均{u['_avg']:>4}字 {u['_name'][:14]:<14} {_agostr(u['_ago'])}")
        print("-" * 62)
    # 【关系温度】近 24h 还在动的 —— 谁还热（按她发言数）
    if _hot:
        print("【关系温度】近24h有消息（她发/我发/轮次/最后谁/多久）:")
        for u in _hot[:8]:
            print(f"   {u['_her']:>3}/{u['_my']:<3} 轮{u['_rounds']:<3} {u['_lastwho']} "
                  f"{_agostr(u['_ago']):<9} {u['_name'][:14]}")
        print("-" * 62)
    # 【凉了·值得唤醒】只挑她真说过话的
    if _rv:
        print("【凉了·值得唤醒】我最后发言后12~72h未回（她发≥5，值得接一句）:")
        for u in _rv[:6]:
            print(f"   {_agostr(u['_ago']):<9} {u['_name'][:14]:<14} 她发{u['_her']:<3} "
                  f"我最后说: {str(u['_lasttext'] or '[非文本]')[:22]}")
        print("-" * 62)
    print("本轮任务（按优先级）:")
    for t in tasks:
        print(f"  P{t['prio']}  {t['detail']}")
    print("=" * 62)


if __name__ == "__main__":
    if "release" in sys.argv:
        # 手动结束本轮：清锁，让下一轮立刻能开始
        import soul_global_lock as GL
        print("释放前:", GL.read_lock())
        GL.end_round()
        print("已释放全局锁")
    elif "lock" in sys.argv:
        import soul_global_lock as GL
        busy, info = GL.lock_status()
        print(f"锁状态: {'被占用（本轮应跳过）' if busy else '空闲（可开始）'}")
        print(f"  详情: {info}")
    else:
        main(as_json="--json" in sys.argv, brief=("--brief" in sys.argv or "-b" in sys.argv))
