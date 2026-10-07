# easyeda-ai-workflow

嘉立创EDA 专业版（EasyEDA Pro）的 **AI 作业手册 + 脚本工具链**。
用来让 Claude Code、Codex 这类 AI 编码助手，通过官方 [`easyeda-api` skill](https://github.com/easyeda/easyeda-api-skill) 和 [Run API Gateway 扩展](https://ext.lceda.cn/item/oshwhub/run-api-gateway)，**安全、可复核地**读写原理图和 PCB。

> *English summary: field notes and helper scripts for driving EasyEDA Pro (JLCEDA Pro) with AI coding agents through the official `easyeda-api` skill and the Run API Gateway extension. 80+ verified API pitfalls, standard schematic / PCB procedures, netlist freeze gates, and a review protocol. Docs are in Simplified Chinese.*

> **非官方项目**：本仓库与嘉立创、JLCPCB、EasyEDA 官方没有关系。"嘉立创EDA""EasyEDA"等名称和商标归各自所有者所有。

## 用之前先知道

- **脚本会直接改你打开的工程**：加件、连线、删件、改属性，然后保存。第一次用请在工程副本上试，或者先备份。
- **桥接能在 EDA 里执行任意 JavaScript**：官方桥只监听本机 `127.0.0.1`，不要把它暴露到网络上。
- **你正在界面里编辑时，不要让 AI 跑会切页的脚本**：只读脚本也会 `openDocument`，抢走你的前台标签页（工具手册 #51）。
- 手册里的结论大多带日期，实测版本见工具手册 §0.1。EDA 升级后个别结论可能失效，以你自己回读的结果为准。

## 里面有什么

| 路径 | 内容 |
|---|---|
| `docs/EDA工具手册-easyeda-api官方桥接-跨项目版.md` | **主手册**：起桥顺序、已知坑总索引（§1，自动生成）、调用速查、脚本索引（§2.14）、提速基准（§4）、交叉审查（§6）、维护规矩（§7） |
| `docs/EDA工具手册-坑档案.md` | 每条坑的**全文**：现象、实证、正解，以及标签 / 静默 / 摘要 / 命中等元数据 |
| `docs/EDA作业通用手册-跨项目版.md` | 方法手册：改图纪律、电路检查判据、BOM、版式、PCB 分组、**审查方法（§9）**、ESP32-S3 专章 |
| `docs/PCB操作手册-跨项目版.md` | PCB 布局、布线、铺铜的方法，以及 PCB 侧 API 实测（§1.4） |
| `tools/project.py` | 项目配置加载：`--project` > 环境变量 `EDA_PROJECT` > 从当前目录向上找 `project.json` |
| `tools/bridge.py` | 桥接调用层：`ex()` / `health()` / `find_bridge()` / `remember_doc()` |
| `tools/v2_*.py` | 原理图作业脚本：建图、加件、接线、换件、避线寻路、几何三查、冻结门禁、注释 |
| `tools/safe_rename.py` | 改板、原理图、页、PCB 的名字，改前后核对没改错对象 |
| `tools/pcb_geom.py`、`tools/pcb_route_batch.py` | PCB 坐标 / 镜像 / 网表的纯函数；按批追加走线和过孔（默认只编译不执行，见 `tools/PCB_ROUTING.md`） |
| `tools/audit/` | 审查方自己拉数据、自己解析的参考实现，以及快照差异工具 `pcb_route_diff.py` |
| `tools/manual_index.py` | 从《坑档案》生成工具手册 §1 和档案开头的接口索引；`--check` 只检查 |
| `tools/check_public.py` | 公开前自查：本机路径、IP、邮箱、密钥、板子专属 id，以及本地禁词表 |
| `tools/patch_easyeda_skill.py` | 可选：在官方 skill 的 `SKILL.md` 里加一段"先读本手册"的入口提示（默认只预览） |
| `templates/project_script.py` | 项目脚本样板：拷进**你自己的项目仓库**再改（只读核对 `project.json` 和当前工程对不对得上） |
| `project.example.json` | 项目配置模板 |
| `AGENTS.md` / `CLAUDE.md` | 给 AI 的入口（`CLAUDE.md` 只是引用 `AGENTS.md`） |

## 准备环境

1. 嘉立创EDA 专业版客户端（手册实测版本见工具手册 §0.1）。
2. 官方 [`easyeda-api` skill](https://github.com/easyeda/easyeda-api-skill)：放进你的 AI 工具的 skills 目录（例如 Claude Code 是 `~/.claude/skills/easyeda-api`），在那个目录跑一次 `npm install`（需要 Node.js）。没装依赖，起桥时会报 `ERR_MODULE_NOT_FOUND`（坑档案 #52）。
3. 在 EDA 里装 [Run API Gateway 扩展](https://ext.lceda.cn/item/oshwhub/run-api-gateway)，并在扩展管理里勾上「外部交互」权限，否则连不上 WebSocket。
4. Python 3.8 以上。脚本只用标准库；Windows 上建议一律加 `-X utf8`。

**起桥**：
1. 先跑 `python -X utf8 tools/bridge.py`，看有没有现成的 Bridge。
2. 没有的话，到 skill 目录后台运行 `node scripts/bridge-server.mjs`。
3. 在 EDA 菜单里点「API Gateway → 重新连接」。

顺序是**先 Bridge 后扩展**，而且全局只能有一个 Bridge：第二个会占下一个端口，看起来像"EDA 没开"（工具手册 §0.1～§0.5）。

## 快速开始

1. 把 `project.example.json` 拷到**你的项目目录**，改名为 `project.json`。填工程 uuid、各页 uuid、网表目录、冻结文件名模板、电源网。这个文件属于你的项目，不要提交到本仓库。
   页 uuid 在 EDA 里用 `eda.dmt_Schematic.getAllSchematicPagesInfo()` 取（工具手册 §2.9）；填完可以用 `templates/project_script.py` 改出来的脚本核对一遍。
2. 在项目目录里跑脚本（脚本会向上找 `project.json`），或者先设环境变量 `EDA_PROJECT=<项目目录>`：

   ```bash
   python -X utf8 <本仓库>/tools/bridge.py          # 扫端口找桥接、看 health
   python -X utf8 <本仓库>/tools/v2_geom_audit.py   # 几何三查（每页画完必跑）
   python -X utf8 <本仓库>/tools/v2_refreeze.py     # 总门禁；加 --freeze 写新冻结和 SHA
   ```

3. 让 AI 开工前先读工具手册 **§0 + §1 + §4**；做 PCB 再读 PCB 手册。顺序和铁律写在 [`AGENTS.md`](AGENTS.md)。
   在你项目的 `AGENTS.md` / `CLAUDE.md` 里加一行："EDA 作业先读 `<本仓库>/AGENTS.md` 和工具手册 §0、§1、§4"；或者用下面第 4 步的 skill 补丁。
4. 可选：`python -X utf8 tools/patch_easyeda_skill.py` 在官方 skill 的 `SKILL.md` 里加一段入口提示。默认只预览，加 `--apply` 才写，写之前自动备份。升级 skill 后要重跑。

## 和你自己的项目怎么配合

- 本仓库只放通用工具和样板。你的 `project.json` 和项目专用脚本，放在**你自己的项目仓库**里。
- 通用工具直接调用。项目专用脚本照 [`templates/project_script.py`](templates/project_script.py) 写，`import` 本仓库 `tools/` 的模块；不要把本仓库的脚本拷过去改。
- 项目里改出了通用的东西，先把项目数据剥成参数或 `project.json` 键，再提交回来。流程见 [`AGENTS.md`](AGENTS.md)「工具仓库和项目仓库的分工」。

## 审查怎么做

- **推荐：单会话子代理红队 + 对辩**（[通用手册 §9.7](docs/EDA作业通用手册-跨项目版.md)）。在同一个会话里开两个只读子代理独立审，结论回来后逐条对辩。不需要第二个 AI 工具。
- **备用：交叉审查**。用 Codex 等第二个 AI 做（通用手册 §9.1～§9.6、[工具手册 §6](docs/EDA工具手册-easyeda-api官方桥接-跨项目版.md)）。
- 两者可以叠加：子代理先审一轮，交叉审查做最后一道。

## 离线测试

不连 EDA、不联网：

```bash
python -X utf8 -m unittest discover -s tools -p "test_*.py"
python -X utf8 -m unittest discover -s tools/audit -p "test_*.py"
```

## 读手册前要知道

- 文档是简体中文。文中「用户」指操作 EDA 的人，「我 / 执行方」指执笔的 AI。
- 坑编号（①～㊿，之后是 #51、#52…）和章节号都**冻结**，只增不改。所以有空号、个别小节位置不按顺序，都属正常。
- 坑分两层：
  - 工具手册 §1 是索引和摘要，由 `tools/manual_index.py` 生成；
  - 全文在《坑档案》里。
- 要调某个接口前，先在《坑档案》开头的接口索引里搜一下接口名。

## 参与维护

- 新踩的坑当天写进《坑档案》（带日期、EDA 版本和实证），然后跑 `python -X utf8 tools/manual_index.py` 重新生成 §1。§1 的生成段不要手改，`tools/test_manual_index.py` 会拦。
- 这是公开仓库，提交前跑 `python -X utf8 tools/check_public.py`，要零命中。
  - 它查本机路径、IP、邮箱、密钥、板子专属 id。
  - 项目名、客户名这类禁词写在仓库根目录的 `.public-denylist.txt`，这个文件不进仓库。
- 设计数据不进本仓库：网表、`project.json`、审查抓取的数据、证据材料，都留在各自的项目目录里（`.gitignore` 已排除）。
- Pull Request 只收通用改进：不带任何项目数据，附上用合成数据写的测试。

## 许可证

- 代码（`tools/`、`templates/` 下的脚本）：[MIT](LICENSE)。
- 文档（`docs/` 下的手册，以及各处的 `*.md` 说明）：[CC BY 4.0](LICENSE-docs)。转载、改编请署名，并附上本仓库链接。
