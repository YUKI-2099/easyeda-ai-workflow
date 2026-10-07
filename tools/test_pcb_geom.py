# -*- coding: utf-8 -*-
"""pcb_geom 的离线回归（单位 mil）。
LOCAL 是一颗四脚侧按键放顶层 0° 时读出的封装局部焊盘；BOTTOM_ROT90 是同型号放底层、转 90° 时读回的绝对焊盘坐标，
整体平移到了原点附近（器件中心改成 (10, 20)），器件与焊盘的相对关系不变，仍是工具手册 #68 的验证样本。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcb_geom as G  # noqa: E402

LOCAL = {'1': (-82.68, 43.305), '2': (82.68, 43.305), '3': (-132.87, -43.305), '4': (132.87, -43.305)}
BOTTOM_ROT90 = ((10.0, 20.0), 90, {'1': (53.305, -62.68), '2': (53.305, 102.68), '3': (-33.305, -112.87), '4': (-33.305, 152.87)})


def close(a, b, tol=0.01):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


class PcbGeomTest(unittest.TestCase):
    def test_bottom_layer_is_y_mirror(self):
        xy, r, pads = BOTTOM_ROT90
        for n, loc in LOCAL.items():
            self.assertTrue(close(G.absolute_from_local(xy, r, True, loc), pads[n]), n)
            self.assertTrue(close(G.local_from_absolute(xy, r, True, pads[n]), loc), n)

    def test_x_mirror_would_be_wrong(self):
        xy, r, pads = BOTTOM_ROT90
        wrong = G.absolute_from_local(xy, r, False, (-LOCAL['1'][0], LOCAL['1'][1]))   # 按"左右镜像"去算
        self.assertFalse(close(wrong, pads['1']))

    def test_top_layer_roundtrip(self):
        for n, loc in LOCAL.items():
            a = G.absolute_from_local((100.0, -50.0), 36.0, False, loc)
            self.assertTrue(close(G.local_from_absolute((100.0, -50.0), 36.0, False, a), loc))

    def test_mirror_to_other_layer(self):
        self.assertEqual(G.mirror_to_other_layer(3.0, 4.0, 90), (-3.0, 4.0, 90))
        self.assertEqual(G.mirror_to_other_layer(3.0, 4.0, 30), (-3.0, 4.0, 150))

    def test_pad_rotation_radians_absolute(self):
        self.assertAlmostEqual(G.pad_local_rot(3.141593, 180), 0.0, places=3)        # 器件 180°、焊盘 π
        self.assertAlmostEqual(G.pad_local_rot(-1.570796, 270), 0.0, places=3)       # 器件 270°、焊盘 −π/2
        self.assertAlmostEqual(G.pad_local_rot(1.570796, 0), 90.0, places=3)         # 器件 0°、焊盘 π/2

    def test_routing_gate(self):
        footprint_silk = [{'layer': 3, 'net': ''}, {'layer': 13, 'net': ''}]         # 封装自带的丝印 / 文档线
        self.assertFalse(G.routing_present(footprint_silk))
        self.assertTrue(G.routing_present(footprint_silk + [{'layer': 15, 'net': ''}]))
        self.assertTrue(G.routing_present([{'layer': 3, 'net': 'GND'}]))
        with self.assertRaises(ValueError):
            G.routing_present([{'layerId': 1, 'net': 'GND'}])                         # 字段名写错必须拦住

    def test_pick_netlist(self):
        def nl(refs):
            blocks = ''.join('[\nDESIGNATOR\n%s\nFOOTPRINT\nX\n]\n' % r for r in refs)
            return 'PROTEL NETLIST 2.0\n' + blocks + '(\nN1\n%s-1\n)\n' % refs[0]
        a, b = nl(['U1', 'R1']), nl(['U7', 'C1'])
        self.assertEqual(G.pick_netlist(a, {'U1', 'R1'}), a)
        self.assertEqual(G.pick_netlist([b, a], {'U1', 'R1'}), a)
        with self.assertRaises(ValueError):
            G.pick_netlist([a, b], {'Q9'})
        self.assertEqual(G.nets_of(a), {'N1': ['U1-1']})


if __name__ == '__main__':
    unittest.main()
