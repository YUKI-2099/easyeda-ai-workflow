## 改了什么、为什么

<!-- 一两句话：改了哪个脚本 / 手册哪一节，解决什么问题；踩坑类的改动写上日期和 EDA 版本。 -->

## 提交前自查

- [ ] 这是**通用改进**：没有任何项目的名字、位号、网名、坐标、页 uuid、本机路径、截图里的项目内容（见 `AGENTS.md`「公开仓库写作规则」）
- [ ] `python -X utf8 tools/check_public.py` 零命中
- [ ] 两组离线测试全过：`python -X utf8 -m unittest discover -s tools -p "test_*.py"` 和 `-s tools/audit`
- [ ] 新功能或修的坑有测试，测试用的是编出来的数据
- [ ] 改了《坑档案》：坑编号没动，跑过 `python -X utf8 tools/manual_index.py` 重新生成 §1
- [ ] 会写 EDA 的脚本带"改前后网表 diff 只允许预期变化"的护栏
- [ ] 同意代码按 MIT、文档按 CC BY 4.0 随本仓库发布

## 怎么验证的

<!-- 跑了哪些测试；如果在真实 EDA 里实测过，写上 EDA 版本和结果（不要附带项目数据）。 -->
