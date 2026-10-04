# -*- coding: utf-8 -*-
"""读屏（MuMu 版，2026-09-28）：**窗口截图 + OCR** 替代 uiautomator dump

迁移原因：
  MuMu 精简镜像**没有 x86_64 的 libnativeloader.so**（只有 arm64 子目录里的，
  是给 houdini 翻译层用的，ABI 不匹配不能混用）→ `uiautomator dump` 直接
  `CANNOT LINK EXECUTABLE "app_process"` 报错，UI 树路线在 MuMu 上不可用。

新方案：
  1. `soul.screenshot()` 截 MuMu 渲染窗口（Soul 在独立 display，screencap 拿不到）
  2. RapidOCR 识别文字 → 返回 (文本, 中心x, 中心y)
  3. 坐标已换算回**模拟器 720x1280 真实分辨率**，点击可直接用

⚠️ 精度差异（务必知悉）：
  - OCR 有识别错误率（尤其艺术字/小字），**不要**用它做精确文本匹配；
    读消息/未读请一律走 **数据库**（soul_im.py），那才是 100% 准确且更快的路子。
  - 本模块只用于「界面上找某个按钮/会话在什么位置」这类**定位**用途。

⚠️ 运行环境：必须用装了 rapidocr 的解释器：
    C:\\Users\\JIANG\\.workbuddy\\binaries\\python\\envs\\soulocr\\Scripts\\python.exe
用法：
  python soul_read.py            # 打印当前界面所有文字（带 y,x 坐标，按 y 排序）
  python soul_read.py grep 昵称   # 只打印含关键字的部分
"""
import sys, io, os, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# 截图尺寸 → 模拟器坐标空间
# ⚠️ 2026-09-30 修（用户当场纠偏"没确定页面就瞎操作"的根因）：
#   · DEV 仍取 **720x1280** —— 实测硬编码点击常量（输入框 410,1203 / 发送 635,1214）
#     至今有效，说明 `mumu-cli input -d N tap` 用的是这套 720x1280 空间。
#   · 但**截图尺寸不能写死**：代码按 562x1000 换算，而 MuMu 15 窗口实际截出 **533x948**
#     → OCR 坐标被系统性算小 ~5.5%，越靠屏幕下方偏得越多（底部偏 60~70px）：
#     底部导航「聊天」会被偏到信息流的「私聊/抢首评」行上 → 点一下进了别人主页。
#     表现极像"页面自己跳了"，其实是**坐标换算错**。
SHOT_W, SHOT_H = 562, 1000          # 兜底值（读不到真实 PNG 尺寸时才用）
# ⚠️⚠️ 2026-09-30 **实测标定**：`mumu-cli input -d N tap` 用的是**真实屏尺寸**（实测 900x1600），
#   不是历史沿用的 720x1280。证据：按 720 空间点底导航「聊天」(492,1268) → 进了别人主页；
#   按 900 空间点 (615,1585) → `在聊天列表页 = True`。
#   ⇒ 凡是按 720x1280 算出来的坐标，全部偏小 1.25 倍（左上方各偏 20%），屏幕越往下偏得越多
#     → 底部导航会落到信息流的「私聊/抢首评」行上（误触真人主页）。
# ⭐ 2026-09-30 二次修：**不再写死**，运行时用 `wm size` 实测（见 dev_size()），
#   这样换分辨率/换机型不会再静默错位。
DEV_W, DEV_H = 900, 1600

_DEV_SIZE = None


def dev_size(refresh=False):
    """当前实例的真实屏幕尺寸（一次 `wm size`，进程内缓存）。

    拿不到时不许瞎猜 —— 返回兜底 900x1600 并在 stderr 之外静默（调用方只关心能用）。
    """
    global _DEV_SIZE, DEV_W, DEV_H
    if _DEV_SIZE and not refresh:
        return _DEV_SIZE
    try:
        import re as _re
        import soul as _s
        out = _s.sh("wm size", timeout=10)
        m = _re.search(r"(\d{3,5})\s*[xX]\s*(\d{3,5})", out)
        if m:
            DEV_W, DEV_H = int(m.group(1)), int(m.group(2))
            _DEV_SIZE = (DEV_W, DEV_H)
            return _DEV_SIZE
    except Exception:
        pass
    return (DEV_W, DEV_H)


def _shot_size():
    """真实截图尺寸（首次调用时从 PNG 读一次并缓存到模块级 SHOT_W/H）。"""
    global SHOT_W, SHOT_H
    try:
        import soul as _s
        w, h = _png_size(_s.SHOT)
        if w and h:
            SHOT_W, SHOT_H = int(w), int(h)
    except Exception:
        pass
    return SHOT_W, SHOT_H

_OCR = None          # PP-OCRv5 引擎（rapidocr 3.x）

# ⭐ 2026-10-04（用户：「把 v3 删除了 就用 v5」）：只保留 PP-OCRv5 一条路。
#   旧的 v3 识别包已从环境卸载，v3 引擎代码与切换开关一并移除。
#   v5 出错时由 items() 层的 try/except 打印错误并返回空列表（fail-closed，绝不猜位置）。
#   实测（同一张 533x948 真实界面图）：v5 稳态 ~1.7s / 46 条。

# ⭐ 2026-09-30 提速：OCR 结果缓存（同一张 PNG 不重复识别）。
#   缓存键 = (路径, mtime, size)，画面一变就失效。关：`SOUL_OCR_CACHE=0`
_OCR_CACHE_ON = os.environ.get("SOUL_OCR_CACHE", "1") != "0"
_OCR_CACHE = {"key": None, "res": None}

# ⭐ 同进程「极短窗口」整体缓存：连续两次 items() 之间若没有任何改界面的动作
#   （tap/swipe/type_text 等都会 soul.invalidate_shot()），直接复用上一次结果。
#   窗口：SOUL_OCR_WINDOW（默认 0.30s，0 = 关）
_OCR_WINDOW = float(os.environ.get("SOUL_OCR_WINDOW", "0.30"))
_OCR_LAST = {"ts": 0.0, "items": None}


def _build_v5():
    """PP-OCRv5 mobile（检测 + 识别）。"""
    from rapidocr import RapidOCR as _R, OCRVersion, ModelType
    return _R(params={"Det.ocr_version": OCRVersion.PPOCRV5,
                      "Det.model_type": ModelType.MOBILE,
                      "Rec.ocr_version": OCRVersion.PPOCRV5,
                      "Rec.model_type": ModelType.MOBILE})


def _norm_ocr(out):
    """v5 返回结构 → [(四点框, 文本, 置信度)]，让 items() 及所有调用方零改动。

    v5(rapidocr 3.x) 结果对象含 .boxes(Nx4x2) / .txts / .scores。
    """
    if out is None:
        return []
    boxes = getattr(out, "boxes", None)
    if boxes is None:
        return []
    txts = list(getattr(out, "txts", None) or ())
    scs = list(getattr(out, "scores", None) or ())
    rows = []
    for i, box in enumerate(boxes):
        b = [[float(p[0]), float(p[1])] for p in box]
        rows.append([b, txts[i] if i < len(txts) else "",
                     scs[i] if i < len(scs) else 1.0])
    return rows


def _ocr_cache_key(path):
    try:
        st = os.stat(path)
        return (path, st.st_mtime, st.st_size)
    except Exception:
        return None


def _ocr():
    """懒加载 PP-OCRv5（首次约 1~3s，之后常驻）"""
    global _OCR
    if _OCR is None:
        _OCR = _build_v5()
    return _OCR


def _run_ocr(path):
    """识别 PNG → [(框, 文本, 置信度)]（带同帧缓存）"""
    if not _OCR_CACHE_ON:
        return _norm_ocr(_ocr()(path))
    key = _ocr_cache_key(path)
    if key and _OCR_CACHE["key"] == key:
        return _OCR_CACHE["res"]
    res = _norm_ocr(_ocr()(path))
    _OCR_CACHE["key"] = key
    _OCR_CACHE["res"] = res
    return res


def shot_to_dev(x, y, shot_w=None, shot_h=None):
    """截图坐标 → 模拟器坐标（**两侧都用实测尺寸**：截图取 PNG 实际宽高、设备取 `wm size**）"""
    dw, dh = dev_size()
    sw, sh = _shot_size()
    sw = shot_w or sw
    sh = shot_h or sh
    return int(round(x * dw / sw)), int(round(y * dh / sh))


def _png_size(path):
    """读 PNG 宽高（纯标准库，避免为这个再拉 Pillow）"""
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def items(xml=None, min_score=0.5, refresh=True):
    """[(文本, 中心x, 中心y)] —— 坐标是模拟器真实 720x1280 分辨率，按 y 排序。

    参数 xml 仅为兼容旧签名（旧版传 XML 路径），这里忽略。
    """
    import soul
    p = soul.screenshot() if refresh else (soul.SHOT or os.path.join(BASE, "wshot.png"))

    # ⭐ 提速短路：距上次识别极近、且期间没人作废缓存 → 直接复用上次结果
    if _OCR_WINDOW > 0 and _OCR_LAST["items"] is not None \
            and (time.time() - _OCR_LAST["ts"]) < _OCR_WINDOW \
            and soul._SHOT_CACHE.get("ts", 0.0) > _OCR_LAST["ts"] - _OCR_WINDOW:
        return _OCR_LAST["items"]

    if not p or not os.path.exists(p):
        print("!! soul_read: 截图失败，无法读屏")
        return []
    w, h = _png_size(p)
    try:
        res = _run_ocr(p)
    except Exception as e:
        print(f"!! soul_read: OCR 失败 {e!r}")
        return []
    out = []
    for box, text, score in (res or []):
        # ⚠️ rapidocr 的 score 是**字符串**（如 "0.7001"），直接比 float 会 TypeError
        try:
            sc = float(score)
        except (TypeError, ValueError):
            sc = 1.0
        if sc < min_score:
            continue
        t = (text or "").strip()
        if not t:
            continue
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        cx, cy = shot_to_dev(sum(xs) / 4.0, sum(ys) / 4.0, w, h)
        out.append((t, cx, cy))
    out = sorted(out, key=lambda z: z[2])
    _OCR_LAST["ts"] = time.time()
    _OCR_LAST["items"] = out
    return out


def find(kw, ns=None):
    """按包含关系找第一个命中的文字项 → (文本, x, y)；找不到返回 None"""
    for t, cx, cy in (ns if ns is not None else items()):
        if kw in t:
            return (t, cx, cy)
    return None


def find_all(kw, ns=None):
    return [(t, cx, cy) for t, cx, cy in (ns if ns is not None else items()) if kw in t]


def show(keyword=None, ns=None):
    for t, cx, cy in (ns if ns is not None else items()):
        if keyword and keyword not in t:
            continue
        print(f"y={cy:<5} x={cx:<5} {t}")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    kw = sys.argv[2] if len(sys.argv) > 2 else None
    show(kw)
