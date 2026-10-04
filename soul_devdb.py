# -*- coding: utf-8 -*-
"""设备端直查 —— 不复制库文件，直接在 MuMu 里用 sqlite3 查，只把**结果文本**取回。

为什么要有它（2026-09-30 实测根因）：
  现有 `soul_im.pull()` 把设备库复制到本地再查，但**换入那一步在 Windows 上会被占住**：
  只要有任何进程开着目标文件（watcher 每 15~20s 读一次库、残留的旧 watcher 也在读），
  `os.replace()` 就抛 WinError 5/32 → 上层只能"保留旧数据，等下次"。
  实测后果：实例1 的 `chat_im.1.db` 长期停在 **0 字节**（一张表都没有）→
  `names()` 拿不到昵称映射 → **该实例永远报"待回 0"**，等于监控是瞎的，
  而日志里只有一行 warn，看起来"只是没同步上"。

  本模块换一条路：**设备上本来就装着 sqlite3**（`/system/bin/sqlite3`），
  直接 `sqlite3 <db> "<sql>"` 取结果。实测 **0.2s/次**，且：
    · 零文件竞争（不碰本地库）
    · 零 WAL 错配（不复制 .db/-wal/-shm 三件套）
    · 零截断风险
    · 不需要 root（MuMu 的 mumu-cli shell 本身就是 root）

用法：
    import soul_devdb as dd
    dd.pending()          # 待回会话（含昵称）
    dd.chat("昵称")        # 某人的对话时间线
    dd.query("SELECT ...") # 自定义 SQL（跑在当前实例的活动库上）
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import soul  # noqa: E402

DBDIR = "/data/data/cn.soulapp.android/databases"

# 活动库探测缓存（库名会随账号变化，且新账号可能与旧账号并存）
_ACTIVE = {"sess": None, "ts": 0.0}
_ACTIVE_TTL = 120.0


# ────────────────────────── 底层 ──────────────────────────
def _sh(cmd, timeout=40):
    return soul.sh(cmd, timeout=timeout)


def _quote(sql):
    """把 SQL 安全地塞进双引号里（sqlite3 命令行只认双引号包整条 SQL）。"""
    sql = " ".join(str(sql).split())          # 压缩空白，避免换行把命令截断
    return sql.replace('"', '\\"')


def query(sql, sess=None, timeout=40):
    """在当前实例的活动库上执行 SQL，返回去掉首尾空白的文本。"""
    db = db_path(sess)
    out = _sh(f'sqlite3 "{db}" "{_quote(sql)}" 2>&1', timeout=timeout)
    return out.strip()


def db_path(sess=None):
    sess = sess or active_sess()
    return f"{DBDIR}/IM-SDK-{sess}-DATA.db"


# ────────────────────── 活动库自动探测 ──────────────────────
def _candidate_sessions():
    """设备上现存的 IM 库会话名列表（去掉 -wal/-shm）。"""
    out = _sh(f"ls -1 {DBDIR}/ 2>/dev/null | grep -E '^IM-SDK-'")
    sess = []
    for line in out.splitlines():
        line = line.strip()
        m = re.match(r"^IM-SDK-(.+?)-DATA\.db$", line)
        if m:
            sess.append(m.group(1))
    return sess


def active_sess(force=False):
    """探测**当前账号正在用的**那个库。

    判据：该库里 chatmsg 的 MAX(localTime) 最新 —— 死库（换账号前的残留）
    时间戳会明显落后。实测：实例1 主号残留库停在 21:02，而账号2 的活动库写到 22:16。
    """
    now = time.time()
    if not force and _ACTIVE["sess"] and (now - _ACTIVE["ts"]) < _ACTIVE_TTL:
        return _ACTIVE["sess"]

    # 优先用 soul_vm.json 里配的会话；它若读不出消息，再退回按新鲜度探测
    try:
        import soul_im as _im
        preferred = _im.SESS
    except Exception:
        preferred = None

    best, best_ts = None, -1
    order = ([preferred] if preferred else []) + \
            [s for s in _candidate_sessions() if s != preferred]
    for sess in order:
        if not sess:
            continue
        ts = query("SELECT IFNULL(MAX(localTime),0) FROM chatmsg;", sess=sess)
        try:
            v = int(re.sub(r"\D", "", ts) or -1)
        except ValueError:
            v = -1
        if v > best_ts:
            best, best_ts = sess, v
    if best:
        _ACTIVE.update({"sess": best, "ts": now})
    return best


# ────────────────────── 业务查询 ──────────────────────
def me_uid():
    """本实例的账号 uid（取自 soul_vm.json / soul_im.ME）。"""
    try:
        import soul_im as _im
        return _im.ME
    except Exception:
        return None


def names():
    """uid → 昵称。活动库所在账号的资料库通常是 chat_<sess>；找不到就扫全部 chat_*。"""
    out = {}
    sess = active_sess()
    for name in [f"chat_{sess}"] + [f"chat_{s}" for s in _candidate_sessions()
                                    if s != sess] + ["chat_default_db", "chat_im_user.db"]:
        rows = query("SELECT userId, signature FROM im_user_bean;", sess=None) \
            if name.startswith("IM-SDK") else \
            _sh(f'sqlite3 "{DBDIR}/{name}" "SELECT userId, signature FROM im_user_bean;" 2>&1')
        for line in rows.splitlines():
            if "|" in line:
                uid, _, nick = line.partition("|")
                uid, nick = uid.strip(), nick.strip()
                if uid and nick and uid not in out:
                    out[uid] = nick
    return out


def pending(limit=40):
    """待回：她最后发言、我还没回的会话。返回 [(昵称, uid, 未读, 她的话, 时间)]。

    判据严格按铁律：**她是不是这个会话里最后说话的人**
    （不能用 unReadCount —— 有僵尸未读，与 UI 角标都对不上）。
    """
    me = str(me_uid() or "0")
    sql = (
        "SELECT s.toUserId, IFNULL(s.unReadCount,0), IFNULL(s.lastMsgText,''), "
        "  datetime(s.timestamp/1000,'unixepoch','+8 hours'), "
        "  (SELECT m.senderId FROM chatmsg m WHERE m.sessionId = s.sessionId "
        "     AND m.text IS NOT NULL AND m.text<>'' "
        "   ORDER BY m.localTime DESC LIMIT 1) AS lastSender "
        "FROM session s "
        "WHERE s.toUserId IS NOT NULL AND s.toUserId<>'' "
        "ORDER BY s.timestamp DESC LIMIT %d;" % limit
    )
    nm = names()
    rows = []
    for line in query(sql).splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        uid, unread, text, ts, last = (p.strip() for p in parts[:5])
        if not uid or last == me:          # 末条是我发的 → 不是待回
            continue
        if not text:
            continue                        # 无文本（系统卡片）→ 跳过
        rows.append((nm.get(uid, uid), uid, unread, text, ts))
    return rows


def chat(uid_or_nick, limit=40):
    """某人的完整时间线（按 uid 或昵称）。返回 [(时间, '我'/'她', 文本)]。"""
    me = me_uid() or "0"
    uid = uid_or_nick
    if not str(uid_or_nick).isdigit():
        for u, n in names().items():
            if n == uid_or_nick:
                uid = u
                break
    sess_row = query(f"SELECT sessionId FROM session WHERE toUserId='{uid}';")
    sids = [x.strip() for x in sess_row.splitlines() if x.strip()]
    if not sids:
        return []
    ph = ",".join("'%s'" % s.replace("'", "''") for s in sids)
    sql = (
        "SELECT datetime(localTime/1000,'unixepoch','+8 hours'), senderId, "
        "  IFNULL(text,'') FROM chatmsg WHERE sessionId IN (%s) "
        "  ORDER BY localTime ASC LIMIT %d;" % (ph, limit)
    )
    out = []
    for line in query(sql).splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        ts, sender, text = parts
        out.append((ts, "我" if str(sender).strip() == str(me) else "她", text))
    return out


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    vm = getattr(soul, "VMI", 0)
    print(f"实例 {vm}｜活动库探测 = {active_sess(force=True)}｜我 = {me_uid()}")
    t0 = time.time()
    rows = pending()
    print(f"待回 {len(rows)} 个（{time.time()-t0:.1f}s）")
    for nick, uid, unread, text, ts in rows:
        print(f"  [{unread:>2}] {ts}  {nick:<18} {text[:34]}")
