"""审查方只读采集（参考实现）。不 import 执行者的导出/解析代码。

    python codex_independent_audit.py          # 冻结门禁：现场重导网表，按原始字节算 SHA，和项目最新的冻结文件比
    python codex_independent_audit.py pages    # 门禁过了以后，按 project.json 的 pages 逐页拉页数据（page-p1.json …）

用到 project.json 的 bridge、project_uuid、pages、netlist_dir、freeze_glob；证据写到 audit_dir（默认 <项目根>/codex-live）。
只读：openDocument 切页读、get*；跑完切回运行前的前台文档（#51）。跑之前确认用户不在 GUI 里。
每次调桥接都留一份记录（<名字>.json）：成功记 response，失败记 http_error / url_error，然后以退出码 2 停下。
逐页拉之前核对门禁记录对的就是现役冻结文件（文件名和 SHA），并先清掉上一轮的 page-p*.json，免得新旧页数据混在一起。
"""
from pathlib import Path
import json, hashlib, urllib.request, urllib.error, datetime, time, atexit

import os, glob
from _paths import cfg, fail, live_dir, read_json, utf8_stdio
BASE = Path(__file__).resolve().parent           # 只用来找同目录的 codex_pull_page.js


def baseline():
    """现役冻结 = 项目网表目录里按 freeze_glob 排序的最后一个。"""
    c = cfg()
    frz = sorted(glob.glob(os.path.join(c['netlist_dir'], c['freeze_glob'])))
    if not frz:
        fail('项目网表目录里没有冻结文件（在 %s 下找 %s）：先冻结一版再审' % (c['netlist_dir'], c['freeze_glob']))
    return Path(frz[-1])


def page_uuids():
    pages = [u for _, u in cfg()['pages']]
    if not pages:
        fail('project.json 的 pages 是空的：门禁要先打开第一页（导网表要求活动文档是原理图），逐页拉也按它走')
    return pages


def now():
    return datetime.datetime.now().astimezone().isoformat()


def record(name, value):
    (live_dir() / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def execute(name, code):
    """调一次桥接 /execute，结果原样记进 <name>.json。连不上、HTTP 错误、回的不是 JSON、EDA 报错：记下来再 fail()。"""
    url = cfg()['bridge']
    req = urllib.request.Request(url+'/execute', data=json.dumps({'code':code}).encode(), headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        body=e.read().decode('utf-8',errors='replace')
        record(name, {'at':now(),'code':code,'http_error':e.code,'body':body})
        fail('桥接返回 HTTP %s（记录在 %s.json）：%s' % (e.code, name, body[:200]))
    except (urllib.error.URLError, OSError) as e:          # 连不上、超时
        record(name, {'at':now(),'code':code,'url_error':str(e)})
        fail('连不上桥接 %s（记录在 %s.json）：%s' % (url, name, e))
    except ValueError as e:
        record(name, {'at':now(),'code':code,'bad_response':str(e)})
        fail('桥接回的不是 JSON（记录在 %s.json）：%s' % (name, e))
    record(name, {'at':now(), 'code':code, 'response':result})
    if not isinstance(result, dict) or not result.get('success'):
        fail('桥接执行出错（记录在 %s.json）：%s' % (name, str(result.get('error') if isinstance(result, dict) else result)[:200]))
    return result.get('result')

JS_DOC = "const d=await eda.dmt_SelectControl.getCurrentDocumentInfo();return d?{uuid:d.uuid,tabId:d.tabId,documentType:d.documentType}:null;"

def restore(before, tag):
    # #51：还原到运行前的文档（activateDocument 只是激活已开的标签页，不算开 PCB），回读确认并留证据
    if before and before.get('tabId'):
        execute('restore-'+tag, "return await eda.dmt_EditorControl.activateDocument("+json.dumps(before['tabId'])+");")
        after=execute('doc-after-'+tag, JS_DOC)
        if (after or {}).get('uuid')!=before['uuid']:
            fail('没切回运行前的文档：' + json.dumps([before,after], ensure_ascii=False))

def freeze_gate():
    c = cfg()
    base = baseline()                              # 配置问题先在这里报，再碰桥接
    p1 = page_uuids()[0]
    expected = hashlib.sha256(base.read_bytes()).hexdigest()
    url = c['bridge']
    try:
        with urllib.request.urlopen(url+'/health', timeout=5) as resp:
            health = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail('连不上桥接 %s：%s（先确认桥接起着，project.json 的 bridge 地址对）' % (url, e))
    record('health', health)
    if health.get('service')!='easyeda-bridge' or health.get('edaWindowCount')!=1:
        fail('桥接状态不对：service=%r，edaWindowCount=%r（要求恰好 1 个 EDA 窗口连着）' % (health.get('service'), health.get('edaWindowCount')))
    record('window', health.get('activeWindowId'))   # 坑⑥：windowId 每次重连都变，只记录不断言
    before=execute('doc-before-gate', JS_DOC); atexit.register(restore, before, 'gate')
    execute('open-p1', "return await eda.dmt_EditorControl.openDocument('"+p1+"');")
    time.sleep(1.5)
    state=execute('scope', 'const p=await eda.dmt_Project.getCurrentProjectInfo(); const d=await eda.dmt_SelectControl.getCurrentDocumentInfo(); return {project:p.uuid,document:d};')
    if c['project_uuid'] and state['project']!=c['project_uuid']:
        fail('scope drift：EDA 里开着的工程是 %s，project.json 的 project_uuid 是 %s' % (state['project'], c['project_uuid']))
    if (state.get('document') or {}).get('uuid')!=p1:
        fail('scope drift：活动文档不是第一页 %s，而是 %s' % (p1, (state.get('document') or {}).get('uuid')))
    data=execute('netlist-export', "const f=await eda.sch_ManufactureData.getNetlistFile('codex-independent','Protel2'); return {s:f?await f.text():''};")
    raw=(data or {}).get('s','').encode('utf-8')
    if not raw:
        fail('网表导不出来（空）：多半是原理图 DRC 有 fatal，先在 EDA 里看')
    (live_dir()/'live.enet.txt').write_bytes(raw)
    result={'frozen_file':base.name,'expected':expected,'live_sha256':hashlib.sha256(raw).hexdigest(),'live_bytes':len(raw),'frozen_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),'frozen_bytes':base.stat().st_size}
    result['pass']=result['live_sha256']==expected==result['frozen_sha256']
    record('freeze-gate',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['pass']:
        raise SystemExit('STOP: frozen baseline mismatch; no downstream audit performed.')

def pull_pages():
    gate=read_json('freeze-gate.json', '先跑 codex_independent_audit.py（不带参数 = 冻结门禁）')
    if not gate.get('pass'):
        fail('冻结门禁没过（freeze-gate.json 的 pass 是 false）：先查清现场网表和冻结文件为什么不一致', code=1)
    base = baseline()
    if gate.get('frozen_file')!=base.name or gate.get('frozen_sha256')!=hashlib.sha256(base.read_bytes()).hexdigest():
        fail('门禁记录对的是 %s，现役冻结是 %s（或者内容变了）：先重跑门禁 codex_independent_audit.py' % (gate.get('frozen_file'), base.name))
    pages=page_uuids()
    project_uuid=cfg().get('project_uuid') or ''
    before=execute('doc-before-pages', JS_DOC)
    for old in live_dir().glob('page-p*.json'):    # 上一轮的页数据：这一轮中途失败时别和新数据混在一起
        old.unlink()
    try:
        for i,page in enumerate(pages,1):
            execute('open-p'+str(i), "return await eda.dmt_EditorControl.openDocument('"+page+"');")
            time.sleep(1.5)
            code='const TARGET_PAGE='+json.dumps(page)+';const PROJECT_UUID='+json.dumps(project_uuid)+';'+(BASE/'codex_pull_page.js').read_text(encoding='utf-8')
            result=execute('page-p'+str(i),code)
            if not isinstance(result, dict) or not isinstance(result.get('components'), list) or not isinstance(result.get('wires'), list):
                fail('page-p%d.json 的内容不是页数据（没有 components / wires）：重拉' % i)
            print('p'+str(i), 'components',len([c for c in result['components'] if c.get('ref')]),'wires',len(result['wires']),flush=True)
    finally:
        restore(before, 'pages')

if __name__=='__main__':
    import sys
    utf8_stdio()
    if len(sys.argv)>1 and sys.argv[1]=='pages': pull_pages()
    else: freeze_gate()
