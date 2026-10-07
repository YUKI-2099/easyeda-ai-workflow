# -*- coding: utf-8 -*-
"""《坑档案》和工具手册 §1 生成段的回归（离线，不连 EDA）。

- 档案能解析；编号 ①～㊿ 连续、#51 起连续且不重复；每条都有标签、摘要。
- 工具手册 §1 生成段、档案接口索引都是最新的（改了档案没跑 tools/manual_index.py 就会在这里失败）。
- 生成段里每个编号都出现在一行清单里；必读条数正确。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manual_index as MI  # noqa: E402


class ManualIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text, _ = MI.read(MI.ARCHIVE)
        cls.pits = MI.parse_archive(text)

    def test_numbers_contiguous_and_unique(self):
        nums = [p.num for p in self.pits]
        self.assertEqual(len(nums), len(set(nums)))
        order = [MI.pit_order(n) for n in nums]
        self.assertEqual(order, list(range(1, len(order) + 1)), '编号要从 ① 连续排到最后一条')

    def test_every_pit_has_metadata(self):
        for p in self.pits:
            self.assertTrue(p.tags, '%s 没有标签' % p.num)
            self.assertTrue(p.summary, '%s 没有摘要' % p.num)
            if '已并入' not in p.title:                       # ㉚ 这类"已并入 ④"的只有标题
                self.assertTrue(p.body.strip(), '%s 没有正文' % p.num)

    def test_generated_blocks_are_current(self):
        self.assertEqual(MI.main(['--check']), 0, '改了《坑档案》要跑 python tools/manual_index.py')

    def test_main_block_lists_all(self):
        block = MI.render_main(self.pits)
        for p in self.pits:
            self.assertIn('- %s ' % p.num, block)
        self.assertEqual(block.count('\n- **'), MI.MUST_READ)

    def test_score_is_deterministic(self):
        a = [p.num for p in MI.ranked(self.pits)[0]]
        b = [p.num for p in MI.ranked(list(reversed(self.pits)))[0]]
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
