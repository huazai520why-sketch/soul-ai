# -*- coding: utf-8 -*-
"""soul_pregen 预生成话术池单元测试：池读写 / take 精确匹配 / 快照隔离"""
import os
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import soul_pregen as pg


class TestPoolIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pregen_test_")
        self.old_dir = pg.PRGEN_DIR
        pg.PRGEN_DIR = self.tmp

    def tearDown(self):
        pg.PRGEN_DIR = self.old_dir
        for f in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def _mkpool(self):
        pool = {"date": "2026-10-06", "items": [
            {"nick": "欢欢", "her_last": "今天好累哦",
             "cands": ["早点休息撒", "明天还要上班吗"]},
            {"nick": "月亮亮了", "her_last": "你吃了吗",
             "cands": ["吃了，食堂今天有回锅肉"]},
        ]}
        pg._write(pool, "2026-10-06")
        return pool

    def test_write_read(self):
        self._mkpool()
        pool = pg._read("2026-10-06")
        self.assertEqual(len(pool["items"]), 2)
        self.assertEqual(pool["items"][0]["nick"], "欢欢")

    def test_take_exact_match(self):
        self._mkpool()
        cands = pg.take("欢欢", "今天好累哦", date="2026-10-06")
        self.assertEqual(cands, ["早点休息撒", "明天还要上班吗"])

    def test_take_wrong_her_last(self):
        # 她最后一句不同 → 不命中（防止上下文错位）
        self._mkpool()
        self.assertIsNone(pg.take("欢欢", "今天很开心", date="2026-10-06"))

    def test_take_wrong_nick(self):
        self._mkpool()
        self.assertIsNone(pg.take("别人", "今天好累哦", date="2026-10-06"))

    def test_take_used_once(self):
        # 取走后标记 used → 第二次取不到（绝不重复使用）
        self._mkpool()
        self.assertIsNotNone(pg.take("欢欢", "今天好累哦", date="2026-10-06"))
        self.assertIsNone(pg.take("欢欢", "今天好累哦", date="2026-10-06"))

    def test_take_missing_file(self):
        self.assertIsNone(pg.take("欢欢", "今天好累哦", date="2099-01-01"))

    def test_take_empty_cands(self):
        pg._write({"date": "2026-10-06", "items": [
            {"nick": "x", "her_last": "y", "cands": []}]}, "2026-10-06")
        self.assertIsNone(pg.take("x", "y", date="2026-10-06"))

    def test_recent_units(self):
        # 秒与毫秒纪元都识别
        now = time.time()
        self.assertTrue(pg._recent(now - 100, hours=1))
        self.assertTrue(pg._recent((now - 100) * 1000, hours=1))
        self.assertFalse(pg._recent(now - 10 * 3600, hours=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
