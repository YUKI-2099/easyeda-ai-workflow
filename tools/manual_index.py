# -*- coding: utf-8 -*-
"""《EDA工具手册》§1 的索引生成器（2026-09-27 分卷后）。

坑的全文和元数据都在 docs/EDA工具手册-坑档案.md；本脚本读它，生成两段（别处不动）：
  1. 工具手册 §1 里两个标记之间的「触发索引 / 必读 / 全部坑一行清单」；
  2. 坑档案开头两个标记之间的「接口索引」（接口名 → 坑编号），给"调某个接口前先查一下"用。

每条坑的格式（标题下面紧跟一段 > 开头的元数据，空一行，再是正文）：
    ### #68 ✅ 🔴🔴 **标题**

    > 标签：PCB 读写与摆件 ｜ 静默：是 ｜ 置顶：否 ｜ 频度初值：3
    > 摘要：一两句正解。
    > 命中：
    > - 2026-09-27 踩到：一句话

    正文……

排序分 = 2×严重度（🔴🔴 3 / 🔴 2 / 其它 1）+ 2×静默 + 频度初值/2（封顶 5）+ 命中条数（封顶 5）+ 近期（离档案里最新命中 ≤30 天 +2，≤90 天 +1）。
「近期」按档案里最晚的命中日期算，不看系统时钟，所以同一份档案每次生成结果一样（--check 不会隔天就失败）。

用法：
    python tools/manual_index.py           # 重新生成并写回
    python tools/manual_index.py --check   # 只检查是否最新（测试用），不写
"""
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL = os.path.join(ROOT, 'docs', 'EDA工具手册-easyeda-api官方桥接-跨项目版.md')
ARCHIVE = os.path.join(ROOT, 'docs', 'EDA工具手册-坑档案.md')
BEGIN = '<!-- 以下由 tools/manual_index.py 生成：改《坑档案》后重跑，这一段不要手改 -->'
END = '<!-- 生成结束 -->'
MUST_READ = 20

# 标签的固定顺序（触发索引按这个顺序出行）；左边是标签，右边是"要做的事"
TAGS = [
    ('执行与回读', '任何写操作之后（返回值不可信、超时 / 500 不等于没执行、回读慢一拍）'),
    ('器件与库', '放件、换料、改器件属性 / Value、库和封装'),
    ('引脚与几何', '按引脚连线、算坐标、挪器件'),
    ('导线与网名', '画线、挪线、改网名'),
    ('端口与网标', '放 / 挪网标、端口'),
    ('网表与 DRC', '导网表、比对、冻结、跑 DRC'),
    ('页面与文档', '开页、切文档、建板、改名'),
    ('PCB', 'PCB 读数、摆件、翻面、规则、板框、铺铜、布线'),
    ('工具链与协作', '起桥接、多 Agent / 多设备协作、本地工具链、采购价格'),
]
TAG_NAMES = [t for t, _ in TAGS]

CIRCLED = {}
for i in range(20):
    CIRCLED[chr(0x2460 + i)] = i + 1          # ①～⑳
for i in range(15):
    CIRCLED[chr(0x3251 + i)] = i + 21         # ㉑～㉟
for i in range(15):
    CIRCLED[chr(0x32B1 + i)] = i + 36         # ㊱～㊿

HEAD_RE = re.compile(r'^### (#\d+|[①-⑳㉑-㉟㊱-㊿])\s+(.*)$')
DATE_RE = re.compile(r'(20\d\d-\d\d-\d\d)')
API_RE = re.compile(r'`(?:eda\.)?((?:sch|pcb|dmt|lib|sys)_[A-Za-z]+(?:\.[A-Za-z_]+)?)')


def read(path):
    with open(path, 'rb') as f:
        b = f.read().decode('utf-8')
    return b.replace('\r\n', '\n'), ('\r\n' in b)


def write(path, text, crlf):
    with open(path, 'wb') as f:
        f.write((text.replace('\n', '\r\n') if crlf else text).encode('utf-8'))


def pit_order(num):
    return CIRCLED[num] if num in CIRCLED else int(num[1:])


class Pit:
    def __init__(self, num, title, meta, body):
        self.num, self.title, self.body = num, title, body
        self.tags = [t.strip() for t in meta.get('标签', '').split('、') if t.strip()]
        self.silent = meta.get('静默', '否') == '是'
        self.pinned = meta.get('置顶', '否') == '是'
        self.freq = int(meta.get('频度初值', '0') or 0)
        self.summary = meta.get('摘要', '').strip()
        self.hits = meta.get('命中', [])
        self.sev = 3 if '🔴🔴' in title else (2 if '🔴' in title else 1)
        self.apis = sorted(set(API_RE.findall(title + '\n' + body)))

    def hit_dates(self):
        return sorted(d for h in self.hits for d in DATE_RE.findall(h)[:1])

    def score(self, ref_date):
        s = 2 * self.sev + 2 * int(self.silent) + min(self.freq, 10) / 2 + min(len(self.hits), 5)
        ds = self.hit_dates()
        if ds and ref_date:
            age = (ref_date - datetime.date.fromisoformat(ds[-1])).days
            s += 2 if age <= 30 else (1 if age <= 90 else 0)
        return s


def parse_archive(text):
    """返回 (坑列表, 档案全文按行)。坑按编号顺序；编号重复或格式不对直接抛错。"""
    lines = text.split('\n')
    heads = [i for i, l in enumerate(lines) if HEAD_RE.match(l)]
    pits, seen = [], set()
    for k, i in enumerate(heads):
        j = heads[k + 1] if k + 1 < len(heads) else len(lines)
        m = HEAD_RE.match(lines[i])
        num, title = m.group(1), m.group(2)
        if num in seen:
            raise ValueError('坑编号重复：%s' % num)
        seen.add(num)
        p = i + 1
        while p < j and not lines[p].strip():
            p += 1
        meta, hits = {}, []
        if p < j and lines[p].startswith('> 标签：'):
            while p < j and lines[p].startswith('>'):
                s = lines[p][1:].strip()
                if s.startswith('- '):
                    hits.append(s[2:].strip())
                elif s.startswith('摘要：'):
                    meta['摘要'] = s[3:].strip()          # 摘要整行都算，里面可以有｜和：
                elif s.startswith('命中'):
                    pass
                else:
                    for part in s.split('｜'):
                        if '：' in part:
                            k2, v = part.split('：', 1)
                            meta[k2.strip()] = v.strip()
                p += 1
        else:
            raise ValueError('%s 缺元数据段（标题下第一段要以「> 标签：」开头）' % num)
        meta['命中'] = hits
        body = '\n'.join(lines[p:j]).strip('\n')
        pit = Pit(num, title, meta, body)
        bad = [t for t in pit.tags if t not in TAG_NAMES]
        if bad:
            raise ValueError('%s 的标签不认识：%s（可用：%s）' % (num, bad, '、'.join(TAG_NAMES)))
        pits.append(pit)
    pits.sort(key=lambda p: pit_order(p.num))
    return pits


def ref_date_of(pits):
    ds = [d for p in pits for d in p.hit_dates()]
    return datetime.date.fromisoformat(max(ds)) if ds else None


def ranked(pits):
    rd = ref_date_of(pits)
    order = sorted(pits, key=lambda p: (-int(p.pinned), -p.score(rd), pit_order(p.num)))
    return order, rd


def short_title(p):
    t = re.sub(r'^(✅|📖|⚠️)\s*', '', p.title).strip()
    return t


def render_main(pits):
    order, rd = ranked(pits)
    must = order[:MUST_READ]
    must_set = {p.num for p in must}
    out = [BEGIN, '']
    out.append('**触发索引**（做这类事之前，先把对应编号在《坑档案》里读全文；**粗体** = 下面"必读"里已经有摘要）')
    out.append('')
    out.append('| 要做的事 | 坑（按分数排） |')
    out.append('|---|---|')
    for tag, task in TAGS:
        ps = [p for p in order if tag in p.tags]
        cell = ' '.join(('**%s**' % p.num) if p.num in must_set else p.num for p in ps) or '—'
        out.append('| %s | %s |' % (task, cell))
    out.append('')
    out.append('**必读**（前 %d 条，按"严重度 × 静默 × 命中 × 近期"排；全文在《坑档案》同编号那条）' % MUST_READ)
    out.append('')
    for p in must:
        hd = p.hit_dates()
        hit = '（命中 %d 次，最近 %s）' % (len(p.hits), hd[-1]) if hd else ''
        flag = '〔静默〕' if p.silent else ''
        pin = '〔置顶〕' if p.pinned else ''
        summary = (p.summary or '（摘要待补）').rstrip('。')
        out.append('- **%s** %s' % (p.num, short_title(p)))
        out.append('  - %s%s%s。%s' % (pin, flag, summary, hit))
    out.append('')
    out.append('**全部坑一行清单**（编号永久固定；标题就是一句话结论）')
    out.append('')
    for p in pits:
        out.append('- %s %s' % (p.num, p.title))
    out.append('')
    out.append('> 生成依据：《坑档案》%d 条，最新命中 %s。' % (len(pits), rd.isoformat() if rd else '无'))
    out.append('')
    out.append(END)
    return '\n'.join(out)


def render_archive_index(pits):
    by_api = {}
    for p in pits:
        for a in p.apis:
            by_api.setdefault(a, []).append(p.num)
    out = [BEGIN, '', '**接口索引**（调某个接口之前，先看它牵涉的坑；也可以直接在本文里搜接口名）', '']
    for a in sorted(by_api, key=str.lower):
        out.append('- `%s` → %s' % (a, ' '.join(by_api[a])))
    out += ['', END]
    return '\n'.join(out)


def replace_block(text, block, where):
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError('%s 里的生成标记不是恰好一对' % where)
    a, b = text.index(BEGIN), text.index(END) + len(END)
    return text[:a] + block + text[b:]


def main(argv):
    check = '--check' in argv
    atext, acrlf = read(ARCHIVE)
    pits = parse_archive(atext)
    mtext, mcrlf = read(MANUAL)
    new_m = replace_block(mtext, render_main(pits), '工具手册')
    new_a = replace_block(atext, render_archive_index(pits), '坑档案')
    stale = [n for n, old, new in (('工具手册 §1', mtext, new_m), ('坑档案接口索引', atext, new_a)) if old != new]
    if check:
        if stale:
            print('过期，要重跑 python tools/manual_index.py：%s' % '、'.join(stale))
            return 1
        print('索引是最新的：%d 条坑' % len(pits))
        return 0
    if new_m != mtext:
        write(MANUAL, new_m, mcrlf)
    if new_a != atext:
        write(ARCHIVE, new_a, acrlf)
    order, rd = ranked(pits)
    print('写好了：%d 条坑；必读 %s' % (len(pits), ' '.join(p.num for p in order[:MUST_READ])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
