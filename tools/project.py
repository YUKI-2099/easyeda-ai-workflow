# -*- coding: utf-8 -*-
"""项目配置装载 —— 所有 v2_* / audit 脚本共用，把"这块板是谁"从脚本里拆出去。

为什么要有它：页 uuid、冻结文件名、电源网、项目目录这些板子的私有事实一旦写死在脚本里，
换一块板就得改代码。现在每个项目根目录放一份 project.json（不进本仓库，.gitignore 已排除），脚本按下面顺序找它：
    1. 显式参数 load_project(path)（有的脚本给了 --project）
    2. 环境变量 EDA_PROJECT=<项目目录或 project.json 路径>
    3. 从当前工作目录逐级向上找 project.json
导入本模块（以及各脚本模块）不读任何文件；load_project() 被调用时才找、才读。

project.json 字段（都有默认值，见 DEFAULTS；示例见仓库根 project.example.json）：
  name               板名，只用于打印
  project_uuid       工程 uuid（有则脚本会断言当前工程就是它，防"写到另一块板"）
  bridge             桥接地址，默认 http://127.0.0.1:49620（坑㊺：别用 localhost）
  pages              [["页名","页uuid"], ...]  —— 几何三查 / 清网名 / 注释 / 审计按此逐页遍历
  netlist_dir        冻结网表目录，相对项目根
  freeze_glob        现役冻结文件的 glob（取排序最后一个）
  freeze_format      写新冻结时的文件名，{tag} 换成 YYYYMMDD-HHMM
  power_nets         哪些网用 Power 标志而不是网络端口（v2_rows / v2_add_parts / v2_swap_part / v2_wire_pins 用）；
                     脚本里不另加任何电源网，默认表以外的电源网（如某路外设供电）请自己列进来
  nonelectrical_refs 非电气标记件的位号（二维码、Logo…）：v2_refreeze 不查它们的 Value / C 号，审查脚本也跳过
  audit_dir          tools/audit 审查脚本的证据目录，相对项目根，默认 codex-live
  audit              可选，tools/audit 各审查脚本的板级参数（要查的供电脚、本轮增量预期等），
                     逐项说明见 tools/audit/README.md
"""
import os, io, json

DEFAULTS = {
    'name': '',
    'project_uuid': None,
    'bridge': 'http://127.0.0.1:49620',
    'pages': [],
    'netlist_dir': 'netlist',
    'freeze_glob': 'FREEZE-*.enet.txt',
    'freeze_format': 'FREEZE-{tag}.enet.txt',
    'power_nets': ['VBUS', 'SYS', '+3V3', '+5V', 'VBAT', 'VCC', 'VDD'],
    # 非电气标记件（二维码、Logo、定位标…）：没有 Value、没有立创 C 号是正常的，
    # 不该让 v2_refreeze 的「Value 空 / supplierId 非C号」两项误判成缺陷。
    # 🔴 只豁免这两项——单脚网、自动网、几何三查照查不误。
    'nonelectrical_refs': [],
}
FILE = 'project.json'


def find_project(start=None):
    """从 start（默认 cwd）逐级向上找 project.json，找不到返回 None。"""
    d = os.path.abspath(start or os.getcwd())
    while True:
        p = os.path.join(d, FILE)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def load_project(explicit=None, required=True):
    src = explicit or os.environ.get('EDA_PROJECT')
    if src and os.path.isdir(src):
        src = os.path.join(src, FILE)
    if not src:
        src = find_project()
    if not src or not os.path.isfile(src):
        if not required:
            return None
        raise SystemExit('找不到 project.json：设环境变量 EDA_PROJECT=<项目目录或 project.json 路径>、'
                         '或在项目目录里运行（带 --project 参数的脚本也可以直接给）。模板见本仓库根目录的 project.example.json')
    cfg = dict(DEFAULTS)
    with io.open(src, encoding='utf-8') as project_file:
        cfg.update(json.load(project_file))
    cfg['root'] = os.path.dirname(os.path.abspath(src))
    cfg['file'] = os.path.abspath(src)
    cfg['netlist_dir'] = os.path.join(cfg['root'], cfg['netlist_dir'])
    bad = [p for p in cfg['pages'] if not (isinstance(p, (list, tuple)) and len(p) == 2)]
    if bad:
        raise SystemExit('project.json 的 pages 必须是 [["页名","页uuid"], ...]，坏项: %r' % bad)
    cfg['pages'] = [tuple(p) for p in cfg['pages']]
    return cfg


def pop_project_arg(argv):
    """从 argv 里摘掉 --project <path>，返回 (path 或 None, 剩余 argv)。给不想引 argparse 的脚本用。"""
    out, path, i = [], None, 0
    while i < len(argv):
        if argv[i] == '--project' and i + 1 < len(argv):
            path = argv[i + 1]; i += 2; continue
        out.append(argv[i]); i += 1
    return path, out


if __name__ == '__main__':
    c = load_project()
    print('project.json =', c['file'])
    for k in ('name', 'project_uuid', 'bridge', 'netlist_dir', 'freeze_glob', 'freeze_format'):
        print('  %-14s %s' % (k, c[k]))
    print('  pages          %d 页: %s' % (len(c['pages']), ', '.join(n for n, _ in c['pages'])))
