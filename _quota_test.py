# -*- coding: utf-8 -*-
"""验收：匹配前「OCR 读剩余次数」（用户 2026-10-06 口径：匹配的时候 OCR 分析一下剩余次数）

用例里的 items 全部取自**真机 OCR 实况**：
  · 有额度：logs/checkin.log 2026-10-02 19:15 星球页
    `灵魂匹配 | 语音匹配 | 今日剩余46次 | 今日剩余3次 | …`
  · 已用完：2026-10-06 05:27 截图 OCR（本会话取证）
    只剩「今日剩余3次」（语音，x=546），灵魂那行没了 + 浮着「今日免费匹配机会已用完(50/50)」
"""
import sys, os, io, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

import soul
import soul_match as M

OK, BAD = [], []


def ck(name, got, want):
    if got == want:
        OK.append(name)
    else:
        BAD.append("%s  得到 %r  期望 %r" % (name, got, want))


# ── 真机实况数据 ──────────────────────────────────────────────
# (文本, 中心x, 中心y)，坐标是模拟器 900x1600 空间（中线 450）
ITEMS_HAS = [                       # 2026-10-02 19:15，灵魂还有 46 次
    ("Soul", 451, 90), ("筛选", 829, 92), ("灵魂测试", 140, 94),
    ("灵魂匹配", 125, 298), ("语音匹配", 556, 298),
    ("今日剩余46次", 142, 340), ("今日剩余3次", 546, 340),
    ("萌面匹配", 125, 664), ("新手优惠", 100, 707),
    ("开始匹配", 128, 534), ("仅匹配女生", 112, 890), ("更多玩法", 793, 1101),
]
ITEMS_OUT = [                       # 2026-10-06 05:27，灵魂已用完
    ("Soul", 451, 90), ("筛选", 829, 92), ("灵魂测试", 140, 94),
    ("灵魂匹配", 125, 298), ("语音匹配", 556, 298),
    ("今日剩余3次", 546, 340),                       # ← 只剩语音，灵魂那行消失了
    ("萌面匹配", 125, 664), ("新手优惠", 100, 707),
    ("开始匹配", 128, 534), ("仅匹配女生", 112, 890),
    ("今日免费匹配机会已用完(50/50)", 428, 994),      # ← 用完弹层
    ("完成活跃任务获取次数", 211, 1103), ("去聊天", 371, 1243),
    ("限时折扣", 374, 1387),
]
ITEMS_ZERO = [t for t in ITEMS_HAS]                  # 灵魂卡片显示 0 次的情形
ITEMS_ZERO[5] = ("今日剩余0次", 142, 340)


# ═══ A 组：soul_quota_left 三种判据 ═══
ck("A1 有额度→(46,3,无弹层)", soul.soul_quota_left(ITEMS_HAS), (46, 3, False))
ck("A2 用完→(None,3,弹层在)", soul.soul_quota_left(ITEMS_OUT), (None, 3, True))
ck("A3 读到0→(0,3,无弹层)", soul.soul_quota_left(ITEMS_ZERO), (0, 3, False))
ck("A4 空屏→(None,None,False)", soul.soul_quota_left([]), (None, None, False))
ck("A5 None→不炸，回未知", soul.soul_quota_left(None) is not None, True)

# ═══ B 组：只有一条「今日剩余N次」时的归位 ═══
ck("B1 只有左侧一条→归灵魂",
   soul.soul_quota_left([("灵魂匹配", 125, 298), ("今日剩余5次", 142, 340)]),
   (5, None, False))
ck("B2 只有右侧一条→归语音",
   soul.soul_quota_left([("语音匹配", 556, 298), ("今日剩余3次", 546, 340)]),
   (None, 3, False))

# ═══ C 组：额度判据规则（与 daemon 里两处判定**必须一致**）═══
def _exhausted(sq, sheet):
    """daemon do_match 里的判定：读到 0，或 读不到+弹层在"""
    return sq == 0 or (sq is None and sheet)


ck("C1 46次+无弹层→不算用完", _exhausted(46, False), False)
ck("C2 0次→算用完", _exhausted(0, False), True)
ck("C3 读不到+弹层→算用完", _exhausted(None, True), True)
ck("C4 读不到+无弹层→**未知**，不判用完", _exhausted(None, False), False)
ck("C5 3次+弹层在→有次数就不算用完", _exhausted(3, True), False)
ck("C6 None==0 是 False（None 绝不当中 0）", (None == 0), False)

# ═══ D 组：端到端（实况数据 → 判定结果）═══
_sq, _vq, _sh = soul.soul_quota_left(ITEMS_HAS)
ck("D1 有额度实况→不匹配后转唤醒? 否", _exhausted(_sq, _sh), False)
_sq, _vq, _sh = soul.soul_quota_left(ITEMS_OUT)
ck("D2 用完实况→应转唤醒", _exhausted(_sq, _sh), True)
ck("D3 用完实况语音侧仍读到 3 次（不影响灵魂判定）", _vq, 3)

# ═══ E 组：planet_match_quota 老接口**行为不变**（切号评估在用）═══
ck("E1 老接口签名仍是 (sq,vq)", len(soul._quota_from_items(ITEMS_HAS)), 2)
ck("E2 老接口有额度→(46,3)", soul._quota_from_items(ITEMS_HAS), (46, 3))
ck("E3 老接口用完→(None,3)", soul._quota_from_items(ITEMS_OUT), (None, 3))

# ═══ F 组：soul_match.planet_quota 缓存 ═══
M._PLANET_LAST["items"] = ITEMS_HAS
M._PLANET_LAST["ts"] = time.time()
ck("F1 缓存新鲜→用缓存(46,3)", M.planet_quota(), (46, 3, False))
M._PLANET_LAST["ts"] = time.time() - 9999          # 过期
_fresh = M.planet_quota()
ck("F2 缓存过期→现读（不炸，返回三元组）", len(_fresh), 3)
M._PLANET_LAST["items"] = None
ck("F3 没进过星球页→现读（不炸）", len(M.planet_quota()), 3)

# ═══ G 组：to_planet 缓存写入（不真点设备，只验钩子存在）═══
_src = open(os.path.join(BASE, "soul_match.py"), encoding="utf-8").read()
# to_planet 有三条 return True 路径，每条都要写缓存（含"点完星球tab重读一帧"那条）
ck("G1 to_planet 三条成功路径都写缓存",
   _src.count('_PLANET_LAST["items"], _PLANET_LAST["ts"] = '), 3)
ck("G2 缓存模块变量已定义", "_PLANET_LAST = " in _src, True)

# ═══ H 组：daemon 接线 ═══
_d = open(os.path.join(BASE, "soul_daemon.py"), encoding="utf-8").read()
_m = open(os.path.join(BASE, "soul_match.py"), encoding="utf-8").read()
ck("H1 do_match 里读了额度", "M.planet_quota()" in _d, True)
ck("H2 用完→返回 quota_out", 'return (0, ["quota_out"])' in _d, True)
ck("H3 上层认 quota_out", '"quota_out" in (_rs or [])' in _d, True)
ck("H4 用完→立刻唤醒（进唤醒条件）", "or _qo_fire" in _d, True)
ck("H5 有冷却防刷屏", 'st["quota_out_at"] = now' in _d, True)
ck("H6 用完弹层顺手清", "soul.close_quota_popup()" in _d, True)
ck("H7 点完按钮读出弹层→no_quota", 'return (None, "no_quota")' in _m, True)
ck("H8 上层把 no_quota 也算读完额", '"no_quota" in (_rs or [])' in _d, True)
# no_quota 必须在等待循环里判（点完才知道），且一次 OCR 同时判"进会话页"+弹层
ck("H9 等待循环里共用一次 OCR", "_it = rd.items()" in _m, True)
# ⭐ 2026-10-06 补：额度静默**到次日**（每日配额，当天再试也不会有新次数）
ck("H10 记当天日期", 'st["quota_out_date"] = _today()' in _d, True)
ck("H11 按当天日期判静默", '_qo_today = str(st.get("quota_out_date") or "") == _today()' in _d, True)
ck("H12 通道故障另算（不该放弃一整天）", "_brk_silent" in _d, True)
ck("H13 静默期仍刷新 last_match", 'st["last_match"] = now      # ⭐' in _d, True)

# ═══ I 组：静默判据（纯逻辑）═══
def _silent_for(qo_date, today):
    """当天额度读完 → 当天静默；跨天自动放行"""
    return qo_date == today


ck("I1 当天判用完→静默", _silent_for("2026-10-06", "2026-10-06"), True)
ck("I2 跨天→自动恢复", _silent_for("2026-10-06", "2026-10-07"), False)
ck("I3 从没判过用完→不静默", _silent_for("", "2026-10-06"), False)

# ═══ L 组：Soul 子页（RN 容器）自救 ═══
# 实测 14:11 卡在「搜索『韩梦慈』结果列表」页：activity 是
# cn.soul.android.soul_rn_sdk.multiengine.RnContainerActivity —— **不含 soulapp**
_s = open(os.path.join(BASE, "soul.py"), encoding="utf-8").read()
ck("L1 soul 有 ensure_replyable", "def ensure_replyable(" in _s, True)
ck("L2 判据覆盖 MainActivity/Conversation", '"MainActivity" in a or "Conversation" in a' in _s, True)
# to_planet 必须用 _is_soul_activity（包名前缀 cn.soul），不能手写 "soulapp" 子串
ck("L3 to_planet 用 _is_soul_activity", "soul._is_soul_activity(a) and \"MainActivity\" not in a" in _m, True)
ck("L4 to_planet 不再用裸 soulapp 子串", '"soulapp" in a and "MainActivity" not in a' in _m, False)
_rp = open(os.path.join(BASE, "soul_reply.py"), encoding="utf-8").read()
ck("L5 入口不再无条件退子页（会关掉奇遇铃刚开的私聊）",
   "soul.ensure_ready()\n    soul.ensure_replyable()" not in _rp, True)
ck("L6 已在会话页就直接发", "已在「{name}」的会话页 → 直接发" in _rp, True)
ck("L7 自救只放在「回不到聊天列表」之后", "尝试退出 Soul 子页后再导航一次" in _rp, True)

# ═══ M 组：切号优先级（2026-10-06 用户定稿「8小时优先切号 高于一切，正常切号排最后」）═══
ck("M1 强制切号跳过切号前复查", "if force:" in _d and "跳过「切号前有人可聊」复查" in _d, True)
ck("M2 强制切号不受 min_gap 限制", "if not forced:" in _d, True)
ck("M3 强制标记由在线时长决定", "forced = online_h >=" in _d, True)
ck("M4 干旱仍排在在线时长之后（在线优先）",
   _d.index("forced = online_h >=") < _d.index("dry, why = _dry_eval(st, book, cur)"), True)
ck("M5 账号冷却仍兜着（防来回横跳）", "if not tgt:" in _d, True)

# ═══ N 组：切号 UI 必须先回主框架（14:28 实测失败）═══
# 强制切号拦截过了，但「切换账号」入口在底导航「自己」→ 会话页/子页没有底导航 → 点落空
ck("N1 soul 有 to_main", "def to_main(" in _s, True)
ck("N2 切号入口先 to_main", "if not to_main():" in _s, True)
ck("N3 to_main 认 MainActivity", '"MainActivity" in a' in _s, True)

# ═══ J 组：状态标记**必须**与唤醒冷却解耦（2026-10-06 真 bug）═══
# 旧写法把 quota_out_date 塞进 `if _qo_fire:` → 冷却期 _qo_fire 恒 False →
# 标记永远写不进去 → 静默从不生效、每轮照进匹配（实测 06:39~06:42 连进 3 轮）
ck("J1 当天标记无条件设置", 'if _quota_out and str(st.get("quota_out_date") or "") != _today()' in _d, True)
ck("J2 标记不再写在 _qo_fire 里", _d.count('st["quota_out_date"] = _today()'), 1)
ck("J3 唤醒间隔会收紧", "WAKE_EVERY_IDLE" in _d and "_wake_gap" in _d, True)
ck("J4 主唤醒处也用收紧后的间隔", "_wgap = WAKE_EVERY_IDLE if" in _d, True)

# ═══ K 组：唤醒堵了也算「干旱」（2026-10-06 用户口径）═══
# 匹配没额度 → 立刻唤醒；唤醒冷却中/无人可唤 → 切号（别原地干等）
ck("K1 池有人但冷却中=这条路堵了", "在冷却中（%d 分钟后才到）" in _d, True)
ck("K2 池空也算堵", '"唤醒池 0 人"' in _d, True)
ck("K3 池有人且能唤→不算干旱", "return (False, \"唤醒池 %d 人（可唤）\" % wk)" in _d, True)
# ⚠️ 必须写 `or 3`：规则表里该键**存在但值可能是 None** → float(None) 抛 TypeError，
#    被外层 except 吞掉后整个切号评估静默失效（2026-10-06 07:31 实测撞到）
ck("K4 额度用完→干旱时阀收紧", 'R.get("dry_hold_fast_min") or 3' in _d, True)
ck("K4b 其他规则也用 or 兜底", 'R.get("dry_hold_min") or 30' in _d, True)
ck("K4c 规则表给了真数字", '"dry_hold_fast_min": 3' in open(os.path.join(BASE, "soul_acct.py"), encoding="utf-8").read(), True)
ck("K5 收紧后的时阀会被记住", 'rec["dry_hold_min_used"] = hold_min' in _d, True)

print("=" * 58)
print("通过 %d 项" % len(OK))
if BAD:
    print("失败 %d 项：" % len(BAD))
    for b in BAD:
        print("  ✗ " + b)
    sys.exit(1)
print("✅ 全部通过")
