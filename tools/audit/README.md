# 审查工具（tools/audit）

两组脚本，都只用 Python 标准库：

1. `pcb_route_diff.py`：PCB 布线快照差异，离线比对，不连 bridge。
2. `codex_*.py`：审查方自己拉数据、自己解析的参考实现（工具手册 §6.4），按 `project.json` 找项目和证据目录。

两组脚本**导入时都不读文件、不连桥接、不打印**，配置和证据目录在真正用到时才加载。

## 一、PCB 布线快照差异

`pcb_route_diff.py` 比较已导出的 PCB 快照，并将创建返回值与实际独立回读对账。它只使用 Python 标准库，不连接 bridge、不启动 EDA、不切换页面；输入文件只读，仅写 `--output` 指定的报告。模块导入无输出、无 EDA 副作用。

### 用法

在设备项目目录执行，输出目录须已存在：

```text
python -X utf8 <本仓库>/tools/audit/pcb_route_diff.py --before audit/before.json --after audit/after.json --created audit/created.json --output audit/route-diff.json
```

- `--before`、`--after`、`--output` 必填；没有创建返回证据时可省略 `--created`。
- 标准输出只有一行紧凑 JSON：各类 added/removed/changed/unchanged 计数、创建回读计数和报告路径。完整坐标/属性差异写入报告，不向终端打印整板。
- 退出码 `0` 表示比较和报告生成完成，**存在差异也返回 0**；它不判定布线通过。输入缺失、格式不符、重复 ID、非有限坐标、输出覆盖输入或写入失败时返回 `2`，错误写入 stderr。
- 不会创建父目录，不可将输出路径指向任一输入文件；输出文件已存在时也会拒绝覆盖，避免销毁上一轮审计证据。调用前的备份、作用域检查和授权仍由执行批次负责。

### 输入约定

两个快照都必须含 `lines`、`vias`、`components`、`pads` 四个数组；确实没有某类图元时用空数组，不能省略字段来代表“未读取”。每个对象必须有唯一的非空字符串 `primitiveId`。

| 类别 | 必须的原生字段 |
|---|---|
| lines | net、layer、startX、startY、endX、endY、lineWidth |
| vias | net、x、y、diameter、holeDiameter |
| components | x、y、rotation、layer |
| pads | x、y、rotation、layer、net、padNumber、pad |

坐标、宽度、孔径和直径单位为 **mil**；组件角度为度，焊盘角度为弧度。输入可为 UTF-8 或 UTF-8 BOM。额外字段参与同 ID 对象的稀疏差异比较；包装字段 `async` 不参与。其它快照类别（如 pours/arcs）不在本工具比较范围。

`--created` 是原生 create 返回对象组成的数组，必须提供 `primitiveType`（Line/Via/Component/Pad）、`primitiveId` 及该类必需字段。不要把删除返回值、成功布尔值或错误结果混入这个数组。

可选 `layers` 来自原生层描述。提供时按 `type=SIGNAL` 且 `layerStatus=1/2`（SHOW/HIDDEN）的记录统计铜线，包含启用内层；隐藏显示不代表铜层被停用。没有完整可识别层描述时铜线分类报告 `unavailable`，仍比较全部 Line，绝不默认只算顶/底层。封装丝印 Line 也可能出现在快照中。

### 报告怎么读

- `categories`：每类计数、added/removed 的精简对象，以及 changed 的逐字段 before/after。线端、对象中心位移同时给 mil/mm；线宽、via 直径和孔径变化也给毫米换算。组件与焊盘角度变化统一另报度数。
- `created_readback`：create 返回同 ID 在 after 中是否存在、哪些字段改变、位置变化候选。`native_snapped_candidates` 指出创建返回与实际回读的坐标差异，**根因不由本工具判断**，不能据此认定是网格开关、用户拖动或持久化失败。
- LINE 起终点交换和等价角度不算物理变化；坐标/尺寸容差为 `0.001 mil`（`0.0000254 mm`），其余字段保持比较。真实对象 ID 替换仍照实列入 added/removed。
- 拆线、合并、替换旧 ID 和创建后被吸收都可能改变数量；**added/removed 或 created ID 缺失不自动判坏**。这份报告不检查连续铜覆盖、网络连通、DRC 或制造规范，相关结论须由其它证据建立。
- `sources` 保存输入路径与 SHA-256，便于对照对应批次，避免混用过时快照。

2026-09-11 在一块实板的小批布线试跑上验证过：识别出原生拆线（新增与删除的 Line 都照实列出）和 1 处 create 回读的位置差（X 向约 0.1 mm），其余旧对象无变化（实测记录未公开）。

## 二、审查方自拉数据（codex_*.py）

审查方不 import 执行方的导出 / 解析代码，自己从桥接拉、自己解析，再和执行方的说法逐项比。协议和"为什么"见工具手册 §6.4：执行方在审查时段不碰桥接；审查方只读（`openDocument` 切页读、`get*`），跑完切回运行前的前台文档（#51）；**跑之前确认用户不在 GUI 里**。脚本名里的 codex 只是沿用，谁当审查方都能用：

- **子代理红队（默认做法，通用手册 §9.7）**：子代理默认不碰桥接。第 1～2 步由执行方跑（同样这两条命令，产物落在 `audit_dir`），子代理只拿执行方导出的 `live.enet.txt` / `page-pN.json` 跑第 3 步以后的离线脚本。第 1～2 步要审查方自己拉，就按工具手册 §6.4 先让执行方让出桥接，审查时段只有审查方一个在用。
- **Codex 等第二个 AI 交叉审查（备用，通用手册 §9.1～§9.6）**：按 §6.4 自己跑第 1～2 步。

### 顺序

都在项目目录里跑（或设 `EDA_PROJECT`），证据写到 `project.json` 的 `audit_dir`（默认 `<项目根>/codex-live`）：

| 步骤 | 命令 | 读 | 写 |
|---|---|---|---|
| 1 冻结门禁 | `codex_independent_audit.py` | 网表目录里最新的冻结文件 | `live.enet.txt`、`freeze-gate.json`（记着对的是哪个冻结文件和它的 SHA）、每次调桥接的记录 |
| 2 逐页拉 | `codex_independent_audit.py pages` | `freeze-gate.json`（门禁要过，而且对的就是现役冻结文件） | 先清掉上一轮的 `page-p*.json`，再写 `page-p1.json`…`page-pN.json`（N = `pages` 条数） |
| 3 独立解析 | `codex_parse_netlist.py [执行方证据.md]` | `live.enet.txt` | `parsed.json`；给了证据文件再写 `netlist-comparison.json` |
| 4 几何 | `codex_geometry.py` | `parsed.json`、每一页的 `page-pN.json`（核对里面记的页 uuid 就是 `pages` 第 N 条） | `geometry.json`、`live-parts.json` |
| 5 去耦距离 | `codex_distances.py [执行方证据.md] [--pin U1-3 …]` | `parsed.json`、`live-parts.json` | `distance-comparison.json` |
| 6 属性 | `codex_properties.py` | `live-parts.json`、`catalog.json` | `property-audit.json` |
| 7 增量 | `codex_increment_check.py <旧冻结.enet.txt> [--expect 预期.json]` | `parsed.json`、旧冻结 | `increment-check.json` |

`catalog.json` 由审查方自己按 C 号拉（`lib_Device` 查到的库条目），存成 `{"C123": [条目, …]}`，每条要有 `supplierId` 和 `otherProperty`；本仓库不带采集脚本。

每次调桥接都留一份 `<名字>.json` 记录：成功记 `response`，失败记 `http_error` / `url_error`。拉取失败的页留下的就是失败记录，第 4 步会认出来、不当成页数据用。

退出码（输出一律 UTF-8，编不了的字符替换掉，管道是 GBK 也不会崩）：

- `0`：完成。
- `1`：核对没过，包括冻结门禁不一致、增量预期没对上。
- `2`：没法开始，打一句话说明原因和该先跑哪一步，不抛栈。原因可能是：
  - `project.json` 找不到、JSON 语法错，或者某项类型不对；
  - 缺上一步的产物，或产物和现在对不上（门禁记录不是现役冻结文件、页数据不是配置里那一页）；
  - 桥接连不上，或者返回了错误。

### project.json 里用到的键

通用键（`bridge`、`project_uuid`、`pages`、`netlist_dir`、`freeze_glob`、`nonelectrical_refs`）的含义见 `tools/project.py` 文件头。本目录另外用到：

- `audit_dir`：证据目录，相对项目根，默认 `codex-live`。
- `audit`：可选，各脚本的板级参数。都不配时，`codex_distances.py` 会报错要你给要查的引脚；`codex_increment_check.py` 只出报告、不核对；其它脚本不需要它。

```json
"audit": {
  "ground_net": "GND",
  "distances": {
    "pins": ["U1-3", "U2-1"],
    "claims": "review/执行方证据.md"
  },
  "renamed_catalog_keys": {"U1": {"器件上的旧属性名": "库里现在的属性名"}},
  "increment": {
    "non_bypass_nets": ["NET_EN"],
    "non_bypass_refs": ["C1"],
    "expect": {
      "ref_delta": 3, "net_delta": 1, "bypass_caps": 12, "non_bypass_caps": 2,
      "nets": {"NET_A": ["R1-1", "U1-6"]},
      "pins": {"U1": {"8": "NET_B"}}
    }
  }
}
```

| 键 | 谁用 | 含义 |
|---|---|---|
| `ground_net` | distances、increment | 地网名，默认 `GND` |
| `distances.pins` | distances | 要查的供电脚（`位号-脚号`）；脚所在的网就是供电网，列出同页上跨在这个网和地之间的电容及距离。命令行 `--pin`（可重复）优先 |
| `distances.claims` | distances | 执行方证据文件（相对项目根）；命令行第一个参数优先。给了才核对坐标和"最近去耦电容"的说法 |
| `renamed_catalog_keys` | properties | `{位号: {旧属性名: 库里现在的属性名}}`，原样写进报告，注明哪些"多出来的属性"其实是库改了名 |
| `increment.non_bypass_nets` | increment | 电容的脚全落在这些网（加上地）上就不算电源去耦，例如复位 / 使能脚上的延时电容 |
| `increment.non_bypass_refs` | increment | 直接点名不算去耦的电容位号 |
| `increment.expect` | increment | 本轮预期，键都可选、给了才核对：`ref_delta` / `net_delta`（新增减删除的位号数 / 网数）、`bypass_caps` / `non_bypass_caps`（两类电容个数）、`nets`（网成员，按集合比）、`pins`（`{位号: {脚号: 网名}}`）。以 `_` 开头的键算注释。命令行 `--expect 文件`（同结构）优先；没对上退出码 1，明细在输出的 `expectations.failed` |

类型先查再跑：`pins`、`non_bypass_nets`、`non_bypass_refs`、`nets` 里的成员表、`nonelectrical_refs` 必须是字符串列表，计数必须是整数，`ground_net`、`claims` 必须是字符串；不对就退出码 2，不会读到一半才崩。

### 执行方证据文件的格式（可选）

`codex_parse_netlist.py` 和 `codex_distances.py` 的比对输入是一份 Markdown，按二级标题的编号分节、按行解析：

- `## 1.` 器件表：`### p1 …` 这样的分页小标题下，每行 `` | `U1` | C 号 | Value | (x, y) | 1=网 2=网 … | ``；
- `## 2.` 去耦说法（distances 用）：`` - **`U1`** … 接 `+3V3` … `C1`(100nF,120) `C2`(1uF,180) ``，括号里是 Value 和中心距（取整）；
- `## 3.` 上下拉表：`` | `R1` | Value | … | 1 脚网 | 2 脚网 | ``；
- `## 4.` 网络表：`` | `网名` | 成员数 | 成员（空格分隔） | ``。

## 验证

在工具仓库目录运行（离线，不连 EDA、不联网；要配置的用例在临时目录造假的 `project.json`）。加不加 `-X utf8` 都要过：

```text
python -X utf8 -B -m unittest discover -s tools/audit -p "test_*.py" -v
python -B -m unittest discover -s tools/audit -p "test_*.py"
```

- `test_pcb_route_diff.py`：11 项，覆盖不变对象/存储规范化、拆线只报告差异、create 返回坐标偏移、网名/层/线宽改变、via 孔径/尺寸/位置、组件/焊盘几何、缺失数据/重复 ID、内层铜与丝印分类、创建对象缺失、CLI 输入保护与紧凑输出、静默导入。
- `test_codex_audit.py` 覆盖以下几块：
  - 几何相交 / T 接 / 浮点噪声、Protel 解析的格式细节（样本都是合成数据）；
  - 导入零副作用；缺 `project.json`、JSON 语法错、类型不对时说人话退出；
  - 几何按 `pages` 页数逐页审，页数据要对得上页 uuid；
  - `audit` 段各参数（引脚、证据文件、改名属性、增量预期）的读法；
  - 用假桥接离线跑通"门禁 → 逐页拉 → 几何"，包括门禁记录过期、桥接连不上、某页 HTTP 500；
  - 起子进程查退出码、stderr 有没有 Traceback，以及本机编码管道下输出 µ、✅ 不崩。
