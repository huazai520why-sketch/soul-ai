# -*- coding: utf-8 -*-
"""
soul_probe_db.py —— 探测 Soul App 其他数据库里有什么（个人主页 / 动态 / 关系链）

独立通道：
  - 设备端中转 /mnt/shared/private_shared/probe  （不与 watcher 的 dbsync_* 冲突）
  - 落盘本地 D:\\AI\\pl\\_probe\\              （不写正式库目录，不打扰主流程）

用法：
  python soul_probe_db.py              # 跑一遍：拉取 + 全表清点 + 预览疑似有用表
  python soul_probe_db.py --tables     # 只列已知本地 _probe 里的表
  python soul_probe_db.py --dump 表名  # 打印某表全部行的前若干列
"""
import os
import sys
import sqlite3
import shutil
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soul  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIR = r"E:\soul"
OUT_WIN = os.path.join(DIR, "_probe")
SHARED_WIN = os.path.join(soul.MUMU_ROOT, "vms", "MuMuPlayer-15.0-0", "private_shared", "probe")
SHARED_AND = "/mnt/shared/private_shared/probe"
DBDIR = "/data/data/cn.soulapp.android/databases"

# 想审的库（设备名 -> 本地名）
TARGETS = [
    ("soul_app.db", "soul_app.db"),
    ("chat_im_user.db", "chat_im_user.db"),
]

# 觉得可能有价值的关键词（表名 / 列名命中就重点打印）
INTEREST = [
    "post", "moment", "feed", "dynamic", "square", "discover", "topic",
    "user", "profile", "relation", "friend", "follow", "fans", "star",
    "greet", "match", "card", "moment", "comment", "like", "visit",
]


def fetch( pairs, timeout=120):
    """设备 -> 共享目录 -> 本地 _probe"""
    os.makedirs(OUT_WIN, exist_ok=True)
    soul.sh(f"mkdir -p {SHARED_AND} && rm -f {SHARED_AND}/*")
    os.makedirs(SHARED_WIN, exist_ok=True)
    for f in os.listdir(SHARED_WIN):
        try:
            os.remove(os.path.join(SHARED_WIN, f))
        except Exception:
            pass

    # 设备上实际存在哪些文件
    exist = soul.sh(f"ls -1 {DBDIR} 2>/dev/null").replace("\r", "")
    present = set(exist.split())
    if not present:
        print("❌ 读不到设备库目录（MuMu 没起来？）")
        return 0

    cmds = []
    for src, dst in pairs:
        for suf in ("", "-wal", "-shm"):
            if f"{src}{suf}" in present:
                cmds.append(f"cp -f {DBDIR}/{src}{suf} {SHARED_AND}/{dst}{suf} 2>/dev/null")
    if not cmds:
        print("❌ 目标库在设备上都不存在")
        return 0
    soul.sh(";".join(cmds) + f"; chmod 666 {SHARED_AND}/* 2>/dev/null")

    got = 0
    for src, dst in pairs:
        for suf in ("", "-wal", "-shm"):
            if f"{src}{suf}" not in present:
                continue
            sp = os.path.join(SHARED_WIN, dst + suf)
            tp = os.path.join(OUT_WIN, dst + suf)
            if not os.path.exists(sp):
                continue
            prev = -1
            for _ in range(10):
                try:
                    cur = os.path.getsize(sp)
                except OSError:
                    cur = -1
                if cur > 0 and cur == prev:
                    break
                prev = cur
                time.sleep(0.3)
            try:
                shutil.copy(sp, tp)
                if os.path.getsize(tp) > 0:
                    got += 1
            except OSError as e:
                print(f"  [warn] 拷入失败 {dst}{suf}: {e!r}")
    return got


def tables_of(path):
    """返回 [(表名, 行数, [列名...])]"""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception as e:
        return None, f"打不开: {e!r}"
    cur = c.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
    except sqlite3.DatabaseError as e:
        return None, f"读表失败: {e!r}"
    out = []
    for n in names:
        try:
            cnt = cur.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{n}")').fetchall()]
        except Exception:
            cnt, cols = -1, []
        out.append((n, cnt, cols))
    c.close()
    return out, None


def score(name, cols):
    """这张表看起来跟"人 / 动态 / 关系"有多大关系"""
    s = 0
    low = name.lower()
    joined = ",".join(cols).lower()
    for k in INTEREST:
        if k in low:
            s += 3
        if k in joined:
            s += 1
    return s


def main():
    args = sys.argv[1:]
    if "--dump" in args:
        i = args.index("--dump")
        tbl = args[i + 1]
        path = os.path.join(OUT_WIN, args[i + 2] if len(args) > i + 2 else "soul_app.db")
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = c.cursor()
        cur.execute(f'SELECT * FROM "{tbl}" LIMIT 50')
        cols = [d[0] for d in cur.description]
        print(" | ".join(cols))
        for row in cur.fetchall():
            line = " | ".join(str(v)[:60] if v is not None else "" for v in row)
            print(line)
        return

    if "--tables" not in args:
        n = fetch(TARGETS)
        print(f"✅ 已拉取 {n} 个文件到 {OUT_WIN}")

    print()
    for src, dst in TARGETS:
        path = os.path.join(OUT_WIN, dst)
        if not os.path.exists(path):
            print(f"--- {dst}：本地不存在 ---")
            continue
        ts, err = tables_of(path)
        size = os.path.getsize(path)
        print("=" * 72)
        print(f"{dst}  （{size} 字节）")
        if err:
            print("   ", err)
            continue
        print(f"   表 {len(ts)} 个")
        print("-" * 72)
        scored = sorted(((score(n, cols), n, cnt, cols) for n, cnt, cols in ts),
                        key=lambda x: -x[0])
        for sc, n, cnt, cols in scored:
            mark = "⭐" if sc >= 3 else ("·" if sc > 0 else " ")
            print(f"  {mark} {n:<34} {cnt:>6} 行   " + (",".join(cols)[:90] if cols else ""))
        print()


if __name__ == "__main__":
    main()
