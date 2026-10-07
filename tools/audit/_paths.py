# -*- coding: utf-8 -*-
"""audit/ 下所有脚本共用：项目配置 + 证据目录。证据（网表 / 页数据 JSON）属于项目，不进工具仓库。

导入本模块不读文件、不建目录：配置在第一次调用 cfg() 时才加载（找法同 tools/project.py：
环境变量 EDA_PROJECT > 从当前目录向上找 project.json），证据目录在第一次要写时才建。

project.json 里本目录用到的键（其余见 tools/project.py 文件头，例子见 project.example.json）：
  audit_dir   证据目录，相对项目根，默认 codex-live
  audit       可选，各审查脚本的板级参数，逐项说明见 tools/audit/README.md

配置有问题（找不到、JSON 语法错、类型不对）或缺上一步的产物，脚本都用 fail() 打一句人话说明缺什么、
该先跑什么，以退出码 2 结束，不抛栈。各脚本入口先调 utf8_stdio()。
"""
import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent          # tools/，project.py 在这里
DEFAULT_AUDIT_DIR = 'codex-live'
_CFG = None


def fail(msg, code=2):
    """配置 / 输入有问题：把原因打到 stderr，以退出码 code 结束（不抛栈）。"""
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def utf8_stdio():
    """脚本入口调一次：stdout / stderr 改成 UTF-8、编不了的字符替换掉。
    管道默认是本机编码（中文 Windows 上是 GBK），输出里一有 µ、✅ 这类字符就崩。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')


def cfg():
    """项目配置（tools/project.py 的 load_project）；找不到、读不了、JSON 语法错都 fail()。"""
    global _CFG
    if _CFG is None:
        if str(TOOLS) not in sys.path:
            sys.path.insert(0, str(TOOLS))
        from project import find_project, load_project
        try:
            _CFG = load_project()
        except SystemExit as e:                          # load_project 的报错本来就是一句人话
            fail(e.code if isinstance(e.code, str) else '找不到 project.json')
        except (ValueError, TypeError, OSError) as e:    # JSON 语法错（JSONDecodeError 是 ValueError）、顶层不是对象、读不了
            src = os.environ.get('EDA_PROJECT') or find_project() or 'project.json'
            fail('project.json 读不了（%s）：%s' % (src, e))
    return _CFG


def reset():
    """清掉缓存的配置（测试里换了 EDA_PROJECT 之后调）。"""
    global _CFG
    _CFG = None


def root():
    return Path(cfg()['root'])


def live_dir(create=True):
    """证据目录 <项目根>/<audit_dir>；create=True 时不存在就建。"""
    d = root() / (cfg().get('audit_dir') or DEFAULT_AUDIT_DIR)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def audit(section=None):
    """project.json 的 audit 段（没有就是 {}）；给了 section 就只取那一节（没有也是 {}）。"""
    a = cfg().get('audit') or {}
    if not isinstance(a, dict):
        fail('project.json 的 audit 必须是对象 {...}，现在是 %r' % (a,))
    if section is None:
        return a
    s = a.get(section) or {}
    if not isinstance(s, dict):
        fail('project.json 的 audit.%s 必须是对象 {...}，现在是 %r' % (section, s))
    return s


def str_list(value, where):
    """应是字符串列表的配置项：没写（None）当空列表；不是 ["…", …] 就 fail()。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        fail('%s 要写成字符串列表 ["…", …]，现在是 %r' % (where, value))
    return value


def ground_net():
    """地网名：audit.ground_net，默认 GND。"""
    g = audit().get('ground_net') or 'GND'
    if not isinstance(g, str):
        fail('project.json 的 audit.ground_net 要写成字符串，现在是 %r' % (g,))
    return g


def read_json(name, hint):
    """读证据目录里的 JSON；文件不在就 fail()，并说清楚该先跑什么（hint）。"""
    p = live_dir(create=False) / name
    if not p.is_file():
        fail('缺 %s：%s' % (p, hint))
    try:
        return json.loads(p.read_text(encoding='utf-8-sig'))
    except ValueError as e:
        fail('%s 不是合法的 JSON：%s' % (p, e))
