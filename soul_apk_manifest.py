# -*- coding: utf-8 -*-
"""解析 Soul APK 的 AndroidManifest.xml —— 找出能承载 soul:// 路由的 Activity

关心：
  1) 所有 Activity 全类名（尤其是 RN / Flutter / 会话 / 主页 容器）
  2) 谁注册了 intent-filter + data scheme（能不能用 am start -d "soul://..." 直接拉起）
  3) 这些容器 Activity 支持哪些 extra（决定能不能带 userIdEcpt / 文本内容进去）
"""
import os
import sys
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

APK = r"D:\MuMuPlayer\vms\MuMuPlayer-15.0-0\private_shared\probe\base.apk"
OUT = r"E:\soul\_probe\AndroidManifest.xml"

KEYWORD = [
    "rncontainer", "reactnative", "flutter", "conversation", "chat",
    "trampoline", "homepage", "userhome", "router", "scheme", "deep",
    "webview", "main", "launch",
]


def main():
    if not os.path.exists(APK):
        print("APK 不在：", APK)
        return

    z = zipfile.ZipFile(APK)
    if "AndroidManifest.xml" not in z.namelist():
        print("APK 里没有 AndroidManifest.xml")
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(z.read("AndroidManifest.xml"))
    print("已导出 AndroidManifest.xml ->", OUT, os.path.getsize(OUT), "字节")

    # pyaxmlparser 的 APK() 要吃整个 apk，不能直接喂已解压的 xml
    try:
        from pyaxmlparser import APK as AXML
    except Exception as e:
        print("pyaxmlparser 不可用:", e)
        return

    try:
        a = AXML(APK)
    except Exception as e:
        print("解析 APK 失败:", e)
        return
    acts = a.get_activities() or []
    print("\n=== Activity 总数 %d ===" % len(acts))

    # 1) 带 intent-filter 的（能被 am start 拉起的）
    print("\n=== ① 重点候选 Activity（类名命中关键词）===")
    hits = [x for x in acts if any(k in (x or "").lower() for k in KEYWORD)]
    for x in sorted(hits)[:60]:
        print("   ", x)

    # 2) 谁注册了 data scheme
    print("\n=== ② 注册了 URL scheme 的 Activity ===")
    try:
        import re
        raw = open(OUT, "rb").read()
        # AXML 里字符串池是 UTF-16LE，粗筛 scheme 名
        for kw in (b"soul", b"soulapp", b"http", b"ul.soulapp.cn"):
            n = raw.count(kw)
            print(f"   manifest 中 {kw.decode()!r} 出现 {n} 次")
    except Exception as e:
        print("   粗筛失败:", e)

    # 3) 输出完整 activity 列表到文件，供人工/后续脚本检索
    lst = os.path.join(os.path.dirname(OUT), "activities.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for x in sorted(acts):
            f.write(x + "\n")
    print("\n完整 Activity 列表 ->", lst)

    # 4) 找 intent-filter 定义（pyaxmlparser 的 xml 形式）
    try:
        xml = a.get_android_manifest_axml().get_xml().decode("utf-8", "ignore") \
            if hasattr(a, "get_android_manifest_axml") else None
        if not xml:
            xml = a.xml  # 兜底
        if xml:
            with open(os.path.join(os.path.dirname(OUT), "manifest_decoded.xml"),
                      "w", encoding="utf-8") as f:
                f.write(xml)
            print("已导出可读 manifest -> _probe/manifest_decoded.xml")
    except Exception as e:
        print("导出可读 manifest 失败:", e)


if __name__ == "__main__":
    main()
