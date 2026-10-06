# -*- coding: utf-8 -*-
"""soul_env.py — 回复用的「当前环境」素材（时间 / 季节 / 天气）

用户 2026-10-06 定稿的回复优先级（从高到低）：
    ① 爽      —— 怎么回能让她读起来爽、想接
    ② 我的目的 —— 推进关系（让她多投入/主动/靠近）
    ③ 当下感  —— 结合当前时间/环境/天气（**次要背景**，是佐料不是主菜）

本模块只负责第 ③ 层的**事实素材**：给出一句话的「现在是什么时候、什么天」，
供提示词引用，让回复有「此时此地」的味道，而不是模板车轱辘话。

设计原则
--------
  · 时间 / 星期 / 时段 / 季节：纯本地系统时钟 → **永远可用**，零依赖。
  · 天气：open-meteo 免费接口（无需 key）→ **带缓存 + fail-open**：
      - 成功：缓存 30 分钟；失败：退避 5 分钟再试。
      - 拿不到就只给时间，**绝不因为网络问题让回复链路报错或明显变慢**
        （超时 4s，且绝大多数时候命中缓存，不产生网络调用）。
  · 城市来自 soul_persona（账号1 重庆 / 账号2 沈阳），读不到就按重庆。
  · 只 import 标准库 + soul_persona（且 try 保护），不 import 任何业务模块，避免循环依赖。
"""
import json
import time
import urllib.request
from datetime import datetime

# ── 地理位置（用户 2026-10-06 定稿：**两个账号都在重庆市綦江区**）────
#   注意：这是**真实定位/环境**（用于【现在】的时间与天气），
#   与 soul_persona 里「人设自称的城市」是两回事 —— 账号2 人设仍自称沈阳（防平台关联）。
LOC_NAME = "重庆綦江"
LOC_LAT, LOC_LON = 29.0284, 106.6514

# ── 天气缓存 ───────────────────────────────────────────────────────
_WX = {"txt": "", "ts": 0.0, "fail": 0.0}
_WX_TTL = 1800.0        # 成功缓存 30 分钟
_WX_FAIL_TTL = 300.0    # 失败后 5 分钟内不再重试（退避）

# WMO weather_code → 中文
_WCODE = {
    0: "晴", 1: "大致晴", 2: "多云", 3: "阴",
    45: "有雾", 48: "冻雾",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨", 99: "雷阵雨",
}


def _city():
    """当前定位（两个账号都在重庆綦江，与实例号无关）。"""
    return LOC_NAME


def _weather(city):
    """一句话天气（如「阴 18℃」）；拿不到返回 ''。带缓存 + 退避。"""
    now = time.time()
    if _WX["txt"] and now - _WX["ts"] < _WX_TTL:
        return _WX["txt"]
    if now - _WX["fail"] < _WX_FAIL_TTL:
        return ""                      # 刚失败过 → 退避，不重试
    lat, lon = LOC_LAT, LOC_LON
    url = ("https://api.open-meteo.com/v1/forecast"
           "?latitude=%.4f&longitude=%.4f"
           "&current=temperature_2m,weather_code&timezone=Asia%%2FShanghai"
           % (lat, lon))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "soul-env/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            d = json.loads(r.read().decode("utf-8"))
        c = d.get("current") or {}
        bits = []
        desc = _WCODE.get(int(c.get("weather_code", -1)))
        if desc:
            bits.append(desc)
        t = c.get("temperature_2m")
        if isinstance(t, (int, float)):
            bits.append("%.0f℃" % t)
        txt = " ".join(bits)
        if txt:
            _WX.update(txt=txt, ts=now)
        return txt
    except Exception:
        _WX["fail"] = now
        return ""


def _part(h):
    """时段（口语）。"""
    if h < 5:
        return "凌晨"
    if h < 8:
        return "清早"
    if h < 11:
        return "上午"
    if h < 13:
        return "中午"
    if h < 17:
        return "下午"
    if h < 19:
        return "傍晚"
    if h < 23:
        return "晚上"
    return "深夜"


def _season(m):
    if 3 <= m <= 5:
        return "春"
    if 6 <= m <= 8:
        return "夏"
    if 9 <= m <= 11:
        return "秋"
    return "冬"


def now_bg():
    """一句话「当前环境」：`【现在】10月6日 周二 凌晨2点 · 秋季 · 重庆 阴 18℃`

    异常时至少给出时间；再不行返回 ''（调用方照常走，不影响回复）。
    """
    try:
        n = datetime.now()
        wd = "周" + "一二三四五六日"[n.weekday()]
        when = "%d月%d日 %s %s%d点" % (n.month, n.day, wd, _part(n.hour), n.hour)
        city = _city()
        wx = _weather(city)
        tail = "%s季 · %s" % (_season(n.month), ("%s %s" % (city, wx)).strip())
        return "【现在】%s · %s" % (when, tail)
    except Exception:
        try:
            return "【现在】%s" % datetime.now().strftime("%m月%d日 %H点")
        except Exception:
            return ""


if __name__ == "__main__":
    try:
        import io
        import sys
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    print("城市    =", _city())
    print("天气    =", repr(_weather(_city())))
    print("now_bg  =", now_bg())
