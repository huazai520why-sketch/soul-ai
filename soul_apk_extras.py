# -*- coding: utf-8 -*-
"""挖 Soul 里各种聊天页 Activity 支持的 Intent extra key

思路：dex 字符串池里，同一个类的 key 常常挨在一起。
     以已知 key（如 userIdEcpt）为锚，打印它周围的候选字符串。
"""
import zipfile
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

APK = r"D:\MuMuPlayer\vms\MuMuPlayer-15.0-0\private_shared\probe\base.apk"

ANCHORS = [
    "userIdEcpt", "targetUserIdEcpt", "userId", "conversationId",
    "nickname", "fromUserId", "chatType", "sessionId", "content",
    "ConversationActivity", "userHomepage",
]

KEY_LIKE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]{3,32}$')


def strings_near(data, needle, span=700):
    """在 data 里找 needle，返回其前后 span 字节内所有'像 key'的字符串"""
    out = []
    for m in re.finditer(re.escape(needle.encode()), data):
        a = max(0, m.start() - span)
        b = min(len(data), m.end() + span)
        frag = data[a:b]
        for s in re.findall(rb'[ -~]{4,40}', frag):
            t = s.decode('ascii')
            if KEY_LIKE.match(t):
                out.append(t)
    return out


def main():
    z = zipfile.ZipFile(APK)
    dexes = sorted([n for n in z.namelist() if n.endswith('.dex')])
    agg = {}
    for d in dexes:
        data = z.read(d)
        for anc in ANCHORS:
            if anc.encode() not in data:
                continue
            hits = strings_near(data, anc)
            agg.setdefault(anc, []).extend(hits)
        del data

    for anc in ANCHORS:
        if anc not in agg:
            print(f"=== {anc}:  dex 里找不到 ===")
            continue
        cnt = {}
        for t in agg[anc]:
            cnt[t] = cnt.get(t, 0) + 1
        top = sorted(cnt.items(), key=lambda x: -x[1])[:28]
        print(f"=== {anc}  周围候选 key（去重 {len(cnt)} 个，列前 28）===")
        line = "  "
        for t, c in top:
            line += f"{t}({c})  "
            if len(line) > 150:
                print(line)
                line = "  "
        if line.strip():
            print(line)
        print()


if __name__ == "__main__":
    main()
