# -*- coding: utf-8 -*-
"""夜间预生成话术池（方案A-③）：把 10~200s 的模型生成延迟，变成白天 <1s 的「池命中」。

运行（副机 jian，pythonw 无窗或前台均可）：
    python soul_pregen.py                 # 预生成「明天」的池
    python soul_pregen.py --for 2026-10-05  # 指定日期
    python soul_pregen.py --limit 15      # 圈人上限（默认 15）
    python soul_pregen.py --dry           # 只圈人不生成（调试圈人逻辑）

消费（soul_daemon.do_reply 开头）：
    import soul_pregen as _pregen
    cands = _pregen.take(nick, her_last)   # 精确匹配 → 候选列表；无命中 → None
    命中后仍过确定性闸 gate()，再走原发送流水线。

安全设计：
    · 任何异常 → take() 返回 None → daemon 走原生成链路，零风险；
    · 匹配键 = 昵称 + 她最后一句**原文**（防止"预生成时她还没说这句、生成后上下文错位"）；
    · 池文件按日期隔离，取走即标记 used，绝不重复使用；
    · 读累积库用快照拷贝，避开 daemon pull() 整库换文件的半文件风险。
"""
import os
import sys
import io
import json
import sqlite3
import shutil
import time
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# 副机 jian（192.168.10.200）→ 本机 Ollama（192.168.10.210）
os.environ.setdefault("OLLAMA_HOST", "192.168.10.210:11434")

PRGEN_DIR = os.path.join(BASE, "_uimap", "daemon", "pregen")
MEMDB = os.path.join(BASE, "soul_memory.db")
POOL_MAX = 15          # 圈人上限（每人预生成 2 条 → 一天最多 30 条池内候选）
CAND_PER = 2           # 每人预生成条数
ACTIVE_H = 72          # 圈人窗口：最近 72h 内有真人往来的会话


def _mkdir():
    try:
        os.makedirs(PRGEN_DIR, exist_ok=True)
    except Exception:
        pass


def pool_file(date=None):
    d = date or datetime.now().strftime("%Y-%m-%d")
    return os.path.join(PRGEN_DIR, d + ".json")


def _read(date):
    try:
        with io.open(pool_file(date), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write(pool, date):
    _mkdir()
    try:
        with io.open(pool_file(date), "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def take(nick, her_last, date=None):
    """daemon 消费：精确匹配（昵称 + 她最后一句原文）→ 取候选并标记 used。
    返回候选文本列表（未过确定性闸，由调用方 gate）；无命中/异常 → None。"""
    try:
        pool = _read(date)
        her_last = str(her_last or "").strip()
        for it in pool.get("items", []):
            if str(it.get("nick", "")) != str(nick):
                continue
            if str(it.get("her_last", "")).strip() != her_last:
                continue
            if it.get("used"):
                continue
            cands = [str(c).strip() for c in (it.get("cands") or []) if str(c).strip()]
            if not cands:
                continue
            it["used"] = True
            it["used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _write(pool, date)
            return cands[:CAND_PER]
    except Exception:
        pass
    return None


def _snapshot_memdb():
    """拷贝累积库读快照（daemon 的 pull() 会整库换文件，直接读有半文件风险）。"""
    tmp = os.path.join(PRGEN_DIR, "_mem_snapshot.db")
    try:
        _mkdir()
        shutil.copy2(MEMDB, tmp)
        return tmp
    except Exception:
        return None


def _recent(lt, hours=ACTIVE_H):
    """localTime 秒/毫秒纪元统一换算后判活跃窗口。"""
    try:
        lt = float(lt)
        if lt > 1e12:
            lt /= 1000.0
        return (time.time() - lt) <= hours * 3600
    except Exception:
        return False


def pick_targets(limit=POOL_MAX, active_h=ACTIVE_H):
    """圈人：最近活跃、末条是真人发言（她说的或她回过）的会话，按活跃度降序。
    返回 [{nick, her_last, sid, hist}]；只读，绝不写库。"""
    import soul_im as im
    db = _snapshot_memdb()
    if not db:
        return []
    out = []
    try:
        c = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
        me = str(getattr(im, "ME", "96691646"))
        nm = {}
        try:
            for uid, name in c.execute("SELECT uid, name FROM nick"):
                nm[str(uid)] = name
        except Exception:
            pass
        rows = c.execute(
            "SELECT sessionId, toUserId, timestamp FROM session "
            "ORDER BY timestamp DESC LIMIT 200").fetchall()
        for sid, uid, ts in rows:
            if str(uid) == me:
                continue
            msgs = c.execute(
                "SELECT senderId, text, msgContent, localTime, msgType FROM chatmsg "
                "WHERE sessionId=? ORDER BY localTime DESC LIMIT 12", (sid,)).fetchall()
            real = None
            for sender, text, content, lt, mt in msgs:
                eff = str(text or "").strip()
                if not eff and str(mt) == "5":
                    eff = im._voice_text(content)
                if not eff:
                    continue
                if im._is_sys(eff, content):
                    continue
                real = (sender, eff, lt)
                break
            if not real:
                continue
            sender, eff, lt = real
            if not _recent(lt, active_h):
                continue
            name = nm.get(str(uid), str(uid))
            if im._is_official(name) or im._is_official_uid(uid):
                continue
            if len(out) >= limit:
                break
            hist = []
            for sender2, text2, content2, _lt2, mt2 in reversed(msgs):
                t = str(text2 or "").strip()
                if not t and str(mt2) == "5":
                    t = im._voice_text(content2)
                if not t:
                    continue
                if im._is_sys(t, content2):
                    continue
                hist.append(("me" if str(sender2) == me else "her", t))
                if len(hist) >= 10:
                    break
            out.append({"nick": name, "her_last": eff, "sid": sid, "hist": hist})
        c.close()
    except Exception as e:
        print("!! pick_targets: %r" % (e,))
    finally:
        try:
            os.remove(db)
        except Exception:
            pass
    return out


def _gen_one(t):
    """对单人生成 CAND_PER 条候选（本地模型 persona 通道，自带复述/违禁词闸）。失败返回 []。"""
    import soul_llm as llm
    hist = "\n".join(("我: " if r == "me" else "她: ") + x for r, x in t["hist"][-8:])
    p = ("【你俩最近的对话，按时间顺序】\n%s\n【她刚发来的这一句】\n%s\n"
         "你是江华（男，在厂里上班，穷、不装富、不吹牛，重庆人）。"
         "接着上下文回复她，直接输出 %d 条消息，每行一条、≤20 字、口语、"
         "不要编号、不要解释、不要复述她的话、不要编造上下文里没有的人和事。"
         % (hist or "(这是你俩首次对话)\n", t["her_last"], CAND_PER))
    try:
        raw = llm.ask("persona", p, CAND_PER)
        return [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]
    except Exception as e:
        print("  !! %s 生成失败: %r" % (t["nick"], e))
        return []


def generate(date=None, limit=POOL_MAX, dry=False):
    """预生成指定日期（默认明天）的话术池。返回池 dict。"""
    if date is None:
        date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    targets = pick_targets(limit=limit)
    if dry:
        for t in targets:
            print("  %s | 她说: %s" % (t["nick"], str(t["her_last"])[:24]))
        print("== 圈人 %d 个（dry，未生成）" % len(targets))
        return {"date": date, "dry": True, "items": []}
    items = []
    for t in targets:
        cands = _gen_one(t)
        if cands:
            items.append({"nick": t["nick"], "her_last": t["her_last"],
                          "cands": cands[:CAND_PER]})
            print("  ✔ %s: %s" % (t["nick"], " / ".join(cands)))
        else:
            print("  ✖ %s 无候选（模型/闸失败）" % t["nick"])
        time.sleep(0.5)
    pool = {"date": date,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "limit": limit,
            "items": items}
    ok = _write(pool, date)
    print("== 池写入 %s：%d/%d 人 %s"
          % (pool_file(date), len(items), len(targets), "OK" if ok else "FAIL"))
    return pool


if __name__ == "__main__":
    args = sys.argv[1:]
    date = None
    limit = POOL_MAX
    dry = False
    if "--for" in args:
        date = args[args.index("--for") + 1]
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    if "--dry" in args:
        dry = True
    generate(date=date, limit=limit, dry=dry)
