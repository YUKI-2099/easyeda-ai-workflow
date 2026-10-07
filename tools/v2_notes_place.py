# -*- coding: utf-8 -*-
"""给每个元件算一个不撞东西的注释落点（注释三步的第二步：v2_notes_dump → 本脚本 → v2_notes）。

🔴 会执行项目目录里的 notes.py（从里面取 NOTES = {位号: 注释}），只在自己的项目里用，别对来路不明的项目目录跑。
   项目根由 project.json 定位；读项目根的 geom.json（v2_notes_dump.py 生成），写 plan.json。导入本模块不读文件。

占位物（都折成矩形）：
  · 器件本体   = 引脚包络外扩，覆盖符号体 + 位号/值两行文字
  · 导线       = 每段折成细矩形
  · 网络符号   = 导线端点里"不落在任何引脚上"的那些，按网名长度向外撑开
  · 已放的注释 = 自己不能互相撞

候选落点按优先级：同行右侧 -> 同行左侧 -> 正下方 -> 正上方 -> 右侧更远。
全撞的报出来人工处理，不硬塞。

用法: python tools/v2_notes_place.py     （在项目目录里跑，不连桥接）
"""
import json, io, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from project import load_project


def load_notes(root):
    """执行 <项目根>/notes.py，取它的 NOTES（{位号: 注释}）。会执行项目目录里的代码——只在自己的项目里用。"""
    if root not in sys.path:
        sys.path.insert(0, root)
    from notes import NOTES
    return NOTES

FONT = 6.0                      # 字号（原理图单位，10mil/单位）；位号那行约 7
CH_W = FONT * 1.00              # 汉字宽
AS_W = FONT * 0.56              # 西文/数字宽
LINE_H = FONT * 1.25


def tw(s):
    return sum(CH_W if ord(c) > 0x2E80 else AS_W for c in s)


def rects_overlap(a, b, pad=0.0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def build(page):
    """返回 (占位矩形列表, 器件表)"""
    occ = []
    parts = page['parts']
    pinset = set()
    for p in parts:
        for x, y in p['pins']:
            pinset.add((x, y))

    for p in parts:
        xs = [q[0] for q in p['pins']] or [p['x']]
        ys = [q[1] for q in p['pins']] or [p['y']]
        # 引脚包络外扩：左右各 12（短桩），上 12（位号行），下 24（值那一行）
        occ.append((min(xs) - 12, min(ys) - 24, max(xs) + 12, max(ys) + 12))
        for k, ax, ay in p['vis']:
            occ.append((ax - 40, ay - 8, ax + 40, ay + 8))

    for L in page['wires']:
        for i in range(0, len(L) - 2, 2):
            x1, y1, x2, y2 = L[i], L[i + 1], L[i + 2], L[i + 3]
            occ.append((min(x1, x2) - 2, min(y1, y2) - 2, max(x1, x2) + 2, max(y1, y2) + 2))

    # 网络符号：导线端点中不落在引脚上的
    for L in page['wires']:
        for (ex, ey) in ((L[0], L[1]), (L[-2], L[-1])):
            if (ex, ey) in pinset:
                continue
            occ.append((ex - 70, ey - 12, ex + 70, ey + 12))
    return occ, parts


def solve(page, notes):
    occ, parts = build(page)
    placed, failed = [], []
    for p in sorted(parts, key=lambda q: (-q['y'], q['x'])):
        d = p['d']
        txt = notes.get(d)
        if not txt:
            failed.append((d, 'NO_NOTE'))
            continue
        xs = [q[0] for q in p['pins']] or [p['x']]
        ys = [q[1] for q in p['pins']] or [p['y']]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        cy = (y0 + y1) / 2.0
        w, h = tw(txt), LINE_H
        cx = (x0 + x1) / 2.0 - w / 2
        if len(p['pins']) >= 5:
            # 多脚件（IC/座子）：引脚在左右两边，符号正上/正下是空的，
            # 往旁边推会离本体太远，看不出注的是谁
            cands = [(cx, y1 + 32), (cx, y1 + 48), (cx, y0 - 36), (cx, y0 - 52),
                     (cx, y1 + 64), (cx, y0 - 68)]
        else:
            cands = []
        cands += [
            (x1 + 85, cy),                    # 同行右侧（网络符号之外）
            (x1 + 120, cy),
            (x0 - 85 - w, cy),                # 同行左侧
            ((x0 + x1) / 2.0 - w / 2, y0 - 34),   # 正下方
            ((x0 + x1) / 2.0 - w / 2, y1 + 30),   # 正上方
            (x1 + 160, cy),
            (x1 + 85, cy + 16), (x1 + 85, cy - 16),
        ]
        # 兜底：以器件为中心做一圈系统扫描，别硬塞也别放弃
        for dy in (0, -34, 30, -50, 46, -68, 64, -86, 82):
            for dx in range(85, 210, 15):
                cands.append((x1 + dx, cy + dy))
                cands.append((x0 - dx - w, cy + dy))
        got = None
        for (tx, ty) in cands:
            r = (tx, ty - h / 2, tx + w, ty + h / 2)
            if not any(rects_overlap(r, o, 2.0) for o in occ):
                got = (tx, ty, r)
                break
        if got is None:
            failed.append((d, 'NO_SPACE'))
            continue
        tx, ty, r = got
        occ.append(r)
        placed.append({'d': d, 'x': round(tx), 'y': round(ty), 't': txt})
    return placed, failed


if __name__ == '__main__':
    root = load_project()['root']
    notes = load_notes(root)
    D = json.load(io.open(os.path.join(root, 'geom.json'), encoding='utf-8'))
    out = {}
    tot = fail = 0
    for pg, v in D.items():
        pl, fa = solve(v, notes)
        out[pg] = {'uuid': v['uuid'], 'texts': pl}
        tot += len(pl); fail += len(fa)
        print('%-14s 放下 %3d   失败 %d %s' % (pg, len(pl), len(fa), fa if fa else ''))
    io.open(os.path.join(root, 'plan.json'), 'w', encoding='utf-8').write(
        json.dumps(out, ensure_ascii=False))
    print('合计 %d 条注释，失败 %d' % (tot, fail))
