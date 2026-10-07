# -*- coding: utf-8 -*-
"""项目脚本样板：拷进你自己的项目仓库改，例如 <项目>/eda/check_project_json.py。不要提交回工具仓库。

工具仓库（easyeda-ai-workflow，本地目录名随意，下面简称"工具仓库"）和项目仓库怎么分工（详见工具仓库 AGENTS.md「工具仓库和项目仓库的分工」）：
  - 工具仓库只放通用工具和样板，不含任何项目数据，可以公开；
  - 项目专用的东西（页 uuid、位号、网名、坐标、改图规则）只写在你的项目仓库里：project.json，或者照这个样板写的脚本；
  - 项目脚本 import 工具仓库的 tools/（bridge、project、v2_rows…），**不要把那些脚本拷过来改**——
    拷了就分叉，上游修的坑你拿不到；通用工具缺功能，就到工具仓库里加参数或 project.json 键，再回来调用。

本样板只读：核对 project.json 和 EDA 里当前打开的工程对不对得上——工程 uuid 是不是同一个、各页 uuid 还在不在。
它不切页、不写任何东西。改成你自己的脚本时，凡是会 openDocument（切页）的，开头加 bridge.remember_doc()（坑 #51）。

用法（在项目目录里跑；project.py 会从当前目录向上找 project.json）：
    python -X utf8 eda/check_project_json.py [--project <项目目录>]
找工具仓库的顺序：环境变量 EDA_WORKFLOW > 从本文件所在目录逐级向上，找名为 easyeda-ai-workflow 或 eda-workflow 的目录。
"""
import os
import sys

# ===== 项目专用参数：只留在你的项目仓库里 =====
# 例：只核对这几页（写 project.json 里的页名）；空表示全部。uuid 统一放 project.json，别写在这里。
ONLY_PAGES = []
# ============================================

# 工具仓库的目录名：GitHub 上叫 easyeda-ai-workflow，作者本机叫 eda-workflow；你改了名就加进来，或者设 EDA_WORKFLOW
REPO_DIR_NAMES = ('easyeda-ai-workflow', 'eda-workflow')


def find_eda_workflow(start=None):
    """返回工具仓库根目录；找不到就退出并说明怎么设。"""
    env = os.environ.get('EDA_WORKFLOW')
    if env:
        if os.path.isfile(os.path.join(env, 'tools', 'bridge.py')):
            return os.path.abspath(env)
        sys.exit('环境变量 EDA_WORKFLOW 指的目录里没有 tools/bridge.py：%s' % env)
    d = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    while True:
        for name in REPO_DIR_NAMES:
            cand = os.path.join(d, name)
            if os.path.isfile(os.path.join(cand, 'tools', 'bridge.py')):
                return cand
        parent = os.path.dirname(d)
        if parent == d:
            sys.exit('找不到工具仓库：设环境变量 EDA_WORKFLOW=<工具仓库目录>')
        d = parent


def compare(cfg, project_uuid, pages):
    """cfg：load_project() 的结果；project_uuid：EDA 当前工程的 uuid；pages：getAllSchematicPagesInfo() 的结果。
    返回 (错误列表, 提示列表)。纯函数，不连桥接，方便离线测试。"""
    errors, notes = [], []
    if cfg.get('project_uuid') and project_uuid != cfg['project_uuid']:
        errors.append('EDA 里当前打开的不是 project.json 写的工程（工程 uuid 不同）')
    actual = {p.get('uuid'): p.get('name') for p in pages or []}
    wanted = [p for p in cfg['pages'] if not ONLY_PAGES or p[0] in ONLY_PAGES]
    if ONLY_PAGES and not wanted:
        errors.append('ONLY_PAGES 里的页名在 project.json 里一个都没有')
    for name, uuid in wanted:
        if uuid not in actual:
            errors.append('「%s」的页 uuid 在当前工程里找不到' % name)
        elif (actual[uuid] or '').lower() != name.lower():
            notes.append('「%s」在工程里叫「%s」（只是名字不同，uuid 对得上）' % (name, actual[uuid]))
    return errors, notes


def main(argv):
    sys.path.insert(0, os.path.join(find_eda_workflow(), 'tools'))
    import bridge
    import project

    path, argv = project.pop_project_arg(argv)
    cfg = project.load_project(path)
    base = bridge.find_bridge()
    if not base:
        sys.exit('没找到桥接：先按工具手册 §0.3 起 Bridge，再连扩展')
    info = bridge.ex('return await eda.dmt_Project.getCurrentProjectInfo();', base=base)
    pages = bridge.ex('return await eda.dmt_Schematic.getAllSchematicPagesInfo();', base=base)
    for r in (info, pages):
        if isinstance(r, dict) and 'HTTP' in r:            # bridge.ex 的失败返回，不能当成功（坑 ㊾）
            sys.exit('桥接调用失败：%r' % r)
    errors, notes = compare(cfg, (info or {}).get('uuid'), pages)
    for n in notes:
        print('  · ' + n)
    for e in errors:
        print('  ✗ ' + e)
    if errors:
        return 1
    print('✅ project.json 和当前工程对得上（%s，%d 页）' % (cfg.get('name') or cfg['file'], len(cfg['pages'])))
    return 0


if __name__ == '__main__':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    sys.exit(main(sys.argv[1:]))
