# -*- coding: utf-8 -*-
"""临时验证：OCR 线程上限是否生效、耗时是否可接受。用完可删。"""
import os
import sys
import time

sys.path.insert(0, r"E:\soul")
os.environ.setdefault("SOUL_OCR_THREADS", "4")

import soul_read  # noqa: E402

print("SOUL_OCR_THREADS =", soul_read._OCR_THREADS)

t = time.time()
eng = soul_read._build_v5()
print("build ok  %.2fs" % (time.time() - t))
print("engine    %r" % type(eng).__name__)

img = r"E:\soul\wsnot.png"
if not os.path.exists(img):
    cands = [os.path.join(r"E:\soul", f) for f in os.listdir(r"E:\soul") if f.endswith(".png")]
    img = cands[0] if cands else None

if img:
    print("image     %s" % img)
    for i in range(3):
        t = time.time()
        try:
            items = soul_read.items(img)
            dt = time.time() - t
            print("OCR run %d: %.2fs, %d items" % (i + 1, dt, len(items or [])))
        except Exception as e:
            print("OCR run %d FAILED: %r" % (i + 1, e))
            break
else:
    print("no test image found, build check only")
