# -*- coding: utf-8 -*-
"""PCB 几何与读数的公共函数：把工具手册 #67～#71 封进代码，新脚本直接调，不用再各自推一遍。

- #67 焊盘 `rotation` 是弧度、绝对角（含器件转角）；器件 `rotation` 是度 → `pad_local_rot()`
- #68 底层器件 = 局部坐标先上下镜像（y 取反）再转 → `absolute_from_local()` / `local_from_absolute()`；
      整组换层做左右镜像：相对 x 取反、转角 → 180 − 转角 → `mirror_to_other_layer()`
- #70 `pcb_PrimitiveLine.getAll()` 会带出封装自带的线 → `routing_present()`：只认铜层或带网名，并先自测 layer 字段读得到
- #71 `pcb_Net.getNetlist()` 开着几份 PCB 就返回几份的数组；网表带 `PCB Layer` → `pick_netlist()` 按位号认板，`nets_of()` 比网络不比文本
坐标单位跟着输入走（API 读数是 mil）；角度一律度，逆时针为正（PCB 手册 §1.2b）。本模块不连桥接，纯函数，可离线测。
"""
import math
import os
import sys

MM = 0.0254                      # 1 mil = 0.0254 mm
COPPER_2L = (1, 2)
COPPER_4L = (1, 2, 15, 16)       # 4 层板实测：顶 1 / 底 2 / 内层 15、16（别的层叠先核实）


def rot(dx, dy, deg):
    a = math.radians(deg)
    return dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)


def pad_local_rot(pad_rot_rad, comp_rot_deg, bottom=False):
    """#67：焊盘读数的 rotation（弧度、绝对）→ 封装里的局部角（度）。底层是镜像后的角。"""
    a = math.degrees(pad_rot_rad or 0)
    return ((a - comp_rot_deg) if not bottom else (comp_rot_deg - a)) % 360


def absolute_from_local(comp_xy, comp_rot_deg, bottom, local_xy):
    """#68：封装局部坐标 → 放到 (x, y, 转角, 层) 之后的绝对坐标。底层先 y 取反再转。"""
    lx, ly = local_xy
    if bottom:
        ly = -ly
    dx, dy = rot(lx, ly, comp_rot_deg)
    return comp_xy[0] + dx, comp_xy[1] + dy


def local_from_absolute(comp_xy, comp_rot_deg, bottom, abs_xy):
    """#68：读回的绝对坐标 → 封装局部坐标（absolute_from_local 的逆运算）。"""
    lx, ly = rot(abs_xy[0] - comp_xy[0], abs_xy[1] - comp_xy[1], -comp_rot_deg)
    return (lx, -ly) if bottom else (lx, ly)


def mirror_to_other_layer(dx, dy, rot_deg):
    """#68：一组件整体换层、保持"从另一面看的样子"做左右镜像时，组内相对坐标和转角怎么变。"""
    return -dx, dy, (180 - rot_deg) % 360


def routing_present(lines, arcs=(), copper_layers=COPPER_4L):
    """#70："已开始布线"门禁。线 / 弧只要在铜层或带网名就算。
    先自测：每根线都要读得到 layer 字段（字段名是 layer 不是 layerId，PCB 手册 §1.2），读不到直接抛错，不许放行。"""
    items = list(lines) + list(arcs)
    for it in items:
        if 'layer' not in it or it['layer'] is None:
            raise ValueError('线对象读不到 layer 字段，门禁没法判断：%s' % str(it)[:120])
    cu = set(copper_layers)
    return any(it['layer'] in cu or it.get('net') for it in items)


def _parse():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, 'audit'))
    from codex_parse_netlist import parse
    return parse


def nets_of(netlist_text):
    """网表文本 → {网名（大写）: 排序后的 "位号-脚号" 列表}。摆件前后比这个，不比文本（#71：网表带 PCB Layer）。"""
    p = _parse()(netlist_text.replace('\r\n', '\n'))
    return {k.upper(): sorted(v) for k, v in p['nets'].items()}


def designators_of(netlist_text):
    p = _parse()(netlist_text.replace('\r\n', '\n'))
    return {c['DESIGNATOR'] for c in p['components']}


def pick_netlist(netlist, designators):
    """#71：getNetlist 返回字符串就直接用；返回数组时按位号集合认出当前板（完全相同的那份），认不出就抛错，不盲取第一份。"""
    if isinstance(netlist, str):
        return netlist
    want = set(designators)
    hits = [t for t in netlist if designators_of(t) == want]
    if len(hits) != 1:
        raise ValueError('网表数组里认不出当前板：%d 份里有 %d 份位号集合对得上' % (len(netlist), len(hits)))
    return hits[0]


# 标准读数脚本（配 bridge.ex 用）：器件 / 焊盘 / 铜线弧 / 网表。读完用上面的函数算。
READ_PCB_JS = r'''
const comps = (await eda.pcb_PrimitiveComponent.getAll()).map(c => { const j = JSON.parse(JSON.stringify(c));
  return {id: j.primitiveId, des: j.designator, layer: j.layer, x: j.x, y: j.y, rot: j.rotation}; });
const pads = (await eda.pcb_PrimitivePad.getAll()).map(q => JSON.parse(JSON.stringify(q))).map(q =>
  ({id: q.primitiveId, num: q.padNumber, net: q.net, x: q.x, y: q.y, rot: q.rotation, layer: q.layer}));
const lines = (await eda.pcb_PrimitiveLine.getAll()).map(l => ({layer: l.layer, net: l.net, id: l.primitiveId}));
const arcs = (await eda.pcb_PrimitiveArc.getAll()).map(l => ({layer: l.layer, net: l.net, id: l.primitiveId}));
return {comps, pads, lines, arcs, netlist: await eda.pcb_Net.getNetlist('Protel2')};
'''
