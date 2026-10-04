# -*- coding: utf-8 -*-
"""UI 勘察：列出当前页面所有「可点击」元素（坐标 + resource-id + 文本），
用来判断哪些能点、哪些不能点（付费页/危险操作）。

用法:
  python soul_ui.py        # 只列可点击元素
  python soul_ui.py all    # 列出全部元素（含不可点击的文本，便于了解页面构成）
"""
import re, sys, io

sys.path.insert(0, r"E:\soul")
import soul_read as rd


def parse(xml, only_clickable=True):
    out = []
    for m in re.finditer(r"<node[^>]*>", xml):
        tag = m.group(0)

        def get(k):
            mm = re.search(k + r'="([^"]*)"', tag)
            return mm.group(1) if mm else ""

        cls = get("class")
        rid = get("resource-id")
        txt = get("text")
        desc = get("content-desc")
        click = get("clickable")
        bounds = get("bounds")
        if only_clickable and click != "true":
            continue
        if not (rid or txt or desc or cls):
            continue
        nums = list(map(int, re.findall(r"\d+", bounds)))
        if len(nums) == 4:
            cx, cy = (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2
        else:
            cx = cy = 0
        short = rid.split("/")[-1] if rid else cls.split(".")[-1]
        out.append((cy, cx, short, (txt or desc)[:44], click == "true"))
    return sorted(out)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    only_click = not (len(sys.argv) > 1 and sys.argv[1] == "all")
    x = rd.dump_xml()
    for cy, cx, rid, txt, ck in parse(x, only_click):
        mark = "点" if ck else "  "
        print(f"{mark} y={cy:<5} x={cx:<5} [{rid[:34]:<34}] {txt}")
