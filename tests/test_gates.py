# -*- coding: utf-8 -*-
"""Soul 项目核心逻辑单元测试（零依赖，stdlib unittest）

覆盖：
  soul_daemon.gate       确定性闸（复述她/复述我/违禁词/装富/说教/超长）
  soul_daemon._same      复述判定（2026-10-04 修误判后：实质相同才算复述）
  soul_daemon._is_fake_last  假末条判定（SKIP_LAST）
  soul_im._voice_text    语音转写提取（Soul 自带 word）
  soul_im._is_sys        系统卡片判定（含语音放行）
  soul_im._is_official / _is_official_uid  官方号判定（uid 硬过滤）
  soul_pick.check        八道筛（长度/噪音/教学/擦边/敏感/油腻/PUA/文艺/分析/零信息/去重）
  soul_pick.split_candidates  拆句策略

跑法（E:\\soul 下）：
  C:\\Users\\JIAN\\.workbuddy\\binaries\\python\\envs\\soulocr\\Scripts\\python.exe -m unittest tests.test_gates -v
"""
import sys
import os
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# soul_daemon 导入时会重定向 stdout 到日志文件 —— 先保存，导入后恢复
_real_out, _real_err = sys.stdout, sys.stderr
import soul_daemon as D
sys.stdout, sys.stderr = _real_out, _real_err

import soul_im as im
import soul_pick as pk


class TestGate(unittest.TestCase):
    """确定性闸 gate(raw, incoming, n, my_recent)"""

    def test_normal_kept_and_cap(self):
        raw = "今天天气不错\n下次一起喝汽水撒"
        kept, dropped = D.gate(raw, "你最近忙啥呢", 2)
        self.assertEqual(kept, ["今天天气不错", "下次一起喝汽水撒"])
        self.assertEqual(dropped, [])

    def test_repeats_her_dropped(self):
        # 「你今天天气不错啊」与她原话相似度≥0.85 → 判复述剔除（正确行为）
        kept, dropped = D.gate("今天天气不错\n你今天天气不错啊", "今天天气不错", 2)
        self.assertNotIn("今天天气不错", kept)
        self.assertNotIn("你今天天气不错啊", kept)
        self.assertTrue(any(d[0] == "复述她" for d in dropped))

    def test_repeats_her_but_other_line_kept(self):
        kept, dropped = D.gate("今天天气不错\n今天适合去河边吹风", "今天天气不错", 2)
        self.assertIn("今天适合去河边吹风", kept)
        self.assertTrue(any(d[0] == "复述她" for d in dropped))

    def test_repeats_me_dropped(self):
        kept, dropped = D.gate("我也在加班呢\n在厂里加班", "你下班没", 2,
                               my_recent=["我也在加班呢"])
        self.assertNotIn("我也在加班呢", kept)
        self.assertTrue(any(d[0] == "复述我" for d in dropped))

    def test_ban_word_dropped(self):
        # BAN_WORDS: 代码/脚本/程序/互联网/程序员/算法/服务器/运维
        kept, dropped = D.gate("我平时写点代码玩\n今天厂里不忙", "你平时干嘛", 2)
        self.assertNotIn("我平时写点代码玩", kept)
        self.assertIn("今天厂里不忙", kept)
        self.assertTrue(any(d[0].startswith("违禁词") for d in dropped))

    def test_rich_bragging_dropped(self):
        # BAN_RICH: 开车/接你/我请客/请你吃饭/转账…
        kept, dropped = D.gate("我开车去接你吧\n周末去河边吹风", "你在哪", 2)
        self.assertNotIn("我开车去接你吧", kept)
        self.assertTrue(any(d[0].startswith("装富") for d in dropped))

    def test_preach_dropped(self):
        # PREACH_WORDS: 你应该/你要改/听我的/别老/老是/这样不好…
        kept, dropped = D.gate("你应该早点睡\n我晓得你睡得晚", "睡不着", 2)
        self.assertNotIn("你应该早点睡", kept)
        self.assertIn("我晓得你睡得晚", kept)
        self.assertTrue(any(d[0].startswith("说教") for d in dropped))

    def test_too_long_dropped(self):
        long_line = "我今天早上起来去楼下吃了碗重庆小面还加了两个煎蛋特别安逸"
        self.assertTrue(len(long_line) > 22)
        kept, dropped = D.gate(long_line + "\n今天早饭吃的撒", "吃早饭没", 2)
        self.assertNotIn(long_line, kept)
        self.assertTrue(any(d[0].startswith("超长") for d in dropped))

    def test_empty_lines_skipped(self):
        kept, dropped = D.gate("  \n   \n在忙啥呢", "干嘛呢", 2)
        self.assertEqual(kept, ["在忙啥呢"])

    def test_n_cap(self):
        kept, _ = D.gate("一句\n二句\n三句\n四句", "你好", 2)
        self.assertEqual(len(kept), 2)


class TestSame(unittest.TestCase):
    """复述判定 _same(a, b)"""

    def test_exact_same(self):
        self.assertTrue(D._same("今天加班", "今天加班"))

    def test_short_no_containment(self):
        # 短句（<4字）不做包含判断，避免误杀
        self.assertFalse(D._same("时间多", "时间多来摆哈龙门阵撒"))

    def test_short_most_of_long(self):
        # 短句占长句 80%+ → 基本照搬
        self.assertTrue(D._same("今天加班好累", "今天加班好累啊"))

    def test_high_ratio(self):
        # 长度相同、几乎雷同 → 判复述
        self.assertTrue(D._same("我今天去爬山了", "我今天去爬山呢"))

    def test_normal_reference_not_repeat(self):
        self.assertFalse(D._same("在厂里上班", "你厂里上班累不累撒"))

    def test_empty(self):
        self.assertFalse(D._same("", "今天天气不错"))
        self.assertFalse(D._same(None, None))


class TestFakeLast(unittest.TestCase):
    """假末条 _is_fake_last(text)：SKIP_LAST 词命中=假末条"""

    def test_empty(self):
        self.assertTrue(D._is_fake_last(""))
        self.assertTrue(D._is_fake_last("   "))

    def test_skip_words(self):
        for w in ("互动消息", "系统通知", "官方号", "想聊天", "打完招呼", "看了看", "wink", "Wink"):
            self.assertTrue(D._is_fake_last(w))

    def test_normal(self):
        self.assertFalse(D._is_fake_last("我今天吃到好吃的了"))


class TestVoiceText(unittest.TestCase):
    """语音转写 _voice_text(content)"""

    def test_json_word(self):
        self.assertEqual(
            im._voice_text('{"duration":2,"word":"这个有什么瞎操心的呀。","mark":-1}'),
            "这个有什么瞎操心的呀。")

    def test_json_word_trimmed(self):
        self.assertEqual(im._voice_text('{"word":" 你好 "}'), "你好")

    def test_no_word(self):
        self.assertEqual(im._voice_text('{"duration":2,"url":"x.m4a"}'), "")

    def test_malformed(self):
        self.assertEqual(im._voice_text("not json at all"), "")
        self.assertEqual(im._voice_text(""), "")
        self.assertEqual(im._voice_text("None"), "")

    def test_regex_fallback(self):
        # 非标准 JSON 但含 "word":"…"
        self.assertEqual(im._voice_text('xxx "word":"好的嘛" yyy'), "好的嘛")


class TestIsSys(unittest.TestCase):
    """系统卡片判定 _is_sys(text, content)：text 有值=真人；否则查卡片/媒体/语音"""

    def test_text_present(self):
        self.assertFalse(im._is_sys("真实消息", ""))

    def test_empty_content(self):
        self.assertTrue(im._is_sys(None, ""))
        self.assertTrue(im._is_sys(None, "None"))

    def test_sys_card_keys(self):
        for c in ('{"messageType":"bubble_im_choice"}', '{"clickItems":1}',
                  '{"tagAuthGuide":1}', '{"activityId":9}'):
            self.assertTrue(im._is_sys(None, c), c)

    def test_media_keys(self):
        self.assertTrue(im._is_sys(None, '{"imageUrl":"x","imageH":600}'))

    def test_voice_with_word_is_real(self):
        # ⭐ 2026-10-04：语音 + Soul 自带转写 = 真人发言，不是卡片
        self.assertFalse(im._is_sys(None, '{"duration":2,"word":"好的","url":"x.m4a"}'))


class TestOfficial(unittest.TestCase):
    """官方号判定"""

    def test_named_official(self):
        for n in ("我的遇见", "系统通知", "官方号消息"):
            self.assertTrue(im._is_official(n))

    def test_numeric_name(self):
        self.assertTrue(im._is_official("123456789"))
        self.assertTrue(im._is_official("478918501"))

    def test_normal_name(self):
        self.assertFalse(im._is_official("欢欢"))
        self.assertFalse(im._is_official("月亮亮了"))

    def test_official_uid_hard_filter(self):
        # ⭐ 2026-09-30：陪伴聊天助手按 uid 硬过滤（昵称会变）
        self.assertTrue(im._is_official_uid("478918501"))
        self.assertFalse(im._is_official_uid("96691646"))

    def test_official_text(self):
        self.assertTrue(im._is_official_uid("123", "你好呀，我是 Soul 里的陪伴聊天助手"))
        self.assertFalse(im._is_official_uid("123", "你好呀"))


class TestPick(unittest.TestCase):
    """八道筛 check(s, existing)"""

    def test_ok(self):
        ok, why = pk.check("今天天气不错", [])
        self.assertTrue(ok)
        self.assertEqual(why, "OK")

    def test_too_short(self):
        ok, why = pk.check("好", [])
        self.assertFalse(ok)
        self.assertEqual(why, "过短")

    def test_too_long(self):
        ok, why = pk.check("我今天早上起来去楼下吃了碗重庆小面还加了两个煎蛋", [])
        self.assertFalse(ok)
        self.assertTrue(why.startswith("超过20字"))

    def test_noise(self):
        ok, why = pk.check("点击这里下载APP", [])
        self.assertFalse(ok)
        self.assertEqual(why, "网页噪音")

    def test_meta(self):
        ok, why = pk.check("记住一半话术核心是真诚", [])
        self.assertFalse(ok)
        self.assertEqual(why, "教学口吻")

    def test_sexual_hard_gate(self):
        ok, why = pk.check("我们今晚一起睡吧", [])
        self.assertFalse(ok)
        self.assertTrue(why.startswith("擦边:"))

    def test_oily(self):
        ok, why = pk.check("宝贝在干嘛", [])
        self.assertFalse(ok)
        self.assertTrue(why.startswith("油腻:"))

    def test_pua(self):
        ok, why = pk.check("她那种打压框架别惯着", [])
        self.assertFalse(ok)
        self.assertTrue(why.startswith("PUA:"))

    def test_arty(self):
        ok, why = pk.check("愿你星河灿烂", [])
        self.assertFalse(ok)
        self.assertTrue(why.startswith("文艺AI味:"))

    def test_analytic(self):
        ok, why = pk.check("我猜你是不喜欢主动", [])
        self.assertFalse(ok)
        self.assertTrue(why.startswith("分析式:"))

    def test_empty_greeting(self):
        ok, why = pk.check("在不在", [])
        self.assertFalse(ok)
        self.assertEqual(why, "零信息量")

    def test_empty_greeting_short_first(self):
        # 「在吗」仅2字 → 先命中「过短」（长度检查在零信息量之前）
        ok, why = pk.check("在吗", [])
        self.assertFalse(ok)
        self.assertEqual(why, "过短")

    def test_duplicate(self):
        ok, why = pk.check("今天天气不错", ["今天天气不错"])
        self.assertFalse(ok)
        self.assertEqual(why, "已存在")

    def test_split_candidates(self):
        # 短行不切
        parts = pk.split_candidates("紧急提问:夏天续命三件套是什么?")
        self.assertEqual(len(parts), 1)
        # 长行且 ≥2 个句末标点 → 按句切
        long_line = "第一句是夏天续命三件套是什么？第二句是西瓜冰可乐和空调。第三句是躺平"
        parts = pk.split_candidates(long_line)
        self.assertGreaterEqual(len(parts), 3)
        # 带序号的行首序号被剥掉
        parts = pk.split_candidates("1、今天天气不错\n2、下次一起喝汽水")
        self.assertEqual(parts, ["今天天气不错", "下次一起喝汽水"])

    def test_norm(self):
        self.assertEqual(pk.norm("  今天 天气 不错 "), "今天天气不错")


if __name__ == "__main__":
    unittest.main(verbosity=2)
