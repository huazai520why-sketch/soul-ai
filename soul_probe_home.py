# -*- coding: utf-8 -*-
"""
soul_probe_home.py —— 实测：打开某人的个人主页，App 会不会把主页数据写进 soul_app.db？

背景：soul_app.db 里有 user_home_cache(_id,name,value)，name 形如
  user_profile_<userIdEcpt> / user_follow_<...> / user_measure_<...> / user_wearing_<...>
本地目前只有**自己**的 4 条 → 怀疑是"看了谁的主页就缓存谁"。本脚本验证这一点。

流程（**全程不发任何消息**）：
  1) Soul 拉前台（RN 页也算，判据已在 soul.py 改成 startswith("cn.soul")）
  2) soul_reply.find(name) 进入会话页，并校验确实进对了人
  3) tap 会话页顶部标题区 (360,105) —— ⚠️ 绝不可碰 (573,145) 那个「邀请通话」按钮
  4) 读 activity / OCR，确认是否落在 RN 主页
  5) Soul 自己会把主页 JSON 写进 user_home_cache
  6) 返回列表；pull soul_app.db（独立 probe 通道）并解析

用法：  python soul_probe_home.py "委委佗佗"
"""
import os
import sys
import json
import time
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soul  # noqa: E402
import soul_reply  # noqa: E402
import soul_read  # noqa: E402
import soul_probe_db  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HOME_PREFIX = ("user_profile_", "user_follow_", "user_measure_", "user_wearing_")
# 主页 JSON 里我们真正关心的字段
KEY_FIELDS = [
    "alias", "gender", "location", "birthday", "follow", "followed",
    "isCertificate", "comeFrom", "postCount", "signature", "avatarName",
    "beFollowNum", "beViewNum", "followNum", "recentViewNum", "postLikeNum",
    "qige", "xinge", "num1", "num2", "num3", "num4",
]


def her_ecpt_hint(name):
    """从 im_data.db 的 session 里找她的 userIdEcpt（缓存表的 key 用这个）"""
    try:
        import soul_im
        c = sqlite3.connect("file:im_data.db?mode=ro", uri=True)
        cur = c.cursor()
        cur.execute("SELECT sessionId, extInfo FROM session")
        nm = soul_im.names()
        hits = set()
        for sid, ext in cur.fetchall():
            who = nm.get(str(sid).replace(soul_im.ME, ""), "")
            if name and name[:4] in who:
                for tok in str(ext or "").replace('"', " ").replace("\\", " ").split():
                    if len(tok) >= 24 and tok.endswith("=="):
                        hits.add(tok)
                if str(sid).replace(soul_im.ME, "").endswith("=="):
                    hits.add(str(sid).replace(soul_im.ME, ""))
        return hits
    except Exception as e:
        print(f"  [warn] 读 session 取 Ecpt 失败: {e!r}")
        return set()


def parse_cache(path=r"E:\soul\_probe\soul_app.db"):
    """按不同用户的 Ecpt 分组整理 user_home_cache"""
    if not os.path.exists(path):
        return None, "soul_app.db 不在 _probe —— 先跑 soul_probe_db.py"
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = c.execute("SELECT name, value FROM user_home_cache").fetchall()
    except Exception as e:
        return None, f"读 user_home_cache 失败: {e!r}"
    people = {}
    for k, v in rows:
        for pre in HOME_PREFIX:
            if k.startswith(pre):
                ecpt = k[len(pre):]
                try:
                    people.setdefault(ecpt, {})[pre.strip("_")] = json.loads(v)
                except Exception:
                    people.setdefault(ecpt, {})[pre.strip("_")] = v
    return people, None


def brief(ecpt, blocks, mine_ecpt):
    tag = "【我】" if ecpt == mine_ecpt else "【她】"
    prof = blocks.get("user_profile", {}) if isinstance(blocks.get("user_profile"), dict) else {}
    foll = blocks.get("user_follow", {}) if isinstance(blocks.get("user_follow"), dict) else {}
    meas = blocks.get("user_measure", {}) if isinstance(blocks.get("user_measure"), dict) else {}
    alias = prof.get("alias") or prof.get("nickname") or "?"
    print(f"  {tag} {ecpt[:16]}…  昵称={alias}  地点={prof.get('location')}  "
          f"性别={prof.get('gender')}  关注我/我关注={prof.get('followed')}/{prof.get('follow')}  "
          f"post={prof.get('postCount')}")
    if foll:
        print(f"        粉丝={foll.get('beFollowNum')}  主页访客={foll.get('beViewNum')}  "
              f"近期访客={foll.get('recentViewNum')}  动态获赞={foll.get('postLikeNum')}")
    if meas:
        print(f"        灵魂={meas.get('xinge')}/{meas.get('qige')}  "
              f"维度={meas.get('num1')}/{meas.get('num2')}/{meas.get('num3')}/{meas.get('num4')}")
    return alias


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "委委佗佗"
    print(f"=== 目标：{name} ===")

    print("\n[1] 拉前台")
    soul.ensure_foreground()
    print("    activity =", soul.activity())

    print("\n[2] 目标人物的 Ecpt 线索（来自 im_data.db session）")
    hints = her_ecpt_hint(name)
    print("   ", hints or "（没拿到，靠主页 alias 反查）")

    print("\n[3] 进入会话页（不发任何消息）")
    ok = soul_reply.find(name)
    print("    find() ->", ok)
    time.sleep(1.5)

    # 进对人了吗？（用会话页标题/内容校验）
    soul.screenshot()
    act = soul.activity()
    print("    activity =", act)

    print("\n[4] 点顶部标题区进入主页 (360,105)")
    before_ok = soul_probe_db.fetch(soul_probe_db.TARGETS)
    p0, _ = parse_cache()
    n0 = len(p0 or {})
    print(f"    点击前：已缓存 {n0} 个人")

    soul.tap(360, 105)
    time.sleep(3.5)
    act2 = soul.activity()
    soul.screenshot()
    print("    activity =", act2)
    rn = "RnContainerActivity" in (act2 or "")
    print("    →", "已进入 RN 页面（主页/搜索/广场之一）" if rn else "⚠️ 没跳 RN 页")

    try:
        txt = soul_read.read_all() or ""
        head = " / ".join([l.strip() for l in txt.splitlines() if l.strip()][:6])
        print("    页面文字:", head[:120])
    except Exception as e:
        print("    OCR 失败:", e)

    time.sleep(1.5)
    after_ok = soul_probe_db.fetch(soul_probe_db.TARGETS)
    print(f"    pull 文件数 {after_ok}")

    people, err = parse_cache()
    if err:
        print("❌", err)
    else:
        mine = None
        for r in sqlite3.connect("file:im_data.db?mode=ro", uri=True).execute(
                "SELECT userIdEcpt FROM session LIMIT 1"):
            mine = r[0]
        print(f"\n=== user_home_cache 现有 {len(people)} 人 ===")
        for ecpt, blocks in people.items():
            brief(ecpt, blocks, mine)

    print("\n[5] 返回聊天列表")
    soul.tap(50, 105)
    time.sleep(1.2)
    if "RnContainerActivity" in (soul.activity() or ""):
        soul.tap(50, 105)
        time.sleep(1.0)
    print("    activity =", soul.activity())


if __name__ == "__main__":
    main()
