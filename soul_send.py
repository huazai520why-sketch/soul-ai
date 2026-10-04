# -*- coding: utf-8 -*-
"""发消息：python soul_send.py "文本内容" [--nosend]

⭐ MuMu 版（2026-09-28）
  底层从 adb + ADBKeyboard 换成 mumu-cli：
    - 输入：`mumu-cli control tool cmd -c input_text -t "中文"`（**原生支持中文**，已验证）
    - 点击：`mumu-cli sh -c "input -d <display> tap x y"`
  验证方式从「看界面(ui dump)」换成「**查数据库**」——
    MuMu 精简镜像没有 libnativeloader.so，uiautomator 不可用；
    而且查库本来就是更硬的证据：消息真的落进 IM 库才算发出去。

沿用两道保险（2026-09-26 并发事故后）：
  ① 跨进程锁 soul_lock.hold("send") —— 抢不到锁就不发，绝不与其它实例抢同一个输入框；
  ② 校验失败重输前先清空输入框（旧版直接重输 → 文本重复两遍）。

⭐ 2026-09-28 长度硬闸（用户当面指出：聊天时不要长篇大论）
  技能里早有「每条 ≤20 字居多」「超过 20 字的句子禁用」，但**只在文档里**，
  脚本无校验 → 实际发出去 34~55 字的"小作文"。规则不落到代码 = 没有规则。
"""
import sys, time, os, re, sqlite3
import soul
import soul_read as rd
from soul_lock import hold

LOCK_WAIT = 8.0

MAXLEN = int(os.environ.get("SOUL_MAXLEN", "40"))

_EMOJI = re.compile(r"\[[^\[\]]{1,10}\]")


def vis_len(text):
    """可见字数：剔除 [笑] 这类表情标记 + 所有空白（用户看到的是这个长度）"""
    s = _EMOJI.sub("", str(text))
    return len(re.sub(r"\s+", "", s))


def check_len(text, maxlen=None):
    """单条长度闸：超上限 → False（调用方**不得发送**）；>20 字仅告警"""
    M = MAXLEN if maxlen is None else int(maxlen)
    n = vis_len(text)
    if n > M:
        print(f"⛔ 长度硬闸：本条 {n} 字 > 上限 {M} 字 —— 聊天不是小作文，"
              f"拆成 2~3 条短句再发。原句：{text}")
        return False
    if n > 20:
        print(f"[warn] 本条 {n} 字，偏长（用户要求：每条 ≤20 字居多）")
    return True


# ⭐ 2026-10-05 修复：以下曾是 900x1600 写死的绝对像素 —— MuMu 重启后屏变成
#   540x960，y=1511/1316 **全部点在屏外**，导致"语音→文字"激活与录音取消全部失效、
#   发送链路静默失败（实测：进入会话页后每轮发送 FAIL）。统一改为比例缩放。
LEFT_ICON = (62, 1511)          # 输入框左侧「语音/文字」模式切换键（6.38.5 + 900x1600 重标定）
CANCEL_REC = (165, 1316)        # 录音界面「取消」按钮
_BOTTOM_Y_1600 = 1050           # 900x1600 下"底部(输入框一带)"的 OCR y 阈值


def _sc(x, y):
    """900x1600 旧绝对坐标 → 当前分辨率（等比缩放，540x960 下自动正确）"""
    return (int(round(x * soul.DEV_W / 900)), int(round(y * soul.DEV_H / 1600)))


def _sc_y(y):
    return int(round(y * soul.DEV_H / 1600))


def _bottom_texts():
    """读当前屏底部（输入框一带）的 OCR 文本"""
    try:
        soul.screenshot()
        return [(t or "").strip() for t, _, cy in (rd.items() or []) if cy > _sc_y(_BOTTOM_Y_1600)]
    except Exception:
        return []


def _is_recording(low):
    """录音界面特征：有「取消」且伴随录音动作词"""
    j = " ".join(low)
    return ("取消" in j) and any(k in j for k in ("暂停录音", "转文字", "发语音"))


def _mode(low):
    """输入框当前模式：rec(录音中) / text(文字) / voice(语音) / unknown

    ⚠️ 判据顺序很重要：文字模式的提示是「发消息或按住说话」，**也含"按住说话"**。
       所以必须先查"发送/发消息"（文字），再查"录音/按住说话"（语音），否则文字模式被误判成语音。
    """
    j = " ".join(low)
    if _is_recording(low):
        return "rec"
    if "发送" in j or "发消息" in j:
        return "text"
    if "录音" in j or "按住说话" in j:
        return "voice"
    return "unknown"


def _to_voice_mode(max_try=5):
    """把输入框调到**语音模式**（出现「录音」按钮）。文字模式则点切换键；录音中先取消。"""
    for _ in range(max_try):
        low = _bottom_texts()
        m = _mode(low)
        if m == "rec":
            soul.tap(*_sc(*CANCEL_REC))
            time.sleep(1.5)
            continue
        if m == "voice":
            return True
        soul.tap(*_sc(*LEFT_ICON))
        time.sleep(1.6)
    return False


def _input(box_xy, text):
    """点输入框 → 清空 → 灌字（MuMu 官方 input_text，原生中文，无需第三方输入法）

    ⚠️ 2026-09-30（Soul 6.38.5）**实测出的正确仪式** —— 前面几版都翻过车：
       · 旧版「点→清→再点→type_text」：新版焦点被点散，字进不去（静默丢失）。
       · 「只点一次框+灌字」：新会话输入框处于未激活态，同样进不去。
       · 「按 OCR 判模式再决定切几次」：**截图有旧帧滞后**，判断错方向 → 越切越歪，
         还会误触**录音界面**（语音模式下点输入框 = 按住说话 → 开始录音）。
       可靠路径（**结果驱动 + 自愈重试**，用户当场指点的「点那个键盘小图标」）：
         ① 先确保输入框在**语音模式**（`_to_voice_mode`，录音中先取消）；
         ② 点一次左侧**键盘小图标** → **语音→文字**，这一下才真正激活输入框（实测关键）；
         ③ 直接 clear_text + 灌字（**不要再点输入框** —— 点框在语音态会触发录音）；
         ④ **验证底部是否出现目标文字**；不成则重试并清理录音态。
       发送按钮同步为 soul.SEND_XY=(635,1214)（新版右移，且**只在有文字时才出现**）。
    """
    key = re.sub(r"\s+", "", text)[:5]
    for attempt in range(3):
        _to_voice_mode()
        soul.tap(*_sc(*LEFT_ICON))      # 语音 → 文字（关键：这一下才激活）
        time.sleep(1.6)
        soul.clear_text()
        time.sleep(0.4)
        soul.type_text(text, box_xy, click=False)
        time.sleep(1.3)
        low = _bottom_texts()
        j = " ".join(re.sub(r"\s+", "", t) for t in low)
        if key and key in j:
            return True                      # 字确实进去了
        print(f"[warn] 第{attempt+1}次灌字未生效（底部={low}）→ 重试")
        if _is_recording(low):
            soul.tap(*_sc(*CANCEL_REC))
            time.sleep(1.5)
    return False


def my_last_text():
    """读本地 IM 库中「我」最后发出的一条消息文本（用于发送校验）"""
    try:
        import soul_im as I
        c = sqlite3.connect(I.IMDB)
        row = c.execute(
            "SELECT text FROM chatmsg WHERE senderId=? AND text IS NOT NULL "
            "ORDER BY localTime DESC LIMIT 1", (str(I.ME),)).fetchone()
        c.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[warn] 校验读库异常: {e!r}")
        return None


def verify_sent(text, timeout=16):
    """⭐ 数据库校验：重新拉库，确认这条消息真的进了 IM 库。

    比看界面硬：界面显示"已输入"不等于发出去了；
    消息落库才是既成事实（对方那条链路才可能收到）。
    """
    import soul_im as I
    key = re.sub(r"\s+", "", text)[:8]
    for i in range(max(1, timeout // 2)):
        I.pull()
        got = my_last_text()
        if got and key and key in re.sub(r"\s+", "", got):
            print(f"✅ 数据库已确认发出（第 {i+1} 次校验）")
            return True
        time.sleep(2)
    print(f"!! 数据库未检出本条消息 —— 最后一条我方消息: {my_last_text()!r} → 视为发送失败")
    return False


def send_msg(text, box_xy=None, verify=True, maxlen=None):
    # ⭐ 长度闸先于抢锁：不合格直接不发（不占锁、不动屏幕）
    if not check_len(text, maxlen):
        return False
    with hold("send", wait=LOCK_WAIT, owner_ok=True) as ok:
        if not ok:
            print("!! 抢不到发送锁（疑似有并发实例在发消息）→ 本次不发，避免串台/重复")
            return False
        box_xy = box_xy or soul.BOX_XY
        print("输入框", box_xy)
        _input(box_xy, text)
        sx, sy = soul.SEND_XY
        print("发送", (sx, sy))
        soul.tap(sx, sy)
        time.sleep(1.5)
        if verify:
            return verify_sent(text)
        return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('用法: python soul_send.py "文本" [--nosend]'); sys.exit(1)
    t = sys.argv[1]
    if "--nosend" in sys.argv:
        print("（--nosend）仅校验长度，不发送")
        print("通过" if check_len(t) else "不通过")
    else:
        r = send_msg(t)
        print(("已发送: " if r else "未发送: ") + t)
