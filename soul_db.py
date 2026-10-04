# -*- coding: utf-8 -*-
"""Soul 好友档案（**只存 Soul 原库没有的那一层**）

2026-09-26 用户定：**能直接拉到 Soul 自己的 IM 库，本地库就不要了**。
  · 会话 / 消息 / 回复数 / 冷却时长 → 一律直读 Soul 原库（`soul_im.py`，pull 后读 im_data.db）
  · 本模块只存**原库没有的东西**：status（该不该继续）、notes（人设备注）、
    match_pct / tags / planet 等**匹配时抓到的资料**（原库里没有）。
  · 存储从 SQLite 换成单文件 JSON `D:\\AI\\pl\\soul_notes.json`（不建库、不复制消息）。

用法（在 python -c 里调函数，避免命令行中文参数编码坑）：
  import soul_db as db
  db.upsert("昵称", source="match", match_pct=95, tags="a、b", notes="...")
  db.get("昵称")            # 查某人档案（对话请查 soul_im.py chat）
  db.set_status("昵称", "stopped", "两条没回,放弃")
  db.bump("昵称", 2)        # 本轮已发条数 +2
  db.list_friends()         # 全部 / db.list_friends("active")
  db.status_map()           # {昵称: status} —— 过滤待回清单用

状态: active=发了等回 | chatting=对话中 | promising=可推进 | stopped=放弃 | skipped=目标不符 | gift=需付费
"""
import json, os, sys, io
from datetime import datetime

try:  # 2026-09-30 双实例：关系档案必须按账号分开（否则账号2 会带着主号的关系档案去聊）
    from soul_instance import state_path as _sp
    NOTES = _sp(r"E:\soul", "soul_notes.json")
except Exception:
    NOTES = r"E:\soul\soul_notes.json"
VALID = ("source", "match_pct", "planet", "zodiac", "mbti", "tags", "common",
         "distance_km", "status", "sent_count", "notes", "updated_at",
         "stage", "stage_at")   # stage = 亲密度阶梯 L0~L4（2026-09-26 三天目标用）

# ⭐ 亲密度阶梯（2026-09-26 用户定："别在一个话题上尬聊，往自己节奏上带，三天内实现目的"）
#   每轮**最多推进一级**；她不接 → 退回一级，不要硬顶。
STAGES = {
    0: "L0 破冰｜她说过的具体细节 + 我一句状态（只描述不评价）",
    1: "L1 共鸣｜找共同处境（都累/都一个人/都熬夜），把『我』摆进去",
    2: "L2 私人化｜生活细节互换：吃啥、住哪、几点睡、家里几口人",
    3: "L3 轻暧昧｜带称呼『你呀』、夸具体行为不夸外貌、开两人懂的玩笑、埋『以后一起』钩子",
    4: "L4 邀约｜给具体时间地点 + 低压力（被拒退回 L3，不追问）",
}


def set_stage(nickname, stage, note=None):
    """设置亲密度阶梯 L0~L4"""
    d = _load()
    rec = d.get(nickname, {})
    rec["stage"] = int(stage)
    rec["stage_at"] = _now()
    if note:
        rec["notes"] = note
    rec["updated_at"] = _now()
    d[nickname] = rec
    _save(d)


def stage_map():
    """{昵称: stage}"""
    return {k: v.get("stage") for k, v in _load().items() if v.get("stage") is not None}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _load():
    if not os.path.exists(NOTES):
        return {}
    try:
        with open(NOTES, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    tmp = NOTES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, NOTES)


def upsert(nickname, **kw):
    """新增/更新档案；tags 传 list 会自动拼接"""
    if kw.get("tags") and isinstance(kw["tags"], (list, tuple)):
        kw["tags"] = "、".join(kw["tags"])
    kw["updated_at"] = _now()
    d = _load()
    rec = d.get(nickname, {})
    rec.update({k: v for k, v in kw.items() if k in VALID and v is not None})
    rec.setdefault("status", "active")
    rec.setdefault("sent_count", 0)
    d[nickname] = rec
    _save(d)


def log(nickname, who, content):
    """已废弃：消息一律以 Soul 原库为准，本地不再记一份（保留函数仅为兼容旧调用）"""
    return


def bump(nickname, n=1):
    d = _load()
    rec = d.get(nickname)
    if rec is None:
        rec = {"status": "active", "sent_count": 0}
        d[nickname] = rec
    rec["sent_count"] = int(rec.get("sent_count") or 0) + n
    rec["updated_at"] = _now()
    _save(d)


def set_status(nickname, status, notes=None):
    d = _load()
    rec = d.get(nickname, {})
    rec["status"] = status
    if notes:
        rec["notes"] = notes
    rec["updated_at"] = _now()
    d[nickname] = rec
    _save(d)


def status_map():
    """{昵称: status} —— 待回/续聊清单用它过滤不该打扰的人"""
    return {k: (v.get("status") or "") for k, v in _load().items()}


def get(nickname, max_msg=60):
    """打印某人档案（对话内容请用 `soul_im.py chat 昵称` 直读 Soul 原库）"""
    d = _load()
    rec = d.get(nickname)
    if not rec:
        print(json.dumps({"error": f"未建档 {nickname}"}, ensure_ascii=False))
        return
    out = dict(rec)
    out["nickname"] = nickname
    out["_hint"] = "对话内容用: python soul_im.py chat 昵称（直读 Soul 原库）"
    print(json.dumps(out, ensure_ascii=False, indent=1))


def list_friends(status=None):
    d = _load()
    rows = [(k, v) for k, v in d.items()
            if (status is None or (v.get("status") or "") == status)]
    rows.sort(key=lambda kv: kv[1].get("updated_at") or "", reverse=True)
    for k, v in rows:
        st = v.get("stage")
        print({"nickname": k, "status": v.get("status"),
               "stage": (f"L{st}" if st is not None else "-"),
               "sent": v.get("sent_count"), "updated": v.get("updated_at"),
               "notes": (v.get("notes") or "")[:36]})


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    a = sys.argv[1:] or [None]
    list_friends(a[0])
