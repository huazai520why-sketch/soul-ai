# -*- coding: utf-8 -*-
"""账号维度隔离 —— 切号后「一个号的记忆绝不能污染另一个号」。

背景（2026-10-05 用户口径：单实例 + 在 Soul App 内手动切号）
------------------------------------------------------------
原来这几个文件都是**全局一份、不区分账号**：

  · `soul_memory.db`   累积库（只增不减；唤醒候选的来源）
  · `soul_notes.json`  好友档案（备注 / status / 亲密阶梯）
  · `state.0.json`     daemon 运行状态（last_match / wake_day / wake_off / 冷却计数…）

切号后必须按账号分开。否则 B 号会：
  ① 拿 A 号的好友清单去「唤醒」→ 列表里找不到 → 白跑；
  ② 昵称撞名时**拿 B 的身份去撩 A 认识的人** → 串号事故（最严重）；
  ③ A 的 last_wake / match 计时污染 B → 节奏错乱。

约定（铁律）
------------
  · **主号沿用原名**（`soul_memory.db` / `soul_notes.json` / `state.0.json`）
    —— 现有数据零迁移、零风险；只有非主号才加 `.<uid>` 后缀。
  · 后缀插在**扩展名之前**：`soul_memory.db` → `soul_memory.402857053.db`。
  · 主号由 `soul_accounts.json` 的 `primary` 指定（默认 96691646）。
  · 路径**运行期解析**（切号立即生效），uid 带 30s 缓存 —— 因为 soul_db 是高频调用，
    不能每次都去 mumu-cli 探设备。

本模块**不得 import soul / soul_im 到模块级**（避免循环依赖），一律函数内惰性 import。
"""
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "soul_accounts.json")

_DEFAULT_UID = "96691646"

# 默认切号规则（用户 2026-10-05 拍板）
DEFAULT_RULES = {
    "online_max_h": 8,        # 连续在线 ≥8h → 强制切号
    "dry_hold_min": 30,       # 三条件（待回0+唤醒池0+匹配0）持续 ≥30min → 切号
    "cool_h": 4,              # 切走后 ≥4h 才能切回该号
    "min_gap_min": 30,        # 两次切号最小间隔（防抖动）
    "dry_need_match0": True,  # 三条件里是否要求「匹配次数=0」
    # ⭐ 2026-10-06（用户：「唤醒冷却中/无人唤醒 → 切号」，嫌等 30 分钟太久）：
    #   当天匹配额度读完为 0（匹配整天没戏）+ 唤醒也堵着 → 干旱时阀收紧到这个值。
    #   ⚠️ 必须是**真数字**：写 None 会让 daemon 里 float(None) 抛 TypeError。
    "dry_hold_fast_min": 3,
}

_C = {"uid": None, "ts": 0.0, "override": None, "override_ts": 0.0}
_TTL = 30.0


# ───────────────────────── 配置 ─────────────────────────
def load_cfg():
    """读 `soul_accounts.json`；不存在则用内置默认（主号 96691646）。"""
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    if not isinstance(d.get("accounts"), list) or not d["accounts"]:
        d["accounts"] = [{"uid": _DEFAULT_UID, "nickname": "抬头仰望星空", "sess": ""}]
    if not d.get("primary"):
        d["primary"] = str(d["accounts"][0].get("uid") or _DEFAULT_UID)
    rules = dict(DEFAULT_RULES)
    rules.update(d.get("rules") or {})
    d["rules"] = rules
    return d


def accounts():
    """[{uid, nickname, sess}] —— 参与轮转的账号（顺序 = 轮转顺序）。"""
    out = []
    for a in (load_cfg().get("accounts") or []):
        uid = str(a.get("uid") or "").strip()
        if uid:
            out.append({"uid": uid,
                        "nickname": str(a.get("nickname") or "").strip(),
                        "sess": str(a.get("sess") or "").strip()})
    return out


def primary():
    return str(load_cfg().get("primary") or _DEFAULT_UID)


def rules():
    return dict(load_cfg().get("rules") or DEFAULT_RULES)


def nickname_of(uid):
    uid = str(uid or "")
    for a in accounts():
        if a["uid"] == uid:
            return a["nickname"] or uid
    return uid


def is_primary(uid=None):
    return str(uid or cur_uid() or "") == primary()


# ───────────────────────── 当前账号 ─────────────────────────
def set_uid(uid, ttl=120.0):
    """**临时覆盖**当前账号（切号动作期间用：UI 已切、设备探测还有缓存时保证一致）。"""
    _C["override"] = str(uid) if uid else None
    _C["override_ts"] = time.time()
    _C["uid"] = str(uid) if uid else None
    _C["ts"] = time.time()
    return _C["uid"]


def clear_override():
    _C["override"] = None
    _C["override_ts"] = 0.0


def cur_uid(force=False):
    """当前登录账号 uid。

    顺序：① 临时覆盖 → ② 进程缓存(30s) → ③ App prefs 权威探测 → ④ soul_vm.json 的 me
          → ⑤ 配置里的主号。任何一步失败都不抛，尽力返回一个值。
    """
    now = time.time()
    if _C["override"] and (now - _C["override_ts"]) < 120:
        return _C["override"]
    if not force and _C["uid"] and (now - _C["ts"]) < _TTL:
        return _C["uid"]
    uid = None
    try:
        import soul_im as im
        uid, _s = im.prefs_identity(force=force)
    except Exception:
        uid = None
    if not uid:
        try:
            import soul_im as im
            uid = str(getattr(im, "ME", "") or "") or None
        except Exception:
            uid = None
    uid = uid or primary()
    _C["uid"] = str(uid)
    _C["ts"] = now
    return _C["uid"]


# ───────────────────────── 路径 ─────────────────────────
def _suffix(name, uid):
    """在扩展名前插 `.<uid>`：soul_memory.db → soul_memory.<uid>.db"""
    root, ext = os.path.splitext(name)
    if not ext:
        return "%s.%s" % (name, uid)
    return "%s.%s%s" % (root, uid, ext)


def path(base_dir, name, uid=None):
    """账号维度的文件路径。主号 → 原名；其余 → 加 `.<uid>` 后缀。"""
    uid = str(uid or cur_uid() or "")
    if not uid or uid == primary():
        return os.path.join(base_dir, name)
    return os.path.join(base_dir, _suffix(name, uid))


def for_uid(base_dir, name, uid):
    """显式指定账号的路径（不带缓存/探测，供迁移与诊断用）。"""
    uid = str(uid or "")
    if not uid or uid == primary():
        return os.path.join(base_dir, name)
    return os.path.join(base_dir, _suffix(name, uid))


def state_path(out_dir, vm, uid=None):
    """daemon 运行状态文件。主号 → `state.<vm>.json`（原名，零迁移）。"""
    return path(out_dir, "state.%s.json" % vm, uid=uid)


# ───────────────────────── 切号簿（全局，跨账号）─────────────────────────
SWITCH_F = os.path.join(BASE, "switch_state.json")


def load_switch():
    try:
        with open(SWITCH_F, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_switch(d):
    try:
        tmp = SWITCH_F + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SWITCH_F)
    except Exception:
        pass


def switch_book():
    """{current_uid, online_since, acct:{uid:{leave_at, dry_since, switches, last_switch_at}}}"""
    d = load_switch()
    d.setdefault("current_uid", None)
    d.setdefault("online_since", None)
    d.setdefault("last_switch_at", None)
    acct = d.setdefault("acct", {})
    for a in accounts():
        r = acct.setdefault(a["uid"], {})
        r.setdefault("leave_at", None)      # 上次切走时刻（冷却基准）
        r.setdefault("dry_since", None)     # 三条件同时成立的起点（连续观察）
        r.setdefault("switches", 0)
    return d


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("primary   =", primary())
    print("cur_uid() =", cur_uid(force=True))
    print("accounts  =", accounts())
    print("rules     =", rules())
    print("soul_memory.db ->", path(BASE, "soul_memory.db"))
    print("soul_notes.json->", path(BASE, "soul_notes.json"))
    print("switch book ->", json.dumps(switch_book(), ensure_ascii=False))
