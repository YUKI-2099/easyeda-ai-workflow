# -*- coding: utf-8 -*-
"""桥接调用层 —— 从 v2_rows.ex() 抽出，所有脚本统一从这里打 /execute。

编码了三个坑：
  ㊺  用 127.0.0.1 不用 localhost（2026-09-10 localhost 客户端 225s 不返回，改 127.0.0.1 后 8.3s；根因未确认）
  ㊾  HTTP 错误必须保存正文再返回，不能让异常把同一脚本里后面的调用吞掉；失败返回不得冒充通过
  ④   超时/500 ≠ 没执行：调用方收到 {'HTTP': 500} 后应等 6s → 重读现状 → 按位号幂等重跑，不重跑整批

地址优先级：ex(base=…) 显式 > 环境变量 EDA_BRIDGE > project.json 的 bridge > 默认 http://127.0.0.1:49620
"""
import os, json, urllib.request, urllib.error

DEFAULT = 'http://127.0.0.1:49620'


def base_url(explicit=None):
    if explicit:
        return explicit.rstrip('/')
    env = os.environ.get('EDA_BRIDGE')
    if env:
        return env.rstrip('/')
    try:
        from project import load_project
        cfg = load_project(required=False)
        if cfg and cfg.get('bridge'):
            return cfg['bridge'].rstrip('/')
    except Exception:
        pass
    return DEFAULT


def ex(code, timeout=120, base=None):
    """执行一段 JS（代码体等价于 async function(eda){...}，必须 return）。
    成功返回 result；HTTP 错误返回 {'HTTP': 状态码, 'body': 错误正文}（调用方用 'HTTP' in r 判断）。"""
    req = urllib.request.Request(base_url(base) + '/execute',
                                 data=json.dumps({'code': code}).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {'HTTP': e.code, 'body': e.read().decode('utf-8', errors='replace')}
    if isinstance(r, dict) and r.get('success') is False:
        return {'HTTP': 200, 'body': r.get('error') or r}
    return r.get('result') if isinstance(r, dict) else r


def health(base=None, timeout=5):
    """{'service':'easyeda-bridge','edaConnected':bool,'edaWindowCount':n,'activeWindowId':...,'pendingRequests':n}"""
    return json.load(urllib.request.urlopen(base_url(base) + '/health', timeout=timeout))


def find_bridge(ports=range(49620, 49630), timeout=2):
    """扫端口找现成的桥接（§0.5：任何 AI 起桥接前必须先扫）。返回 base_url 或 None。"""
    for p in ports:
        try:
            h = json.load(urllib.request.urlopen('http://127.0.0.1:%d/health' % p, timeout=timeout))
            if h.get('service') == 'easyeda-bridge':
                return 'http://127.0.0.1:%d' % p
        except Exception:
            continue
    return None


if __name__ == '__main__':
    b = find_bridge()
    print('bridge:', b or '未找到（先起一个，先 Bridge 后扩展，见手册 §0.3）')
    if b:
        print(json.dumps(health(b), ensure_ascii=False))


# ── 前台文档记忆/切回（坑 #51）──────────────────────────────────────────────
# 2026-09-11：用户在 GUI 里布 PCB，我在后台跑只读回归，脚本逐页 openDocument 把他的 PCB 标签页切走了。
# 只读脚本也算打扰：跑之前先确认用户不在 GUI；脚本开头 remember_doc()，进程退出时自动切回原文档。
_JS_DOC = ("const d=await eda.dmt_SelectControl.getCurrentDocumentInfo();"
           "return d?{uuid:d.uuid,tabId:d.tabId,documentType:d.documentType,project:d.parentProjectUuid||null}:null;")


def current_doc(base=None):
    """此刻前台文档 {'uuid','tabId','documentType','project'}；没开文档或桥接不通返回 None。"""
    try:
        r = ex(_JS_DOC, timeout=30, base=base)
    except Exception:
        return None
    return r if isinstance(r, dict) and r.get('uuid') else None


def restore_doc(doc, base=None):
    """切回 doc：先 activateDocument(tabId)——只是激活已开的标签页，不算读写它；失败再 openDocument(uuid)。"""
    if not doc:
        return None
    r = None
    if doc.get('tabId'):
        r = ex('return await eda.dmt_EditorControl.activateDocument(%s);' % json.dumps(doc['tabId']), timeout=60, base=base)
    if r is not True:
        r = ex('return await eda.dmt_EditorControl.openDocument(%s);' % json.dumps(doc['uuid']), timeout=60, base=base)
    return r


def remember_doc(base=None, quiet=False):
    """脚本开头调一次：记住用户此刻的前台文档，进程退出（含 sys.exit / 未捕获异常）时自动切回去。"""
    import atexit
    doc = current_doc(base)
    if doc:
        atexit.register(restore_doc, doc, base)
        if not quiet:
            print('（前台文档 %s… 已记住，脚本退出时自动切回）' % doc['uuid'][:8])
    return doc
