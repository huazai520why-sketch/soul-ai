# -*- coding: utf-8 -*-
"""快速回复路径（2026-10-03）：把单条回复的 OCR 从 ~15 次压到 4~6 次。

背景（埋点实测）：soul_reply.reply 全安全路径单条 45~66s，其中
  OCR 15~17 次 × 2.4s ≈ 36~40s 是最大头；verify_sent 的 8×pull(2.3s) 次之。
本模块**不重写安全闸**（长度/重复/频率/死磕/串台/发送锁 全部复用 soul_reply/soul_send），
只砍「读屏次数」和「全量拉库」：

  省掉的 OCR：
    · _on_chat_list() 前置/点行前校验（~3 次）→ 用 soul.activity()（0.3s sh）代替
    · _page_state() 打印（2 次）→ 不打
    · find() 默认 FIND_PAGES 页扫描 → pages=1（pending 的人都在列表顶部，实测）
  省掉的 pull：
    · verify_sent 的最多 8 次全量拉库 → 设备端 sqlite3 直查（0.3s/次，同一颗库）
    · 连发闸的 im.pull → 同上
  保留的 OCR（安全底线，不省）：
    · find() 找会话行（1~3 次；找不到就不发）
    · _on_session_of() 标题防串台硬闸（2026-09-26 真实事故换来的，绝不省）
    · soul_send._input 灌字仪式（2026-09-30 真实事故换来的，不动）

返回约定：
  "SENT"  —— 发出 ≥1 条（含部分发出，**调用方不要再重试**，避免重发）
  "GATED" —— 硬闸拦截（长度/重复/频率）→ 答案就是「不发」，回退全路径也是同样结论
  "MISS"  —— 没找到/没进去/环境异常 → 值得回退 soul_reply.reply（它有搜索框兜底）
  "FAIL"  —— 发送环节失败 → 回退无意义（同一条 send_msg），不要重试
"""
import os, re, sqlite3, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import soul
import soul_im as im
import soul_reply as sr
import soul_send as ss

# ⭐ 2026-10-05：累积库**按当前登录账号**解析（切号后立即换文件）。见 soul_acct。
def _memdb():
    try:
        import soul_acct
        return soul_acct.path(BASE, "soul_memory.db")
    except Exception:
        return os.path.join(BASE, "soul_memory.db")


MEMDB = _memdb()      # 兼容快照；真正的读写请用 _memdb()
MAX_BACK = 4

# ── 让 soul_reply 的「库里聊过≥3句」判定同时看累积库 ──────────────
#   背景：Soul 会清空设备本地库（2026-10-03 实测 2811→55 条），
#   _resolve_db_target 只读正式库 → 谁都"没聊过" → 全面拒发。
#   累积库（只增不减）才是全量事实。monkey-patch，不动生产脚本本体。
_orig_hist_uids = sr._hist_uids


def _hist_uids_mem(minn=3):
    uids = set()
    try:
        uids |= set(_orig_hist_uids(minn) or [])
    except Exception:
        pass
    try:
        c = sqlite3.connect(MEMDB)
        rows = c.execute(
            "SELECT s.toUserId, (SELECT count(*) FROM chatmsg m "
            " WHERE m.sessionId IN (SELECT sessionId FROM session s2 "
            "  WHERE s2.toUserId = s.toUserId)) "
            "FROM session s").fetchall()
        for uid, n in rows:
            if uid and (n or 0) >= minn:
                uids.add(str(uid))
        c.close()
    except Exception:
        pass
    return uids


sr._hist_uids = _hist_uids_mem


def _act():
    try:
        return soul.activity() or ""
    except Exception:
        return ""


def _dev_sql(sql, timeout=12):
    """设备端 sqlite3 直查 IM 库（0.3s/次，替代 im.pull 的 2.3s 全量拉库；同一颗库，证据等硬）"""
    p = "%s/IM-SDK-%s-DATA.db" % (im.DBDIR, im.SESS)
    q = sql.replace('"', '""')
    try:
        return soul.sh('sqlite3 "%s" "%s" 2>/dev/null' % (p, q), timeout=timeout).strip()
    except Exception:
        return ""


def _last_sender_is_me_dev(uid):
    """连发闸（设备端）：该人会话最后一条是不是我发的。None=查不到（新会话→放行）"""
    if not uid:
        return None
    sids = _dev_sql("SELECT sessionId FROM session WHERE toUserId='%s'" % uid)
    if not sids:
        return None
    sid_list = ",".join("'%s'" % s.strip() for s in sids.splitlines() if s.strip())
    if not sid_list:
        return None
    r = _dev_sql("SELECT senderId FROM chatmsg WHERE sessionId IN (%s) "
                 "ORDER BY localTime DESC LIMIT 1" % sid_list)
    if not r:
        return None
    return r.splitlines()[0].strip() == str(im.ME)


def _verify_sent_dev(text, tries=6, gap=1.5):
    """发送校验（设备端）：我的最后一条消息包含本文前 8 字。
    与 soul_send.verify_sent 同等硬度（读同一颗库），但 0.3s/次 vs 2.3s/次。"""
    key = re.sub(r"\s+", "", text)[:8]
    if not key:
        return False
    for _ in range(tries):
        got = _dev_sql("SELECT text FROM chatmsg WHERE senderId='%s' AND text IS NOT NULL "
                       "ORDER BY localTime DESC LIMIT 1" % im.ME)
        if got and key in re.sub(r"\s+", "", got):
            return True
        time.sleep(gap)
    return False


def _to_chat_list():
    """导航回聊天列表 tab：回退到主框架 → 点聊天 tab → 颜色证据确认选中；没中再点一次。"""
    for _ in range(MAX_BACK):
        if "MainActivity" in _act():
            break
        soul.sh("input keyevent 4")
        time.sleep(1.0)
    for _i in range(2):
        soul.tap(*soul.TAB_CHAT)
        time.sleep(1.3)
        try:
            soul.screenshot(force=True)
            if sr._tab_sel("聊天"):
                return
        except Exception:
            pass
    print("  !! [fast] 聊天 tab 选中态未确认（继续，找不到人即放弃）")


def _lap(t0, label):
    """分段计时（定位耗时大头用）"""
    now = time.time()
    print("   ⏱ [fast] %-16s %7.0f ms" % (label, (now - t0) * 1000))
    return now


def fast_reply(name, texts, my_recent=None, maxlen=40):
    """快速回复主入口。返回 "SENT"/"GATED"/"MISS"/"FAIL"（语义见模块 docstring）。"""
    t0 = time.time()
    lap = t0
    texts = [t for t in (texts or []) if (t or "").strip()]
    if not texts:
        return "GATED"

    # ── 闸 1：长度（与 soul_reply.reply 同一判据）──
    for t in texts:
        if ss.vis_len(t) > (maxlen or ss.MAXLEN):
            print("⛔ [fast] 长度硬闸：%d 字 > %d —— %r" % (ss.vis_len(t), maxlen, t))
            return "GATED"

    # ── 闸 2：跨轮重复（用调用方给的累积库 my_recent；没有才落回 sr 版）──
    if my_recent is None:
        try:
            my_recent = sr._my_recent_texts(name, k=6) or []
        except Exception:
            my_recent = []
    for t in texts:
        for mine in (my_recent or [])[-6:]:
            try:
                if sr._similar(t, mine) > 0.5:
                    print("⛔ [fast] 重复拦截：「%s」≈ 我说过的「%s」" % (t[:20], str(mine)[:20]))
                    return "GATED"
            except Exception:
                pass

    # ── 闸 3：频率（同 soul_reply.reply）──
    try:
        ok, why = sr._my_burst_gate(name)
        if not ok:
            print("⛔ [fast] 频率闸：%s" % why)
            return "GATED"
    except Exception:
        pass

    # ── 闸 4：死磕告警（只喊不拦，同 soul_reply.reply）──
    try:
        w = sr._topic_stuck_warn(name, texts)
        if w:
            print("⚠️ [fast] 死磕告警：%s" % w)
    except Exception:
        pass

    # ── 前置：前台 + 清障（奇遇铃优先处理是用户定的规矩，保持）──
    lap = _lap(lap, "gates")
    try:
        soul.connect()
        if not soul.ensure_ready():
            print("!! [fast] ensure_ready 失败（前台拉不起来）")
            return "MISS"
    except Exception as e:
        print("!! [fast] 前置异常: %r" % (e,))
        return "MISS"

    # ⭐ 2026-10-06 修（P1#4 根因①·对称短路）：若此刻**已经在她会话页**
    #   （奇遇铃点完「立即私聊」等场景直接落到本路径），与 soul_reply.reply 同样
    #   **不回聊天列表、不 find** —— 列表里根本没有她的行，find 会空跑滚顶+搜索兜底、
    #   最后报"未找到" → 永远发不出。判据复用同一份 `sr._on_session_of`。
    in_session = False
    try:
        in_session = bool(sr._on_session_of(name))
    except Exception:
        in_session = False
    if in_session:
        print("  [fast] 已在「%s」的会话页 → 跳过导航/查找，直接发" % name)
        pos = None
    else:
        _to_chat_list()
        lap = _lap(lap, "ready+nav")
        # ── 找会话行（保留 OCR；pending 的人都在列表顶部，pages=1 足够）──
        try:
            pos = sr.find(name, pages=1)
        except Exception as e:
            print("!! [fast] find 异常: %r" % (e,))
            pos = None
        if not pos:
            # ⭐ 列表首页没有渲染行（老联系人沉在列表深处 / 末条是卡片不渲染）
            #   → 走**搜索昵称通道**（用户 2026-10-03 指点）。soul_reply.find_by_search
            #   自带重名保护：用「本地库有没有往来记录」判定本人，不会误进同名生人。
            print("  [fast] 列表首页未找到「%s」→ 搜索昵称通道" % name)
            try:
                in_session = bool(sr.find_by_search(name))
            except Exception as e:
                print("!! [fast] 搜索通道异常: %r" % (e,))
                in_session = False
            lap = _lap(lap, "search")
            if not in_session:
                print("!! [fast] 搜索也没找到「%s」→ 放弃本次" % name)
                return "MISS"

    if pos:
        # 点行前确认还在主框架（替代 _on_chat_list 的 OCR，0.3s：
        # 能拦住"页面已切到会话/WebView"这类事故；同 tab 内列表自体滚动，
        # _hit 用的是 2 秒内的新帧，风险窗口极小）
        if "MainActivity" not in _act():
            print("!! [fast] 点行前页面已离开主框架 → 中止（防误触会话页按钮）")
            return "MISS"
        soul.tap(*pos)
        time.sleep(1.8)
    lap = _lap(lap, "find+tap")

    # ── 防串台硬闸（保留 OCR，2026-09-26 事故换来的，绝不省）──
    try:
        if not sr._on_session_of(name):
            print("!! [fast] 串台拦截：未进入「%s」会话 → 中止" % name)
            soul.tap(*soul.BACK_XY)
            return "MISS"
    except Exception as e:
        print("!! [fast] 标题校验异常: %r → 按不过处理" % (e,))
        soul.tap(*soul.BACK_XY)
        return "MISS"
    lap = _lap(lap, "title_check")

    uid = None
    try:
        uid = sr._uid_of(name)
    except Exception:
        pass

    # ── 干跑模式（测速/联调用）：走完除「灌字+发送」外的全部环节 ──
    if os.environ.get("SOUL_FAST_DRY") == "1":
        print("🧪 [fast] DRY 模式：已到会话页且标题校验通过，不发送")
        try:
            soul.tap(*soul.BACK_XY)
        except Exception:
            pass
        print("⏱ [fast] 链路 %.1fs（DRY）" % (time.time() - t0))
        return "DRY"

    sent = 0
    for i, t in enumerate(texts):
        if i > 0:
            m = _last_sender_is_me_dev(uid)
            if m is True:
                print("⛔ [fast] 连发拦截：她还没回 → 不发第二条")
                break
        try:
            if not ss.send_msg(t, maxlen=maxlen, verify=False):
                print("!! [fast] send_msg 失败（锁/长度/灌字）→ 停（%d 条已发）" % sent)
                break
        except Exception as e:
            print("!! [fast] send_msg 异常: %r → 停（%d 条已发）" % (e, sent))
            break
        lap = _lap(lap, "send_msg")
        if not _verify_sent_dev(t):
            print("!! [fast] 设备端未检出本条 → 视为未发出，停（%d 条已发）" % sent)
            break
        lap = _lap(lap, "verify_dev")
        sent += 1
        print("✅ [fast] 已发: %s" % t)
        time.sleep(0.8)

    try:
        soul.tap(*soul.BACK_XY)
    except Exception:
        pass
    time.sleep(0.8)
    print("⏱ [fast] 链路 %.1fs，发出 %d/%d 条" % (time.time() - t0, sent, len(texts)))

    if sent > 0:
        return "SENT"
    return "FAIL"


if __name__ == "__main__":
    # 手动测试：python soul_fast.py "昵称" "消息1" ["消息2"]
    if len(sys.argv) < 3:
        print('用法: python soul_fast.py "昵称" "消息1" ["消息2"]')
        sys.exit(1)
    r = fast_reply(sys.argv[1], sys.argv[2:])
    print("结果:", r)
