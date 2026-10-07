"""器件属性审计：每个器件身上有哪些属性键，是它的库条目里现在没有的。

    python codex_properties.py

输入（都在 <audit_dir>）：live-parts.json（codex_geometry.py）、catalog.json（审查方自己按 C 号拉的库条目，
格式 {"C123": [lib_Device 查到的条目, …]}，每条要有 supplierId 和 otherProperty）。
project.json 可选：audit.renamed_catalog_keys = {"位号": {"器件上的旧属性名": "库里现在的属性名"}}，原样写进报告，
注明哪些"多出来的键"其实是库改了名；nonelectrical_refs 里的位号（Logo、二维码这类）跳过。输出 property-audit.json。
"""
import json, collections
from _paths import audit, cfg, fail, live_dir, read_json, str_list, utf8_stdio


def run():
    renamed = audit().get('renamed_catalog_keys') or {}
    if not isinstance(renamed, dict) or not all(isinstance(m, dict) and all(isinstance(v, str) for v in m.values())
                                                for m in renamed.values()):
        fail('project.json 的 audit.renamed_catalog_keys 要写成 {"位号": {"旧属性名": "新属性名"}}，现在是 %r' % (renamed,))
    skip = set(str_list(cfg().get('nonelectrical_refs'), 'project.json 的 nonelectrical_refs'))
    parts = read_json('live-parts.json', '先跑 codex_geometry.py')
    catalog = read_json('catalog.json', '审查方自己按 C 号拉一份库条目，存成 {"C123": [lib_Device 查到的条目, …]}')
    todo = [c for c in parts if c['ref'] not in skip]
    missing = sorted({(c['ref'], c.get('supplier')) for c in todo if not catalog.get(c.get('supplier') or '')})
    if missing:
        fail('catalog.json 里缺这些器件的库条目（位号, C 号）：%s —— 补拉后再跑；非电气件登记到 project.json 的 nonelectrical_refs' % missing)
    wrong = sorted({c['supplier'] for c in todo if any(r.get('supplierId') != c['supplier'] for r in catalog[c['supplier']])})
    if wrong:
        fail('catalog.json 里这些 C 号下的条目 supplierId 对不上：%s' % wrong)
    extra = {}
    for c in todo:
        rows = catalog[c['supplier']]
        standard = set().union(*(set(r.get('otherProperty', {})) for r in rows)) | {'Value', 'Description', 'Name'}
        difference = {k: v for k, v in (c.get('properties') or {}).items() if k not in standard}
        if difference: extra[c['ref']] = difference
    result = {'part_count': len(parts), 'parts_with_otherProperty': sum(bool(c.get('properties')) for c in parts),
              'duplicate_refs': [r for r, n in collections.Counter(c['ref'] for c in parts).items() if n > 1],
              'catalog_suppliers': len(catalog), 'skipped_nonelectrical': sorted(skip & {c['ref'] for c in parts}),
              'keys_not_in_current_catalog': extra, 'recognized_renamed_catalog_keys': renamed,
              'provenance_limit': 'getState_OtherProperty does not identify who created each property. Current catalog comparison is not original-library revision history.'}
    (live_dir() / 'property-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    utf8_stdio()
    run()
