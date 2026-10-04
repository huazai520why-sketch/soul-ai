# -*- coding: utf-8 -*-
"""
soul_intent.py —— 验证能不能「不滚列表、直接一步到会话页」（靠 Intent / URL scheme）

背景
----
静态分析结论（已验证）：
  · Soul = 原生 + React Native(Hermes) + Flutter 三套混编，568 个 Activity
  · dex 里挖出 182 条内部路由，形如
        soul://ul.soulapp.cn/chat/conversationActivity?userIdEcpt=
        soul://ul.soulapp.cn/account/userHomepage
        soul://ul.soulapp.cn/post/postDetail
  · AndroidManifest **没有注册 soul:// scheme**（dumpsys 权威确认：
    只有 soulapp:// / http://ul.soulapp.cn / pushscheme / honorpush）
    ⇒ `am start -d "soul://..."` 大概率 ActivityNotFound，但值得实测确认
  · 兜底路线：直接 `am start -n <容器Activity> --es <key> <value>`
    候选 key：userIdEcpt / userId / targetUserIdEcpt / fromUserId
  · dex 里有方法 getConversationActivityIntent → 说明确实有标准 Intent 入口

本脚本做什么
------------
  ① 安全检查：有活跃轮次在跑就**拒绝执行**（不跟人家抢输入框）
  ② 逐个候选方案 startActivity，**只开页面，绝不发消息**
  ③ 每次开完：读 activity + OCR 顶部 → 判断是否真的落在目标会话
  ④ 全流程不改任何数据，测完逐层返回

用法
----
  python soul_intent.py "委委佗佗"           # 试全部候选
  python soul_intent.py "委委佗佗" --uid 359737367
"""
import os
import sys
import json
import time
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soul          # noqa: E402
import soul_read     # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PKG = "cn.soulapp.android"
ACT_CHAT = f"{PKG}/.component.chat.ConversationActivity"
ACT_CHAT2 = f"{PKG}/.component.cg.singleChat.ChatActivity"
LOCK_F = r"E:\soul\.soul_auto.lock"
LOCK_STALE = 15 * 60


def _round_proc_alive():
    """进程级判据：有没有**轮次** python 进程活着（排除常驻 watcher）。

    为什么不能只看心跳：进程被 kill 后锁文件的心跳还"新鲜"（血缘残留），
    只看心跳会把死实例误判成活轮；反之若真有輪在跑，就必须让位。
    """
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        ).stdout.decode("gbk", "ignore")
    except Exception as e:
        return None, f"查进程失败: {e}"

    marks = ("soul_auto.py", "soul_reply.py", "soul_match.py",
             "soul_clear_draft.py", "soul_clear_unread.py", "soul_round.py",
             "winshot.py", "soul_intent.py")
    alive = []
    for line in out.splitlines():
        if "python" not in line.lower():
            continue
        for m in marks:
            if m in line:
                # 取 pid（CSV: "映像名称","PID",...）
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2:
                    alive.append((m, parts[1]))
                break
    return (len(alive) > 0), (alive if alive else "无轮次进程")


def round_alive():
    """有活跃轮次吗？有就不能动模拟器。

    双重判据（心跳 **AND** 进程）—— 任一不成立都视为空闲：
      心跳停了               → 死实例
      心跳新鲜但进程已不在   → 也是死实例（被 kill / 用户手动停了）
    """
    proc_alive, proc_why = _round_proc_alive()
    if proc_alive:
        return True, f"有轮次进程在跑：{proc_why}"

    if not os.path.exists(LOCK_F):
        return False, f"无锁 + {proc_why} → 空闲"
    try:
        d = json.load(open(LOCK_F, encoding="utf-8"))
        age = time.time() - float(d.get("heartbeat", 0))
    except Exception as e:
        return False, f"锁损坏({e}) + {proc_why} → 视为空闲"
    return False, (f"锁存在(step={d.get('step')}，心跳 {age/60:.1f} 分钟前)"
                   f" 但 {proc_why} → 判为死实例，可接管")


def uid_of(name):
    """从 im_data.db 拿她的明文 uid"""
    try:
        sys.path.insert(0, r"E:\soul")
        import soul_im
        nm = soul_im.names()
        c = sqlite3.connect(r"file:E:\soul\im_data.db?mode=ro", uri=True)
        for sid, uid in c.execute("SELECT sessionId, toUserId FROM session"):
            if name and name[:3] in nm.get(str(uid), ""):
                return str(uid), nm.get(str(uid), "")
    except Exception as e:
        print("  取 uid 失败:", e)
    return None, None


def top_text():
    """OCR 全部文本，返回顶部若干行，判断落在谁的会话"""
    try:
        soul.screenshot()
        # soul_read.items() 返回 [(text, cx, cy)]，已按 y 排序
        its = soul_read.items(refresh=True) or []
        if its:
            return " / ".join(str(t[0]) for t in its[:8])[:110]
        txt = soul_read.show() or ""
        lines = [l.strip() for l in str(txt).splitlines() if l.strip()]
        return " / ".join(lines[:6])[:110]
    except Exception as e:
        return f"(OCR失败 {e})"


def try_open(tag, cmd):
    """起一次页面，返回 (activity, OCR顶部)"""
    out = soul.sh(cmd, timeout=60)
    time.sleep(3.0)
    act = soul.activity() or ""
    top = top_text()
    ok = "Error" not in out and "Exception" not in out
    print(f"  [{tag}]")
    print(f"     activity = {act}")
    print(f"     顶部     = {top}")
    if not ok:
        print(f"     am 输出  = {out.strip()[:160]}")
    return act, top, out


def go_home(depth=6):
    """逐层返回，回到聊天列表。

    ⚠️ 判据必须覆盖所有"还在里层"的页面名：
     ConversationActivity / ChatActivity / RnContainer / Flutter 都算没退干净。
    """
    INNER = ("Conversation", "ChatActivity", "RnContainer", "Flutter", "HomePage")
    for i in range(depth):
        a = soul.activity() or ""
        if not any(k in a for k in INNER):
            break
        try:
            soul.tap(50, 105)
        except Exception:
            pass
        time.sleep(1.3)
    return soul.activity()


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "委委佗佗"
    uid_arg = None
    if "--uid" in sys.argv:
        uid_arg = sys.argv[sys.argv.index("--uid") + 1]

    print("=" * 66)
    print(f"soul_intent  ——  目标：{name}")
    print("=" * 66)

    busy, why = round_alive()
    print(f"\n[安全闸] {why}")
    if busy:
        print("\n❌ 有活跃轮次在跑，按第 0 步铁律：本脚本拒绝操作模拟器。")
        print("   等它结束（或锁过期）再跑。")
        return

    uid, nick = (uid_arg, name) if uid_arg else uid_of(name)
    print(f"\n[目标] 昵称={nick}  明文uid={uid}")
    if not uid:
        print("❌ 拿不到 uid，无法测试")
        return

    print("\n[前提] 先把 Soul 拉到前台")
    soul.ensure_foreground()
    time.sleep(1.5)
    print("   当前 activity =", soul.activity())

    print("\n" + "=" * 66)
    print("① 测 URL scheme（预期失败：manifest 没注册 soul://）")
    print("=" * 66)
    try_open("soul://conversationActivity",
             f'am start -a android.intent.action.VIEW '
             f'-d "soul://ul.soulapp.cn/chat/conversationActivity?userIdEcpt={uid}"')
    go_home()

    print("\n" + "=" * 66)
    print("② 测直接 start ConversationActivity（各种 extra key）")
    print("=" * 66)
    results = []
    for act_name in (ACT_CHAT, ACT_CHAT2):
        short = act_name.split('/')[-1].split('.')[-1]
        for key in ("userIdEcpt", "userId", "targetUserIdEcpt", "fromUserId"):
            tag = f"{short} / {key}={uid}"
            try:
                a, top, out = try_open(
                    tag,
                    f'am start -n "{act_name}" --es {key} "{uid}"')
            except Exception as e:
                print(f"  [{tag}] 异常 {e}")
                a, top = "", ""
            # 判据：落在聊天类 Activity 即可疑有用；顶部出现她的昵称才算真命中
            likely = ("onversation" in a) or ("hat" in a and "Soul" not in a)
            hit = nick and nick[:3] in (top or "")
            results.append((tag, a, top, likely, hit))
            print(f"     → {'✅疑似命中' if hit else ('⚠️打开了会话页' if likely else '❌无效')}")
            go_home()
            time.sleep(1.0)

    print("\n" + "=" * 66)
    print("汇总")
    print("=" * 66)
    for tag, a, top, likely, hit in results:
        flag = "✅✅ 真·一步到位" if hit else ("⚠️ 打开但不确定" if likely else "❌")
        print(f"  {flag:12s} {tag}")
        print(f"               activity={a[:70]}")
        print(f"               顶部   ={top[:70]}")

    print("\n[收尾] 确保回到列表")
    go_home()
    print("   最终 activity =", soul.activity())


if __name__ == "__main__":
    main()
