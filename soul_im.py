# -*- coding: utf-8 -*-
"""直读 Soul IM 数据库（root 后可用）—— 最快最准的读消息方式
比截图/读屏都快：一次 SQL 拿到全部会话、未读数、消息原文；零 token 成本、100% 准确、不可能串台。

用法:
  python soul_im.py pull            # 先从模拟器拉最新库（含 -wal），再查之前建议先 pull
  python soul_im.py unread          # 未读会话（= 红点来源，含未读条数）
  python soul_im.py list            # 全部会话 + 最新消息 + 时间
  python soul_im.py chat 昵称       # 某人的对话记录（最近 40 条）

数据来源: /data/data/cn.soulapp.android/databases/
  IM-SDK-<SESS>-DATA.db  → chatmsg(消息) / session(会话+未读)
  chat_<SESS>            → im_user_bean(用户资料: userId→昵称)
"""
import subprocess, sqlite3, sys, io, os, time, shutil, re
from datetime import datetime

# ⚠️⚠️ 2026-09-28 迁移 MuMu：**adb 通道不可用**
#    （MuMu 不向 Windows 暴露 adb 端口，16384/5555 均拒绝连接；
#      实测关防火墙、试各端口、试 usingNormalADBPort 全部无效）
#    改走 mumu-cli 的 `sh` 免端口通道；文件传输改用共享目录：
#      Windows  D:\MuMuPlayer\vms\MuMuPlayer-15.0-0\private_shared
#      Android  /mnt/shared/private_shared
import soul as _soul

SHARED_WIN = _soul.SHARED_WIN       # ⚠️ 2026-09-30 双实例：单一来源（按实例取），别再各自写死
SHARED_AND = "/mnt/shared/private_shared"
SDBOX_AND = SHARED_AND + "/dbsync"


def active_dev(force=False):
    """兼容旧 API。MuMu 走 CLI 无「设备地址」概念，恒返回 'mumu'。"""
    return "mumu"

DIR = r"E:\soul"
DBDIR = "/data/data/cn.soulapp.android/databases"

# ---- 账号身份：按实例取（2026-09-30 双实例）----
# 写死主号身份会让实例1 把"账号2 自己发的话"当成"她发的" → 待回列表全反、乃至自己回自己。
# soul_vm.json 形如 {"1": {"me": "<uid>", "sess": "<sessionId>"}}；缺失/解析失败一律回退实例0 的历史值。
_ME_FALLBACK = "96691646"
_SESS_FALLBACK = "SmNjOUhiUUhZa1RWVlgvZUh1NEExdz09"


def _load_identity():
    try:
        import json as _json
        import soul_instance as _si
        p = os.path.join(DIR, "soul_vm.json")
        with open(p, "r", encoding="utf-8") as f:
            cfg = _json.load(f) or {}
        d = cfg.get(str(_si.vm_index())) or {}
        me = str(d.get("me") or "").strip() or _ME_FALLBACK
        sess = str(d.get("sess") or "").strip() or _SESS_FALLBACK
        return me, sess
    except Exception:
        return _ME_FALLBACK, _SESS_FALLBACK


ME, SESS = _load_identity()
# ⚠️ 2026-09-30 双实例：这两个**本地落库文件**也必须按实例分开！
#    原来两个实例共用一份 → ① 实例1 的 stats/门槛读到主号数据（串号）；
#    ② 两边每 15~20s 各 pull 一次，互相覆盖对方的本库（竞态级静默错误）。
try:
    from soul_instance import state_path as _sp
except Exception:
    def _sp(base, name):
        return os.path.join(base, name)

IMDB = _sp(DIR, "im_data.db")
CHATDB = _sp(DIR, "chat_im.db")

# 落库目标的唯一映射：pull() 换库时必须用它，**不许再写 os.path.join(DIR, dst)**
# ⚠️ 2026-09-30 实测踩到：换库那行原来写死 DIR → 实例1 的 pull 覆盖实例0 的正式库，
#    而实例1 自己那份永远不存在（表现为"换入后正式库校验失败（本地库不存在）"）。
_LOCAL_DB = {"im_data.db": IMDB, "chat_im.db": CHATDB}


def _local_of(dst):
    """本地正式库路径（按实例）。未知库名退回 DIR + 实例后缀。"""
    return _LOCAL_DB.get(dst) or _sp(DIR, dst)


def memdb():
    """当前账号的**累积库**路径（只增不减）。按登录账号解析（切号即换文件）。"""
    try:
        import soul_acct as _acct
        return _acct.path(DIR, "soul_memory.db")
    except Exception:
        return os.path.join(DIR, "soul_memory.db")


# ⭐ 2026-10-06：**设备原生** sessionId 集合（每次成功 pull 时刷新，回填**之前**抓取）。
#   用途：把累积库回填进正式库后，`pending()` 里两类会话必须区别对待 ——
#     · 设备真实会话（在集合里）→ 照旧不限时限（可能是攒了几天的未读，仍该回）；
#     · 回填进来的老会话（不在集合里）→ 套 24h 时限（口径同 `pending_mem`，冷过就算聊死了）。
#   否则回填会把 100+ 小时前的老会话全变成"待回"，脚本转头去打扰死人。
_DEVICE_SIDS = set()

def _adb(*a, **kw):
    """兼容垫片（2026-09-28 MuMu 迁移）—— 全部走 mumu-cli 的 sh 免端口通道。

    只处理 'shell ...' 形态；'connect'/'pull' 已由 pull() 内部改为共享目录，
    不再经过这里。MuMu 模拟器本身就是 root，原先的 `su -c` 也不再需要。
    """
    timeout = kw.pop("timeout", 30)
    args = [str(x) for x in a]
    if args and args[0] == "shell":
        args = args[1:]
    cmd = " ".join(args)
    # MuMu 内已是 root，去掉多余的 su 包裹（su 在部分镜像里不存在会直接报错）
    cmd = cmd.replace("su -c '", "").rstrip("'")
    return _soul.sh(cmd, timeout=timeout)

def _db_maxlt(path):
    """本地库的最新消息时间戳；读不到返回 -1（用于校验 pull 是否真的拿到了最新数据）"""
    import sqlite3 as _s
    for suf in ("-wal", "-shm"):       # 只读打开时若 wal 不配套，先不删（交由 pull 处理）
        pass
    try:
        c = _s.connect(f"file:{path}?mode=ro", uri=True)
        r = c.execute("SELECT MAX(localTime) FROM chatmsg").fetchone()
        c.close()
        return int(r[0] or 0)
    except Exception:
        return -1


def integrity(path=IMDB):
    """本地库是否结构完好。返回 (ok, 说明)。

    ⚠️ 2026-09-27 新增：实测出现过「**本地副本损坏**」而设备库完好 ——
    pull() 先删本地 -wal/-shm 再逐个写回，中间存在「新 .db + 旧 -wal」的窗口，
    任何在此窗口读库的进程都会看到结构错乱（`database disk image is malformed`
    / `Rowid out of order`）。而 `_db_maxlt()` 只查 MAX(localTime)，
    损坏时**照样能返回数字** → 上层误判"拉取成功"，故障被静默吞掉。
    所以校验必须查**结构**，不能只查"能否读出一行"。
    """
    import sqlite3 as _s
    try:
        if not os.path.exists(path):
            return False, "本地库不存在"
        c = _s.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
        try:
            r = str(c.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            c.close()
        return (r == "ok"), (r if r == "ok" else r.replace("\n", " ")[:160])
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ═══════════════ 当前登录账号：**权威探测（App prefs）** ═══════════════
# ⭐ 2026-10-05（用户口径：「如何确认当前账号 只需要点击导航栏的 自己即可」）：
#   UI 昵称是准的，但 OCR 慢且有误差。更优解 —— 直接读 **App 自己写的 prefs**，
#   App 内部就以它为准，切号**立即生效**、无需等新消息、零 OCR：
#     · sp_info_gather.xml → <string name="userid">96691646</string>   ← 当前登录 uid
#     · soul_startup.xml   → sp_key_crash_uid_name = <SESS>             ← 当前登录会话密钥
#   实测（2026-10-05 23:34，与 UI 截图三方对齐）：
#     UI 昵称「抬头仰望星空」/ userid=96691646 / crash_uid_name=SmNjOUhi…  ← 主号，一致 ✅
#   为什么不能用 _dd.active_sess()：它按「谁最后收到消息」选库 → 切号后新号还没收到
#   新消息，旧号库时间戳更晚 → **持续猜错**（实测把主号猜成了 402857053 离殇）。故它降级为兜底。
PREFS_DIR = "/data/data/cn.soulapp.android/shared_prefs"
_PREFS_ID = {"uid": None, "sess": None, "ts": 0.0}
_PREFS_TTL = 15.0


def _own_uid_of_sess(sess):
    """某个会话库「自己」的 uid（库内 senderId 计数最高者）。取不到返回 None。"""
    if not sess:
        return None
    try:
        db = "%s/IM-SDK-%s-DATA.db" % (DBDIR, sess)
        out = _soul.sh(
            'sqlite3 "%s" "SELECT senderId FROM chatmsg WHERE senderId IS NOT NULL '
            'GROUP BY senderId ORDER BY COUNT(*) DESC LIMIT 1;" 2>/dev/null' % db)
        v = (out or "").strip()
        return v or None
    except Exception:
        return None


def prefs_identity(force=False):
    """当前登录账号 (uid, sess)。读不到的那项为 None。

    两个信号，**交叉校验**（2026-10-05 23:53 实测标定）：
      · `sp_info_gather.xml` → `userid`           —— 切号**立即跟随**（可靠）✅
      · `soul_startup.xml`   → `sp_key_crash_uid_name` —— ⚠️ **只在 App 启动时写一次**，
          切号后**不跟随**（实测：切到账号2 后它仍是主号的会话）→ **不可单独采信**。
    因此：
      uid  ← `userid`
      sess ← **由 uid 在设备库里反查**（哪个库以它为"自己"，soul_devdb 直查，0.4s）
             ；反查不到才退回 crash_uid_name，再退回配置里的 sess。
    """
    now = time.time()
    if not force and _PREFS_ID["ts"] and (now - _PREFS_ID["ts"]) < _PREFS_TTL:
        return _PREFS_ID["uid"], _PREFS_ID["sess"]
    uid = crash_sess = None
    try:
        g = _soul.sh("cat %s/sp_info_gather.xml 2>/dev/null" % PREFS_DIR, timeout=15)
        m = re.search(r'name="userid">([^<]+)<', g or "")
        if m:
            uid = m.group(1).strip() or None
    except Exception:
        pass
    try:
        s = _soul.sh("cat %s/soul_startup.xml 2>/dev/null" % PREFS_DIR, timeout=15)
        m = re.search(r'name="sp_key_crash_uid_name">([^<]+)<', s or "")
        if m:
            crash_sess = m.group(1).strip() or None
    except Exception:
        pass
    # sess：优先「由 uid 反查库」，其次 crash_uid_name，最后配置
    sess = None
    if uid:
        sess = _sess_of_uid(uid)
        if not sess:
            sess = crash_sess
    if not sess:
        sess = crash_sess
    if not uid and sess:
        uid = _own_uid_of_sess(sess)
    _PREFS_ID.update({"uid": uid, "sess": sess, "ts": now})
    return uid, sess


def _sess_of_uid(uid):
    """设备上「以 uid 为自己」的会话库（senderId 计数最高者 == uid）。找不到返回 None。"""
    if not uid:
        return None
    try:
        import soul_devdb as _dd
        for s in _dd._candidate_sessions():
            db = "%s/IM-SDK-%s-DATA.db" % (DBDIR, s)
            out = _soul.sh(
                'sqlite3 "%s" "SELECT senderId FROM chatmsg WHERE senderId IS NOT NULL '
                'GROUP BY senderId ORDER BY COUNT(*) DESC LIMIT 1;" 2>/dev/null' % db)
            if (out or "").strip() == str(uid):
                return s
    except Exception:
        pass
    return None


def _device_active_sess():
    """探测设备上当前账号的活动库会话名（重新登录/切号后密钥会变）。

    优先级（2026-10-05 改）：
      ① **权威**：App prefs 记的当前会话密钥（切号立即生效，不等新消息）；
      ② 由 prefs 的 uid 反查哪个库以它为「自己」；
      ③ 兜底：按 chatmsg 新鲜度猜（_dd.active_sess，切号后可能滞后）；
      ④ 再兜底：目录里第一个 IM-SDK-*.db。
    """
    try:
        _uid, ps = prefs_identity()
        if ps:
            return ps
        s2 = _sess_of_uid(_uid)
        if s2:
            return s2
    except Exception:
        pass
    try:
        import soul_devdb as _dd
        found = _dd.active_sess(force=True)
        if found:
            return found
    except Exception:
        pass
    # 兜底：目录里第一个 IM-SDK-*.db 的名字
    try:
        out = _soul.sh(f"ls -1 {DBDIR}/ 2>/dev/null | grep -E '^IM-SDK-'")
        for line in out.splitlines():
            m = re.match(r"^IM-SDK-(.+?)-DATA\.db$", line.strip())
            if m:
                return m.group(1)
    except Exception:
        pass
    return SESS


_DEV_UID_CACHE = {"ts": 0.0, "s": None}
_DEV_UID_TTL = float(os.environ.get("SOUL_DEV_UID_TTL", "20"))


def device_uids(ttl=None):
    """设备端**当前真实存在**的会话对象 uid 集合（一次 sqlite3 直查设备库，~0.3s，带缓存）。

    ⭐ 2026-10-06 用户口径「**没有就算了 不要死磕**」：
      Soul 会把本地会话删掉 —— 实测 杨三岁(363494435)/意中人♑️(446415751)/
      甜心姐姐丶(488096709)/💕小謎(357893109) 在设备端 **33 个库里零命中**，
      但累积库里还留着旧消息 → 脚本每轮把它们当待回，跑 UI 全路径硬找 ≈100s，
      最后必然失败（`⚠️发送未成功`）→ 一轮 480s 里近一半被吃光，还饿死匹配/唤醒流程。
      有了这个集合，发送前 0.3s 就能判「设备端根本没这个会话」→ 直接放弃。

    返回 set[str]；**拿不到返回 None**（sqlite3 不可用 / adbd 掉权 / 目录空）
    → 调用方一律**不拦**（宁可不省时间，也绝不误杀真人）。
    """
    ttl = _DEV_UID_TTL if ttl is None else float(ttl)
    now = time.time()
    if _DEV_UID_CACHE["s"] is not None and (now - _DEV_UID_CACHE["ts"]) < ttl:
        return _DEV_UID_CACHE["s"]
    out = None
    try:
        sess = _device_active_sess()
        db = "%s/IM-SDK-%s-DATA.db" % (DBDIR, sess)
        txt = _soul.sh('sqlite3 "%s" "SELECT toUserId FROM session;" 2>/dev/null' % db,
                       timeout=15)
        txt = (txt or "").replace("\r", "")
        uids = {ln.strip() for ln in txt.splitlines() if ln.strip().isdigit()}
        if uids:
            out = uids
        else:
            print("[warn] device_uids()：设备会话表读空（可能 adbd 掉权/未登录）→ 本轮不做该闸")
    except Exception as e:
        print(f"[warn] device_uids()：设备直查失败（{e!r}）→ 本轮不做该闸")
    _DEV_UID_CACHE["ts"], _DEV_UID_CACHE["s"] = now, out
    return out


def has_device_session(uid):
    """设备端**还有没有**这个人的会话。拿不到设备数据 → 恒 True（不拦，fail-open 到原路径）。"""
    s = device_uids()
    if s is None:
        return True
    return str(uid) in s


def _device_me():
    """探测当前登录账号的 uid（活动库里发消息最多的人 = 账号自己）。

    换号/重登后 ME 可能变（实测实例重启后变成账号1 96691646）。
    取不到返回 None，调用方自行决定是否用静态 ME。

    ⭐ 2026-10-05：**优先 App prefs 的 userid**（权威、切号立即生效）；
    只有 prefs 读不到才退回「库内 senderId 计数最高者」的老办法（切号后会滞后猜错）。
    """
    try:
        uid, _s = prefs_identity()
        if uid:
            return uid
    except Exception:
        pass
    try:
        sess = _device_active_sess()
        db = f"{DBDIR}/IM-SDK-{sess}-DATA.db"
        out = _soul.sh(f'sqlite3 "{db}" "SELECT senderId, COUNT(*) c FROM chatmsg '
                       f'WHERE senderId IS NOT NULL GROUP BY senderId ORDER BY c DESC LIMIT 1;" 2>/dev/null')
        line = (out or "").strip().splitlines()
        if line and "|" in line[0]:
            return line[0].partition("|")[0].strip()
    except Exception:
        pass
    return None


def pull(retry=4):
    """把两个库(+wal/shm)复制到 sdcard 再拉到本地。

    ⚠️ 2026-09-27 修**静默丢新消息**（最危险的一类 bug，本轮实测踩到）：
      旧实现有两个坑 ——
        ① 本地旧的 `-wal` **不先删**：某次 `-wal` 拉取失败（`cp`/`pull` 的错误被
           `2>/dev/null` 吞掉）时，本地就留着**上一版 -wal 配新 .db**；
           SQLite 检测 salt 不匹配会**静默忽略整个 wal** → 最新消息全丢，
           而上层只看到"无新消息"，一切正常。
        ② 只要求 `ok>=2`（拉到主库就算成功）→ 带 wal 的库拉一半也算成功。
      现在：**先删本地 -wal/-shm**（杜绝错配）→ 按设备上**实际存在的文件**逐个校验，
      全部拉到才算成功，并用 `MAX(localTime)` 兜底确认能读。

    ⚠️⚠️ 2026-09-27 **二次修复：本地副本被 pull 自己写坏**（实测 `integrity_check`
       报 `Rowid out of order` / `session` 表 `malformed`，而**设备库完好**）。
       根因：上面那个"先删本地 -wal/-shm、再逐个写回"的做法，制造了一个
       **「新 .db + 旧/无 -wal」的中间窗口**；此刻任何读库的进程（watcher 每 60s 读一次、
       agent 侧 `_my_last_text()`）都会看到结构错乱的库，抛
       `database disk image is malformed`。而 `_db_maxlt()` 只查 MAX(localTime)，
       损坏时**照样返回数字** → 上层误判"拉取成功"，故障被静默吞掉。

       现在改为 **「先落 stage 目录 → 校验 → 再原子换入」**：
         ① 全部文件先下到 `_dbsync/stage/`，**绝不在窗口期碰正式文件**；
         ② 在 stage 上跑 `PRAGMA integrity_check` + `MAX(localTime)`，**只有干净才换**；
         ③ 换入用 `os.replace` 整体替换，读者要么看到旧的、要么看到新的（不会看到半成品）；
            Windows 上若被别的进程占着句柄（WinError 5/32）就退避重试；
         ④ 换入后**再校验一次正式库**，失败则明确报错返回 0（上层必须当成失败，不许当"没消息"）。
    """
    # ⭐ 2026-10-05 动态会话发现：账号重新登录/切换后 SESS 密钥会变（实测
    #   账号2 的 WGpB… 在模拟器重启后被账号1 的 SmNj… 取代，静态 SESS 直接拉空）。
    #   拉取前先探测设备上「当前活动库」（按 chatmsg 新鲜度），再按它构造文件名；
    #   探测不到才退回静态 SESS。
    sess = _device_active_sess()
    pairs = [(f"IM-SDK-{sess}-DATA.db", "im_data.db"), (f"chat_{sess}", "chat_im.db")]
    SUF = ("", "-wal", "-shm")
    # ⭐ 2026-09-29 并发隔离（读写分离 watcher 落地后实测踩到）：
    #   STAGE / 中转目录原本是**全局唯一**的。后台 watcher 与主流程同时 pull 时，
    #   两边都先 `rm -rf` 对方刚写进 stage 的文件 → 双双报
    #   "stage 校验不过（本地库不存在）"，而上层只看到"无新消息"（静默失败）。
    #   现在按 SOUL_PULL_TAG 分目录（默认 main；watcher 传 watch），改完互不干扰。
    # ⚠️ 2026-09-30 双实例：默认 tag 必须带实例号，否则两个实例共用 `stage_main`
    #    → 互删对方 stage 里的文件（历史上 watcher 与主流程就吃过这个亏）。
    _tag = os.environ.get("SOUL_PULL_TAG", "").strip() or (
        "main" if getattr(_soul, "VMI", 0) <= 0 else "main.%d" % _soul.VMI)
    STAGE = os.path.join(DIR, "_dbsync", f"stage_{_tag}")
    os.makedirs(STAGE, exist_ok=True)
    ok = 0
    for attempt in range(retry):
        # ① 设备上实际存在哪些文件（-wal/-shm 可能不存在）
        exist = _soul.sh("ls -1 " + DBDIR + " 2>/dev/null").replace("\r", "")
        present = set(exist.split())
        if not present:
            # ⭐ 2026-10-06 修复：空目录最常见的原因是 **adbd 掉回 shell 权限**
            #   （uid=2000 读不了 /data/data），而不是"MuMu 没启动"。
            #   旧文案 + 旧逻辑（root 每进程只查一次）叠加 → 一旦掉权，整条链路
            #   默默报废到进程结束（实测 00:50 起 6 个待回全部"库里有没聊过"被拒）。
            #   现在：立即**强制复核 root**（adb root），成功就再试一次。
            _healed = False
            try:
                if _soul._adb_ensure_root(force=True):
                    exist = _soul.sh("ls -1 " + DBDIR + " 2>/dev/null").replace("\r", "")
                    present = set(exist.split())
                    _healed = bool(present)
            except Exception as _e:
                print(f"[warn] pull：adb root 复核异常 {_e!r}")
            if not present:
                print(f"[warn] pull 第 {attempt+1} 次：读不到设备库目录 "
                      f"（adbd 非 root 且自愈失败 / MuMu 未启动 → {DBDIR} 为空）")
                time.sleep(2.0)
                continue
            print(f"[warn] pull：adbd 掉权已自愈 → 库目录可读（{len(present)} 个文件）")
        # ② 清空 stage（上一轮的残留绝不能混进这一轮）
        for name in os.listdir(STAGE):
            try:
                os.remove(os.path.join(STAGE, name))
            except Exception:
                pass
        # ③ 设备 → 共享目录（MuMu 免端口传文件；原先的 adb push/pull 已不可用）
        #    ⚠️ 只用一份中转目录，先清空，避免上一轮的旧文件被当成本轮结果
        # 中转目录同样按 tag 隔离（见上方 STAGE 注释）
        mid = os.path.join(SHARED_WIN, f"dbsync_{_tag}")
        os.makedirs(mid, exist_ok=True)
        for name in os.listdir(mid):
            try:
                os.remove(os.path.join(mid, name))
            except Exception:
                pass
        _SDBOX = SHARED_AND + f"/dbsync_{_tag}"
        _soul.sh(f"mkdir -p {_SDBOX} && rm -f {_SDBOX}/*")
        cmds = [f"cp -f {DBDIR}/{src}{suf} {_SDBOX}/{dst}{suf} 2>/dev/null"
                for src, dst in pairs for suf in SUF]
        _soul.sh(";".join(cmds) + f"; chmod 666 {_SDBOX}/* 2>/dev/null")
        # ④ 共享目录 → stage（**不碰正式文件**）
        need, got = 0, 0
        for src, dst in pairs:
            for suf in SUF:
                if f"{src}{suf}" not in present:
                    continue               # 设备上没有 → 不算缺
                need += 1
                sp = os.path.join(mid, dst + suf)
                tp = os.path.join(STAGE, dst + suf)
                if not os.path.exists(sp):
                    continue
                # 共享目录写入可能有延迟 → 等大小稳定再拷
                prev = -1
                for _ in range(8):
                    try:
                        cur = os.path.getsize(sp)
                    except OSError:
                        cur = -1
                    if cur > 0 and cur == prev:
                        break
                    prev = cur
                    time.sleep(0.25)
                try:
                    shutil.copy(sp, tp)
                    if os.path.getsize(tp) == os.path.getsize(sp) and os.path.getsize(tp) > 0:
                        got += 1
                except OSError as e:
                    print(f"[warn] 共享目录拷入失败 {dst}{suf}: {e!r}")
        if not need or got < need:
            time.sleep(1.5)
            continue
        # ⑤ 在 stage 上校验（结构 + 可读 + 有内容）——**干净才允许换入**
        smax = _db_maxlt(os.path.join(STAGE, "im_data.db"))
        sok, swhy = integrity(os.path.join(STAGE, "im_data.db"))
        if not sok or smax < 0:
            print(f"[warn] pull 第 {attempt+1} 次：stage 校验不过（{swhy}）→ 不换入，重试")
            time.sleep(1.5)
            continue
        # ⑤.5 ⭐ 2026-10-06：趁 stage 还是**纯设备数据**，先记下设备原生的 sessionId 集合
        #      （换入 + 回填之后就没法区分了）。pending() 靠它区分"设备真会话/回填老会话"。
        try:
            _sp_db = os.path.join(STAGE, "im_data.db")
            _sc = sqlite3.connect("file:%s?mode=ro" % _sp_db.replace("\\", "/"), uri=True)
            _DEVICE_SIDS.clear()
            _DEVICE_SIDS.update(str(r[0]) for r in _sc.execute("SELECT sessionId FROM session"))
            _sc.close()
        except Exception as _e:
            print(f"[warn] pull：抓设备原生 sessionId 集合失败（{_e!r}）→ 本轮 pending 全按回填处理")
        # ⑥ 原子换入：整体替换，读者不会看到"新 db + 旧 wal"的半成品
        staged = []
        for _, dst in pairs:
            for suf in SUF:
                sp = os.path.join(STAGE, dst + suf)
                tp = _local_of(dst) + suf      # ⚠️ 必须按实例！写死 DIR 会覆盖另一个实例的库
                if os.path.exists(sp):
                    staged.append((sp, tp))
                elif suf in ("-wal", "-shm") and os.path.exists(tp):
                    # 设备上不存在的 wal/shm：本地必须一起清掉，否则又是错配
                    try:
                        os.remove(tp)
                    except Exception:
                        pass
        moved, locked, soft = 0, False, []
        for sp, tp in staged:
            done = False
            for back in range(8):
                try:
                    os.replace(sp, tp)
                    moved += 1
                    done = True
                    break
                except OSError:
                    time.sleep(0.25)       # 被读进程占着句柄 → 退避重试
            if done:
                continue
            # ⭐ 2026-09-30 降级路径（双实例实测）：
            #   Windows 上 `os.replace` 是**重命名**语义 —— 只要目标文件被任何进程
            #   打开过（哪怕只是只读打开、且已关闭），重命名就会被拒（WinError 5）。
            #   实测：`chat_im.1.db` 永远换不进去 → 该文件长期停在 0 字节 →
            #   `names()` 拿不到昵称映射 → **整个实例静默报"待回 0"**。
            #   而"就地覆写"（open 'wb' 写内容）在同样情况下是成功的（不涉及重命名）。
            #   ⚠️ 代价：覆写不是原子的，读者可能读到半截文件 —— 所以只在
            #   `os.replace` 彻底失败后才用，并且**写完立刻校验**（下面 ⑦ 会兜）。
            try:
                with open(sp, "rb") as fsrc, open(tp, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1 << 20)
                soft.append(os.path.basename(tp))
                moved += 1
            except OSError as e:
                print(f"[warn] pull：{os.path.basename(tp)} 既不能换入也不能覆写（{e!r}）")
                locked = True
                break
        if soft:
            print(f"[warn] pull：{', '.join(soft)} 无法原子换入，已降级为**就地覆写**"
                  f"（读者可能读到半截，已即时校验）")
        if locked:
            print("[warn] pull：正式库被其他进程占用，未能原子换入 → 本轮不换（保留旧数据，等下次）")
            time.sleep(1.5)
            continue
        # ⑦ 换入后再校验正式库：坏就明确失败（绝不返回"成功"）
        lok, lwhy = integrity(IMDB)
        if not lok:
            print(f"[warn] pull：换入后正式库校验失败（{lwhy}）→ 报告失败")
            time.sleep(1.5)
            continue
        ok = moved
        # ⭐ 2026-10-06 用户拍板「把累积库合并到正式库里面啊」：
        #   正式库刚被设备数据整体替换 → **立即把累积库补回正式库**，让正式库
        #   变成「设备 ∪ 累积库全量」，所有只读正式库的下游一次到位。
        #   顺序很重要：先回填（累积→正式），再增量并入（正式→累积，收设备新消息）。
        #   两步都失败也绝不影响 pull 返回值（拿到的设备数据已经落库）。
        try:
            _bf = backfill_imdb()
            if _bf:
                print(f"[pull] 已把累积库回填进正式库 {_bf} 行")
        except Exception as _e:
            print(f"[warn] pull 后 backfill_imdb 异常（不影响本轮拉取）: {_e!r}")
        try:
            _n = merge_memory()
            if _n:
                print(f"[pull] 已增量并入累积库 {_n} 条")
        except Exception as _e:
            print(f"[warn] pull 后 merge_memory 异常（不影响本轮拉取）: {_e!r}")
        return ok
    return ok

def _names_db():
    """（原 `names()` 实现）**设备/正式**昵称库 `chat_im.db.im_user_bean` 的原始昵称表。
    userId → 昵称。库/表还没建出来时返回 {}（并**明确告警**，不许静默当成功）。

    ⚠️ 2026-09-30 双实例实测：新账号在 App 首次同步出会话之前，本地还没有
    `chat_<Ecpt>` 这个库 → 老代码直接抛 `no such table: im_user_bean` 把整轮打断
    （实例1 的 watcher 连续报"本次跳过（pending 异常）"）。缺库/缺表属正常状态，
    降级成空映射；其他错误照旧抛出，免得把真故障藏起来。

    ⚠️⚠️ 2026-09-30 **再修：昵称不全在 chat_<sess> 里**（账号2 实测）。
    账号2（402857053）的 `chat_WGpB…` 里 `im_user_bean` **一行都没有**，
    它的 4 个联系人昵称全在 **`chat_default_db`**。
    后果：待回判定要靠昵称显示，映射为空 → 实例1 明明有 1 个待回却报「**待回 0 个**」，
    而设备直查能看到「脑袋淡淡放空感」——**同一个问题两条路径结论相反**，属最危险的一类。
    现在：本地库读空时，**回退到设备端**补昵称（设备上三张候选表全扫），
    仍然拿不到才认空。回退只发生在"本地确实没有"时，不影响正常实例。
    """
    d = {}
    try:
        c = sqlite3.connect(CHATDB)
        try:
            d = {str(u): (s or str(u)) for u, s in
                 c.execute("SELECT userId, signature FROM im_user_bean")}
        finally:
            c.close()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e) or "unable to open" in str(e):
            print(f"[warn] _names_db()：聊天库尚未就绪（{e}）→ 尝试设备端回退")
        else:
            raise
    if d:
        return d

    # ── 设备端回退：扫本实例所有可能的昵称表 ──
    try:
        cands = [f"{DBDIR}/chat_{SESS}", f"{DBDIR}/chat_default_db",
                 f"{DBDIR}/chat_im_user.db"]
        rows = _soul.sh("; ".join(
            f'sqlite3 "{p}" "SELECT userId, signature FROM im_user_bean;" 2>/dev/null'
            for p in cands))
        n = 0
        for line in rows.splitlines():
            if "|" in line:
                u, _, s = line.partition("|")
                u, s = u.strip(), s.strip()
                if u and s and u not in d:
                    d[u] = s
                    n += 1
        print(f"[warn] _names_db()：本地昵称库为空 → 设备端回退补齐 {n} 条")
    except Exception as e:
        print(f"[warn] _names_db()：设备端回退失败（{e!r}）")
    if not d:
        print("[warn] _names_db()：本地与设备端都拿不到昵称 → 按空映射处理，"
              "该实例本轮只会有系统/无待回结果")
    return d


_NAMES_ALL_TTL = float(os.environ.get("SOUL_NAMES_ALL_TTL", "15"))
_names_all_cache = {"ts": 0.0, "d": {}}


def names(ttl=None):
    """userId → 昵称。**统一入口** = 正式昵称库 ∪ 累积库 `nick` 表（只增不减）。

    ⭐ 2026-10-06 用户拍板「把累积库合并到正式库里面」：

    🔴 为什么必须并（用户 2026-10-05/06 反复踩到）：
      正式昵称库 `chat_im.db.im_user_bean` 只有 **77 条**，而累积库 `nick` 有 **258 条**
      —— 差额的 181 人是被 Soul 裁剪掉的老联系人，昵称映射一起没了；
      `soul_reply._resolve_db_target` 用昵称判定「库里有没有聊过且骨架含这个名字」
      → 判否 → **对真人拒发**（宁可不发，用户看到的就是"还有人没回复"）。
      并集后这两处直接自愈，`pending()`/`sessions()` 显示也不再退化成 uid。

    实现：`_names_db()`（设备/正式）优先，缺失的用累积库 nick 补；带 TTL 缓存（默认 15s），
    避免高频调用反复开累积库。返回**副本**，调用方随意改不影响缓存。
    （`im_user_bean` 有 41 列、多个 NOT NULL 无默认 → 不适合把累积库昵称硬写进去，
      改在**读取入口**并集，语义等价且零脏数据风险。）
    """
    ttl = _NAMES_ALL_TTL if ttl is None else float(ttl)
    now = time.time()
    if _names_all_cache["d"] and (now - _names_all_cache["ts"]) < ttl:
        return dict(_names_all_cache["d"])
    out = dict(_names_db() or {})
    try:
        p = memdb()
        if os.path.exists(p):
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            try:
                for uid, name in c.execute("SELECT uid,name FROM nick"):
                    u = str(uid)
                    if u and name and u not in out:
                        out[u] = str(name)
            finally:
                c.close()
    except Exception as e:
        print(f"[warn] names()：累积库昵称读取失败（{e!r}）→ 仅用正式昵称库")
    _names_all_cache["ts"], _names_all_cache["d"] = now, out
    return dict(out)


def names_all(ttl=None):
    """兼容旧名：`names()` 的统一入口（正式 ∪ 累积库）。"""
    return names(ttl)


def merge_memory():
    """把正式库(`IMDB`)增量并入累积库（`soul_memory.db`）—— Soul 会清库，这里只增不减。

    ⭐ 2026-10-06 用户拍板「把累积库和正式库合并一下不就行了」：
      原实现只挂在 `soul_daemon` 每轮末尾调一次 → 累积库**每轮才同步一次，天然滞后**
      （实测我 00:45 发的句子 00:56 才进累积库）。现在改挂在 `pull()` 成功之后
      —— **只要拉了正式库就立即并入**，滞后窗口从"一整轮"缩到"一次 pull 之内"。
    `soul_daemon.merge_memory()` 保留同名薄包装，老调用点行为不变。
    """
    src = IMDB
    if not os.path.exists(src):
        return 0
    ME_S = str(ME)
    m = sqlite3.connect(memdb())
    m.execute("""CREATE TABLE IF NOT EXISTS chatmsg(
      sessionId TEXT, msgId TEXT, senderId TEXT, receiverId TEXT, localTime INTEGER,
      msgType INTEGER, text TEXT, msgContent TEXT, PRIMARY KEY(sessionId, msgId))""")
    m.execute("""CREATE TABLE IF NOT EXISTS session(
      sessionId TEXT PRIMARY KEY, toUserId TEXT, chatType INTEGER, unReadCount INTEGER,
      timestamp INTEGER, lastMsgText TEXT)""")
    m.execute("CREATE TABLE IF NOT EXISTS nick(uid TEXT PRIMARY KEY, name TEXT)")
    m.execute("CREATE INDEX IF NOT EXISTS idx_cm_sid ON chatmsg(sessionId, localTime)")
    m.commit()
    added = 0
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        ccols = set(x[1] for x in s.execute("PRAGMA table_info(chatmsg)").fetchall())
        use = [c for c in ("sessionId", "msgId", "senderId", "receiverId", "localTime",
                           "msgType", "text", "msgContent") if c in ccols]
        if use:
            for r in s.execute("SELECT %s FROM chatmsg" % ",".join(use)).fetchall():
                d = dict(zip(use, r))
                if not str(d.get("sessionId") or "").startswith(ME_S):
                    continue
                try:
                    cur = m.execute("INSERT OR IGNORE INTO chatmsg(%s) VALUES(%s)"
                                    % (",".join(use), ",".join("?" * len(use))),
                                    [d.get(k) for k in use])
                    added += cur.rowcount or 0
                except Exception:
                    pass
        scols = set(x[1] for x in s.execute("PRAGMA table_info(session)").fetchall())
        suse = [c for c in ("sessionId", "toUserId", "chatType", "unReadCount",
                            "timestamp", "lastMsgText") if c in scols]
        if suse:
            for r in s.execute("SELECT %s FROM session" % ",".join(suse)).fetchall():
                d = dict(zip(suse, r))
                if not str(d.get("sessionId") or "").startswith(ME_S):
                    continue
                try:
                    m.execute("INSERT OR REPLACE INTO session(%s) VALUES(%s)"
                              % (",".join(suse), ",".join("?" * len(suse))),
                              [d.get(k) for k in suse])
                except Exception:
                    pass
        s.close()
        m.commit()
    except Exception as e:
        print(f"[warn] merge_memory 出错: {e!r}")
    # 昵称映射（正式库为准）
    try:
        for uid, name in (names() or {}).items():
            m.execute("INSERT OR REPLACE INTO nick(uid,name) VALUES(?,?)", (str(uid), str(name)))
        m.commit()
    except Exception:
        pass
    m.close()
    return added


def backfill_imdb():
    """⭐ 2026-10-06 用户拍板「**把累积库合并到正式库里面啊**」：

    把累积库(`soul_memory.db`)里**正式库没有的** session / chatmsg 回填进正式库
    (`im_data.db`)，让「正式库」= **设备数据 ∪ 累积库全量**：
      · 所有只读正式库的下游（`pending` / `sessions` / `leads` / `follow` /
        `soul_reply._hist_uids` 的「聊过 ≥3 句」身份核对）都直接看到全量历史，
        不必再各自打累积库补丁；
      · 被 Soul 裁剪掉的会话全部回来（实测正式库仅 26 个 session，累积库 473 个）。

    ⚠️ 正式库每次 pull 都被设备数据**整体替换** → 必须在 pull 成功后调用，回填才持久。
    ✅ 幂等：`session.sessionId` / `chatmsg.msgId` 上都是 **UNIQUE 索引** → `INSERT OR IGNORE`
       不会重复，也**不会覆盖设备刚拉下来的新数据**（设备行先到，OR IGNORE 保它）。
    📌 正式库表比累积库多列、且若干 NOT NULL 无默认 → 按目标表列清单补齐（缺的填 0/派生值）。
    返回本轮回填的行数（0 = 无需回填）。
    """
    src, dst = memdb(), IMDB
    if not (os.path.exists(src) and os.path.exists(dst)):
        return 0
    ME_S = str(ME)
    spec = (
        # (表名, 值来自累积库的列→目标列默认表达式, 过滤条件)
        ("session",
         {"userType": "0", "msgStatus": "0", "status": "0", "dbStatus": "0"},
         "sessionId LIKE ?"),
        ("chatmsg",
         {"serverTime": "localTime", "msgStatus": "0", "msgReceiveStatus": "0",
          "ack": "0", "snapChat": "0", "msgSource": "0", "showType": "0", "dbStatus": "0"},
         "msgId IS NOT NULL AND msgId <> '' AND sessionId LIKE ?"),
    )
    added = 0
    try:
        c = sqlite3.connect(dst, timeout=20)
    except Exception as e:
        print(f"[warn] backfill_imdb：打开正式库失败（{e!r}）")
        return 0
    try:
        c.execute("ATTACH DATABASE ? AS mem", (src,))
        for tname, defs, where in spec:
            try:
                dinfo = c.execute("PRAGMA table_info(%s)" % tname).fetchall()
                dcols = [r[1] for r in dinfo]
                notnull = {r[1] for r in dinfo if r[3] and r[4] is None and r[5] == 0}
                # id 是自增主键 → 不写，交给 SQLite
                tgt = [x for x in dcols if x != "id"]
                mcols = set(r[1] for r in c.execute("PRAGMA mem.table_info(%s)" % tname))
                sel, missing = [], []
                for col in tgt:
                    if col in mcols:
                        sel.append(col)
                    elif col in defs:
                        sel.append(defs[col])
                    elif col in notnull:
                        missing.append(col)
                        sel.append("0")
                    else:
                        sel.append("NULL")
                if missing:
                    print(f"[warn] backfill_imdb：{tname} 有 NOT NULL 列无处取值 {missing} → 跳过该表")
                    continue
                cur = c.execute(
                    "INSERT OR IGNORE INTO %s(%s) SELECT %s FROM mem.%s WHERE %s"
                    % (tname, ",".join(tgt), ",".join(sel), tname, where),
                    (ME_S + "%",))
                added += cur.rowcount or 0
            except Exception as e:
                print(f"[warn] backfill_imdb：回填 {tname} 失败（{e!r}）→ 跳过")
        c.commit()
        try:
            c.execute("DETACH DATABASE mem")
        except Exception:
            pass
    except Exception as e:
        print(f"[warn] backfill_imdb 出错: {e!r}")
    finally:
        try:
            c.close()
        except Exception:
            pass
    return added


def _fmt(ts):
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
    except Exception:
        return "?"

def sessions(unread_only=False):
    nm = names()
    c = sqlite3.connect(IMDB)
    q = ("SELECT sessionId, toUserId, unReadCount, timestamp, lastMsgText FROM session "
         + ("WHERE unReadCount>0 " if unread_only else "")
         + "ORDER BY timestamp DESC")
    n = 0
    for sid, uid, unread, ts, last in c.execute(q):
        flag = f" ★未读{unread}" if unread else ""
        print(f"{_fmt(ts)}  {nm.get(str(uid), uid)}{flag}\n    {str(last)[:50]}")
        n += 1
    c.close()
    print(f"--- 共 {n} 个会话 ---")

# ⭐ 2026-10-06 新增：**累积库兜底待回**（修「有人没回复、脚本却看不见」）
_MEM_PEND_TTL = float(os.environ.get("SOUL_MEM_PEND_TTL", "20"))
_MEM_PEND_MAX_H = float(os.environ.get("SOUL_MEM_PEND_MAX_H", "24"))
_mem_pend_cache = {"ts": 0.0, "rows": []}


def pending_mem(max_h=None, ttl=None):
    """⭐ 2026-10-06 新增：**累积库兜底待回**。

    🔴 为什么必须要有（用户 2026-10-06 当场质疑「好像还有人没回复啊」）：
      `pending()` 只读**正式库** `im_data.db`，而 **Soul 会自己裁剪本地库** ——
      实测 00:39：正式库只有 **14 个 session / 44 条消息**（时间跨度仅 10-03 起），
      而累积库 `soul_memory.db` 里当前账号有 **411 个 session**、其中 **21 个末条是她发的**。
      ⇒ 被 Soul 裁掉的老会话，脚本**完全看不见** → 她明明在等回复，脚本却报「待回 0 个」
        → 于是转去匹配/唤醒（用户看到的就是"怎么跑到唤醒流程去了"）。

    口径（保守，防误发 / 防炒冷饭）：
      · 只认**当前账号**的会话（`sessionId` 以当前 uid 开头）—— 跟随 App 内切号
      · 只认「最近一条真人消息是**她**发的」（跳过系统卡片/图片/语音按转写算）
      · 只认 **冷 ≤ max_h 小时**（默认 24h）—— 更老的算聊死了，不再回也不主动打扰
      · 与正式库重复的会话由 `pending()` 统一去重
      · 结果带 TTL 缓存（默认 20s），避免每轮全表扫描

    返回与 `pending()` 同构的 7 元组列表：`(timestamp, name, unread, text, localTime, sessionId, msgType)`
    （这里 `timestamp` 与 `localTime` 都取她的 `localTime`，`unread` 恒 0 —— 正式库才有未读数）
    """
    max_h = _MEM_PEND_MAX_H if max_h is None else float(max_h)
    ttl = _MEM_PEND_TTL if ttl is None else float(ttl)
    now = time.time()
    if _mem_pend_cache["rows"] and (now - _mem_pend_cache["ts"]) < ttl:
        return _mem_pend_cache["rows"]
    try:
        import soul_acct as _acct
        db = _acct.path(DIR, "soul_memory.db")
    except Exception:
        db = os.path.join(DIR, "soul_memory.db")
    out = []
    if not os.path.exists(db):
        return out
    me = str(ME)
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        nicks = {str(u): n for u, n in c.execute("SELECT uid,name FROM nick")}
        cutoff = int((now - max_h * 3600) * 1000)
        rows = c.execute(
            "SELECT sessionId, senderId, text, msgContent, localTime, msgType FROM ("
            "  SELECT sessionId, senderId, text, msgContent, localTime, msgType,"
            "         ROW_NUMBER() OVER (PARTITION BY sessionId ORDER BY localTime DESC) rn"
            "  FROM chatmsg WHERE sessionId LIKE ?"
            ") WHERE rn <= 4 AND localTime >= ? ORDER BY sessionId, localTime DESC",
            (me + "%", cutoff)).fetchall()
        c.close()
    except Exception as e:
        print(f"  !! 累积库待回扫描失败（保守返回空）: {e!r}")
        return out
    by_sess = {}
    for sid, sender, text, content, lt, mt in rows:
        by_sess.setdefault(sid, []).append((str(sender), text, content, int(mt or 0), int(lt or 0)))
    for sid, msgs in by_sess.items():
        real = None
        for sender, text, content, mt, lt in msgs:      # 已按 localTime DESC
            eff = str(text).strip() if (text and str(text).strip()) else ""
            if not eff and mt == VOICE_MT:
                eff = _voice_text(content)              # 语音 → Soul 自带转写
            if not eff:
                continue                                # 图片/视频/系统卡片 → 不算她说话
            if _is_sys(eff, content):
                continue
            real = (sender, eff, lt, mt)
            break
        if not real:
            continue
        sender, eff, lt, mt = real
        if sender == me:
            continue                                    # 末条是我发的 → 不是待回
        uid = sid[len(me):] if sid.startswith(me) else ""
        name = nicks.get(uid) or uid or sid
        if _is_official(name) or _is_official_uid(uid, eff):
            continue
        out.append((lt, name, 0, eff, lt, sid, mt))
    out.sort(key=lambda z: z[0], reverse=True)
    _mem_pend_cache["rows"] = out
    _mem_pend_cache["ts"] = now
    return out


def pending():
    """⭐ 待回（**全靠数据库，不读屏**）：所有会话中「最后一条真实消息是对方发的」= 我该回。

    2026-09-26 用户口径：
      · **未读 + 已读未回 都算待回**，不因为"不是未读"就跳过；
      · 系统卡片（text 为空 + clickItems/bgUrl 等）不算她发言，必须滤掉；
      · 已标 stopped/skipped/gift 的不打扰。

    ⭐ 2026-10-04 语音消息：她发语音 = 一条真实发言（文字取 Soul 自带转写 content.word）。
    返回元组固定 7 元，尾部新增 msgType（>=6 元的老读法用 `[:6]` 或显式 7 元解包）：
      (timestamp, name, unread, text, localTime, sessionId, msgType)
      其中 msgType == VOICE_MT(5) 表示"她末条是语音"→ 上层应尽量用语音回。
    """
    pull()
    nm = names()
    status = _db_status()
    c = sqlite3.connect(IMDB)
    rows = c.execute("SELECT sessionId, toUserId, unReadCount, timestamp FROM session "
                     "ORDER BY timestamp DESC").fetchall()
    out = []
    answered = set()        # ⭐ 2026-10-06：正式库里"末条是我发的"的 sid（已回过，防累积库滞后重报）
    for sid, uid, unread, ts in rows:
        name = nm.get(str(uid), str(uid))
        if _is_official(name) or status.get(name) in ("stopped", "skipped", "gift"):
            continue
        # 逐条往前跳过系统卡片，取「最近一条真人消息」
        # ⭐ 2026-10-04：带 msgType —— 语音(msgType=5)的文字在 content.word 里，不在 text 列。
        raw = c.execute("SELECT senderId, text, msgContent, localTime, msgType FROM chatmsg "
                        "WHERE sessionId=? ORDER BY localTime DESC LIMIT 8", (sid,)).fetchall()
        real = None
        for sender, text, content, lt, mt in raw:
            eff = str(text).strip() if (text and str(text).strip()) else ""
            if not eff and int(mt or 0) == VOICE_MT:
                eff = _voice_text(content)     # 语音 → Soul 自带转写当"她的话"
            if not eff:
                # 没有可读文本（系统卡片 / 图片 / 视频 / 转写失败的语音）→ 不算她说话。
                # ⚠️ 比旧逻辑更严（旧逻辑对 text=None 且非卡片的内容会带出 text=None 的假末条）。
                continue
            if _is_sys(eff, content):
                continue
            real = (sender, eff, lt, int(mt or 0))
            break
        if not real:
            continue
        sender, text, lt, mt = real
        if _is_official_uid(uid, text):        # 官方助手 → 不算待回
            continue
        # ⭐ 2026-10-06：回填进正式库的**老会话**要套时限（口径同 pending_mem）——
        #   设备原生会话不在这个分支，行为与改造前逐字节一致。
        if sid not in _DEVICE_SIDS:
            _age_h = (time.time() * 1000 - int(lt or 0)) / 3600000.0
            if _age_h > _MEM_PEND_MAX_H:
                continue
        if str(sender) != ME:
            # ⭐ 2026-10-03 带出 sid（历史兜底）；⭐ 2026-10-04 再带 msgType（供上层判"该用语音回"）
            out.append(((ts or 0), name, unread, text, lt, sid, mt))
        else:
            # ⭐ 2026-10-06：这个会话**末条是我发的** = 已回过。记下来给下面的累积库合并用。
            #   正式库是**刚从设备 pull 下来的**（最新），累积库 soul_memory.db 由后台同步、
            #   会滞后（实测 00:45 我发给「匆匆那年」的那句还没同步进去）→ 若只按 sid 去重，
            #   累积库仍会把她当"待回"并进来 → **对同一个人重复发消息**。
            answered.add(sid)
    c.close()
    # ⭐ 2026-10-06 并上**累积库兜底**：Soul 会裁剪本地正式库（实测只剩 14 个 session），
    #   被裁掉的老会话在正式库里查不到 → 她明明在等回复却报「待回 0 个」。
    #   只认当前账号 + 末条是她发的 + 冷 ≤24h（见 pending_mem），与正式库按 sid 去重。
    _from_mem = 0
    try:
        _have = {r[5] for r in out}
        _skip_mem = 0
        for _r in pending_mem():
            # ⭐ 2026-10-06 补：兜底分支**也必须按档案状态过滤**。
            #   正式库那段有 `status.get(name) in stopped/skipped/gift` 过滤，这里漏了
            #   → 已「停手转人工」(skipped) 的人从累积库照样报成真待回。
            #   实测 06:57：4 个 skipped 的人（杨三岁/小謎/意中人/甜心姐姐）**永远**占着
            #   `pending()` → 唤醒一开就「发现 4 个真待回 → 中断」→ **唤醒从没真正发过**，
            #   待回又永远清不掉 → 切号条件①永远不成立 → 机器整晚空转。
            if _is_official(_r[1]) or status.get(_r[1]) in ("stopped", "skipped", "gift"):
                continue
            # ⭐ 2026-10-06：`_r[5] in answered` = 正式库说"这个会话我已经回过了"
            #   → 累积库滞后（还没同步到我这句）也不许再报，否则重复发消息。
            if _r[5] in _have or _r[5] in answered:
                if _r[5] in answered and _r[5] not in _have:
                    _skip_mem += 1
                continue
            out.append(_r)
            _have.add(_r[5])
            _from_mem += 1
        if _skip_mem:
            print(f"  （累积库兜底里有 {_skip_mem} 个已在正式库回过 → 跳过，防重复发）")
    except Exception as e:
        print(f"  !! 累积库待回合并失败（不影响正式库结果）: {e!r}")
    out.sort(key=lambda z: z[0], reverse=True)
    print("---------- 待回（未读 + 已读未回，数据库口径）----------")
    for ts, name, unread, text, lt, _sid6, _mt in out:
        flag = f"未读{unread}" if unread else "已读"
        tip = "  ← 收尾语，用新话题接" if (text and str(text) in CLOSERS) else ""
        vt = "  🎙语音" if _mt == VOICE_MT else ""
        print(f"  [{flag:>4}] {_fmt(lt)}  {name:<18} 她: {str(text)[:34]}{vt}{tip}")
    if _from_mem:
        print(f"  （其中 {_from_mem} 个来自**累积库兜底**：Soul 已从本地正式库裁掉这些会话）")
    print(f"--- 待回 {len(out)} 个 ---")
    return out


OFFICIAL = ("我的遇见", "系统通知", "官方号消息")

# ⭐ 2026-09-30：Soul 官方「陪伴聊天助手」是一个**有正常昵称的账号**，按昵称判不出来。
#   实测（账号2 新号）：它开场发两条「你好呀，我是 Soul 里的陪伴聊天助手。接下来我会用几个
#   轻松的小问题先认识你一下」+「最近生活过得怎么样呀」，uid=478918501。
#   后果很严重：`pending()` 把它算成"她最后发言"→ 永远有 1 个待回 →
#   `soul_match.py` 的抢占检查点每次开跑就被它打断 → **新号的匹配永久卡死**
#   （实测实例1 匹配 3 个，一次都没匹配上就 `结束原因: pending`）。
#   ⇒ 按 **senderId** 硬过滤（昵称会变，uid 不会）。
OFFICIAL_UIDS = ("478918501",)

# 官方助手的特征文案（命中即视为官方，防 uid 变化）
OFFICIAL_TEXT = ("Soul 里的陪伴聊天助手", "Soul里的陪伴聊天助手")
CLOSERS = ("晚安", "睡了", "好", "嗯", "哦", "好的", "哈哈", "行")
SYS_KEYS = ("clickItems", "buttonConfig", "jumpUrl", "activityId", "highlightText",
            "tagAuthGuide", "bgUrl", "emotionUrl", "偶遇crush", "Soulmate", "soulmate",
            # 2026-09-26 21:36 补：Soul 的**互动推送卡片**（type=35，senderId 挂在对方/我 ID 下）
            # 典型文案："你wink了一下XXX，打完招呼…"、"Ta现在想聊天，打个招呼吧"、"你好奇地看了看XXX"
            # 这几条不补的话会被误判成"她说话了"，凭空多出待回任务（实测一次多出 2 条假的）
            "trackId", "pushType", "chat-interact-push", "interact-push",
            "打招呼吧", "想聊天", "打完招呼", "emojiInfo", "actionContent",
            "wink", "Wink", "看了看",
            # 2026-09-29 补：进入会话时 App 自动写入的**引导卡片**（用我的 senderId 落库）
            #   实测本轮出现 4 类：「authTags」标签引导、「tagAuthGuide」、
            #   msgType=35 的 little_tip「你们聊得很不错,对方在等你聊天」、「NEW_USER_HINT」
            #   不补的话，换成对方 senderId 就会被误判成"她说话了"，凭空多出待回。
            "authTags", "authTagType", "commonlyOwn", "little_tip", "NEW_USER_HINT",
            "对方在等你聊天", "等你聊天",
            # 2026-09-29 补（**静默漏判，实测遮掉了真实待回**）：
            #   mt=35 的「个性化消息气泡」卡片 content 含 "messageType":"bubble_im_choice"，
            #   不在旧 SYS_KEYS 里 → 被当成"真人消息" → 在 pending() 里排在前面且 senderId=我，
            #   直接把 -Heart. 07:39 那句真实回复「特别吗」**盖掉**，该会话从待回队列消失。
            #   实测：mt IN(27,35,32,9) 中共 19 条卡片未被旧判据识别，全部含 "messageType"。
            #   已验证真实媒体消息 mt=2/5/8（图片/语音/表情包）**从不含** "messageType"，
            #   故整体加入是安全的。
            "messageType", "bubble_im_choice")


# ⭐ 2026-10-03 补媒体键：图片/语音/视频（text=None + content 含媒体字段）此前被
#   当"真人消息"顶掉真实文本末条 → pending 取到 text=None → daemon _is_fake_last
#   判"假末条"排除 → **未读被静默漏掉**（实测：今天快乐~ ~ 图片末条 未读2 漏回）。
#   现在媒体消息按"非真人文本"跳过，pending 落到真实文本末条（如"打牌破防了"）。
# ⭐ 2026-10-04 **键名纠错**（用户提「对方发语音我该用语音回」时查库发现）：
#   旧表里的 voiceUrl/audioUrl 是**臆想键名**，真实语音消息一个都不命中。
#   实测真值（账号2 库）：msgType=5, text=None,
#     {"duration":2,"localUrl":"","url":"https://...m4a","word":"这个有什么瞎操心的呀。","mark":-1}
#   → 旧逻辑把语音当"真人消息"带出 text=None，到 daemon 又被当"假末条"剔除
#     ⇒ **她发语音 = 石沉大海**（既不回也不会出现在待回列表）。
#   现在：media 键改用真实名，且语音单独走 `_voice_text()` 取其自带转写。
MEDIA_KEYS = ("imageH", "imageW", "imageUrl", "localUrl", "videoUrl", "fileUrl", "picUrl",
              "duration", "word")

# 语音消息类型（Soul msgType）——content.word 是 Soul **自带语音转文字**，无需自建 ASR。
VOICE_MT = 5


def _voice_text(content):
    """语音消息 → Soul 自带转写文本（content.word）；解析不出返回 ""（fail-closed）。"""
    s = str(content or "")
    if not s:
        return ""
    try:
        import json as _json
        j = _json.loads(s)
        if isinstance(j, dict):
            w = j.get("word")
            if w:
                return str(w).strip()
    except Exception:
        pass
    m = re.search(r'"word"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
    if m:
        try:
            import json as _json
            return str(_json.loads('"%s"' % m.group(1))).strip()
        except Exception:
            return m.group(1).strip()
    return ""


def _is_sys(text, content):
    if text:
        return False
    s = str(content or "")
    if not s or s == "None":
        return True          # 空内容不可能是真人消息 → 卡片（fail-closed）
    if _voice_text(s):
        # ⭐ 2026-10-04 语音 + Soul 自带转写 = 她的真实发言（**不是卡片/媒体**）。
        #   取文本用 _voice_text(content)；若不在这里放行，语音会被当卡片滤掉
        #   → 「她发语音」永远不产生待回、永远不回。
        return False
    return any(k in s for k in SYS_KEYS) or any(k in s for k in MEDIA_KEYS)


def _is_official(name):
    n = str(name)
    if n in OFFICIAL:
        return True
    return n.replace("-", "").replace("_", "").replace(" ", "").isdigit()


def _is_official_uid(uid, text=""):
    """官方助手判定（按 uid 硬过滤，昵称会变）。
    见 OFFICIAL_UIDS 注释：它被算成待回会**卡死新号的匹配**。"""
    if str(uid).strip() in OFFICIAL_UIDS:
        return True
    t = str(text or "")
    return any(k in t for k in OFFICIAL_TEXT)


def _db_status():
    """读本地档案状态（JSON，不是 SQLite —— 2026-09-26 起本地库已废弃）"""
    try:
        import soul_db
        return soul_db.status_map()
    except Exception:
        return {}


# ⭐ 互动推送 = **她在向你示好**（wink / 看过你 / Ta想聊天）—— 2026-09-26 用户口径：
#   "系统推的卡片可以聊的就聊啊"。这类**不是待回消息**（她没说话），
#   但**是最热的新开场线索**，单独用 `leads` 列出来主动打招呼。
LEAD_KEYS = ("wink", "Wink", "看了看", "想聊天", "打招呼吧", "打完招呼",
             "interact-push", "chat-interact-push")


def _is_lead(text, content):
    if text:
        return False
    s = str(content or "")
    return any(k in s for k in LEAD_KEYS)


def leads(hours=48, limit=20):
    """列出「系统推送 = 她主动示好」的真人：wink / 看过我 / Ta想聊天。

    **不是待回**（她没说话），是**最值得主动开场的热线索**。
    """
    pull()
    nm = names()
    status = _db_status()
    c = sqlite3.connect(IMDB)
    since = (time.time() - hours * 3600) * 1000
    rows = c.execute("SELECT sessionId, toUserId, timestamp FROM session "
                     "ORDER BY timestamp DESC").fetchall()
    out = []
    for sid, uid, ts in rows:
        name = nm.get(str(uid), str(uid))
        if _is_official(name) or status.get(name) in ("stopped", "skipped", "gift"):
            continue
        raw = c.execute("SELECT senderId, text, msgContent, localTime FROM chatmsg "
                        "WHERE sessionId=? ORDER BY localTime DESC LIMIT 10", (sid,)).fetchall()
        hit = None
        for sender, text, content, lt in raw:
            if _is_lead(text, content):
                hit = (sender, lt, str(content))
                break
        if not hit:
            continue
        sender, lt, content = hit
        if lt < since:
            continue
        # 判断是不是已经聊过（有真人消息往来）
        real = [r for r in raw if r[1]]
        talked = len(real)
        kind = ("她 wink/看过你" if ("wink" in content or "Wink" in content or "看了看" in content)
                else "Ta 想聊天")
        out.append({"name": name, "ts": lt, "last": _fmt(lt), "kind": kind,
                    "talked": talked, "from_me": str(sender) == ME})
    c.close()
    out.sort(key=lambda z: z["ts"], reverse=True)
    print(f"---------- 可聊的推送线索（近 {hours}h，她主动示好）----------")
    if not out:
        print("  （无）")
    for p in out[:limit]:
        tag = f"已聊{p['talked']}句" if p["talked"] else "**还没开口**"
        _p_line = (f"  {p['name']:<20} {p['kind']}  {p['last']}   {tag}")
        print(_p_line)
    print(f"--- 线索 {len(out)} 个（优先挑「还没开口」的主动打招呼）---")
    return out


def follow(min_msgs=10, cool_h=12, sync=False):
    """⭐ 可推进关系 · 隔 12 小时续聊（2026-09-26 用户定）

    门槛（2026-09-26 用户原话）："**只要和我聊上有十句的都可以续聊**"
      → min_msgs = 双方真实消息**合计** ≥ 10 句（她回 + 我说），系统卡片不计。

    逻辑（**全数据库**）：
      · promising = 她**回复过 ≥ min_replies 条**（真聊起来过的，不是单方面输出）；
      · 候选 = promising 且**最后一条是我发的**（说明她没接、对话凉了）且**冷了 ≥12 小时**；
      · 按冷却时长降序，冷的越久越优先主动开口。
    --sync 会把新达标的人标记 status='promising'（写 soul_notes.json）。
    """
    pull()
    nm = names()
    status = _db_status()
    c = sqlite3.connect(IMDB)
    rows = c.execute("SELECT sessionId, toUserId, timestamp FROM session "
                     "ORDER BY timestamp DESC").fetchall()
    out = []
    for sid, uid, ts in rows:
        name = nm.get(str(uid), str(uid))
        if _is_official(name) or status.get(name) in ("stopped", "skipped", "gift"):
            continue
        msgs = c.execute("SELECT senderId, text, msgContent, localTime FROM chatmsg "
                         "WHERE sessionId=? ORDER BY localTime DESC LIMIT 300", (sid,)).fetchall()
        real = [m for m in msgs if not _is_sys(m[1], m[2])]
        if not real:
            continue
        hers = sum(1 for m in real if str(m[0]) != ME)
        total = len(real)                  # 双方合计句数（用户口径：聊上十句）
        last_sender, last_text, last_lt = real[0][0], real[0][1], real[0][3]
        if total < min_msgs:
            continue
        if str(last_sender) != ME:
            continue                       # 她最后发言 → 属 pending，不在这里重复
        idle_h = (time.time() * 1000 - last_lt) / 3600000.0
        if idle_h < cool_h:
            continue
        out.append((idle_h, name, hers, total, last_text, last_lt))
    c.close()
    out.sort(key=lambda z: z[0], reverse=True)

    print(f"---------- 可推进续聊（合计≥{min_msgs}句 · 冷却≥{cool_h}h · 数据库口径）----------")
    if not out:
        print("  （无 —— 没有够格且冷够 12 小时的人）")
    for idle_h, name, hers, total, last_text, lt in out:
        print(f"  {name:<20} 合计 {total:>3} 句(她{hers:>3}) | 上次 {_fmt(lt)} | 已冷 {idle_h:.1f}h")
        print(f"      我最后说: {str(last_text)[:40]}")
    print(f"--- 可推进 {len(out)} 个 ---")

    if sync and out:
        try:
            import soul_db
            n = 0
            st = soul_db.status_map()
            for idle_h, name, hers, total, _, _ in out:
                if st.get(name) in ("", "active", None):
                    soul_db.set_status(name, "promising")
                    n += 1
            print(f"[sync] 已把 {n} 人标记为 promising")
        except Exception as e:
            print("[sync] 写入失败:", e)
    return out


def sync(verbose=True):
    """⭐ 「同步」= 直接拉 Soul 原库（2026-09-26 用户定）

    用户口径：**能直接拿到 Soul 自己的库，本地再抄一份消息就没意义了。**
    所以本函数只做 pull()，不再往 soul_chat.db 复制消息。

    · 消息/会话/回复数/冷却时长 → 一律**直读 Soul 原库** `im_data.db`（本模块全部函数都是）；
    · `soul_chat.db` 只保留**原库没有的那一层**：status(stopped/skipped/gift/promising)、
      notes（人设备注）、match_pct、tags 等**我自己的标注**。
    """
    n = pull()
    if verbose:
        print(f"[sync] 已拉取 Soul 原库（{n} 个文件）→ 消息直读 im_data.db，"
              f"本地库只存档案/状态/备注")
    return n


def chat(nick, limit=40):
    nm = names()
    # ⭐ 2026-09-28：精确优先 + 多候选告警。原实现 nick in v 取第一个命中，
    #   "如初"（新匹配）会被 "若只如初见"（老会话）抢走 → 看到的是别人的聊天记录。
    cands = [(k, v) for k, v in nm.items() if nick in v]
    if not cands:
        print("找不到昵称:", nick); return
    exact = [(k, v) for k, v in cands if str(v).strip() == str(nick).strip()]
    if exact:
        uid = exact[0][0]
    else:
        uid = cands[0][0]
        if len(cands) > 1:
            print("⚠ 昵称模糊匹配到 %d 个会话，取的是：%s" % (len(cands), cands[0][1]))
            for k, v in cands[:6]:
                print("    候选 - %s (uid %s)" % (v, k))
    c = sqlite3.connect(IMDB)
    sids = [r[0] for r in c.execute("SELECT sessionId FROM session WHERE toUserId=?", (uid,))]
    if not sids:
        print("没有会话记录"); c.close(); return
    ph = ",".join("?" * len(sids))
    rows = c.execute(f"SELECT senderId, text, localTime FROM chatmsg WHERE sessionId IN ({ph}) "
                     f"ORDER BY localTime DESC LIMIT ?", (*sids, limit)).fetchall()
    c.close()
    for sid, text, lt in reversed(rows):
        who = "我" if str(sid) == ME else nm.get(str(sid), str(sid))[:8]
        print(f"[{_fmt(lt)}] {who}: {text}")
    print(f"--- {nm[uid]} 共显示 {len(rows)} 条 ---")

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "unread"
    if cmd == "pull":
        print("已拉取文件数:", pull())
    elif cmd == "unread":
        sessions(True)
    elif cmd == "list":
        sessions(False)
    elif cmd == "pending":
        pending()
    elif cmd == "sync":
        sync()
    elif cmd == "leads":
        leads(hours=int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 48)
    elif cmd == "follow":
        args = sys.argv[2:]
        follow(min_msgs=int(args[0]) if args and args[0].isdigit() else 10,
               cool_h=int(args[1]) if len(args) > 1 and args[1].isdigit() else 12,
               sync="--sync" in args)
    elif cmd == "chat":
        chat(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 40)
    else:
        print(__doc__)
